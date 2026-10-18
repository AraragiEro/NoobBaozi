"""投影层单位与数值换算（L3）。

职责边界（开发规范 §4.1 / 软件设计 §7.4）：

- 这是投影层**唯一**允许出现「毫秒 / ×1000 缩放」语义的入口；业务代码只拿物理值
  （秒 / 米 / hp / GJ …），不得自行乘除 1000（红线 N5：对外不输出毫秒与 ×1000 原值）。
- **缩放规则不在这里，也不允许在这里重写**：一律委托
  `evefit.sde.attribute_dict.to_physical`。缩放表只有一份，手工维护在
  `evefit/config/attributes.py`（`SCALE_BY_ATTR`，8 项 = 6 毫秒 + 2 ×1000）。
  这是软件设计 §7.4 的明确要求，也是 P0-3 交付物的既有接口。
- 全部纯函数：无 IO、无网络、无随机、无全局可变状态（红线 N1），可在任意进程直接调用。

为什么不在这里再写一张缩放表：两张表一定会漂移，而漂移的后果是「同一个属性在
两个视角里差 1000 倍」——这类错误在输出层几乎不可发现。所以这里只有转发。
"""

from __future__ import annotations

import math

from evefit.sde import attribute_dict as _dict

DEFAULT_DIGITS: int = 3
"""`fmt` 的默认小数位。字段规格 §0.1 的 overview 体量下 3 位足够，且能吃掉浮点噪声。"""

MS_PER_S: float = 1000.0
"""1 秒 = 1000 毫秒。毫秒 ↔ 秒的唯一换算系数（禁止在别处裸写 1000）。"""

PERCENT: float = 100.0
"""比率 → 百分比的乘数。"""


def physical(attribute_id: int, raw: float) -> float:
    """原始存储值 → 物理值（委托 `attribute_dict.to_physical`）。

    例（实测，SDE build 3607705 / 引擎 13.6.0）：
    - `51`（speed，存储毫秒）`2226.068` → `2.226068` 秒
    - `160`（trackingSpeed，×1000）`391.67` → `0.39167` rad/s
    - `1985`（stanceSwitchTime，存储毫秒）`10000.0` → `10.0` 秒

    未登记缩放的属性原值返回（`alignTime` 的 `11.8611` 仍是 `11.8611` 秒）。
    """
    return _dict.to_physical(attribute_id, raw)


def pct(ratio: float) -> float:
    """共振率 → 抗性百分比（`0.875` → `12.5`，即常说的「12.5% 抗性」）。

    语义依据字段规格 §2：**真实抗性 = 共振率**，正 ID（护盾 271–274 / 护甲 267–270 /
    结构 109·110·111·113）；抗性百分比只是共振率的展示形式，不参与任何叠加计算
    （模块加成一律按共振率相乘，硬结论 4）。

    调用方负责用 `fmt` 去噪；本函数只做算术。
    """
    return (1.0 - float(ratio)) * PERCENT


def ms_to_s(milliseconds: float) -> float:
    """毫秒 → 秒（显式换算，供投影层内部对非缩放表数值使用）。"""
    return float(milliseconds) / MS_PER_S


def s_to_ms(seconds: float) -> float:
    """秒 → 毫秒（仅在需要回填引擎输入时使用，不得出现在对外输出里）。"""
    return float(seconds) * MS_PER_S


def fmt(value: float, digits: int = DEFAULT_DIGITS) -> float:
    """统一小数位，把浮点噪声挡在输出之外（`0.30000000000000004` → `0.3`）。

    - `-0.0` 归一成 `0.0`（否则序列化后出现「负零」，读起来像误差）；
    - 非有限值（NaN / inf）直接 `ValueError`：宁可炸，也不让 NaN 流进 JSON
      （JSON 里 NaN 不是合法数，序列化后会出现 `NaN` 字样，调用方无法解析）。
    """
    if not isinstance(digits, int) or isinstance(digits, bool) or digits < 0:
        raise ValueError(f"digits 必须是非负整数，实际是 {digits!r}")

    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"fmt 只接受有限数值，实际是 {value!r}")

    rounded = round(number, digits)
    # 红线 N11：不用 float ==；这里要判的正是「是否为零」，故给零容差 isclose。
    if math.isclose(rounded, 0.0, rel_tol=0.0, abs_tol=0.0):
        return 0.0
    return rounded
