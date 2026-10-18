"""`unknown` 的原因（T-32 / 字段规格 §0.2.1「方案 A」）—— 三态**不变**，每个 `unknown` 多一条 `reason`。

为什么需要它（第 20 轮裁决，PROGRESS §3 第 179 条）
--------------------------------------------------
`unknown` 这个态今天装着五种完全不同的情况，而下游（agent）对它们的处置方式正好相反：

| `reason` | 含义 | 真实例子 | 处置 |
|---|---|---|---|
| `input_incomplete` | **输入数据不完整** | f4 的 `dph: null`（killmail 不带 mutation） | 换途径补数据，或降低置信度 |
| `not_applicable` | **该字段对本配装不适用** | 炮船的 `application.explosion_radius_m`（没导弹） | 直接忽略 |
| `ambiguous` | **无唯一答案** | `thinnest.hull.damage_type`（四系抗性并列） | 说「四系相同」，不是「不知道」 |
| `engine_omits` | **引擎永不提供** | `-70 maxTargets`（恒缺） | 别用这个字段 |
| `unclassified` | 兜底（**应为 0，出现即待补规则**） | — | 视为缺陷上报 |

**为什么不加第四态**：三态是验收基线（36 样本 / 1548 字段）的一部分，加态等于整条基线重算；
而「同一个值到底为什么不可知」是**附加信息**，不是替代信息。所以裁决走「方案 A」：
三态原样，`unknown_reasons` 是**稀疏平行表**，只在 `provenance == "unknown"` 的路径上出现：

```json
"views": {"firepower": {
  "overview": {...},
  "provenance": {"dph": "unknown"},
  "unknown_reasons": {"dph": "input_incomplete"}
}}
```

因此本表的三条不变量（与 `confidence` 的稀疏表口径**同构**，渲染层 `_reason_block()` 保证）：

1. `set(unknown_reasons) ⊆ set(provenance)`，且**每个 `unknown` 叶子恰好一条**（不多不少）；
2. 取值只允许 `UNKNOWN_REASONS` 里的五个字符串（写错即炸，不静默降级）；
3. 值**不是** `unknown` 的路径**不出现**（稀疏：默认情况不需要被写出来）。

判定优先级（**命中即停**，与规格 §0.2.1 逐字对应）
---------------------------------------------------
1. **有 flag 直接解释该字段** → `input_incomplete`。flag 是**本次计算**里发现的自相矛盾
   （§0.6），它比任何静态推断都强：`weapon_damage_zero`（有弹药却零伤害）直接把 `dph`
   判成输入不完整，即使 `dph` 同时是「条件字段」。映射表见 `FLAG_REASONS`。
2. **该字段是条件字段** → `not_applicable`。条件字段 = **只有某条件成立时才产出**的字段
   （有导弹才有爆炸半径、有无人机才有 `dps.drone`、电容会耗尽才有 `depletes_in_s`、
   当前技能档有缺口才有 `tier_violations`…）。条件不成立时值必然是 `None`，
   所以「不适用」是正确解读，不是「不可知」。清单见 `CONDITIONAL_LEAVES` /
   `CONDITIONAL_SUBTREES`（按视角分组，因为同一个路径片段在不同视角里语义可能不同）。
3. **来源属性在「引擎永不提供」清单** → `engine_omits`。清单见 `ENGINE_OMITS_ATTRS`
   （当前只有 `-70 maxTargets`：三轴实测恒为 0，引擎镜像里根本没有这个派生）。
   **注意顺序**：条件字段（2）排在它前面——`application.tracking_rad_s` 若某天被指到 `-70`，
   仍然是「这套配装没有炮」更准确。
4. **字段语义是「在多值中选一个」且并列** → `ambiguous`。规格点名两组：`*.damage_type`
   （四系选一）与 `thinnest.*`（每层选一）。典型：一块 `Damage Control II` 把结构层四系
   共振率压成同一个值 → 「最薄系」不存在。见 `AMBIGUOUS_SUFFIXES` / `AMBIGUOUS_SUBTREES`。
5. 否则 → `unclassified`：**这是缺陷信号**，说明缺一条规则。渲染层照实输出（不隐瞒），
   但 `tests/test_render.py` 的守门测试对四个真实样本断言它的数量为 **0**
   ——「出现即缺陷」不是口号，是那条断言。

两条工程约束
------------
- **本模块是纯函数**：不读文件、不碰引擎、不看实例值，只按路径 / 来源属性 / flag id 判。
  于是它可以在没有引擎的机器上单测（`tests/test_projection_core.py`）。
- **表必须指向真实字段**：`CONDITIONAL_LEAVES` 的每条路径、`CONDITIONAL_SUBTREES` 的每个
  前缀都要能在对应视角的 `FIELD_SOURCES` 里找到（`tests/test_render.py` 有反查断言），
  否则打错一个字就会让那条规则**永不生效**、悄悄退化成 `unclassified`。

与「三态」的分工（别混）
------------------------
`provenance` 回答「这个值是否依赖假设」；本模块回答「当它就是不可知时，**为什么**」。
两者都不是「可信度」——那是 `project/confidence.py` 的事（A/C/E，正交维度）。
"""

