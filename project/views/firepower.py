"""视角一 · 火力 `firepower`（字段规格 §1）。

本模块只产出**事实**（数值 + 口径），不做判断：诊断（可疑点）由渲染层统一调一次
`diagnostics.evaluate(ctx)`，所以 `flags` 恒为空列表——六个视角各算一遍会把冷启动
12.6 ms 变成 6 倍。

口径要点（每条都实测过，要推翻请重跑脚本）：

1. **单位**：`ctx.attrs()` / `ctx.items()` 给的值已经是物理单位（秒 / 米），本模块
   不做任何缩放。唯一例外是导弹 `explosionDelay`（281）：它单位是**毫秒**，但
   **不在** `SCALE_BY_ATTR`（8 项）里，故用 `units.ms_to_s()` 显式换算——这正是
   `project/units.py` 对「非缩放表数值」提供的合法入口（红线 N5 只禁止裸乘除 1000，
   不禁止这个函数）。
2. **dps / dph**：`dps.base` = `-12`、`dps.with_reload` = `-13`；`dph`（首轮齐射伤）
   = **Σ 武器槽装备级 `-21`**，Σ 为 0 时回退舰船级 `-11`（T-20 / D7 改动，见 `_dph`）。
   `volley`（`-21`）只在**装备级**，舰船级没有；实测 f1 `Σ = 943.667678` 与 `-11`
   逐位相等、f5 `Σ = 2105.579310` 与 `-11` 逐位相等（不变式 I2）。
   **注意**：舰船级 `-11` **包含发射器**（f5 就是导弹船，I2 成立），D7 原先「`-11`
   只聚合炮台」的假设已被实测证伪；f4 的 `-11`/逐件 `-21` 同为 0 的根因是
   「killmail 不带 mutation 的 Abyssal BCS 把发射器伤害乘子压成 0」（见报告）。
   **T-22 起**：Σ 与回落值**同时**为 0、但存在带弹药的武器时，`dph` 输出 `None`
   （输入不完整 → 未知），并由 `weapon_damage_zero` flag 说明原因（字段规格 §0.6）。
   零伤害本身不触发 null：没有武器的配装照实给 `0.0`。
   **T-36 起**：**一个武器系统都没有**的配装一律 `0.0`，**包括引擎连 `-11` 键都不输出
   的配装**。引擎只在「至少有一个高槽模块」时才派生 `-11` / `-12`（实测：f67 有高槽
   模块 → `-11 = 0.0`；f58 空配装、f61 只有低 / 中槽模块 → 两个键都不存在）。那是
   **引擎的输出习惯**，不是「不知道」——同一类事实不能因为少一个键就变成 `null`。
   注意「一个武器系统都没有」**不等于**「没装炮」：无人机与舰载机的伤害不进 `-11`
   （f4 实测 drone `-21 = 83.866` 而 `-11 = 0.0`），所以它们**算**武器系统（f26 航母
   机库 8 架载机 → `dph` 仍 `None` + `not_applicable`，由 `dps.fighter` 表达）。
   三种情形 + 一条边界的口径（T-36 定稿，测试在 `tests/test_views_a.py` 同时钉住）：

   | 情形 | `dph` | 依据 |
   | --- | --- | --- |
   | 完全没有武器系统（没炮 / 没无人机 / 机库空，含引擎不给 `-11` 键） | `0.0` | 确定的事实：这套配装没有伤害 |
   | 有武器但没装弹药 | `0.0` | 同上（引擎给 `-11 = 0.0`，齐射是真 0） |
   | 有带弹药的武器却齐射为 0 | `None` | 自相矛盾 → 输入不完整（`weapon_damage_zero`）|
   | 只有无人机 / 机库载机**且引擎不给 `-11`**（f26） | `None` | `dph` 不适用（`dps.drone` / `dps.fighter` 表达）|

   **T-37**：`dps.base` / `dps.with_reload` / `overload.{off,on,delta}.dps` 用**同一个判据**
   （`_has_any_weapon_system()`）补齐：**一个武器系统都没有**时，引擎缺 `-12` / `-13` 也
   给 `0.0`，不再是 `None`。改前同一情形有两种输出——f67（有高槽模块，`-12` 键在且为 0.0）
   与 f26（有载机）是 `0.0`，f58（空配装）与 f61（全中 / 低槽）因缺键是 `None`。T-36 已经
   在 `dph` 上裁决过「引擎少给一个键不是「不知道」」，`dps` 不能还是两套口径。有武器系统
   却缺 `-12` 的情形（如只有载机）**保持** `None` —— 与 `dph` 的边界完全一致，不新造判据。
3. **伤害构成按伤害量加权**（不是按弹种数量）：形状取弹药四系（114/116/117/118），
   量级取装备级 `volley`（`-21`；缺失时才退回「弹药 × 武器 `damageMultiplier`（64）」）。
   实测 Void S = 8.9 动能 + 8.9 热能（50/50），**不是**单系；弹药自身也带 64
   （Void S = 1.08），但引擎的 `-21` 证明它**不**参与：`(8.9 + 8.9) × 9.4669715 =
   168.51209` 正好等于装备级 volley。量级为什么优先用 `-21`：导弹的加成在
   `missileDamageMultiplier`（212/213）上、发射器没有 64，混装时用 64 会把导弹
   低估近一个数量级（详见 `_damage_split` 的 docstring）。
4. **射程**：`54 maxRange` 与 `158 falloff` **已经**含弹药 `weaponRangeMultiplier`
   （120）的修正，**不得**再乘一次。实测同一门 Light Neutron Blaster II 换弹药：
   Void S（120 = 0.75）→ 1350、Null S（1.4）→ 2520、Iron（1.6）→ 2880，基准 1800。
   导弹没有 54：射程由弹药 `maxVelocity`（37）× `explosionDelay`（281，毫秒）求出
   （实测 Scourge Light Missile：5250 m/s × 5 s = 26250 m）。
5. **application 只给参数、不给命中结论**（应用层命中率未建模）：`tracking_rad_s` /
   `optimal_signature_radius_m` / `explosion_radius_m` / `explosion_velocity_mps`。
   多组武器时取**最不利**值（跟踪最小、签名分辨率最大、爆炸半径最大、爆炸速度最小），
   这里只有参数，任何「打得中/打不中」的结论都不在本层。
6. **`ammo_endurance_s` 是单弹匣时长**，不是货舱总量：
   弹匣发数 = 模块 `capacity`（38）÷（弹药 `volume`（161）× `chargeRate`（56）），
   时长 = 发数 × 周期（51）。实测中子炮 0.2 ÷ 0.0025 = 80 发 × 2.304636 s = 184.371 s。
   多组武器取**最短者**（最先打空的那一组）。
   **算法只有一份**：T-20 起搬到 `views/shared.py`（`weapon_records()` +
   `ammo_endurance_s()`），本视角与 `stability.consumables.ammo_endurance_s` 都调它，
   因此两个视角的数值逐位相同（D5）。别再在本模块里另写一份取最小值的实现。
7. **战斗机 / 无人机**：`-58`（fighterDps，舰船级 = Σ 架数 × 逐机 `-12`）与
   `-14`（droneDps）已经并入 `-12`，这里仍按字段单独给，避免下游把「纯无人机配装」
   误读成「没有火力」；缺值给 `None`（不补 0）。注意 `damage_split` 的来源是**弹药**
   四系伤害：无人机自带 `114/116/118` 这类四系值（实测 Hobgoblin II 全热能），能算；
   战斗机的中队条目**没有**四系伤害（伤害在能力 DPS `-55/-56` 里），所以战斗机配装的
   `damage_split` 四值全是 `None`——那是「无法推断」，不是「没有伤害」。
8. **`family` 只认规格列的 7 类**；表外武器（如 Precursor 分裂炮）`family=None` 但
   仍计入伤害与射程——宁可少一个标签，也不要丢掉一门炮。
9. **`fighter_bay` 表达「机库里的载机」（T-29）**：`count` / `types` /
   `capacity_used_m3`（船级 `-57`）。机库载机**不进 `weapons[]`**：它们不参与任何伤害/
   槽位计算，把它当武器会让「有战斗机在输出」变成假事实（`dps.fighter` 恒 0）。
   无载机时给空结构（`count 0 / types [] / capacity_used_m3 0.0`），schema 恒在。
   注意 `dps.fighter = 0.0` 在机库场景是**正确的 0**，绝不改成 `null` 或报错——
   「有没有在打」由 `dps.fighter` 说，「机库里有没有货」由 `fighter_bay` 说。
10. **拦截弹发射器不算伤害（T-43）**：`Defender Launcher I`（SDE group
    `Missile Launcher Defender`）的弹药是**拦截弹**（`Defender Missile I`），用来打掉来袭
    导弹、不打船。引擎仍为它派生 `-12 = 160` / `-21 = 800`（f11 实测），pyfa/eos 同一件给
    `0`（T-42 报告 §4.2 唯一例外）。判定只写一份：`shared.NON_DAMAGE_WEAPON_GROUPS` +
    `shared.classify_weapon()`（把它挡在 `weapons[]` 外，于是 `dph` / `damage_split` /
    `range` / `ammo_endurance_s` 全都不含它），**船级** `-12` / `-13` 的那部分由
    `shared.non_damage_weapon_dps()` 扣掉（`dps.base` / `dps.with_reload` /
    `overload.*.dps` 都走 `_dps_raw()` 的 `excluded`）。判据是**业务事实**（这批发射器的
    弹药是拦截弹），不是「引擎给不给键」——后者会随实现细节漂移（PROGRESS §3 第 245 条）。

`FIELD_SOURCES` 的映射约定：能指到**唯一来源属性**的给属性 ID（归一化/聚合的量指到
它的定义性属性，例如「四系伤害占比」→ 对应的弹药伤害属性）；由多个属性共同决定的
合成值给 `None`（渲染层据此标 `assumed`，而不是硬塞一个看起来像的来源）。

数组字段一律用 `[]` **元素约定**（与 `resources.upgrade_space[].fits` 同一套口径）。
`weapons` 的四个叶子按元素展开为 `weapons[].family` / `weapons[].name` /
`weapons[].count` / `weapons[].ammo_name`，四条都登记 `None`：整张武器清单是**合成值**
（由 `ctx.items()` 与 SDE 索引拼出，指不到单一来源属性），渲染层据 `None` 判 `assumed`，
不冒充实测。
（为什么是元素约定：上一轮登记容器路径 `weapons: None`，靠 `render._tag()` 的容器回退
把四个叶子一起覆盖——那是本视角独有的**第二套写法**，六视角对外必须一套口径。
T-16 起 `tests/test_views_a.py` 的 walker 已支持 `[]` 段，于是换回元素约定，provenance
的键空间与其它五个视角完全对齐。**walker 需支持 `[]` 段**：改动 `_resolve()` /
`_numeric_leaf_paths()` 时别退回「列表整块算一个叶子」，否则容器写法会重新被逼回来。）
"""

