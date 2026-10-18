"""视角三 · 机动 `mobility`（字段规格 §3）。

只给数据，不给判词（红线：判词归 agent）——本模块不写「跑不掉」「太慢」这类结论，
也不设任何绝对阈值。

口径要点（实测，推翻请重跑脚本）：

1. **单位**：`ctx.attrs()` 的值已经是物理单位。`-1 alignTime` **引擎给的就是秒**
   （实测 11.8611345863），不得再 ÷1000；`37 maxVelocity` 是 m/s（1271.613）；
   `552 signatureRadius` 是米（385.0）；`4 mass` 是 kg（1550000.0）；`70 agility`
   无量纲（5.52）；`600 warpSpeedMultiplier` 的数值**就是 AU/s**（实测 4.5，
   既不是 600 也不是 4.5×3）。
2. **状态影响是数量级的**，所以三档必须都给：
   `base`（当前状态）速度 1271.613 / 信号 385.0 / 对齐 11.861；
   `off`（全模块 offline）216.0 / 70.0 / 8.035；
   `overload`（全模块 overload）1799.419 / 385.0 / 11.861。
   实测 active 相对 online：速度 ×5.89、信号半径 ×5.5（385 / 70）、对齐 +47.6%。
3. 键名说明（两份文档口径不同，这里统一交代）：
   - `"base"` = **当前状态**（引擎导入规则的 state；本样本推进模块是 active，
     所以与 `"active"` 数值相同，但语义不同，照实分开给）；
   - `"off"` = 全部模块 `offline`（任务书里写作 `offline` 档，`acceptance/field_spec.json`
     的子键名是 `off`，此处取后者以免下游对不上）；
   - `"active"` = 全部模块 `active`（用 `engine/scenarios.build_variant` 造变体，
     不手写状态注入——变体语义只有 `scenarios` 一份）；
   - `"overload"` = 全部模块 `overload`。
4. `mass` 也随推进状态变化（实测 offline 1050000 / active 1550000，差 500000
   正是 5MN MWD 的 massAddition）。本视角按字段规格只给**当前状态**的 `mass_kg`；
   其它状态下的质量需要时请用对齐时间反推（`align ∝ mass × agility`），本层不另设字段。
5. 进场时间 = `align_time_s` + 跃迁飞行时间；后者引擎不给、属自研公式，不在本视角
   （字段规格 §3 明确：单配装视角只给 `warp_speed_au_s` 与对齐时间）。

`FIELD_SOURCES` 的映射约定同其它视角：单属性决定的给属性 ID，合成值给 `None`。
"""

from __future__ import annotations

from evefit.config import attributes as _a
from evefit.engine import scenarios
from evefit.project import units
from evefit.project.views import register
from evefit.project.views.base import BASE, OFFLINE, OVERLOAD, ViewContext, ship_attributes

VIEW_NAME = "mobility"

ACTIVE_VARIANT: str = "mobility:active"
"""临时变体缓存键（`calculate_variant` 的 key，不得与预置变体名冲突）。"""

ACTIVE_SPEC: dict = {
    "name": ACTIVE_VARIANT,
    "states": {scenarios.ALL_KEY: "active"},
    "mode_id": None,
}
"""`"active"` 档的变体描述：全部已装模块 `active`。

语义由 `engine/scenarios.build_variant` 提供（与本文件夹 `base.py` 的预置变体同一套
机制），这里**不**手写状态注入、也不另设默认假设（红线 N9）。
"""

STATE_KEYS: tuple[str, ...] = ("base", "off", "active", "overload")
"""`propulsion_states` 的四档（语义见模块 docstring §3）。"""

STATE_FIELDS: tuple[tuple[str, int], ...] = (
    ("speed_mps", _a.ATTR_MAX_VELOCITY),
    ("sig_m", _a.ATTR_SIGNATURE_RADIUS),
    ("align_time_s", _a.ATTR_ALIGN_TIME),
)
"""每档要给的三个量 → 属性 ID（37 / 552 / -1）。"""

