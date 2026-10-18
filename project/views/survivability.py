"""视角二 · 生存 `survivability`（字段规格 §2）。

**混合 EHP 不是四系平均——这是本视角最容易被读错的一条，务必随值一起交代。**

`ehp_mixed` 既**不等于** `ehp_by_damage` 四值的算术平均，也**不等于**调和平均：
三层结构下两者都不成立。实测（样本 killmail 139246907）混合 **8854.959**、
算术平均 9352、调和平均 9219——三者互不相等。正确的算式只有按层复算：

    四系纯伤：EHP_i = Σ_layer  HP_layer / r_{i,layer}
    混合    ：EHP   = Σ_layer  HP_layer / Σ_i (w_i × r_{i,layer})

本模块的输出约定：

1. `ehp_mixed` **直接取引擎 `-43`**（不是自己算的），`layer_ehp` 取 `-30/-28/-29`，
   两者满足不变式 I1（`Σ 三层 EHP == ehp_mixed`，实测 1238.0952196 + 1786.6400489 +
   5830.2235037 = 8854.9587722，误差 0）。
2. `ehp_by_damage` 用**按层真实共振率复算**（`Σ_layer HP_layer / r_{i,layer}`），
   不换伤害剖面重算——两种口径都合法，本模块选前者，理由是：全程只用正 ID
   （护盾 271–274 / 护甲 267–270 / 结构 **113/111/109/110**，注意结构层不连号），
   四次纯单系重算(≈1.3 ms)可以省掉。已实测两者等价：引擎 `environment.damage_profile`
   传纯单系得到的 `-43` 与复算结果四位小数完全一致（EM 8549.5512 / EXP 8413.9770 /
   KIN 11347.1379 / THM 9095.4679）。
3. `repair` 的 `effective_ehp_s` 已经是「raw ÷ 加权共振率」，**直接用 `-48/-50/-52`**，
   `raw` 用 `-45/-47/-51`（`-46/-49` 是结构层）。护盾层有两个来源，分开给：
   `shield` = 主动盾修（`-47/-50`）、`shield_passive` = 被动回充（`-51/-52`）——
   两者相加才是护盾层总恢复，混在一起就分不清「模块修的」与「自己回的」。
4. **无来源的层给 `0.0`，不给 `None`**（T-19 裁决 C4，取代原 Q5 的 provenance 口径）：
   `raw_hp_s` / `effective_ehp_s` **一律是数**——引擎没输出 raw（本例没有盾修/结构修，
   `-47`/`-46` 不存在）时给 `0.0`，有效值 `-50`/`-49` 本来就是 `0.0`。下游数值 agent
   因此能直接算，不必自己补 0。**`None` 会让数值运算污染**，也让读三态的 agent 把
   「没带护盾回修器」误读成「不可知」——这两件事本轮分开处理。
5. **「有没有来源」是独立事实，用 `has_source`（bool）表达，不拿 provenance 当替身**：
   引擎**输出了**该分组的 raw 属性 → `has_source=True`；否则 `False`。于是「`0.0`
   且 `has_source=False`」= 这一层没有修复/回充来源，而「`0.0` 且 `has_source=True`」
   = 有来源但速率为 0（例如离线模块）——**两种零分得开**，这正是原实现想守的事实。
   护盾层两个来源各自一个分组、各自一个 `has_source`（见 §3），判「护盾层是否有任何
   恢复」= `shield.has_source or shield_passive.has_source`；不合并，否则又分不清
   「模块修的」与「自己回的」。注意被动回充是**舰体自带**（有护盾容量就有，实测各样本
   `-51` 恒 ≈ 3.047 hp/s），所以 `shield_passive.has_source` 在几乎所有舰船上都是
   `True`——这是事实，不是噪声。
6. **provenance 不强行改判**：这些 `0.0` 仍按 `FIELD_SOURCES` 的属性级实测量表
   （`noobbaozi/project/provenance.py`）打标：`-45/-48/-49/-50/-51/-52` → `assumed`；
   `-46/-47` 在实测表里是 `unknown`（本样本没有结构修/盾修模块，引擎不输出该派生值，
   表里逐条记了原因）。本视角**不手工塞 `measured`**：属性级表只能表达「这个值由哪个
   属性决定」，**无法表达「因为没装模块所以是 0」**；真正的解法是两级模型（属性级类 +
   实例级缺值覆盖，PROGRESS §5 欠账 24），不在本轮。
   `has_source` 登记为 `None`（合成值 → 渲染层标 `assumed`），与既有约定一致。
7. `overload` 用 `ctx.attrs("overload")` 与 `"base"` 对比：实测混合 EHP 不变
   （超载不改共振率），护甲修复 32.500 → 42.059 hp/s（有效 69.126 → 89.457）。
8. 消耗品续航不在这里（字段规格 §2 尾注：归「不稳定」视角）。

`FIELD_SOURCES` 的映射约定同火力视角：单属性决定的给属性 ID，多属性合成值给 `None`。
"""