from __future__ import annotations

from noobbaozi.config import attributes as _a
from noobbaozi.project import units
from noobbaozi.project.views import register, shared
from noobbaozi.project.views.base import BASE, OVERLOAD, ViewContext

VIEW_NAME = "firepower"

# 武器判定 / 弹药续航的公共实现已抽到 `views/shared.py`（T-20 起，见该模块 docstring）。
# 这里只做 re-export，保持既有引用（`firepower.ATTR_EXPLOSION_DELAY` 等）不变。
ATTR_EXPLOSION_DELAY: int = shared.ATTR_EXPLOSION_DELAY
"""`explosionDelay`（281）—— 常量与来源说明见 `views/shared.py`（同一处定义）。"""

DAMAGE_TYPES: tuple[tuple[str, int], ...] = shared.DAMAGE_TYPES
"""四系伤害 → 弹药侧属性 ID（读弹药，不读武器）；定义见 `views/shared.py`。"""

WEAPON_SLOT_TYPES: frozenset[str] = shared.WEAPON_SLOT_TYPES
"""武器挂架槽位（high/medium/low）；`dph` 只聚合这几类槽位里的 `-21`。"""

FIELD_SOURCES: dict[str, int | None] = {
    "weapons[].family": None,
    "weapons[].name": None,
    "weapons[].count": None,
    "weapons[].ammo_name": None,
    "fighter_bay.count": None,
    "fighter_bay.types": None,
    "fighter_bay.capacity_used_m3": _a.ATTR_FIGHTER_BAY_USED,
    "dps.base": _a.ATTR_DAMAGE_DPS,
    "dps.with_reload": _a.ATTR_DAMAGE_DPS_WITH_RELOAD,
    "dps.drone": _a.ATTR_DRONE_DPS,
    "dps.fighter": _a.ATTR_FIGHTER_DPS,
    "dph": _a.ATTR_DAMAGE_ALPHA,
    "damage_split.em": _a.ATTR_EM_DAMAGE,
    "damage_split.explosive": _a.ATTR_EXPLOSIVE_DAMAGE,
    "damage_split.kinetic": _a.ATTR_KINETIC_DAMAGE,
    "damage_split.thermal": _a.ATTR_THERMAL_DAMAGE,
    "range.optimal_m": _a.ATTR_MAX_RANGE,
    "range.optimal_plus_falloff_m": _a.ATTR_FALLOFF,
    "range.optimal_plus_2falloff_m": _a.ATTR_FALLOFF,
    "range.max_range_m": None,
    "application.tracking_rad_s": _a.ATTR_TRACKING_SPEED,
    "application.optimal_signature_radius_m": _a.ATTR_OPTIMAL_SIG_RADIUS,
    "application.explosion_radius_m": _a.ATTR_AOE_CLOUD_SIZE,
    "application.explosion_velocity_mps": _a.ATTR_AOE_VELOCITY,
    "ammo_endurance_s": None,
    "overload.off.dps": _a.ATTR_DAMAGE_DPS,
    "overload.off.range_m": _a.ATTR_MAX_RANGE,
    "overload.on.dps": _a.ATTR_DAMAGE_DPS,
    "overload.on.range_m": _a.ATTR_MAX_RANGE,
    "overload.delta.dps": None,
    "overload.delta.range_m": None,
    # **没有 `hardpoints_free.*`**（T-54 审查追加）：炮位归 `resources.hardpoints`，
    # 本视角不再登记、不再输出（详见文件底部 `_hardpoints_free` 删除处的说明）。
}
"""overview 点路径 → 来源属性 ID（渲染层据此打 provenance）。

`weapons[].*` 四条是**合成值**：清单由已装模块 + SDE 索引聚合而来（`family` 来自 group
名、`ammo_name` 来自索引库），指不到单一来源属性，故 `None`（渲染层判 `assumed`）。

`fighter_bay.capacity_used_m3` 映射 `-57 fighterCapacityLoad`（机库占用 m³，**只在船级**）；
`fighter_bay.count` / `fighter_bay.types` 是**合成值**（由已装条目数出，指不到单一属性），
故 `None`。`fighter_bay.types` 是字符串列表，`FIELD_SOURCES` 仍必须登记：渲染层把标量列表
当**一个**叶子（`render._leaf_values` 的「其余」分支），漏登记就会掉成 `unknown`。

`range.max_range_m` / `ammo_endurance_s` / `overload.delta.*` 同样是**合成值**
（飞行时间 × 速度、弹匣 × 周期、两次重算之差），指不到单一属性，故 `None`。

`dph` 保持映射 `-11`（T-22 未改机制）：`null` 的语义由 `weapon_damage_zero` flag 表达，
**不**靠 provenance 三态表达——`FIELD_SOURCES` 是静态表，同一路径没法按值变形，
硬把它登记成 `None` 会让正常样本的 `dph` 从 `measured` 掉成 `assumed`。

> T-36 实测更正：`-11` 是**负 ID**（引擎 pass 4 派生值），`provenance.tag(-11)` 就是
> `assumed`，所以 `dph` 有值时**一直**是 `assumed`（f1 / f5 / f67 实测均为 `assumed`），
> 上面那句「正常样本是 measured」是旧表时代的说法。T-36 的「完全没有武器 → `0.0`」
> 因此落在 `assumed`（值是真的，三态由静态表决定）。要让它变成 `measured` 必须动
> `project/provenance.py`（把 `-11` 从 `ASSUMED_ATTRS` 挪到 `MEASURED_ATTRS`，或加
> 实例级规则），**不在本视角的机制里**，已作为偏离项上报、未改。
>
> T-37 同理：`dps.base` / `dps.with_reload` / `overload.*.dps` 映射的 `-12` / `-13`
> 也是负 ID，缺键补 `0.0` 之后三态仍是 `assumed`（**不是** `unknown`，也不需要
> `unknown_reasons` 条目）—— 与 `dph` 完全一致，同样**不许**为了「看起来更稳」改成
> `measured`（实例级只升格、不降格）。
"""


