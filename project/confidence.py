"""输出层可信度 `confidence`（A–E 的**输出子集**）—— 与 provenance **正交**的第二个维度。

**约定（T-18，先读这条）**：`confidence` 是稀疏表，缺省 = `"A"`；消费方必须先按叶子
路径补齐默认值再查表。

为什么要单独一层（T-17 裁决）
----------------------------
字段规格 §10.2 承诺过「可信度」，但 P2 的渲染层只给了 `provenance`（measured / assumed /
unknown）。两者回答的**不是同一个问题**：

- `provenance` 回答「这个值**是否依赖假设**」（技能档 / 模块状态 / 伤害剖面 / 模式）；
- `confidence` 回答「这个值的**口径可信到什么程度**」（有没有对拍过、能不能用、建没建模）。

典型反例（必须记住，否则很容易把两个维度合成一张表）：

- `firepower.dph` 是合成值 → `assumed`，但不变式 I2 严格成立 → `A`；
- `firepower.application.tracking_rad_s` 直接取自引擎 `160 trackingSpeed`（数值本身
  就是引擎算的，provenance 该给什么给什么），但**命中率没建模** → `confidence = E`。

**红线：两者正交、不可互推。** 不得用本表反推 provenance（`--` 字段规格的
「可信度」列不得用于反推三态，见 PROGRESS §3 第 50 条），也不得把「未建模 / 不可用」
塞进 provenance 的三态里——那是本表的事。

三档语义（输出层只产生这三档）
------------------------------
- **A**：口径已实测对拍。输入直接决定或已与外部基线 / 不变式严格对上，输出可以直接当结论用。
- **C**：不可用。引擎**不给值**（例如 `-70 maxTargets` 实测三轴恒为 0，镜像里根本没有
  这个派生），或该字段的原料已确认但口径不可用于判断。看到 `C` 就不要拿它做结论。
- **E**：未建模。视角**只给参数、不给结论**（应用层命中率、爆炸半径这类），
  agent 拿到值后必须自己建模才能用。

**B（「需换算」）在输出层已不存在**：换算（毫秒 → 秒、×1000 → 物理值）已经全部由
投影层 `views/base.py` 完成（`units.physical()`），渲染层拿到的一律是物理值，
所以「还需不需要换算」这个旧档位在输出层没有意义。`D`（需对拍 / 属推断）同理收敛为 `A`：
输出层没有第三档可表达「半可信」，与其造一个没有判据的中间档，不如默认 A 并在
`confidence_basis` 的归属处（字段规格文档）保留讨论。

键空间与 `provenance` 一致，但输出是**稀疏**的
-----------------------------------------------
`CONFIDENCE[view]` 的键是**同一个点路径**（`_leaf_paths()` 的产物，数组元素用
`weapons[].family` 约定），未被登记的路径一律 `DEFAULT_CONFIDENCE = "A"`。

**`confidence` 是稀疏表，缺省 = `"A"`；消费方必须先按叶子路径补齐默认值再查表。**
具体含义：

- 渲染层只为每个视角输出**登记成非 A 的那些叶子**（`!= DEFAULT_CONFIDENCE`），
  未登记的路径**不出现在表里**——默认值不需要被显式写出来；
- 于是 `views[<view>]["confidence"]` 的键是 `provenance` 键的**子集**（可以是空表
  `{}`，但**键恒在**，schema 稳定）；
- 消费方标准写法：先按 `provenance` 的键（或 `_leaf_paths()`）补齐
  `DEFAULT_CONFIDENCE`，再用本表覆盖；直接 `confidence[path]` 取未登记的路径会
  `KeyError`——那是**用法错误**，不是「这个字段不可信」。

T-17 曾经是全量平行表（162 个叶子每条都写一遍，其中约 159 条是 `"A"`），实测
`+5,093 chars`（13,519 → 18,954），迫使 `BUDGET_CHARS` 抬到 23,000——只为重复声明
「这个是 A」，不值。T-18 改成稀疏后，同一份样本的 `views` 回到 ~13,700 chars
（见 `render.BUDGET_CHARS` 的实测注释）。

"""

from __future__ import annotations

DEFAULT_CONFIDENCE: str = "A"
"""未登记路径的默认档位：`A`（口径已实测对拍）。

默认给 A 而不是 C/E，理由是「默认」必须等于**没有额外保留**；反过来把未知项默认成
C/E 会让「登记」失去意义（每条都得显式写 A），也没法区分「漏登记」与「真的不可用」。
"""