from __future__ import annotations

from typing import Iterable, Mapping

from noobbaozi.config import attributes as _a

UNKNOWN_REASONS: tuple[str, ...] = (
    "input_incomplete",
    "not_applicable",
    "engine_omits",
    "ambiguous",
    "unclassified",
)
"""合法的 `reason` 取值（**顺序 = 判定优先级**，最后一个是兜底）。

取值域是**封闭**的：`reason_for()` 只会返回这五个之一，多一个都算篡改契约。
"""

FALLBACK_REASON: str = "unclassified"
"""兜底取值 —— **出现即缺陷**（缺一条规则），守门测试断言真实样本上它的数量为 0。"""

REASON_DOCS: dict[str, str] = {
    "input_incomplete": "输入数据不完整（killmail 不带 mutation、值自相矛盾…）",
    "not_applicable": "该字段对本配装不适用（没有导弹 / 没有无人机 / 电容稳定…）",
    "engine_omits": "引擎永不提供（-70 maxTargets：镜像里没有这个派生）",
    "ambiguous": "无唯一答案（四系抗性并列，选不出「最薄系」）",
    "unclassified": "兜底：还没有规则能解释它 —— 出现即缺陷，不要用它冒充结论",
}
"""每个 `reason` 的中文含义（探针 / 报表 / 文档共用同一份措辞）。"""

ARRAY_MARKER: str = "[]"
"""数组元素约定里的标记（`cap_by_module[].cap_use_gj_s`），匹配前会被规范化掉。"""


# =============================================================================
# 一、规则 1：flag → 它直接解释的字段
# =============================================================================

FLAG_REASONS: dict[str, tuple[str, ...]] = {
    "weapon_damage_zero": ("dph",),
}
"""flag id → 它**直接解释**的字段路径（命中即判 `input_incomplete`）。

- `weapon_damage_zero`（`project/diagnostics.py`）：有带弹药的武器却零齐射 → 输入不完整
  （字段规格 §0.6 第一类：killmail 不暴露 Abyssal/mutated 的 `mutation`）。
  对应视图行为就是 `firepower.dph = None`，所以这两个字段是同一件事的两面。

新增「自相矛盾」类 flag 时在这里登记它解释哪个字段；**不要**在视角里另写一套判据。
"""


# =============================================================================
# 二、规则 3：「引擎永不提供」的属性（来源属性 → engine_omits）
# =============================================================================

ENGINE_OMITS_ATTRS: frozenset[int] = frozenset({_a.ATTR_MAX_TARGETS})
"""引擎**永远不会**产出的属性 ID（实测三轴恒为 0 / 镜像里根本没有这个派生）。

当前只有 `-70 maxTargets`：`project/provenance.py` 把它手工覆盖成 `unknown`（不是 assumed：
它既不是「依赖假设」也不是「实测稳定」，而是「引擎没在算」）。于是任何以它为来源的字段
都只能判 `engine_omits`。

**与常量表同源**：用 `config/attributes.py` 的 `ATTR_MAX_TARGETS`，不写裸数字（红线 N12）；
`tests/test_render.py` 另有断言把它与 `provenance.UNKNOWN_ATTRS` 钉在一起（两表不得漂移）。
"""