# =============================================================================
# 小工具
# =============================================================================


def _value(raw: float | None) -> float | None:
    """物理值 → 输出值（统一 3 位小数）；缺值保持 `None`（不补 0，红线 N4）。"""
    return None if raw is None else units.fmt(raw)


def _diff(on: float | None, off: float | None) -> float | None:
    """`on - off`；任一侧缺失就返回 `None`（不拿 0 冒充）。"""
    if on is None or off is None:
        return None
    return units.fmt(on - off)


def _has_armed_weapon(records: list[dict]) -> bool:
    """是否**存在带弹药的武器**（装填了弹药的挂架武器；无人机/战斗机不算）。

    判据用「有弹药」（`charge_type_id` 有值），**不用模块类型穷举**：这是
    `docs/配装理解-字段规格-v1.md` §0.6 的裁决——检测 Abyssal/mutated 类型会随版本失效，
    而「有弹药却零伤害」这个自相矛盾不会（f4 的 5 门 HML 装填了 Scourge HAM，
    `charge_type_id = 27453` 有值，但逐件 `-21` 与舰船级 `-11` 一起是 0）。
    """
    for record in records:
        if record.get("slot_type") not in WEAPON_SLOT_TYPES:
            continue
        if record.get("charge_type_id") is not None:
            return True
    return False