from __future__ import annotations

import math

from noobbaozi.config import attributes as _a
from noobbaozi.project import units
from noobbaozi.project.views import register
from noobbaozi.project.views.base import BASE, OVERLOAD, ViewContext

VIEW_NAME = "survivability"

TIE_REL_TOL: float = 1e-9
"""判定「四系共振率并列」的相对容差（红线 N11：不用 `float ==`）。"""

EHP_MIXED_NOTE: str = (
    "混合 EHP 不是四系平均（既非算术平均也非调和平均）：三层结构下两者都不成立。"
    "本值直接取引擎 -43；正确算法是 Σ_layer HP_layer / Σ_i(w_i × r_i,layer)。"
)
"""随 `ehp_mixed` 一起输出的口径提示（字段规格 §2 的强制说明）。"""

DAMAGE_TYPES: tuple[str, ...] = ("em", "explosive", "kinetic", "thermal")

LAYERS: tuple[tuple[str, int, int], ...] = (
    ("shield", _a.ATTR_SHIELD_CAPACITY, _a.ATTR_EHP_SHIELD),
    ("armor", _a.ATTR_ARMOR_HP, _a.ATTR_EHP_ARMOR),
    ("hull", _a.ATTR_HULL_HP, _a.ATTR_EHP_HULL),
)
"""层名 → （基础 HP 属性, 分层 EHP 属性）：263/265/9 与 -30/-28/-29。"""

RESONANCE_ATTRS: dict[str, tuple[int, int, int, int]] = {
    "shield": (
        _a.ATTR_SHIELD_EM_RESONANCE,
        _a.ATTR_SHIELD_EXPLOSIVE_RESONANCE,
        _a.ATTR_SHIELD_KINETIC_RESONANCE,
        _a.ATTR_SHIELD_THERMAL_RESONANCE,
    ),
    "armor": (
        _a.ATTR_ARMOR_EM_RESONANCE,
        _a.ATTR_ARMOR_EXPLOSIVE_RESONANCE,
        _a.ATTR_ARMOR_KINETIC_RESONANCE,
        _a.ATTR_ARMOR_THERMAL_RESONANCE,
    ),
    "hull": (
        _a.ATTR_HULL_EM_RESONANCE,
        _a.ATTR_HULL_EXPLOSIVE_RESONANCE,
        _a.ATTR_HULL_KINETIC_RESONANCE,
        _a.ATTR_HULL_THERMAL_RESONANCE,
    ),
}
"""三层真实共振率（**正 ID**）。

结构层四个 ID **不连号**：EM=113 / EXP=111 / KIN=109 / THM=110（PROGRESS §3 第 31 条）。
顺序统一按 `DAMAGE_TYPES`（EM / EXP / KIN / THM）。
"""

REPAIR_LAYERS: tuple[tuple[str, int, int], ...] = (
    ("shield", _a.ATTR_SHIELD_BOOST_RATE, _a.ATTR_SHIELD_EFFECTIVE_BOOST_RATE),
    (
        "shield_passive",
        _a.ATTR_PASSIVE_SHIELD_RECHARGE,
        _a.ATTR_PASSIVE_SHIELD_EFFECTIVE_RECHARGE,
    ),
    ("armor", _a.ATTR_ARMOR_REPAIR_RATE, _a.ATTR_ARMOR_EFFECTIVE_REPAIR_RATE),
    ("hull", _a.ATTR_HULL_REPAIR_RATE, _a.ATTR_HULL_EFFECTIVE_REPAIR_RATE),
)
"""修复来源 → （raw 属性, 有效属性）。护盾分主动盾修与被动回充两条（见 docstring §3）。"""