# =============================================================================
# 三、规则 2：条件字段（只有某条件成立时才产出 → not_applicable）
# =============================================================================

CONDITIONAL_LEAVES: dict[str, frozenset[str]] = {
    "firepower": frozenset(
        {
            # 没有武器 → 火力字段整块不适用（f26 航母：载机停在机库、炮位全空）
            "weapons",
            "ammo_endurance_s",
            "dph",
            # 没有无人机 / 战斗机 → 这两条 DPS 不适用（f1 Hecate 两者都没有）
            "dps.drone",
            "dps.fighter",
            # 武器没装填弹药（无人机/战斗机/空发射器）→ 没有「弹药名」可言
            "weapons[].ammo_name",
            # 没有射程（无炮 / 无导弹：f26）→ 过载前后的射程变化也不存在。
            # **T-37 收窄**：原先整个 `overload` 前缀挂在本表对面的 `CONDITIONAL_SUBTREES`
            # 里，把 `overload.*.dps` 也顺带说成「不适用」；现在三档 `dps` 在「一个武器系统
            # 都没有」时统一给 `0.0`（有值），再留着前缀就是「有值却说不适用」的污染
            # （T-34 消灭过的那类错误）。所以只把**确实只有条件成立才产出**的三条射程叶子
            # 登记在这里，`dps` 三条不再被任何条件规则覆盖。
            "overload.off.range_m",
            "overload.on.range_m",
            "overload.delta.range_m",
            # **T-40 补回（缺陷 1）**：T-37 把 `dps.base` / `dps.with_reload` /
            # `overload.{off,on,delta}.dps` 从本表整条删掉，理由是「一个武器系统都没有时
            # 它们已被补齐成 `0.0`，有值就不再需要说不适用」。但 **`reason_for()` 只对
            # `provenance == "unknown"` 的路径求值** —— 删掉规则之后，**值为 `None`** 的
            # 那两类配装就没有任何规则可匹配，落进 `unclassified` 兜底（硬门缺陷）。
            # 教训（文档 §8.2）：**删一条规则前先枚举它当前在哪些样本上生效**，而不是只想
            # 「现在有值了所以用不上」。
            #
            # 这两条路径值仍是 `None` 的样本与 `dph` **完全同源**，归属也必须是同一个：
            # 实测（T-40，`ctx.attrs(BASE)` 逐键核对）—— f12 / f34 的船级
            #   `-11 damageAlpha` / `-12 damageDps` / `-13 damageDpsWithReload`
            #   **三个键全部不存在**（`-14 droneDps` / `-58 fighterDps` 同样不存在），
            # 而对照样本 f67 三个键都在（均为 `0.0`）。
            # 更进一步：引擎的判据**不是** T-36 猜的「至少有一个高槽模块」——
            # f12 有 4 件 `Large Remote Armor Repairer II`、f34 有 5 件
            # `Small Knave Scoped Energy Nosferatu`（都在 high 槽），照样没有这三个键；
            # f67 之所以有，是因为它有一件 `Expanded Probe Launcher I`（装了探针、
            # 逐件 `-21 volley` = `0.0`）。也就是说：**引擎只在「至少有一件模块产出了
            # `-21`（伤害管线）」时才派生 `-11`/`-12`/`-13`**，而这两套配装的模块一件
            # `-21` 都没有（后勤/ECM 无人机也没有伤害属性）。
            # 结论：值缺失的原因是「这套配装没有可产出伤害的模块」= 该字段对本配装
            # **不适用**（`not_applicable`），与同一配装 `dph` 的判定逐字相同。
            # **不是** `input_incomplete`（killmail 没缺数据：后勤/ECM 无人机本来就不产生
            # 伤害，换渠道补数据也变不出 dps），也**不是** `engine_omits`（那是「引擎对
            # 任何配装都不提供」，如 `-70 maxTargets`；`-12` 对 f67 等配装是照给的）。
            # **有值时这些条目完全不生效**（reason 只为 unknown 求值），所以与 T-37 的裁决
            # 不冲突：f67 的 `0.0` 仍是 `assumed`，不会被说成「不适用」。
            "dps.base",
            "dps.with_reload",
            "overload.off.dps",
            "overload.on.dps",
            "overload.delta.dps",
        }
    ),
    "resources": frozenset(
        {
            # 没有 rig 时引擎不输出 `1152 upgradeLoad`（f3/f6 裸船样本），没有「标定占用」可给
            "calibration.load",
        }
    ),
    "constraints": frozenset(
        {
            # 一个模块都不要求技能（f6 极简样本）→ 「门槛上限」不存在
            "skill_floor.max_level",
        }
    ),
    "stability": frozenset(
        {
            # 电容稳定（引擎 `-7 = -1.0` 哨兵）→ 没有「耗尽时长」这件事（见 shared.depletes_in_s）
            "capacitor.depletes_in_s",
            # 没有耗电模块（f6）→ 引擎不给 `-4 capPeakLoad`
            "capacitor.peak_load",
        }
    ),
}
"""视角名 → **叶子级**条件字段路径（命中即 `not_applicable`）。

判定为「条件字段」的依据必须写在这里的注释里（凭什么说它「对本配装不适用」），
否则下一个人会把「真的缺数据」也塞进来 —— 那会把 `input_incomplete` 吃掉。
"""