def _has_any_weapon_system(records: list[dict], bay_fighter_count: int) -> bool:
    """配装里有没有**任何**武器系统：武器挂架 / 无人机 / 发射管里的战斗机 / 机库载机。

    为什么要这么宽：`dph`（齐射伤）只覆盖 `-11`，而无人机（`dps.drone`）与舰载机
    （`dps.fighter`）的伤害**不进** `-11`（f4 实测：`-11 = 0.0` 而 drone `-21 = 83.866`）。
    于是「装了无人机 / 载机的配装」在本视角里 `dph` 是**不适用**（这套配装的伤害由别的
    字段表达，渲染层据此报 `not_applicable`），不是「确定没有伤害」；把这种配装报成
    `0.0` 才是伪事实（航母的 α 伤害是真的，只是不在 `dph` 里）。反之，**一个武器系统
    都没有**（f58 / f61 / f67）时 `0.0` 是确定的事实（T-36）。

    `records` 来自 `views/shared.weapon_records()`，只含**武器与无人机 / 战斗机**；
    机库里的载机**不在**里面（T-29 的裁决）——所以机库侧要单独看 `fighter_bay.count`。
    """
    return bool(records) or bay_fighter_count > 0


def _dph(
    attrs: dict[int, float],
    records: list[dict],
    *,
    bay_fighter_count: int,
) -> float | None:
    """首轮齐射伤（齐射 α）= **Σ 武器槽装备级 `-21`**，Σ 为 0 时回退舰船级 `-11`。

    为什么不再直接读 `-11`（T-20 / D7 的**实测结论**，与任务书的初始假设不同，务必先看
    `PROGRESS` 与本轮报告）：

    - 舰船级 `-11` **确实包含发射器**（f5 实测：6 × HAM II 各 `-21 = 350.929885`，
      Σ = 2105.579310，与 `ship -11 = 2105.579310` 逐位相等）→ 「`-11` 只聚合炮台」
      这个假设是**错的**；
    - 但 `-11` 仍然是**引擎聚合出来的黑盒**，一旦引擎在某件武器上算崩（f4 实测：
      未变异 Abyssal BCS 把 5 门 HML 的 `-21`/`-12` 一起压成 0，`-11` 也随之 0），
      视角就只剩一个 0 可读，无法与逐件 `-21` 交叉验证；
    - 改成 Σ `-21` 之后，`dph == Σ 武器 volley` 变成**构造性成立**（不变式 I2 不再是
      「引擎碰巧成立」），并且当引擎只给了逐件 `-21`、没给 `-11`（或 `-11` 缺键）时
      依然有正确值。

    只聚合**武器挂架**（`high` / `medium` / `low`）：无人机 / 战斗机的中队条目也带
    `-21`（f4：drone `-21 = 83.866`），但舰船级 `-11` 不含它们（f4 `-11 = 0.0`），
    混进来会让 I2 当场破掉。

    Σ 为 0 时回退 `-11`：这是「引擎只给了舰船级、逐件缺失」的兜底路径。

    **矛盾判据（T-22 / §0.6 裁决）**：回落值也是 0、但**确实存在带弹药的武器**时，
    输出 `None`（`unknown`）而不是 `0.0`。理由：装填了弹药的武器不可能真的零齐射，
    这个矛盾只可能来自输入不完整（killmail **不暴露 mutation 数据**，引擎把未变异的
    Abyssal BCS 当成 0 伤害乘子相乘）。此时 `0.0` 是伪事实，必须由
    `diagnostics` 的 `weapon_damage_zero` flag 显式说明「为什么是 null」。

    「有弹药却零伤害」只在两端**同时**为 0 时才成立：没有武器的配装（或武器没装填弹药）
    齐射就是真 0，照实给 `0.0`——零伤害本身不是矛盾，**有弹药**才是。

    **T-36：引擎不给键 ≠ 不知道**。引擎只在「至少有一个高槽模块」时才派生 `-11` / `-12`：
    空配装（f58）与只有低 / 中槽模块的配装（f61）**两个键都不存在**，而有任意高槽模块
    的配装（f67 的扫描发射器 / 吸电 / 隐形；以及「有炮没弹药」的配装）一定是 `-11 = 0.0`。
    所以同一个事实（「一个武器系统都没有」）之前会因为引擎少给一个键而分成 `0.0` 与
    `None` 两种输出——这是**实现的不一致，不是引擎的语义**（T-36 / f67 随堂测试暴露）。

    现在按「**配装里有没有任何武器系统**」三分（判据在 `_has_any_weapon_system()`）：

    - **一个都没有**（f58 / f61 / f67：没炮、没无人机、机库也空）→ 缺键的回落落在 `0.0`，
      「这套配装没有伤害」是**确定的事实**；
    - **有武器却不算进 `-11`**（无人机 / 舰载机：f26 航母机库 8 架）→ 保持缺键时的 `None`
      （这套配装的 α 伤害由 `dps.drone` / `dps.fighter` 表达，`dph` 对它**不适用**，
      渲染层报 `not_applicable`；报 `0.0` 才是伪事实）；
    - **有武器挂架模块**（装了炮 / 发射器）→ 走上面的既有判据：带弹药且两端为 0 → `None`
      + `weapon_damage_zero`；没装弹药 → 引擎的 `0.0`（齐射是真 0）。
    """
    total = 0.0
    for record in records:
        if record.get("slot_type") not in WEAPON_SLOT_TYPES:
            continue
        volley = record.get("volley")
        if volley is None or volley <= 0.0:
            continue
        total += volley
    if total > 0.0:
        return _value(total)

    fallback = attrs.get(_a.ATTR_DAMAGE_ALPHA)
    if fallback is not None and fallback <= 0.0 and _has_armed_weapon(records):
        # 自相矛盾：有弹药却零伤害 → 输入不完整（突变数据缺失），不许输出 0.0
        return None
    if fallback is None and not _has_any_weapon_system(records, bay_fighter_count):
        # T-36：引擎没有 `-11`（= 一个高槽模块都没有），且配装里一个武器系统都没装 →
        # 「这套配装没有伤害」是确定的事实，给 0.0（不是 None）。
        return _value(0.0)
    return _value(fallback)