FIELD_SOURCES: dict[str, int | None] = {
    "layers.shield_hp": _a.ATTR_SHIELD_CAPACITY,
    "layers.armor_hp": _a.ATTR_ARMOR_HP,
    "layers.hull_hp": _a.ATTR_HULL_HP,
    "layer_ehp.shield": _a.ATTR_EHP_SHIELD,
    "layer_ehp.armor": _a.ATTR_EHP_ARMOR,
    "layer_ehp.hull": _a.ATTR_EHP_HULL,
    "ehp_by_damage.em": None,
    "ehp_by_damage.explosive": None,
    "ehp_by_damage.kinetic": None,
    "ehp_by_damage.thermal": None,
    "ehp_mixed": _a.ATTR_EHP,
    "ehp_mixed_note": None,
    "thinnest.shield.damage_type": None,
    "thinnest.shield.ehp": None,
    "thinnest.armor.damage_type": None,
    "thinnest.armor.ehp": None,
    "thinnest.hull.damage_type": None,
    "thinnest.hull.ehp": None,
    "repair.shield.has_source": None,
    "repair.shield.raw_hp_s": _a.ATTR_SHIELD_BOOST_RATE,
    "repair.shield.effective_ehp_s": _a.ATTR_SHIELD_EFFECTIVE_BOOST_RATE,
    "repair.shield_passive.has_source": None,
    "repair.shield_passive.raw_hp_s": _a.ATTR_PASSIVE_SHIELD_RECHARGE,
    "repair.shield_passive.effective_ehp_s": _a.ATTR_PASSIVE_SHIELD_EFFECTIVE_RECHARGE,
    "repair.armor.has_source": None,
    "repair.armor.raw_hp_s": _a.ATTR_ARMOR_REPAIR_RATE,
    "repair.armor.effective_ehp_s": _a.ATTR_ARMOR_EFFECTIVE_REPAIR_RATE,
    "repair.hull.has_source": None,
    "repair.hull.raw_hp_s": _a.ATTR_HULL_REPAIR_RATE,
    "repair.hull.effective_ehp_s": _a.ATTR_HULL_EFFECTIVE_REPAIR_RATE,
    "overload.ehp_delta": None,
    "overload.repair_delta": None,
}
"""overview 点路径 → 来源属性 ID。

`ehp_by_damage.*` 是「三层各自的共振率 + 三层 HP」的合成值（每系要三个共振率，
指不到单一属性），`thinnest.*` 与 `overload.*` 同理，故 `None`。

`repair.<layer>.has_source` 是 `_has_source()` 从「raw 属性是否存在」导出的布尔
（合成值，指不到单一属性）→ `None`（渲染层据此标 `assumed`，与既有约定一致）。
它**不**代替 provenance：`raw_hp_s` 仍按各自属性 ID 打标（`-45/-47/-51` → `assumed`），
「没装模块所以是 0」这个事实由这两级模型欠账（PROGRESS §5 第 24 条）解决，不在这里硬塞。
"""


# =============================================================================
# 小工具
# =============================================================================


def _value(raw: float | None) -> float | None:
    """物理值 → 输出值（统一 3 位小数）；缺值保持 `None`（不补 0，红线 N4）。"""
    return None if raw is None else units.fmt(raw)


def _layer_hp(attrs: dict[int, float]) -> dict[str, float | None]:
    """三层基础 HP（263/265/9）；缺失层给 `None`。"""
    return {
        layer: attrs.get(hp_attribute_id)
        for layer, hp_attribute_id, _ehp_attribute_id in LAYERS
    }


def _resonances(attrs: dict[int, float], layer: str) -> tuple[float, ...] | None:
    """某层四系共振率；任一项缺失返回 `None`（不拿部分数据充完整矩阵）。"""
    values = tuple(attrs.get(attribute_id) for attribute_id in RESONANCE_ATTRS[layer])
    if any(value is None for value in values):
        return None
    return tuple(float(value) for value in values)  # type: ignore[arg-type]


def _ehp_by_damage(
    attrs: dict[int, float], layer_hp: dict[str, float | None]
) -> dict[str, float | None]:
    """四系纯伤 EHP = `Σ_layer HP_layer / r_{i,layer}`（不换伤害剖面重算，见 docstring §2）。"""
    result: dict[str, float | None] = {}
    for index, damage_type in enumerate(DAMAGE_TYPES):
        total = 0.0
        usable = True
        for layer, _hp_attribute_id, _ehp_attribute_id in LAYERS:
            hp_value = layer_hp[layer]
            resonances = _resonances(attrs, layer)
            if hp_value is None or resonances is None or resonances[index] <= 0.0:
                usable = False
                break
            total += hp_value / resonances[index]
        result[damage_type] = units.fmt(total) if usable else None
    return result


def _thinnest(attrs: dict[int, float], layer_hp: dict[str, float | None]) -> dict[str, dict]:
    """每层最薄系 + 该系下的该层 EHP。

    「最薄」= 抗性最弱 = **共振率最大**那一系（受到的伤害最多）。
    该层 EHP = `HP_layer / r_{该系,layer}`；共振率矩阵不全时两项都给 `None`。

    **四系共振率相同时 `damage_type` 给 `None`**（并列第一就不存在「最薄系」）。
    这不是罕见情况：一块 Damage Control II 会把结构层四系压成同一个值，实测样本
    结构层 EM/EXP/KIN/THM 全等于 0.402（2343.75 / 5830.224）——此时报「EM 最薄」
    是编出来的结论，宁可留空。`ehp` 仍然照给（四系同值，这个数是对的）。
    """
    result: dict[str, dict] = {}
    for layer, _hp_attribute_id, _ehp_attribute_id in LAYERS:
        resonances = _resonances(attrs, layer)
        hp_value = layer_hp[layer]
        if resonances is None or hp_value is None:
            result[layer] = {"damage_type": None, "ehp": None}
            continue

        index = max(range(len(resonances)), key=lambda position: resonances[position])
        resonance = resonances[index]
        tied = sum(
            1
            for other in resonances
            if math.isclose(other, resonance, rel_tol=TIE_REL_TOL, abs_tol=0.0)
        )
        result[layer] = {
            "damage_type": None if tied > 1 else DAMAGE_TYPES[index],
            "ehp": None if resonance <= 0.0 else units.fmt(hp_value / resonance),
        }
    return result