FIELD_SOURCES: dict[str, int | None] = {
    "speed_mps": _a.ATTR_MAX_VELOCITY,
    "align_time_s": _a.ATTR_ALIGN_TIME,
    "signature_radius_m": _a.ATTR_SIGNATURE_RADIUS,
    "mass_kg": _a.ATTR_MASS,
    "agility": _a.ATTR_AGILITY,
    "warp_speed_au_s": _a.ATTR_WARP_SPEED_MULTIPLIER,
    "propulsion_states.base.speed_mps": _a.ATTR_MAX_VELOCITY,
    "propulsion_states.base.sig_m": _a.ATTR_SIGNATURE_RADIUS,
    "propulsion_states.base.align_time_s": _a.ATTR_ALIGN_TIME,
    "propulsion_states.off.speed_mps": _a.ATTR_MAX_VELOCITY,
    "propulsion_states.off.sig_m": _a.ATTR_SIGNATURE_RADIUS,
    "propulsion_states.off.align_time_s": _a.ATTR_ALIGN_TIME,
    "propulsion_states.active.speed_mps": _a.ATTR_MAX_VELOCITY,
    "propulsion_states.active.sig_m": _a.ATTR_SIGNATURE_RADIUS,
    "propulsion_states.active.align_time_s": _a.ATTR_ALIGN_TIME,
    "propulsion_states.overload.speed_mps": _a.ATTR_MAX_VELOCITY,
    "propulsion_states.overload.sig_m": _a.ATTR_SIGNATURE_RADIUS,
    "propulsion_states.overload.align_time_s": _a.ATTR_ALIGN_TIME,
}
"""overview 点路径 → 来源属性 ID（各档都是同一组属性的不同状态重算结果）。"""


# =============================================================================
# 小工具
# =============================================================================


def _value(raw: float | None) -> float | None:
    """物理值 → 输出值（统一 3 位小数）；缺值保持 `None`（不补 0，红线 N4）。"""
    return None if raw is None else units.fmt(raw)


def _active_attrs(ctx: ViewContext) -> dict[int, float]:
    """`"active"` 档的船体属性（全部已装模块 active）。"""
    variant_fit = scenarios.build_variant(ctx.fit, ACTIVE_SPEC)
    return ship_attributes(ctx.calculate_variant(ACTIVE_VARIANT, variant_fit))


def _state_block(attrs: dict[int, float]) -> dict[str, float | None]:
    """一档推进状态下的 `{speed_mps, sig_m, align_time_s}`。"""
    return {name: _value(attrs.get(attribute_id)) for name, attribute_id in STATE_FIELDS}


# =============================================================================
# 视角入口
# =============================================================================


@register(VIEW_NAME)
def build(ctx: ViewContext) -> dict:
    """构建机动视角 overview。

    Args:
        ctx: 视角上下文（只读）。

    Returns:
        `{"view": "mobility", "overview": {...}, "flags": []}`——
        `flags` **恒为空**：可疑点由渲染层统一算一次（见模块 docstring）。
    """
    base_attrs = ctx.attrs(BASE)

    overview = {
        "speed_mps": _value(base_attrs.get(_a.ATTR_MAX_VELOCITY)),
        "align_time_s": _value(base_attrs.get(_a.ATTR_ALIGN_TIME)),
        "signature_radius_m": _value(base_attrs.get(_a.ATTR_SIGNATURE_RADIUS)),
        "mass_kg": _value(base_attrs.get(_a.ATTR_MASS)),
        "agility": _value(base_attrs.get(_a.ATTR_AGILITY)),
        "warp_speed_au_s": _value(base_attrs.get(_a.ATTR_WARP_SPEED_MULTIPLIER)),
        "propulsion_states": {
            "base": _state_block(base_attrs),
            "off": _state_block(ctx.attrs(OFFLINE)),
            "active": _state_block(_active_attrs(ctx)),
            "overload": _state_block(ctx.attrs(OVERLOAD)),
        },
    }
    return {"view": VIEW_NAME, "overview": overview, "flags": []}