CONDITIONAL_SUBTREES: dict[str, tuple[str, ...]] = {
    "firepower": (
        # 炮才有 tracking / 签名分辨率；导弹才有爆炸半径 / 爆炸速度（字段规格 §1 的 application 行）
        "application",
        # 只有带四系伤害的弹药/无人机才有「伤害构成」；战斗机的中队条目没有四系伤害（f26）
        "damage_split",
        # 炮才有 54/158 三档射程；导弹才有 max_range_m（无武器时全为 None）
        "range",
        # **T-37 起这里没有 `overload` 前缀**：`overload.{off,on,delta}.dps` 在「一个武器系统
        # 都没有」时已是 `0.0`（有值），前缀会把有值字段说成「不适用」；三条射程叶子改在
        # `CONDITIONAL_LEAVES` 里逐条登记（它们才是真的条件产出）。要恢复前缀必须同时把
        # `dps` 三条从 `FIELD_SOURCES` 的有值集合里排除 —— 那等于把 T-37 的裁决推翻。
        # **T-40**：`dps.base` / `dps.with_reload` / `overload.{off,on,delta}.dps` 这五条走的是
        # **叶子级**登记（见 `CONDITIONAL_LEAVES`），**不是**把前缀加回来 —— 前缀覆盖面更大，
        # 以后 `dps` 一旦出现「非条件性的缺失」就会被前缀误判成不适用（T-34 的教训）。
    ),
    "stability": (
        # 没有耗电模块 → 逐模块排行 / 逐模块敏感性列表为空（f6）
        "cap_by_module",
        "cap_sensitivity",
        # 弹药续航（无带弹药的武器）与纳米胶续航（没带 paste）都是条件产出
        "consumables",
    ),
    "constraints": (
        # 整个违规清单只在「当前技能档下真有缺口」时出现（列表为空时整块折叠成容器键
        # `tier_violations`）；元素子字段再按违规类型二分：resource 违规带
        # `used`/`available`/`shortfall`/`resource`，skill 违规带 `required`/`level`/
        # `missing_levels`/`skill_type_id`/`skill_name` —— **另一分支的字段对本条违规
        # 根本不适用**，值就是 None（`_tier_base()` 先把它们全置 None，再由对应分支填值）。
        #
        # **T-34 之后这条前缀只该命中「值是 None 的分支互斥字段」**：有值的
        # `kind` / `message` / `resource` / `rule.*` 与 `tier_violations` 容器本身已在
        # `constraints.FIELD_SOURCES` 登记（合成 / 枚举 → `None` → `assumed`），而本表
        # 只对 `provenance == "unknown"` 的路径求值（`render._reason_block()` 负责筛），
        # 所以它们**走不到这里** —— 「有值却说不适用」的污染（T-32 的欠账）已消除。
        #
        # 保留前缀而不是逐条列 9 个叶子：逐条列既啰嗦又容易漏。**已知代价**：若哪天这里
        # 冒出一个「值是 None 但属于缺数据」的字段，前缀会把它错判成不适用 —— 那时必须
        # 改成叶子级登记（`tests/test_render.py` 的 `not_applicable` 清单会先红）。
        "tier_violations",
    ),
}
"""视角名 → **子树级**条件字段前缀（`前缀` 本身或 `前缀.<子路径>` 命中 → `not_applicable`）。

为什么用前缀：这些块在「条件不成立」时整块退化（空列表 / 全 None），逐个叶子登记既啰嗦
又容易漏（`cap_sensitivity[].depletes_in_s` 这类元素路径一登记就是十几条）。前缀的代价是
**覆盖面更大**：以后往这些块里加叶子，只要它也是条件产出就自动正确；若加了「无条件产出」
的叶子，就必须把它从这里挪进一个更窄的表（或让规则 1 先命中）。
**T-34 的教训**：这条「覆盖面更大」也是**污染源** —— 有值却漏登记的字段一旦落在前缀下，
就会被说成「不适用」。所以有值的结构性/枚举性叶子必须先登记进 `FIELD_SOURCES`
（→ `assumed`），再谈它适不适用；`tests/test_render.py` 的
`test_not_applicable_bucket_holds_only_conditional_fields` 就是守这条的。

**这是本模块唯一的「拍板点」，必须记住它的边界**：条件字段只说「条件不成立时值必然缺失」，
反过来不成立——某条路径值缺失**不一定**是因为不适用（可能是输入不完整）。所以：
① 规则 1（flag）排在它前面，能把「确实是输入问题」的字段抢回来；
② 表里每一条都写了「凭什么说它不适用」的实测依据（见上面注释），不写理由的条目不许进。
"""


