"""属性字典查询与单位换算（`sde` 数据层的属性视图）。

职责：
1. 把引擎 / SDE 给出的**原始存储值**换算成物理值（秒 / 米 / 原值）；
2. 回答"这个属性叫什么、单位是什么、是否需要缩放"；
3. 提供常量表自检（`audit()`），供测试与后续 ingest 对拍。

硬约束（开发规范 §2 / §8）：
- **纯函数**：无 IO、无网络、无随机、无全局可变状态，可在任意进程直接调用；
- 只依赖 `evefit/config/attributes.py` 这一张纯常量表，**不 import 引擎、不读 SDE**；
- 缩放只允许出现在这里与投影层入口（§4.1：业务代码内部一律物理单位）；
- 未知属性 ID **不抛异常**（`name_of` 回 `attr_<id>`），因为引擎会返回大量
  未登记的派生属性，抛异常等于让整个视角失败。

缩放语义（契约 `AttributeRow.scale`）：
- `SCALE_ONE`：原值返回；
- `SCALE_MILLI`（0.001）：存储值是毫秒 → ×0.001；
- `SCALE_KILO`（1000.0）：存储值被放大 1000 倍 → ÷1000。
两种情况算术上都是 ×0.001，但**语义不同**，所以按"存储值比物理值大/小"分支，
不做 `float ==` 比较（红线 N11）。
"""

from __future__ import annotations

import math

from evefit.config import attributes as _attrs

# 缩放系数的比较容差。系数只有 0.001 / 1.0 / 1000.0 三档，取相对误差 1e-9
# 已远离任何浮点抖动（红线 N11 要求用 isclose 而非 ==）。
_SCALE_REL_TOL: float = 1e-9


def _constant_names() -> list[str]:
    """常量表里的全部 `ATTR_*` 名字（按字母序，保证 audit 输出稳定）。"""
    return sorted(name for name in dir(_attrs) if name.startswith(_attrs.CONSTANT_PREFIX))


def _constant_values() -> dict[str, int]:
    return {name: getattr(_attrs, name) for name in _constant_names()}


def _is_known_scale(scale: float) -> bool:
    return math.isclose(scale, _attrs.SCALE_MILLI, rel_tol=_SCALE_REL_TOL) or math.isclose(
        scale, _attrs.SCALE_KILO, rel_tol=_SCALE_REL_TOL
    )


def scale_of(attribute_id: int) -> float:
    """该属性的缩放系数：1.0 / 0.001 / 1000.0（语义见模块 docstring）。

    未登记的属性一律视为 1.0（原值）——这比抛异常安全：引擎的派生负 ID
    里常有我们没有登记的量，但"不缩放"对它们通常是正确默认。
    """
    return _attrs.SCALE_BY_ATTR.get(attribute_id, _attrs.SCALE_ONE)


def to_physical(attribute_id: int, raw: float) -> float:
    """原始存储值 → 物理值。

    - 毫秒类（`SCALE_MILLI`）：`2226.068` → `2.226068` 秒
    - ×1000 类（`SCALE_KILO`）：`391.67` → `0.39167` rad/s
    - 其余：原值返回（`alignTime` 的 `11.8611` 仍是 `11.8611` 秒）

    返回值恒为 `float`，便于统一做 `math.isclose` 比较。
    """
    scale = scale_of(attribute_id)
    value = float(raw)
    if scale < _attrs.SCALE_ONE:  # 0.001：存储值是毫秒，数值比物理值大
        return value * scale
    if scale > _attrs.SCALE_ONE:  # 1000.0：存储值被放大 1000 倍
        return value / scale
    return value


def is_scaled(attribute_id: int) -> bool:
    """是否需要缩放（等价于"该属性是否出现在缩放表里"）。"""
    scale = scale_of(attribute_id)
    return scale < _attrs.SCALE_ONE or scale > _attrs.SCALE_ONE