def _dps_raw(
    attrs: dict[int, float],
    attribute_id: int,
    *,
    has_weapon_system: bool,
    excluded: float = 0.0,
) -> float | None:
    """引擎的 DPS 属性（`-12` / `-13`）→ 原始值；**缺键口径与 `_dph()` 一致**（T-37）。

    引擎只在「至少有一个高槽模块」时才派生 `-11` / `-12` / `-13`（T-36 实测）：

    | 样本 | 武器系统 | `-12` 键 | `dps.base` 改前 → 改后 |
    | --- | --- | --- | --- |
    | f67（Bifrost，无武器） | 无 | 有（`0.0`） | `0.0` → `0.0` |
    | f26（Thanatos，载机在机库） | **有**（载机） | 有（`0.0`） | `0.0` → `0.0` |
    | f58（空 EFT）/ f61（全中 / 低槽） | 无 | **缺** | `None` → `0.0` |

    所以「缺键」本身不带语义：**缺键 + 一个武器系统都没有** = 「这套配装没有伤害」这个
    确定事实 → `0.0`；**缺键 + 有武器系统**（例如只有机库载机）= 该值不适用于这个配装
    → 保持 `None`（与 `_dph()` 对 f26 的判据逐字对齐，不新造口径）。

    只补这一种缺键：值存在时原样返回（`0.0` 也是值，不是缺值），所以既有样本逐位不变。

    `excluded`（T-43）：**不产生对敌伤害**的模块对这条船级属性的贡献合计
    （`shared.non_damage_weapon_dps()`，目前只有拦截弹发射器）。引擎把拦截弹的
    `-12 = 160` / `-13 = 133.33` 也算进了船级值（f11 实测），逐件 `-12` 之和与船级值
    差 < 1e-12，所以**相减不是近似**。扣减放在**原始值**上做（`_value()` 之前）：
    先各自取 3 位小数再相减会让末位与「逐件重算」错开 1e-3。
    """
    value = attrs.get(attribute_id)
    if value is None and not has_weapon_system:
        return 0.0
    if value is None:
        return None
    return float(value) - excluded