# =============================================================================
# 四、规则 4：「在多值中选一个」且并列（ambiguous）
# =============================================================================

AMBIGUOUS_SUBTREES: tuple[str, ...] = ("thinnest",)
"""`thinnest.*`：每层要选一个「最薄系」——四系并列时**没有**最薄系（规格 §0.2.1 点名）。"""

AMBIGUOUS_SUFFIXES: tuple[str, ...] = (".damage_type",)
"""`*.damage_type`：语义就是「四系里选一个」，并列即无答案（规格 §0.2.1 点名）。

已知实例：`survivability.thinnest.<layer>.damage_type`（`Damage Control II` 把结构层四系
压成同一值，实测 f1 全 0.402 → `damage_type = None`，而 `ehp` 照给）。
"""


# =============================================================================
# 五、判定入口
# =============================================================================


def _normalize(path: str) -> str:
    """去掉数组元素标记（`cap_by_module[].cap_use_gj_s` → `cap_by_module.cap_use_gj_s`）。

    数组元素约定只是**输出**形态的写法（每个元素同一个字段），判定语义时它等价于
    普通点路径；两边都规范化后再比较，表里就不必为每个元素路径写两遍。
    """
    return path.replace(ARRAY_MARKER, "")


def _under(path: str, prefix: str) -> bool:
    """规范化后，`path` 是不是 `prefix` 本身或它的子孙。"""
    normalized_path = _normalize(path)
    normalized_prefix = _normalize(prefix)
    return normalized_path == normalized_prefix or normalized_path.startswith(
        normalized_prefix + "."
    )


_CONDITIONAL_LEAVES_NORMALIZED: dict[str, frozenset[str]] = {
    view: frozenset(_normalize(path) for path in paths)
    for view, paths in CONDITIONAL_LEAVES.items()
}
"""`CONDITIONAL_LEAVES` 的规范化形式（模块导入时算一次，`reason_for()` 不在热路径上反复建集合）。"""


def _explained_by_flag(path: str, flags: Iterable[str]) -> bool:
    """规则 1：有没有 flag **直接解释**这条路径（含数组元素 / 子字段）。"""
    for flag_id in flags:
        for explained in FLAG_REASONS.get(str(flag_id), ()):  # 表外 flag 不解释任何字段
            if _under(path, explained):
                return True
    return False