def _has_source(attrs: dict[int, float], raw_attribute_id: int) -> bool:
    """该修复分组**有没有来源**：引擎输出了这一组的 raw 属性即算有。

    只判 raw 的**存在性**（不看大小）：`-48/-49/-50` 这类有效值引擎恒输出
    （无模块时是 `0.0`），拿它判来源会把「没装模块」误判成「有来源」。
    """
    return attrs.get(raw_attribute_id) is not None


def _repair(attrs: dict[int, float]) -> dict[str, dict[str, float | bool | None]]:
    """每个修复分组的 `{has_source, raw_hp_s, effective_ehp_s}`。

    `effective_ehp_s` 已经是「raw ÷ 加权共振率」，直接用引擎的有效值，**不**自己再除一次。

    值口径（T-19 裁决 C4）：**无来源给 `0.0`，不给 `None`**——`None` 会污染下游数值运算，
    也会把「没装模块」误读成「不可知」。「有没有来源」由 `has_source` 独立表达；
    `provenance` 不承担这个语义（见模块 docstring §4–§6 与两级模型欠账）。
    """
    result: dict[str, dict[str, float | bool | None]] = {}
    for layer, raw_attribute_id, effective_attribute_id in REPAIR_LAYERS:
        raw = attrs.get(raw_attribute_id)
        effective = attrs.get(effective_attribute_id)
        result[layer] = {
            "has_source": _has_source(attrs, raw_attribute_id),
            "raw_hp_s": 0.0 if raw is None else units.fmt(raw),
            "effective_ehp_s": 0.0 if effective is None else units.fmt(effective),
        }
    return result


def _effective_repair_total(attrs: dict[int, float]) -> float:
    """三层（含护盾两条来源）有效修复率之和，供 `overload.repair_delta` 用。"""
    total = 0.0
    for _layer, _raw_attribute_id, effective_attribute_id in REPAIR_LAYERS:
        value = attrs.get(effective_attribute_id)
        if value is not None:
            total += value
    return total


def _overload_block(ctx: ViewContext) -> dict[str, float | None]:
    """过载前后：混合 EHP 的变化与修复速率（有效值合计）的变化。"""
    base_attrs = ctx.attrs(BASE)
    overload_attrs = ctx.attrs(OVERLOAD)

    base_ehp = base_attrs.get(_a.ATTR_EHP)
    overload_ehp = overload_attrs.get(_a.ATTR_EHP)
    return {
        "ehp_delta": (
            None if base_ehp is None or overload_ehp is None else units.fmt(overload_ehp - base_ehp)
        ),
        "repair_delta": units.fmt(
            _effective_repair_total(overload_attrs) - _effective_repair_total(base_attrs)
        ),
    }


# =============================================================================
# 视角入口
# =============================================================================


@register(VIEW_NAME)
def build(ctx: ViewContext) -> dict:
    """构建生存视角 overview。

    Args:
        ctx: 视角上下文（只读）。

    Returns:
        `{"view": "survivability", "overview": {...}, "flags": []}`——
        `flags` **恒为空**：可疑点由渲染层统一算一次（见模块 docstring）。
    """
    attrs = ctx.attrs(BASE)
    layer_hp = _layer_hp(attrs)

    overview = {
        "layers": {
            "shield_hp": _value(layer_hp["shield"]),
            "armor_hp": _value(layer_hp["armor"]),
            "hull_hp": _value(layer_hp["hull"]),
        },
        "layer_ehp": {
            layer: _value(attrs.get(ehp_attribute_id))
            for layer, _hp_attribute_id, ehp_attribute_id in LAYERS
        },
        "ehp_by_damage": _ehp_by_damage(attrs, layer_hp),
        "ehp_mixed": _value(attrs.get(_a.ATTR_EHP)),
        "ehp_mixed_note": EHP_MIXED_NOTE,
        "thinnest": _thinnest(attrs, layer_hp),
        "repair": _repair(attrs),
        "overload": _overload_block(ctx),
    }
    return {"view": VIEW_NAME, "overview": overview, "flags": []}