def _weapon_list(records: list[dict]) -> list[dict]:
    """按（type_id, 弹药）合并成 `{family, name, count, ammo_name}` 清单。

    顺序 = 首次出现顺序（与装配顺序一致，天然确定，不需要额外排序键）。
    """
    groups: dict[tuple[int | None, int | None], dict] = {}
    for record in records:
        key = (record["type_id"], record["charge_type_id"])
        group = groups.get(key)
        if group is None:
            groups[key] = {
                "family": record["family"],
                "name": record["name"],
                "count": record["count"],
                "ammo_name": record["ammo_name"],
            }
            continue
        group["count"] += record["count"]
    return list(groups.values())


def _damage_split(records: list[dict]) -> dict[str, float | None]:
    """四系伤害**占比**（按伤害量加权，四项之和 = 1）。

    两件事分开算：
    - **形状**（四系相对比例）来自弹药四系伤害 114/116/117/118；
    - **量级**（这一组武器占多少）优先用装备级 `volley`（`-21`，引擎已含全部乘子），
      缺失时才退化为「弹药四系 × `damageMultiplier`（64）」。

    为什么量级不直接用「弹药 × 64」：导弹的伤害加成在 `missileDamageMultiplier`
    （212/213）上、发射器本身没有 64（实测 Launcher 的 `-21 = 136.95`，而弹药只有
    124.5，差的 1.1 就是弹道控制）。混合炮 + 导弹配装若把炮乘 9.47、导弹乘 1，
    导弹的占比会被低估近一个数量级。炮这两种口径完全等价（实测
    `(8.9+8.9) × 9.4669715 = 168.51209` 正好等于 `-21`），所以换成 `-21` 不改变
    纯炮配装的结果，只把混合配装从错的改成对的。

    四系全为 0（无弹药 / 战斗机这类四系伤害不在条目上的武器）时四值给 `None`——
    分母是 0 的时候没有「占比」可言，给 0 会读成「这一系没伤害」。
    """
    totals = {name: 0.0 for name, _attribute_id in DAMAGE_TYPES}
    for record in records:
        damage_total = sum(record["damage"].values())
        if damage_total <= 0.0:
            continue  # 不知道形状就不猜（宁可不计入，也不按件数均分）
        magnitude = record["volley"]
        if magnitude is None or magnitude <= 0.0:
            magnitude = damage_total
        share = magnitude * record["count"]
        for name, _attribute_id in DAMAGE_TYPES:
            totals[name] += share * record["damage"][name] / damage_total

    grand_total = sum(totals.values())
    if grand_total <= 0.0:
        return {name: None for name, _attribute_id in DAMAGE_TYPES}
    return {name: units.fmt(totals[name] / grand_total) for name, _attribute_id in DAMAGE_TYPES}


