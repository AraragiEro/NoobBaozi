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
   实测 active 相对 online：速度 ×5.89、信号半径 ×5.5（385 / 70）、对齐 +47.6%。3. 键名说明（两份文档口径不同，这里统一交代）：
   - `"base"` = **当前状态**（引擎导入规则的 state；本样本推进模块是 active，
     所以与 `"active"` 数值相同，但语义不同，照实分开给）；
   - `"off"` = 全部模块 `offline`（任务书里写作 `offline` 档，`acceptance/field_spec.json`
     的子键名是 `off`，此处取后者以免下游对不上）；
   - `"propulsion_off"` = **只把推进模块置 offline**、其余模块保持当前状态
     （T-35 补的第五档：这才是「推进关闭」的真实状态，也是 `mwd_sig_increase` 文案
     里「关闭时」那个数的来源，见下）；
   - `"active"` = 全部模块 `active`（用 `engine/scenarios.build_variant` 造变体，
     不手写状态注入——变体语义只有 `scenarios` 一份）；
   - `"overload"` = 全部模块 `overload`。
   **为什么必须有第五档（T-35 / f67 实测）**：`off` 是**全部模块**离线，f67 里
   `off.sig_m = 60.0`（船体裸值）；而诊断 flag 说的「推进关闭」是只关推进，
   实测 `65.72 m` —— 差的 5.72 m 是 `Small Shield Extender II` 这类模块的信号半径罚值，
   它不属于推进模块。两个数各自都对，但**同一视角里出现两个「关闭时」**就是自相矛盾
   （agent 无法判断该信哪个）。现在「推进关闭」有了独立字段，flag 文案里的数字与
   `propulsion_states.propulsion_off.*` 逐位相同（`tests/test_views_a.py` 钉住），
   `off` 的语义**不变**（仍是全模块离线）。构造只写一份：`views/shared.py` 的
   `propulsion_off_attrs()`（诊断规则也调它）。
   ⚠️ 本档**不是**求解：它不挑「保留 AB 还是保留 MWD」，双推进样本里两个都关；
   「只开 AB / 只开 MWD」那种分支由 `"single"` 表达（T-43，见第 4 条）。
4. **`"single"` 是「单推进」分支（T-43 新增）**：列表，每个已装**推进类**模块一条
   `{module, speed_mps, sig_m, align_time_s}` —— 语义是「**只启用这一个**推进模块，
   其余推进模块置 offline，其它模块保持当前状态」（构造在 `shared.with_single_propulsion()`,
   与 `propulsion_off` 同一套写法）。「推进类」= `config/activation_groups.json` 登记的
   group 46 / 1189 / 1533（AB/MWD · MJD · MJFG）：Panther 的 `MWD + MJD` 是两条不同组
   的推进模块，只按 group 名数会把它数成 1 个。
   它回答的是**玩家真实会用的状态**：`base` / `active` 是「引擎按导入规则把能开的全开」，
   双推进样本里那是个**不可达状态**（同一时刻只能启用其一，见诊断 `state_exclusive`），
   拿它当真值就会读出 f67 的 59,928 m/s 这种数。
   - **顶层 `speed_mps` / `signature_radius_m` 数值不变**（仍是引擎全 active 值）：它是既有
     契约字段，改口径会动 L0/L1 与全部验收基线。可达性提示由诊断 flag `state_exclusive`
     的文案给出（「顶层数值按这些模块同时启用计算（不可达状态）」），不在本层加字段
     （L1 体积预算紧，`render.BUDGET_CHARS = 17,000`）。
   - 只有 **0 或 1 个**推进模块时给 `[]`：没有可选状态，不额外计算（省一次引擎调用）。
   - 顺序 = 装配顺序（`shared.propulsion_positions()` 的原下标顺序），不按数值排序：
     读者要靠顺序对上「船上那两件」，排序会让它变成另一套清单。
5. `mass` 也随推进状态变化（实测 offline 1050000 / active 1550000，差 500000
   正是 5MN MWD 的 massAddition）。本视角按字段规格只给**当前状态**的 `mass_kg`；
   其它状态下的质量需要时请用对齐时间反推（`align ∝ mass × agility`），本层不另设字段。
6. 进场时间 = `align_time_s` + 跃迁飞行时间；后者引擎不给、属自研公式，不在本视角
   （字段规格 §3 明确：单配装视角只给 `warp_speed_au_s` 与对齐时间）。

`FIELD_SOURCES` 的映射约定同其它视角：单属性决定的给属性 ID，合成值给 `None`。
"""

from __future__ import annotations

from noobbaozi.config import attributes as _a
from noobbaozi.engine import scenarios
from noobbaozi.project import units
from noobbaozi.project.views import register, shared
from noobbaozi.project.views.base import BASE, OFFLINE, OVERLOAD, ViewContext, ship_attributes

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

STATE_KEYS: tuple[str, ...] = ("base", "off", "propulsion_off", "active", "overload")
"""`propulsion_states` 的五档（语义见模块 docstring §3）。