def unit_of(attribute_id: int) -> str | None:
    """物理单位标签（`s` / `m` / `hp` / `GJ` / `x` …）。

    `None` 表示"SDE 与语义都给不出"，调用方必须原样传递，**不得用猜测值
    填充**（红线 N4 的同一条精神：不用默认值冒充 `unknown`）。
    """
    return _attrs.UNIT_BY_ATTR.get(attribute_id)


def unit_id_of(attribute_id: int) -> int | None:
    """ESI/SDE 的 `unit_id` 原值（供 ingest 对拍；`None` 表示 ESI 未给）。"""
    return _attrs.UNIT_ID_BY_ATTR.get(attribute_id)


def name_of(attribute_id: int) -> str:
    """SDE canon 属性名；未登记的返回 `attr_<id>`，**永不抛异常**。"""
    return _attrs.NAME_BY_ATTR.get(attribute_id) or f"attr_{attribute_id}"


def audit() -> dict:
    """常量表自检：计数、重复值、孤儿行、单位标签一致性。

    返回结构（全部为纯计算，无 IO）：
    - `constant_count`：`ATTR_*` 常量个数
    - `attribute_count`：`ATTRIBUTES` 行数
    - `scaled_count` / `milli_count` / `kilo_count`：缩放项计数
    - `duplicate_values`：撞值的常量值（复制粘贴写错时会非空）
    - `non_int_constants`：不是 `int` 的常量名（`bool` 也算不合格）
    - `orphan_constants`：有常量但表里没有元数据的名字
    - `orphan_rows`：表里有元数据但没有常量名的属性 ID
    - `unknown_scale_values`：缩放表里出现非 0.001/1000.0 的系数
    - `unit_label_mismatches`：`unit_id` 推出的标签与实际单位不一致的属性 ID
    - `unit_none_count`：单位留空（确实无法确定）的属性个数
    - `consistent`：上面所有错误项为空时为 True
    """
    constants = _constant_values()
    values = list(constants.values())

    duplicate_values = sorted({value for value in values if values.count(value) > 1})
    non_int_constants = sorted(
        name
        for name, value in constants.items()
        if not isinstance(value, int) or isinstance(value, bool)
    )

    constant_ids = set(values)
    table_ids = set(_attrs.ATTRIBUTES)

    orphan_constants = sorted(
        name for name, value in constants.items() if value not in table_ids
    )
    orphan_rows = sorted(table_ids - constant_ids)

    unknown_scale_values = sorted(
        attr_id
        for attr_id, scale in _attrs.SCALE_BY_ATTR.items()
        if not _is_known_scale(scale)
    )

    unit_label_mismatches = sorted(
        attr_id
        for attr_id, unit_id in _attrs.UNIT_ID_BY_ATTR.items()
        if unit_id is not None
        and _attrs.UNIT_BY_ATTR.get(attr_id) != _attrs.UNIT_LABELS.get(unit_id)
    )

    scaled_ids = [attr_id for attr_id in constant_ids if is_scaled(attr_id)]
    milli_count = sum(
        1 for attr_id in scaled_ids if math.isclose(
            scale_of(attr_id), _attrs.SCALE_MILLI, rel_tol=_SCALE_REL_TOL
        )
    )
    kilo_count = sum(
        1 for attr_id in scaled_ids if math.isclose(
            scale_of(attr_id), _attrs.SCALE_KILO, rel_tol=_SCALE_REL_TOL
        )
    )

    return {
        "constant_count": len(constants),
        "attribute_count": len(_attrs.ATTRIBUTES),
        "scaled_count": len(scaled_ids),
        "milli_count": milli_count,
        "kilo_count": kilo_count,
        "duplicate_values": duplicate_values,
        "non_int_constants": non_int_constants,
        "orphan_constants": orphan_constants,
        "orphan_rows": orphan_rows,
        "unknown_scale_values": unknown_scale_values,
        "unit_label_mismatches": unit_label_mismatches,
        "unit_none_count": sum(1 for unit in _attrs.UNIT_BY_ATTR.values() if unit is None),
        "consistent": not (
            duplicate_values
            or non_int_constants
            or orphan_constants
            or orphan_rows
            or unknown_scale_values
            or unit_label_mismatches
        ),
    }