def _range_block(records: list[dict]) -> dict[str, float | None]:
    """三档射程（多组武器时各档取**最远**值，即配装能覆盖的最远档）。

    `optimal_plus_falloff_m` 与 `optimal_plus_2falloff_m` 是在 `optimal_m` 上做加法，
    不是重新取来源属性。
    """
    optimals = [record["optimal_m"] for record in records if record["optimal_m"] is not None]
    falloffs = [record["falloff_m"] for record in records if record["falloff_m"] is not None]
    missiles = [record["max_range_m"] for record in records if record["max_range_m"] is not None]

    optimal = max(optimals) if optimals else None
    falloff = max(falloffs) if falloffs else None

    return {
        "optimal_m": _value(optimal),
        "optimal_plus_falloff_m": (
            None if optimal is None or falloff is None else units.fmt(optimal + falloff)
        ),
        "optimal_plus_2falloff_m": (
            None if optimal is None or falloff is None else units.fmt(optimal + 2.0 * falloff)
        ),
        "max_range_m": _value(max(missiles) if missiles else None),
    }


def _application_block(records: list[dict]) -> dict[str, float | None]:
    """应用层参数（**未建模**：只给参数，不给命中结论）。

    多组武器时取**最不利**值：跟踪速度取最小、签名分辨率取最大、爆炸半径取最大、
    爆炸速度取最小。这里刻意不做加权，也不判断「够不够用」。
    """
    trackings = [
        record["tracking_rad_s"] for record in records if record["tracking_rad_s"] is not None
    ]
    sig_radii = [
        record["optimal_sig_m"] for record in records if record["optimal_sig_m"] is not None
    ]
    radii = [
        record["explosion_radius_m"]
        for record in records
        if record["explosion_radius_m"] is not None
    ]
    velocities = [
        record["explosion_velocity_mps"]
        for record in records
        if record["explosion_velocity_mps"] is not None
    ]

    return {
        "tracking_rad_s": _value(min(trackings) if trackings else None),
        "optimal_signature_radius_m": _value(max(sig_radii) if sig_radii else None),
        "explosion_radius_m": _value(max(radii) if radii else None),
        "explosion_velocity_mps": _value(min(velocities) if velocities else None),
    }


def _ammo_endurance_s(records: list[dict]) -> float | None:
    """单弹匣时长（多组武器取最短者）：**唯一实现在 `views/shared.ammo_endurance_s`**。

    不稳定视角的 `consumables.ammo_endurance_s` 调的是同一个函数、同一份记录，
    所以两个视角逐位相同（D5 的收口方式；不要再在这里另写一份取最小值的逻辑）。
    """
    return shared.ammo_endurance_s(records)


def _overload_block(
    ctx: ViewContext,
    base_range: dict[str, float | None],
    *,
    has_weapon_system: bool,
) -> dict[str, dict[str, float | None]]:
    """过载前后的 `dps` 与 `optimal_m` 变化（`overload` 变体 = 全部模块 overload）。

    实测：中子炮 `-12` 365.594 → 420.433（+54.839），射程不变（`54` 不变，超载只加
    伤害乘子）。`base_range` 由调用方传入，避免把 base 变体算第二遍。

    T-37：两档 `dps` 都走 `_dps_raw()`，所以「一个武器系统都没有」时缺 `-12` 也给 `0.0`，
    而 `delta.dps` 由两个**原始值**相减（`_diff`）——先各自取 3 位小数再相减会让末位与
    「两次重算之差」错开 1e-3，既有断言正是按原始值算的。
    """
    base_attrs = ctx.attrs(BASE)
    overload_attrs = ctx.attrs(OVERLOAD)
    overload_range = _range_block(shared.weapon_records(ctx, OVERLOAD))

    off_dps = _dps_raw(
        base_attrs,
        _a.ATTR_DAMAGE_DPS,
        has_weapon_system=has_weapon_system,
        excluded=shared.non_damage_weapon_dps(ctx, _a.ATTR_DAMAGE_DPS, BASE),
    )
    on_dps = _dps_raw(
        overload_attrs,
        _a.ATTR_DAMAGE_DPS,
        has_weapon_system=has_weapon_system,
        excluded=shared.non_damage_weapon_dps(ctx, _a.ATTR_DAMAGE_DPS, OVERLOAD),
    )
    off_range = base_range["optimal_m"]
    on_range = overload_range["optimal_m"]

    return {
        "off": {"dps": _value(off_dps), "range_m": off_range},
        "on": {"dps": _value(on_dps), "range_m": on_range},
        "delta": {"dps": _diff(on_dps, off_dps), "range_m": _diff(on_range, off_range)},
    }