CONFIDENCE_LEVELS: frozenset[str] = frozenset({"A", "B", "C", "D", "E"})
"""合法取值域（A–E 全五档）。

输出层**只产生** `A` / `C` / `E` 三档（见模块 docstring）；`B` / `D` 留在域里是因为
字段规格文档仍用它们描述口径讨论，这里保留合法域以免下游把文档值当非法输入直接炸。
"""

CONFIDENCE: dict[str, dict[str, str]] = {
    "firepower": {
        # 应用层参数：**只给参数、不给命中结论**（视角 §5 自己写明了）。
        # 引擎给的是跟踪速度 / 爆炸半径 / 爆炸速度这些「原料」，任何「够不够打中」
        # 的结论都要 agent 自己建命中模型 → E（未建模）。
        "application.tracking_rad_s": "E",
        "application.explosion_radius_m": "E",
        "application.explosion_velocity_mps": "E",
    },
}
"""视角名 → 点路径 → 可信度。**只登记不等于 A 的路径**（其余默认 A）。

键必须与 `render._leaf_paths()` 的产物同形（数组元素用 `a[].b`，整数键逐级展开）；
登记在**容器**路径上时，元素路径会向上继承（见 `confidence_of()`），所以
「整张列表都不信」只需要登记一条容器路径。
"""

FIELDS_FROM_MAX_TARGETS: tuple[str, ...] = ()
"""来自 `-70 maxTargets` 的字段路径 → 判 `C`（不可用）。

`-70` 实测三轴（技能 / 状态 / 剖面）恒为 0，引擎**没有实现**这个派生（`provenance.py`
的 `ATTR_MAX_TARGETS_UNVERIFIED` 与此同源）。任何视角若把 `maxTargets` 暴露成字段，
都必须登记在这里判 `C`——「引擎不给值」属「不可用」，不是「依赖假设」也不是「未建模」。

当前为**空**：六个视角都没有输出 `maxTargets` 字段（实测 grep 过，见 T-17 报告）。
"""


def _container_of(path: str) -> str | None:
    """`weapons[].family` → `weapons`；不含 `[]` 时返回 `None`。"""
    head, marker, _rest = path.partition("[]")
    return head if marker else None


def _int_key_container_of(path: str) -> str | None:
    """`skill_floor.count_by_level.4` → `skill_floor.count_by_level`（尾段是整数才继承）。"""
    head, _dot, tail = path.rpartition(".")
    return head if head and tail.isdigit() else None


def confidence_of(view: str, path: str) -> str:
    """一个 `(视角, 点路径)` → 可信度档位（A / C / E）。

    **语义在 T-18 没有变**：未登记的路径仍然返回 `DEFAULT_CONFIDENCE`（`A`）。稀疏化只
    发生在**渲染层**——`render._confidence_block()` 只把 `!= DEFAULT_CONFIDENCE` 的项写进
    输出，本函数照旧可以逐叶子问「这条是什么档」，是渲染层与测试共用的唯一口径。

    查表顺序（与 `render._tag()` 的三态查表同构，便于两套表一起维护）：

    1. `path` 命中 `FIELDS_FROM_MAX_TARGETS` → `C`（引擎不给值，最高优先：
       即使某视角误登记成 A，也不能冒充「可用」）；
    2. `path` 命中 `CONFIDENCE[view]` → 用它；
    3. 否则若路径在数组元素里，退回**容器**路径（`weapons[].family` → `weapons`）；
    4. 否则若路径是**整数键字典的元素路径**（`count_by_level.4`），退回容器路径；
    5. 都没有 → `DEFAULT_CONFIDENCE`（`A`）。

    Raises:
        ValueError: 登记值不在 `CONFIDENCE_LEVELS` 里（写错档位会立刻炸，不静默降级）。
    """
    if path in FIELDS_FROM_MAX_TARGETS:
        return _checked("C", view, path)

    table = CONFIDENCE.get(view) or {}
    for candidate in (
        path,
        _container_of(path),
        _int_key_container_of(path),
    ):
        if candidate is not None and candidate in table:
            return _checked(table[candidate], view, path)
    return DEFAULT_CONFIDENCE


def _checked(level: str, view: str, path: str) -> str:
    """校验档位合法并归一成大写（`CONFIDENCE` 是手写表，写错必须炸出来）。"""
    normalized = str(level).strip().upper()
    if normalized not in CONFIDENCE_LEVELS:
        raise ValueError(
            f"{view}.{path} 的可信度档位非法：{level!r}；合法值 = {sorted(CONFIDENCE_LEVELS)}"
        )
    return normalized


__all__ = [
    "CONFIDENCE",
    "CONFIDENCE_LEVELS",
    "DEFAULT_CONFIDENCE",
    "FIELDS_FROM_MAX_TARGETS",
    "confidence_of",
]