第六个键 `"single"` 不是「一档状态」而是一组状态（每个推进模块各一条），所以不进
`STATE_KEYS`：`summary._propulsion_state()` 之类的「逐档相等比对」只该看单值档。
"""

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
    "propulsion_states.propulsion_off.speed_mps": _a.ATTR_MAX_VELOCITY,
    "propulsion_states.propulsion_off.sig_m": _a.ATTR_SIGNATURE_RADIUS,
    "propulsion_states.propulsion_off.align_time_s": _a.ATTR_ALIGN_TIME,
    "propulsion_states.active.speed_mps": _a.ATTR_MAX_VELOCITY,
    "propulsion_states.active.sig_m": _a.ATTR_SIGNATURE_RADIUS,
    "propulsion_states.active.align_time_s": _a.ATTR_ALIGN_TIME,
    "propulsion_states.overload.speed_mps": _a.ATTR_MAX_VELOCITY,
    "propulsion_states.overload.sig_m": _a.ATTR_SIGNATURE_RADIUS,
    "propulsion_states.overload.align_time_s": _a.ATTR_ALIGN_TIME,
    # T-43 的「单推进」分支：容器本身是合成值（整张清单由已装模块枚举而来）→ `None`；
    # 三个量仍是同一组属性的重算结果。**容器必须登记**：只有 0/1 个推进模块时
    # `single` 是空列表，渲染层会把它折叠成容器叶子 `propulsion_states.single`，
    # 没登记就落 `unknown` → `reasons.py` 里没有 mobility 的条件字段规则 → `unclassified`
    # （`test_render.py` 的守门断言「出现即缺陷」会立刻红）。
    "propulsion_states.single": None,
    "propulsion_states.single[].speed_mps": _a.ATTR_MAX_VELOCITY,
    "propulsion_states.single[].sig_m": _a.ATTR_SIGNATURE_RADIUS,
    "propulsion_states.single[].align_time_s": _a.ATTR_ALIGN_TIME,
}
"""overview 点路径 → 来源属性 ID（各档都是同一组属性的不同状态重算结果）。

`propulsion_states.single[].module` 是**模块名**（字符串，元素内的定性字段），渲染层靠
容器/元素登记给三态；它不在这里登记属性 ID（`FIELD_SOURCES` 只放 `int` 或 `None`），
而由 `"propulsion_states.single": None` 的容器回退覆盖（`render._source_of()` 第 2 条）。
"""


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


def _single_states(ctx: ViewContext) -> list[dict]:
    """「只启用这一个推进模块」的逐模块清单（模块 docstring §4）。

    只有 **0 / 1 个**推进模块时返回 `[]`：没有可选状态，也不浪费一次引擎调用。
    顺序 = 装配顺序；每个条目一条 `{module, speed_mps, sig_m, align_time_s}`。

    「推进模块」= **推进类**（`shared.propulsion_class_positions()`：配置表登记的 group 46 /
    1189 / 1533），不是只有 group 名 `Propulsion Module` —— Panther 的 `MWD + MJD` 是两条
    不同组的推进模块，只按 group 名数会把它数成 1 个（T-43 的跨组口径同一个来源）。

    `module` 是**模块名**而不是「保留哪个」的裁决：本视角只枚举事实，不替玩家决定开哪个。
    """
    positions = shared.propulsion_class_positions(ctx)
    if positions is None or len(positions) < 2:
        return []

    items = ctx.items(BASE)
    entries: list[dict] = []
    for position in positions:
        attrs = shared.propulsion_single_attrs(ctx, position)
        if attrs is None:
            continue  # 判定不了就跳过（不拿 base 冒充，红线 N4）
        block = _state_block(attrs)
        entries.append(
            {
                "module": items[position].get("name"),
                "speed_mps": block["speed_mps"],
                "sig_m": block["sig_m"],
                "align_time_s": block["align_time_s"],
            }
        )
    return entries


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
    # 推进模块整体关闭（其余模块保持当前状态）——flag `mwd_sig_increase` 的「关闭时」
    # 就是这个状态，两边共用 `shared.propulsion_off_attrs()` 的同一次计算（T-35）。
    propulsion_off = shared.propulsion_off_attrs(ctx)

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
            # 没有索引（判定不了哪些是推进模块）→ 三字段 `None`，不用 base 冒充
            "propulsion_off": _state_block(propulsion_off or {}),
            "active": _state_block(_active_attrs(ctx)),
            "overload": _state_block(ctx.attrs(OVERLOAD)),
            # T-43：玩家真实会用的状态（只开一个）；0 / 1 个推进模块时是 `[]`。
            # 顶层 `speed_mps` / `signature_radius_m` **不改口径**（仍是引擎全 active 值），
            # 「那组值不可达」由诊断 flag `state_exclusive` 说出口（见模块 docstring §4）。
            "single": _single_states(ctx),
        },
    }
    return {"view": VIEW_NAME, "overview": overview, "flags": []}