# `_hardpoints_free()` **已删除**（T-54 审查追加）：炮位事实只有一个出处
# `views/resources.hardpoints`。它曾直读 102 `turretSlotsLeft` / 101 `launcherSlotsLeft`
# 当「空闲挂点」—— 那是船体的**基准炮位数**，与装配无关（同一艘 Osprey NI 装 0 / 3 / 5 门
# 发射器都输出 `{turret: 2.0, launcher: 3.0}`）。`tests/test_t54_audit.py` 有一条防线测试
# 钉住「六视角里只有 `resources.hardpoints` 一个出处、`hardpoints_free` 在哪都取不到」。


# =============================================================================
# 视角入口
# =============================================================================


@register(VIEW_NAME)
def build(ctx: ViewContext) -> dict:
    """构建火力视角 overview。

    Args:
        ctx: 视角上下文（只读）。

    Returns:
        `{"view": "firepower", "overview": {...}, "flags": []}`——
        `flags` **恒为空**：可疑点由渲染层统一算一次（见模块 docstring）。
    """
    attrs = ctx.attrs(BASE)
    records = shared.weapon_records(ctx, BASE)
    range_block = _range_block(records)
    fighter_bay = shared.fighter_bay_summary(ctx, BASE)
    # 「有没有任何武器系统」只判一次：`dph`（T-36）与 `dps` 系列（T-37）共用同一个事实，
    # 两处各写一套判据就是下一轮「同一情形两种输出」的来源。
    has_weapon_system = _has_any_weapon_system(records, fighter_bay["count"])
    # T-43：拦截弹发射器不产生对敌伤害。`weapon_records()` 已经不收它（`weapons[]` / `dph` /
    # 射程 / 弹药续航都不会把它算进来），但船级 `-12` / `-13` 是引擎派生值、仍含它的
    # 160 / 133.33 → 这里按**同一张**排除表（`shared.NON_DAMAGE_WEAPON_GROUPS`）扣掉。
    excluded_dps = shared.non_damage_weapon_dps(ctx, _a.ATTR_DAMAGE_DPS, BASE)
    excluded_dps_reload = shared.non_damage_weapon_dps(
        ctx, _a.ATTR_DAMAGE_DPS_WITH_RELOAD, BASE
    )

    overview = {
        "weapons": _weapon_list(records),
        "fighter_bay": fighter_bay,
        "dps": {
            # T-37：缺 `-12` / `-13` 且一个武器系统都没有 → `0.0`（见 `_dps_raw()`）。
            "base": _value(
                _dps_raw(
                    attrs,
                    _a.ATTR_DAMAGE_DPS,
                    has_weapon_system=has_weapon_system,
                    excluded=excluded_dps,
                )
            ),
            "with_reload": _value(
                _dps_raw(
                    attrs,
                    _a.ATTR_DAMAGE_DPS_WITH_RELOAD,
                    has_weapon_system=has_weapon_system,
                    excluded=excluded_dps_reload,
                )
            ),
            "drone": _value(attrs.get(_a.ATTR_DRONE_DPS)),
            "fighter": _value(attrs.get(_a.ATTR_FIGHTER_DPS)),
        },
        # 机库载机数一并传下去：T-36 的「一个武器系统都没有」判据要看它（载机是武器系统，
        # 只是伤害不走 `-11`；见 `_has_any_weapon_system()` 的 docstring）。
        "dph": _dph(attrs, records, bay_fighter_count=fighter_bay["count"]),
        "damage_split": _damage_split(records),
        "range": range_block,
        "application": _application_block(records),
        "ammo_endurance_s": _ammo_endurance_s(records),
        "overload": _overload_block(ctx, range_block, has_weapon_system=has_weapon_system),
        # **没有 `hardpoints_free`**（T-54 审查追加）：炮位只有一个出处
        # `resources.hardpoints`。这里曾经直读 102 / 101（船体**基准炮位数**）当「空闲挂点」，
        # 与装配无关 —— 同一艘 Osprey NI 装 0 / 3 / 5 门发射器都输出 `{turret:2, launcher:3}`。
        # 那不是「火力」事实，是**装配**事实；留在这里等于同一产品里同时存在错误的老数字
        # 和正确的新数字，agent 走 `fit_view("firepower")` 就会拿到老的那个。
    }
    return {"view": VIEW_NAME, "overview": overview, "flags": []}