def _is_conditional(view: str, path: str) -> bool:
    """规则 2：该路径在这份视角里是不是**条件字段**。"""
    if _normalize(path) in _CONDITIONAL_LEAVES_NORMALIZED.get(view, frozenset()):
        return True
    return any(_under(path, prefix) for prefix in CONDITIONAL_SUBTREES.get(view, ()))


def _is_engine_omit(source: int | None) -> bool:
    """规则 3：来源属性在「引擎永不提供」清单里（`None` = 合成值，不参与）。"""
    return source is not None and int(source) in ENGINE_OMITS_ATTRS


def _is_ambiguous(path: str) -> bool:
    """规则 4：字段语义是「在多值中选一个」（且这里没有唯一答案）。"""
    normalized = _normalize(path)
    if any(normalized.endswith(suffix) for suffix in AMBIGUOUS_SUFFIXES):
        return True
    return any(_under(path, prefix) for prefix in AMBIGUOUS_SUBTREES)


def reason_for(
    path: str,
    *,
    view: str,
    source: int | None = None,
    flags: Iterable[str] = (),
) -> str:
    """一个**已经是 `unknown` 的叶子路径** → 它的原因（判定优先级见模块 docstring）。

    只对 `provenance == "unknown"` 的路径调用（渲染层 `render._reason_block()` 负责筛）。
    对一个有值的路径调用本函数没有意义，但也不会出错——本模块不看值。

    Args:
        path: 叶子点路径（数组元素用 `a[].b` 约定）。
        view: 视角名（条件字段表**按视角**登记：同一个片段在不同视角里语义可能不同）。
        source: 该路径在 `FIELD_SOURCES` 里的来源属性 ID；`None` = 合成值 / 未登记映射。
        flags: 本视角**归位后**的 flag id 序列（`views[<view>]["flags"]` 的 id）。

    Returns:
        `input_incomplete` / `not_applicable` / `engine_omits` / `ambiguous` / `unclassified`
        —— 必定落在 `UNKNOWN_REASONS` 里。
    """
    if _explained_by_flag(path, flags):  # 规则 1：本次计算的自相矛盾最硬
        return "input_incomplete"
    if _is_conditional(view, path):  # 规则 2：条件不成立 ⇒ 不适用
        return "not_applicable"
    if _is_engine_omit(source):  # 规则 3：引擎永不提供
        return "engine_omits"
    if _is_ambiguous(path):  # 规则 4：多值并列，无唯一答案
        return "ambiguous"
    return FALLBACK_REASON  # 规则 5：兜底 = 缺一条规则（缺陷）


# =============================================================================
# 六、自检与报表（守门测试与探针共用）
# =============================================================================


def unclassified(reasons: Mapping[str, str]) -> list[str]:
    """稀疏表里落在兜底 `unclassified` 的路径（**应为空**；非空即缺陷）。

    单独抽成函数是为了让「守门断言」与「变异自检」用**同一份判据**：
    守门测试对真实样本断言它返回 `[]`，变异测试故意抹掉一条规则后断言它非空。
    """
    return sorted(path for path, reason in reasons.items() if reason == FALLBACK_REASON)


def distribution(reasons: Mapping[str, str]) -> dict[str, int]:
    """各 `reason` 的出现次数（按 `UNKNOWN_REASONS` 顺序，**0 也保留键**）。

    探针与报表用它打印「各样本 reason 分布」；保留 0 值键是为了让分布的形状稳定
    （少一个键通常是「规则没接上」的迹象，而不是「这一类不存在」）。
    """
    counts = {reason: 0 for reason in UNKNOWN_REASONS}
    for reason in reasons.values():
        counts[reason] = counts.get(reason, 0) + 1
    return counts


__all__ = [
    "AMBIGUOUS_SUBTREES",
    "AMBIGUOUS_SUFFIXES",
    "CONDITIONAL_LEAVES",
    "CONDITIONAL_SUBTREES",
    "ENGINE_OMITS_ATTRS",
    "FALLBACK_REASON",
    "FLAG_REASONS",
    "REASON_DOCS",
    "UNKNOWN_REASONS",
    "distribution",
    "reason_for",
    "unclassified",
]
