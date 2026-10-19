"""视角四 · 资源 `resources`（字段规格 §4）。

只输出事实与边界：CPU / PG / 标定的绝对余量、四类槽位（+ T3C 的子系统槽）、挂点、
无人机带宽与舱容、货舱占用、空槽数、同组更高 meta 候选能否装下。**不设绝对阈值、不给判词**
（红线：可疑点判定统一由 `project/diagnostics.py` 算一次，本视角 `flags` 恒为 `[]`）。

数值纪律（`views/base.py` 三条纪律 + 字段规格 §0.3）：

1. `ctx.attrs()` / `ctx.items()` 给的**已经是物理单位**，本模块不再做任何缩放；
2. **缺值不补 0**：属性缺失给 `None`，由渲染层标 `unknown`（红线 N4）；
3. 余量一律**绝对值**（不给百分比，主人明确要求）；
4. 浮点一律过 `units.fmt`（`round(x, 3)` 并吃掉浮点噪声），件数保持 `int`。

字段 → 来源属性（`FIELD_SOURCES`，渲染层据此标 provenance）：

- `cpu.output/load/free` ← 48 / 49 / **-9**；`powergrid` ← 11 / 15 / **-10**；
  `calibration` ← 1132 / 1152 / **-69**；
- `slots.*.total` ← 14 / 13 / 12 / 1137（**属性 ID**，样本值分别是 6 / 4 / 4 / 3）；
  **子系统槽（T3C）**：`slots.subsystem.total` ← **1367 `maxSubSystems`**（常量已登记在
  `config/attributes.py`，T-23；红线 N12），仅当船体给值且 > 0 时出现（Loki 5 / 4 已装）。
  **为什么是 1367 而不是 1366 `subSystemSlot`**：1366 是**子系统模块自身**的标记
  （在这些船上取不到值），1367 才是船体侧的槽位上限。实测（SDE build 3607705 /
  引擎 13.6.0）：Loki（T3C）= 5，Hecate / Drake / Griffin 引擎连键都不给。
  这是 `FIELD_SOURCES` 里唯一的**条件字段**（见 `CONDITIONAL_SOURCES`）；
- `hardpoints.{turret,launcher}.total` ← 102 `turretSlotsLeft` / 101 `launcherSlotsLeft`
  （**船体基准值**，`measured`；`used` / `free` 见下）；
- `drone.*.total` ← 1271 `droneBandwidth` / 283 `droneCapacity`；`cargo.total` ← 38 `capacity`。

**`hardpoints`（T-54）：高槽 ≠ 炮位，`free` 必须扣掉已装的炮/发射器**。旧的
`hardpoints_free` 是 **T-54 之前的口径错误**：它直接把 102 / 101（船体的**基准炮位数**）
当成「剩余」，实测**与装配无关**（同一艘 Osprey Navy Issue 的高槽装 0 / 3 / 5 门
`Rapid Light Missile Launcher II`，旧字段恒为 `{"turret": 2, "launcher": 3}`）。
「高槽 5 个」**不是**「5 个发射器挂点」——这是 agent 把 `slots.high.total` 当挂点数、
进而得出「白扔两个发射器」的直接原因（战报 139274776 实测：5 高槽 / 2 turret / 3 launcher）。

现在的三段是**算出来的**（`total` 与 `used` 的口径不同，谁都替代不了谁）：

| 段 | 来源 | 三态 |
|---|---|---|
| `total` | 船体属性 102 / 101（引擎已叠加模块的 `turretHardPointModifier` 等修正） | `measured` |
| `used` | **我们自己数已装模块**（分类规则见下） | `assumed` |
| `free` | `total - used`，**可以为负**（负数 = 非法装配，由 `hardpoint_violation` flag 报） | `assumed` |
| `non_hardpoint` | 高槽里**确定不占硬点**的件数（隐身 / 智能炸弹 / 吸电 / 遥修…），**一件都不进 `used`** | `assumed` |
| `non_hardpoint_modules` | 上面那件事的**样例名字**（最多 3 个，人读时不用猜是哪件） | `assumed` |
| `unclassified` | 高槽里**分不清**是炮还是发射器的件数（`Festival Launcher` 一族，**不许猜进 `used`**） | `assumed` |

`total` 缺失（属性没有）时 `free` 也是 `None`（缺值不补 0，红线 N4）；`used` 仍然是数出来的
真值（0 就是真 0）。

**模块分类规则（可审计常量表，`HARDPOINT_*`）**——只在**高槽模块**里分（炮位只在高槽：

低槽的 `Mining Laser Upgrade` 名字含 `Laser`，但不是炮位，靠「只看高槽」这一条挡掉）：

1. 组名命中 `HARDPOINT_NON_HARDPOINT_GROUPS` → **确定不占硬点**，进 `non_hardpoint`
   （`Cloaking Device` / `Smart Bomb` / `Energy Neutralizer` / `Energy Nosferatu` /
   `Remote Armor Repairer` / `Scan Probe Launcher` / `Tractor Beam` / `Command Burst`…，
   每一行为什么确定都写在表里）；
2. 组名命中 `HARDPOINT_UNCLASSIFIED_GROUPS` → **分不清**，进 `unclassified`
   （`Festival Launcher` / `Interdiction Sphere Launcher`——**不许猜**）；
3. 名字或组名含 `launcher` → `launcher`（`Rapid Light Missile Launcher` /
   `Heavy Missile Launcher` / `Torpedo Launcher` / `Rocket Launcher` / `Cruise Missile Launcher`…）；
4. 名字或组名含 `HARDPOINT_TURRET_KEYWORDS` 之一 → `turret`；其中 `ion` 按**整词**匹配
   （SDE 实测：`ion` 当子串会命中 447 个模块——凡是 `-tion` 结尾的普通词都会中，
   整词匹配后只剩 `Ion Blaster` 这类真正的炮）；
5. 都不命中 → `unclassified`（计数，不给结论）。

**为什么把 `non_hardpoint` 和 `unclassified` 拆成两个数**（T-54 审查）：拆之前它们挤在一个
`unclassified` 里，于是 Osprey 真实装配的 `unclassified: 1` 读起来像「有一件武器我们认不出」，
实际是那件**隐身**（确定不占硬点）。现在：确定的进 `non_hardpoint`（还带样例名字），
只有**真正分不清**的才留在 `unclassified` —— 后者 `> 0` 就是一条维护信号（「可能漏收了一族」，
`Gas Cloud Harvester` 当年就躲在这里）。

`used` 一律 `assumed`：这是**按名字/组名的启发式分类**，不是引擎算出来的（SDE 里没有
「这个模块占几个炮位」的属性，只有船体侧的 102 / 101 总量）。分不清就进 `unclassified`，
不猜、不补。

**`feasible` / `over_budget` 是 T-22 新增的可行性事实（字段规格 §0.6）**：

- `feasible`：`cpu.free >= 0 and powergrid.free >= 0 and calibration.free >= 0` 的三项合判
  ——**任一余量为负即 `False`**（超预算），任一余量**缺失**则整体 `None`（未知不下结论，
  红线 N4）；
- `over_budget`：`{cpu, powergrid}` = 各自缺口 `max(0, -free)`；没超就是 `0.0`
  （缺值仍是 `None`）。只有 `feasible is False` 时才可能出现非零值；
- **只给事实与提示，不做求解**：本视角**不会**把模块自动降级成 offline 去凑预算
  （那是求解，不在本期），`cpu` / `powergrid` 的数值也仍是引擎给的理论值（按全 active 计）。
  「为什么超预算」的解释由 `diagnostics` 的 `resource_over_budget_unflyable` flag 承担
  （f4 实测：`cpu.load 10572.1` vs 真实可飞 `472.1`，差的就是 covert cloak 的 10000 罚值，
  而能免罚的子系统躺在 flag 177 舱里——见 `engine/normalize.py` 的舱内提示）。

**`upgrade_space` 本视角始终给完整候选清单**（事实归视角），overview 里只留摘要、
完整清单走 `render_detail(view="resources")` 是**渲染层的粒度决定**
（`render.OVERVIEW_DOWNGRADES`），不在这里删字段、也不改任何数值。

**已用量一律从 `ctx.items()` 统计**，不用「总量 − 剩余」反推：

- 槽位：按 `slot_type` 过滤（`"cargo"` 不是已装，见 `base.SLOT_CARGO`）；
- 无人机：逐架 `droneBandwidthUsed`（带宽）+ `volume`（舱容 m³）× 携带数量；
- 货舱：`slot_type == "cargo"` 的项按 `数量 × types.volume` 累加（体积来自
  `ctx.index`；**没有索引就给 `None`**，绝不猜）。

三个坑（实测）：

- `rigSlots`(1137) 与 `upgradeSlotsLeft`(1154) 是**两回事**：前者是槽位总量，后者是
  装配后剩余。混用会让 rig 的 `used` 永远错，所以 `used` 只从 items 数。
- 样本 Hecate 的 `droneBandwidth`/`droneCapacity` 都是 **0**（无无人机舱）——
  `0` 是**真值**，不是缺值，不能当 `unknown`。
- **子系统槽（T3C）是条件字段**（T-20 / X2）：`maxSubSystems`(1367) 只出现在 T3C
  船体上（Loki = 5，Hecate / Drake / Griffin 引擎连键都不给）。所以
  `slots.subsystem` / `empty_slots.subsystem` **只在船真有子系统槽时出现**，
  非 T3C 的输出必须与加这个字段之前**逐字节相同**（不许拿 `total = 0` 冒充四类齐全）。
"""

from __future__ import annotations

import math
import re

from noobbaozi.config import attributes as A
from noobbaozi.config.attributes import ATTR_DRONE_BANDWIDTH_USED, ATTR_MAX_SUBSYSTEMS
from noobbaozi.project import units
from noobbaozi.project.views import register
from noobbaozi.project.views.base import BASE, SLOT_CARGO, ViewContext, ship_attributes

# =============================================================================
# 槽位与上限
# =============================================================================

SLOT_SOURCES: dict[str, int] = {
    "high": A.ATTR_HI_SLOTS,
    "medium": A.ATTR_MED_SLOTS,
    "low": A.ATTR_LOW_SLOTS,
    "rig": A.ATTR_RIG_SLOTS,
}
"""槽位类别 → 总量属性 ID（14 / 13 / 12 / 1137）。顺序即输出顺序。"""

SUBSYSTEM_SLOT_TYPE: str = "subsystem"
"""T3C 子系统槽位的 `slot.type`（`ctx.items()` 里 4 个子系统模块的槽位类别）。"""

# =============================================================================
# 炮位（T-54）：高槽 ≠ 炮位
# =============================================================================

HARDPOINT_SLOT_TYPE: str = "high"
"""炮位只出现在**高槽**：turret / launcher 是两种硬点，都长在高槽模块上。"""

HARDPOINT_SOURCES: dict[str, int] = {
    "turret": A.ATTR_TURRET_SLOTS_LEFT,
    "launcher": A.ATTR_LAUNCHER_SLOTS_LEFT,
}
"""炮位类别 → **船体基准值**属性 ID（102 `turretSlotsLeft` / 101 `launcherSlotsLeft`）。顺序即输出顺序。"""

LAUNCHER_KEYWORDS: tuple[str, ...] = ("launcher",)
"""`launcher` 类的名字/组名关键词（含 `Rapid Light Missile Launcher` / `Torpedo Launcher`…
——SDE 实测 12 个 `Missile Launcher *` 组 + `Missile Launcher Bomb` 的模块名全都带这个词）。"""

HARDPOINT_TURRET_KEYWORDS: tuple[str, ...] = (
    "cannon",
    "artillery",
    "railgun",
    "blaster",
    "laser",
    "beam",
    "pulse",
    "neutron",
    "ion",
    "electron",
    "disintegrator",
    "miner",
    "harvester",
)
"""`turret` 类的名字/组名关键词（SDE 实测：`Cannon` / `Artillery` 落在 `Projectile Weapon`，
`Railgun` / `Blaster` / `Neutron` / `Ion` / `Electron` 落在 `Hybrid Weapon`，`Laser` / `Beam` /
`Pulse` 落在 `Energy Weapon`，`Miner` 落在 `Mining Laser` / `Strip Miner` ——**这四个组都占炮位**）。

`miner` 是任务书里带问号的那一项，这里**收**：`Mining Laser II` / `Strip Miner I` 确实占炮位；
低槽的 `Mining Laser Upgrade` 名字也含 `Laser`，但它不是高槽模块，被 `HARDPOINT_SLOT_TYPE`
这一条挡在分类之外（不靠关键词表兜）。

`disintegrator` 是任务书清单**之外**的一个（三神裔 `Precursor Weapon` 组的
`Light Entropic Disintegrator II`）：那一组同样是**炮位**（SDE group 1986），
不收就会白白落进 `unclassified`。关键词表的取舍标准是「SDE 组名 + 是否占炮位」，
不是「任务书列了几个词」——多收一个**有证据**的词，比让一件真炮去 unclassified 更有用。

`harvester` 是**独立审查补的漏分类**（T-54 只收了 `miner`）：`Gas Cloud Harvester I/II`
落在 SDE group `Gas Cloud Harvesters`，名字与组名都不含上面任何一个词，于是整族落进
`unclassified`。它是**占炮位**的采掘炮——Prospect（2 turret 硬点）装 2 门后实测
`turret {total:2, used:0, free:2}`：`free` **虚报 2**，装 3 门（超 1 门）时**一条 flag 都不出**。
全 SDE 4714 个 Module/Subsystem 类型里，含 `harvester` 的**高槽**模块只有 Gas Cloud Harvester
一族；其余命中是 `Gang Coordinator`（`Mining Foreman Link - Harvester …`）/ `Mining Upgrade`
（`Ice Harvester Upgrade`）/ `Rig Resource Processing` / `Strip Miner`（`Ice Harvester` 本来
就该算炮位）—— 前三个都不在高槽。
"""

HARDPOINT_WHOLE_WORD_KEYWORDS: frozenset[str] = frozenset({"ion"})
"""必须**整词**匹配的关键词。`ion` 当子串在 SDE 里命中 **447** 个模块——凡是 `-tion`
结尾的普通词（`Compression` / `Reaction` / `Station`…）都会中，整词匹配后只剩
`Ion Blaster` / `Ion Siege Blaster` 这类真正的炮。"""

HARDPOINT_NON_HARDPOINT_GROUPS: frozenset[str] = frozenset(
    {
        # —— **确定不占硬点**的高槽模块组（T-54 审查拆分：这些不再是「分不清」）
        #
        # 白名单本身就是判据（SDE 里**没有**「这个模块占几个炮位」的属性），所以每一行都要能
        # 说清「为什么确定」。取证方式：用引擎 `normalize` 逐件实测槽位 —— **全部落在 `high`**，
        # 且「装它不消耗 turret / launcher 硬点」是游戏内的确定事实（不是我们猜的）。
        "Cloaking Device",  # 隐形（Improved / Covert Ops / Prototype 同组，实测 high）
        "Smart Bomb",  # 智能炸弹（实测 high，不占硬点）
        "Energy Nosferatu",  # 吸电（实测 high）
        "Energy Neutralizer",  # 毁电（实测 high）
        "Remote Armor Repairer",  # 遥修装甲（实测 high）
        "Remote Shield Booster",  # 遥修护盾（实测 high）
        "Remote Hull Repairer",  # 遥修结构（实测 high）
        "Remote Capacitor Transmitter",  # 遥修电容 / 传电（实测 high）
        "Salvager",  # 打捞器（实测 high，不占硬点）
        "Tractor Beam",  # 牵引光束：名字含 `Beam`（group 650，实测 high）
        "Scan Probe Launcher",  # 探针发射器：名字含 `launcher`，但不占发射器硬点（group 481）
        "Survey Probe Launcher",  # 同上（group 1226）
        "Cynosural Field Generator",  # 诱导力场发生器（常规与隐形**同组**，实测 high）
        "Jump Portal Generator",  # 跳桥发生器（实测 high）
        "Clone Vat Bay",  # 克隆舱（实测 high）
        "Siege Module",  # 会战模块：`Bastion` / `Siege` / `Triage` 三族**同组**（实测 high）
        "Command Burst",  # 指挥脉冲（Shield / Armor / Skirmish / Information / Mining 同组，实测 high）
        "Super Weapon",  # 旗舰末日武器（group 588）
        "Gang Coordinator",  # 指挥模块（`Mining Foreman Link - Laser Optimization` 名字含 Laser）
    }
)
"""**确定不占硬点**的高槽模块组 → 计入 `hardpoints.non_hardpoint`（**不是** `unclassified`）。

为什么不含 `Micro Jump Field Generator`：引擎实测它是**中槽**，永远进不了硬点统计。
"""

HARDPOINT_UNCLASSIFIED_GROUPS: frozenset[str] = frozenset(
    {
        # —— **分不清**（既证明不了它占硬点，也证明不了它不占）→ 只计数，不猜
        "Festival Launcher",
        "Breacher Pod Launchers",
        "Interdiction Sphere Launcher",  # 实测在 high 槽，但「是否消耗发射器硬点」没有证据
    }
)
"""**分不清**的高槽模块组 → 计入 `hardpoints.unclassified`（`used` 一件都不许加）。

T-54 审查拆分（诚实性）：拆之前这两类**混在同一个 `unclassified` 计数**里 ——
Osprey 真实装配的 `unclassified: 1` 其实是**隐身**（确定不占硬点），读起来却像
「有一件武器我们认不出」。现在确定的那类有独立字段 `non_hardpoint`（带样例名字）。
"""

HARDPOINT_UNCLASSIFIED: str = "unclassified"
"""`hardpoints` 里**分不清**是炮还是发射器的件数字段名（只装 `HARDPOINT_UNCLASSIFIED_GROUPS`）。"""

HARDPOINT_NON_HARDPOINT: str = "non_hardpoint"
"""`hardpoints` 里**确定不占硬点**的件数字段名（装 `HARDPOINT_NON_HARDPOINT_GROUPS`）。"""

HARDPOINT_NON_HARDPOINT_MODULES: str = "non_hardpoint_modules"
"""`hardpoints` 里 `non_hardpoint` 的**样例名字**（最多 `NON_HARDPOINT_SAMPLE` 个，便于人看懂）。

人读 `non_hardpoint: 1` 会问「哪一件？」——所以这里带 1~3 个真名字。**只放真实存在的模块名**
（来自 `${item['name']}`），查不到名字就只给计数、不编。
"""

NON_HARDPOINT_SAMPLE: int = 3
"""`non_hardpoint_modules` 最多列几个名字（题面要求「最多 3 个」；再多就只是噪音）。"""

HARDPOINT_LABELS: dict[str, str] = {"turret": "炮位", "launcher": "发射器"}
"""硬点类别的中文短名（`diagnostics` 的 evidence 文案用它；`summary` **从这里导入**，不另抄一份）。

**只做领域词翻译，不产生任何新事实**：键就是 `HARDPOINT_KINDS` 的两个值。
"""

HARDPOINT_NON_HARDPOINT_GROUPS_FOLDED: frozenset[str] = frozenset(
    name.casefold() for name in HARDPOINT_NON_HARDPOINT_GROUPS
)
"""`HARDPOINT_NON_HARDPOINT_GROUPS` 的小写影子表（组名比对统一走小写，避免大小写分叉）。"""

HARDPOINT_UNCLASSIFIED_GROUPS_FOLDED: frozenset[str] = frozenset(
    name.casefold() for name in HARDPOINT_UNCLASSIFIED_GROUPS
)
"""`HARDPOINT_UNCLASSIFIED_GROUPS` 的小写影子表（组名比对统一走小写，避免大小写分叉）。"""

HARDPOINT_KINDS: tuple[str, ...] = tuple(HARDPOINT_SOURCES)
"""两个硬点类别（顺序即输出顺序：`turret` → `launcher`）。"""

# 为什么**删掉**了旧键 `hardpoints_free`（任务书允许「删除并说明」的选项）
# ------------------------------------------------------------------------
# 保留一个版本要花约 **150 字符**（body 48 + 逐叶子 provenance ~96 + 分隔符），而
# `render.BUDGET_CHARS` 只剩几百字符余量：实测 f12-logistics-oneiros 加完 `hardpoints`
# 后是 **19,112**，超预算触发降级（`flags` 整块丢 `evidence` —— 那不是「少几行」，是
# 诊断证据消失）。删掉旧键后 **18,962 < 19,000**，f47 / f67 也都 < 18,000。
# 另一个理由：旧键的价值是「不破坏既有断言」，但它的值必须改成真实 `free` 才正确——
# 那既有断言的期望值**本来就要改**（原来断言的是 `total`），所以「保留旧键」并不能
# 真的让老断言继续绿：新增 `hardpoints.*.free` 才是唯一正确口径，一份数据说两遍只会漂移。
# 改掉的断言：`tests/test_views_b.py::test_resources_hardpoints_drones_cargo`
# （原来断言 `hardpoints_free.turret == 102 属性值`，现在按 `hardpoints` 三段核对）。


CONDITIONAL_SOURCES: frozenset[str] = frozenset(
    {
        "slots.subsystem.used",
        "slots.subsystem.total",
        "empty_slots.subsystem",
    }
)
"""**条件字段**：只有船体有子系统槽（T3C）时才出现在 overview 里。

渲染层按叶子查 `FIELD_SOURCES`，所以这三条必须登记（否则 T3C 的子系统槽位是
「无 provenance 的数值」）；但非 T3C 船上它们**没有对应叶子**，因此不能像其它字段
那样无条件断言「可达」。`tests/test_views_b.py` 据此三分：无条件字段必须可达、
条件字段在本样本必须**不可达**、并在 T3C 样本上必须可达（含 `used == 4`）。
"""

PENDING_ATTRIBUTES: dict[str, int] = {}
"""**待登记**属性白名单 —— 现在是**空的**（T-23：`maxSubSystems(1367)` 已迁进
`config/attributes.py`，红线 N12 达成）。

保留这个空表是因为 `tests/test_views_b.py` 的「来源必须是常量表里的常量」断言仍会读它
（`getattr(module, "PENDING_ATTRIBUTES", {})`）：将来再有「常量表来不及登记」的情形，
按下表约定登记在**本模块以 `ATTR_` 前缀声明的具名常量**上，裸写数字仍会被抓住。
"""

SLOT_DRONE_BAY = "drone_bay"
"""无人机舱的 `slot.type`（`ctx.items()` 里无人机的槽位类别）。"""

MAX_UPGRADE_CHECKS: int = 16
"""`upgrade_space` 的**总**重算预算（带 validate 一次 0.38 ms → 上限约 6 ms）。"""

UPGRADE_CANDIDATES_PER_MODULE: int = 2
"""每个已装模块最多验几个更高 meta 候选（取 meta 最接近的两个，避免 Abyssal 噪声）。"""

BLOCKING_HARD_RULE_TYPES: frozenset[str] = frozenset(
    {
        "wrong_slot",
        "slot_taken",
        "subsystem_taken",
        "rig_size",
        "ship_restricted",
        "capital_item",
        "max_group",
        "max_type",
        "charge_group",
        "charge_size",
    }
)
"""换装后出现这些**与技能无关的硬违规**，就说明这件候选根本装不上，不算候选（直接跳过）。

`capital_item` 实测有效：把 `Small Ancillary Armor Repairer` 换成
`Capital Ancillary Armor Repairer` 时引擎报 `{"type": "capital_item"}`。
"""

# =============================================================================
# 字段 → 属性 ID
# =============================================================================

FIELD_SOURCES: dict[str, int | None] = {
    # CPU / PG / 标定：输出、占用、余量（余量是引擎派生的负 ID，绝对值）
    "cpu.output": A.ATTR_CPU_OUTPUT,
    "cpu.load": A.ATTR_CPU_LOAD,
    "cpu.free": A.ATTR_CPU_FREE,
    "powergrid.output": A.ATTR_POWER_OUTPUT,
    "powergrid.load": A.ATTR_POWER_LOAD,
    "powergrid.free": A.ATTR_POWER_FREE,
    "calibration.output": A.ATTR_CALIBRATION_OUTPUT,
    "calibration.load": A.ATTR_CALIBRATION_LOAD,
    "calibration.free": A.ATTR_CALIBRATION_FREE,
    # 可行性（T-22）：`feasible` 是三个余量的合判（合成值），缺口是 `max(0, -free)` 的派生值
    "feasible": None,
    "over_budget.cpu": None,
    "over_budget.powergrid": None,
    # 槽位总量来自船体属性；`used` 是从 items 数出来的合成值 → None
    "slots.high.used": None,
    "slots.high.total": A.ATTR_HI_SLOTS,
    "slots.medium.used": None,
    "slots.medium.total": A.ATTR_MED_SLOTS,
    "slots.low.used": None,
    "slots.low.total": A.ATTR_LOW_SLOTS,
    "slots.rig.used": None,
    "slots.rig.total": A.ATTR_RIG_SLOTS,
    # 子系统槽位（T3C）：总量来自船体 1367 maxSubSystems，已用从 items 数
    # 只在船体真有子系统槽时出现（见 CONDITIONAL_SOURCES）
    "slots.subsystem.used": None,
    "slots.subsystem.total": ATTR_MAX_SUBSYSTEMS,
    # 挂点（T-54）：`total` 来自船体 102 / 101；`used` 是数出来的、`free` 是相减的
    # → 后两者登记 `None`（合成值，判 `assumed`，不冒充 `measured`）
    "hardpoints.turret.total": A.ATTR_TURRET_SLOTS_LEFT,
    "hardpoints.turret.used": None,
    "hardpoints.turret.free": None,
    "hardpoints.launcher.total": A.ATTR_LAUNCHER_SLOTS_LEFT,
    "hardpoints.launcher.used": None,
    "hardpoints.launcher.free": None,
    # 两种「不计入 hardpoint」的计数（T-54 审查拆分）：
    # `non_hardpoint` = 确定不占硬点；`unclassified` = 真分不清。两者都是数出来的合成值。
    "hardpoints.non_hardpoint": None,
    "hardpoints.non_hardpoint_modules": None,
    "hardpoints.unclassified": None,
    # 无人机：上限来自船体，占用是从 items 合成的
    "drone.bandwidth.used": None,
    "drone.bandwidth.total": A.ATTR_DRONE_BANDWIDTH,
    "drone.capacity.used": None,
    "drone.capacity.total": A.ATTR_DRONE_CAPACITY,
    # 货舱：38 只给容量，占用需按体积累加 → 合成值
    "cargo.used": None,
    "cargo.total": A.ATTR_CARGO_CAPACITY,
    # 空槽 = 总量 − 已用（由 slots 推导）
    "empty_slots.high": None,
    "empty_slots.medium": None,
    "empty_slots.low": None,
    "empty_slots.rig": None,
    "empty_slots.subsystem": None,
    # 升级空间：候选是否装得下由重算合成（`[]` 段表示数组元素字段）
    "upgrade_space[].slot": None,
    "upgrade_space[].module": None,
    "upgrade_space[].module_type_id": None,
    "upgrade_space[].candidate": None,
    "upgrade_space[].candidate_type_id": None,
    "upgrade_space[].meta_level_now": None,
    "upgrade_space[].meta_level": None,
    "upgrade_space[].cpu_free": A.ATTR_CPU_FREE,
    "upgrade_space[].power_free": A.ATTR_POWER_FREE,
    "upgrade_space[].calibration_free": A.ATTR_CALIBRATION_FREE,
    "upgrade_space[].fits": None,
}
"""overview 点路径 → 来源属性 ID（渲染层据此打 provenance）。

`upgrade_space[].slot` / `.module` / `.candidate` 是**字符串叶子**（槽位类别 / 当前模块名 /
候选名，来自 `ctx.items()` 与 SDE 索引），不是单一属性直读 → 登记 `None`（合成/enum 值，
渲染层判 `assumed`，红线 N4：不冒充 `measured`）。

数组一律用 `[]` 元素约定（`upgrade_space[].fits`）：这是六视角的**同一套口径**，
渲染层的容器回退（`render._tag()`）与此兼容。

`feasible` / `over_budget.*` 一律登记 `None`：它们是**合成值**（三项余量的合判 / 缺口
`max(0, -free)`），不是单一属性直读；登记 `None` 让渲染层判 `assumed`，
不冒充 `measured`（红线 N4）。
"""


# =============================================================================
# 小工具
# =============================================================================


def _number(values: dict[int, float], attribute_id: int | None) -> float | None:
    """取一个物理值；属性 ID 为 `None` 或缺失时返回 `None`（**不补 0**）。"""
    if attribute_id is None:
        return None
    return values.get(attribute_id)


def _fmt(value: float | None) -> float | None:
    """浮点去噪（`round(x, 3)`）；`None` 原样传递。"""
    return None if value is None else units.fmt(value)


def _count(value: float | None) -> int | None:
    """件数类属性（槽位/挂点天然是整数）→ `int`；缺失仍返回 `None`。"""
    return None if value is None else int(round(value))


def _fitted(items: list[dict]) -> list[tuple[int, dict]]:
    """已装模块（排除货舱），返回 `(在 fit["items"] 里的下标, 合并视图)`。

    下标与 `ctx.fit["items"]` 严格对应（引擎按序回 items，硬结论 44）。
    """
    return [
        (position, item)
        for position, item in enumerate(items)
        if item.get("slot_type") != SLOT_CARGO
    ]


def _group_name(ctx: ViewContext, type_id: object) -> str | None:
    """type_id → SDE group 名；没有索引或类型不存在时返回 `None`。"""
    if ctx.index is None or not isinstance(type_id, int):
        return None
    row = ctx.index.get_type(type_id)
    return (row or {}).get("group_name")


# =============================================================================
# 各段计算
# =============================================================================


def _slots(attrs: dict[int, float], fitted: list[tuple[int, dict]]) -> dict:
    """槽位 `{used, total}`；`used` 只从 items 数，不用「总量 − 剩余」反推。

    **子系统槽（T3C）是条件字段**：只有船体给出 `maxSubSystems(1367) = n > 0` 时才
    追加 `subsystem` 一类，`total = n`（Loki 实测 5）、`used` = 已装子系统数（实测 4）。
    非 T3C 船（Hecate / Drake / Griffin）引擎**不给** 1367 这个键 → 该类别整体不出现，
    输出与 T-20 之前逐字节相同（`tests/test_views_b.py` 有正反两向断言）。
    """
    slots: dict[str, dict] = {}
    for name, attribute_id in SLOT_SOURCES.items():
        used = sum(1 for _position, item in fitted if item.get("slot_type") == name)
        slots[name] = {"used": used, "total": _count(_number(attrs, attribute_id))}

    total = _count(_number(attrs, ATTR_MAX_SUBSYSTEMS))
    if total is not None and total > 0:
        used = sum(
            1 for _position, item in fitted if item.get("slot_type") == SUBSYSTEM_SLOT_TYPE
        )
        slots[SUBSYSTEM_SLOT_TYPE] = {"used": used, "total": total}
    return slots


def _matches_keyword(text: str, keyword: str) -> bool:
    """`text`（已小写）是否命中 `keyword`：整词表里的按**整词**，其余按子串。"""
    if keyword in HARDPOINT_WHOLE_WORD_KEYWORDS:
        return re.search(rf"(?<![0-9a-z]){re.escape(keyword)}(?![0-9a-z])", text) is not None
    return keyword in text


def _hardpoint_kind(name: object, group_name: object) -> str:
    """一个高槽模块 → **四态之一**（不是「是/不是炮」的二值）：

    | 返回 | 含义 | 谁消费 |
    |---|---|---|
    | `"turret"` / `"launcher"` | 关键词命中，**推定**占该类硬点 | 计入 `used` |
    | `"non_hardpoint"` | 组名在 `HARDPOINT_NON_HARDPOINT_GROUPS` 里，**确定不占** | 计入 `non_hardpoint` 计数 |
    | `"unclassified"` | 既判不出武器类、也不在确定白名单里 —— **分不清** | 只计数，不进 `used` |

    规则顺序：确定白名单 →「分不清」组 → launcher 关键词 → turret 关键词 → 兜底 `unclassified`。
    白名单**必须先行**：`Scan Probe Launcher` 组名含 `launcher`、`Tractor Beam` 含 `beam`，
    先判关键词就会把它们算成武器。
    """
    group = str(group_name or "").strip()
    folded = group.casefold()
    if folded in HARDPOINT_NON_HARDPOINT_GROUPS_FOLDED:
        return HARDPOINT_NON_HARDPOINT
    if folded in HARDPOINT_UNCLASSIFIED_GROUPS_FOLDED:
        return HARDPOINT_UNCLASSIFIED
    text = f"{name or ''} {group}".lower()
    if not text.strip():
        return HARDPOINT_UNCLASSIFIED
    if any(_matches_keyword(text, keyword) for keyword in LAUNCHER_KEYWORDS):
        return "launcher"
    if any(_matches_keyword(text, keyword) for keyword in HARDPOINT_TURRET_KEYWORDS):
        return "turret"
    # 没命中任何关键词、也不在确定白名单里 → **不猜**（这可能是我们漏收的一族真炮，
    # 历史实证：`Gas Cloud Harvester` 就是这样躲过关键词表的）
    return HARDPOINT_UNCLASSIFIED


def hardpoints(attrs: dict[int, float], fitted: list[tuple[int, dict]]) -> dict:
    """炮位三段 `{kind: {total, used, free}}` + `non_hardpoint` / `unclassified` 两个计数（T-54）。

    - `total` ← 船体属性 102 / 101（**基准值**，引擎已把模块的 hardpoint 修正叠进去）；
    - `used` ← **数**高槽里被 `_hardpoint_kind()` 判成该类的已装模块（启发式 → `assumed`）；
    - `free = total - used`，**可以为负**（负数 = 非法装配，`hardpoint_violation` 负责报）；
    - `total` 缺失 → `free = None`（缺值不补 0）；`used` 仍是数出来的真值；
    - `non_hardpoint` ← **确定不占硬点**的件数（隐身 / 智能炸弹 / 吸电 / 遥修…）；
      `non_hardpoint_modules` 带最多 `NON_HARDPOINT_SAMPLE` 个真名字（人读时不用再猜是哪件）；
    - `unclassified` ← 只剩**真正分不清**的（`Festival Launcher` / `Interdiction Sphere Launcher`）。
    """
    counted = dict.fromkeys(HARDPOINT_KINDS, 0)
    non_hardpoint = 0
    non_hardpoint_modules: list[str] = []
    unclassified = 0
    for _position, item in fitted:
        if item.get("slot_type") != HARDPOINT_SLOT_TYPE:
            continue
        kind = _hardpoint_kind(item.get("name"), item.get("group_name"))
        if kind in counted:
            counted[kind] += 1
            continue
        if kind == HARDPOINT_NON_HARDPOINT:
            non_hardpoint += 1
            module_name = item.get("name")
            if (
                isinstance(module_name, str)
                and module_name.strip()
                and module_name not in non_hardpoint_modules
                and len(non_hardpoint_modules) < NON_HARDPOINT_SAMPLE
            ):
                non_hardpoint_modules.append(module_name)
            continue
        unclassified += 1

    block: dict[str, object] = {}
    for kind in HARDPOINT_KINDS:
        total = _count(_number(attrs, HARDPOINT_SOURCES[kind]))
        used = counted[kind]
        block[kind] = {
            "total": total,
            "used": used,
            "free": None if total is None else total - used,
        }
    block[HARDPOINT_NON_HARDPOINT] = non_hardpoint
    block[HARDPOINT_NON_HARDPOINT_MODULES] = non_hardpoint_modules
    block[HARDPOINT_UNCLASSIFIED] = unclassified
    return block


def _empty_slots(slots: dict) -> dict:
    """空槽数 = 总量 − 已用；任一未知则 `None`，超装时夹到 0（不当负数输出）。"""
    empty: dict[str, int | None] = {}
    for name, slot in slots.items():
        total = slot["total"]
        empty[name] = None if total is None else max(total - slot["used"], 0)
    return empty


def _drone_used(drones: list[tuple[int, dict]], attribute_id: int) -> float | None:
    """无人机占用合计 = Σ(数量 × 逐架属性值)；没有无人机时是**真 0**。

    任何一架取不到该属性就整体 `None`（不做「部分相加」的假合计，红线 N4）。
    """
    if not drones:
        return units.fmt(0.0)
    total = 0.0
    for _position, item in drones:
        per_unit = item["attributes"].get(attribute_id)
        if per_unit is None:
            return None
        total += float(per_unit) * int(item.get("quantity") or 1)
    return units.fmt(total)


def _cargo_used(ctx: ViewContext, items: list[dict]) -> float | None:
    """货舱占用 = Σ(数量 × types.volume)；没有索引或体积缺失 → `None`。"""
    if ctx.index is None:
        return None
    total = 0.0
    for item in items:
        if item.get("slot_type") != SLOT_CARGO:
            continue
        type_id = item.get("type_id")
        if not isinstance(type_id, int):
            return None
        row = ctx.index.get_type(type_id)
        if row is None or row.get("volume") is None:
            return None
        total += float(row["volume"]) * int(item.get("quantity") or 1)
    return units.fmt(total)


def _feasibility(attrs: dict[int, float]) -> tuple[bool | None, dict[str, float | None]]:
    """资源可行性 `(feasible, over_budget)`（T-22 / 字段规格 §0.6）。

    判据是**自相矛盾**，不是数值阈值：CPU、PG、标定三项余量（`-9` / `-10` / `-69`）
    **任一为负**就说明「按全 active 算装不下」→ `feasible = False`。
    这不是求解：模块状态与占用**一个都不改**（降级 offline 是求解，不在本期）。

    - 任一余量缺失 → `feasible = None`（未知不给结论，红线 N4：`None >= 0` 不成立
      但也不等于「超预算」）；
    - `over_budget` 是各自的**缺口** `max(0, -free)`：没超是 `0.0`（真事实，不是缺值），
      余量本身缺失时对应项仍是 `None`；
    - `calibration` 只参与 `feasible` 判定，不生成缺口项——字段规格的 `over_budget`
      只要 `cpu` / `powergrid` 两项（标定是「够不够装 rig」的布尔约束，不是预算分配）。
    """
    cpu_free = _number(attrs, A.ATTR_CPU_FREE)
    power_free = _number(attrs, A.ATTR_POWER_FREE)
    calibration_free = _number(attrs, A.ATTR_CALIBRATION_FREE)

    over_budget: dict[str, float | None] = {
        "cpu": None if cpu_free is None else _fmt(max(0.0, -cpu_free)),
        "powergrid": None if power_free is None else _fmt(max(0.0, -power_free)),
    }

    if cpu_free is None or power_free is None or calibration_free is None:
        return None, over_budget
    feasible = cpu_free >= 0.0 and power_free >= 0.0 and calibration_free >= 0.0
    return feasible, over_budget


def _fit_with_swap(fit: dict, position: int, type_id: int) -> dict:
    """把 `fit["items"][position]` 换成另一个 type_id（不改入参，保留弹药与数量）。"""
    items = [dict(item) for item in fit.get("items") or []]
    if 0 <= position < len(items):
        items[position] = {**items[position], "type_id": int(type_id)}
    return {**fit, "items": items}


def _upgrade_plans(ctx: ViewContext, fitted: list[tuple[int, dict]]) -> list[dict]:
    """每个**同型模块**一组候选（同型只建一份计划：换装后的资源余量与它在哪个槽位无关）。"""
    if ctx.index is None:
        return []

    plans: list[dict] = []
    seen_types: set[int] = set()
    for position, item in fitted:
        type_id = item.get("type_id")
        if not isinstance(type_id, int) or type_id in seen_types:
            continue
        row = ctx.index.get_type(type_id)
        if row is None or row.get("group_id") is None:
            continue
        seen_types.add(type_id)
        meta_now = int(row.get("meta_level") or 0)
        volume_now = row.get("volume")

        candidates = []
        for candidate in ctx.index.types_in_group(int(row["group_id"])):
            if not candidate.get("published") or candidate["type_id"] == type_id:
                continue
            if int(candidate.get("meta_level") or 0) <= meta_now:
                continue
            volume = candidate.get("volume")
            # 同体积档才算同尺寸（模块体积就是尺寸档：Small 5 / Medium 10 / Large 50 / Capital 4000）
            if volume_now is not None and volume is not None and not math.isclose(
                float(volume), float(volume_now), rel_tol=1e-9
            ):
                continue
            candidates.append(candidate)

        # 取 meta 最接近的更高档（asc），避免先把 Abyssal（meta 10）这类极端件当成「升级」
        candidates.sort(
            key=lambda entry: (
                int(entry.get("meta_level") or 0),
                entry.get("name") or "",
                entry["type_id"],
            )
        )
        if not candidates:
            continue
        plans.append(
            {
                "position": position,
                "item": item,
                "type_id": type_id,
                "meta_now": meta_now,
                "candidates": candidates[:UPGRADE_CANDIDATES_PER_MODULE],
            }
        )
    return plans


def _upgrade_space(ctx: ViewContext, fitted: list[tuple[int, dict]]) -> list[dict]:
    """同组更高 meta 候选（每型取两个最近档），重算后报告资源余量与是否装得下。

    候选口径（三关全部过才算候选，不做任何数值阈值判断）：

    1. **同 group、更高 `meta_level`、`published=true`**；
    2. **同体积档**：`types.volume` 与当前模块一致（模块体积就是尺寸档：Small 5 / Medium 10 /
       Large 50 / Capital 4000 m³；不按名字前缀猜，实测 `Ancillary Armor Repairer` 组内
       小中大三种体积）；
    3. 换装后**不出现与技能无关的硬违规**（如 `capital_item`）——出现就说明装不上，跳过。

    候选**按轮取**（每型先取第 1 个，还有预算再取第 2 个），避免预算被前两个槽位吃光；
    同型模块只验一次（资源占用是逐模块相加的，与具体槽位无关）。

    「装得下」= 引擎在该变体上**没有 `resource` 类违规**（CPU / PG / 标定的 used ≤ available
    由引擎自己判，不是我方阈值）；三个余量值一并回显便于溯源。

    没有索引库时返回 `[]`（字段规格 §4 允许：候选来源必须是自建索引，不能伪造）。
    """
    if ctx.index is None:
        return []

    plans = _upgrade_plans(ctx, fitted)
    results: list[dict] = []
    checks = 0
    for round_index in range(UPGRADE_CANDIDATES_PER_MODULE):
        for plan in plans:
            candidates = plan["candidates"]
            if round_index >= len(candidates):
                continue
            if checks >= MAX_UPGRADE_CHECKS:
                return results
            checks += 1

            item = plan["item"]
            candidate = candidates[round_index]
            # 换装判定要同时看属性与违规：`Engine.calculate(..., validate=True)` 一次给两边
            # （视角里允许直接走 `ctx.engine.calculate`，见 base.py 的 `calculate_variant` 说明）
            calculation = ctx.engine.calculate(
                _fit_with_swap(ctx.fit, plan["position"], int(candidate["type_id"])),
                validate=True,
            )
            rules = [
                violation.get("rule") or {}
                for violation in (calculation.get("violations") or [])
                if isinstance(violation, dict)
            ]
            if any(rule.get("type") in BLOCKING_HARD_RULE_TYPES for rule in rules):
                continue  # 尺寸/槽位就不对，不是候选

            after = ship_attributes(calculation)
            results.append(
                {
                    "slot": item.get("slot_type"),
                    "module": item.get("name"),
                    "module_type_id": plan["type_id"],
                    "candidate": candidate.get("name"),
                    "candidate_type_id": int(candidate["type_id"]),
                    "meta_level_now": plan["meta_now"],
                    "meta_level": int(candidate.get("meta_level") or 0),
                    "cpu_free": _fmt(after.get(A.ATTR_CPU_FREE)),
                    "power_free": _fmt(after.get(A.ATTR_POWER_FREE)),
                    "calibration_free": _fmt(after.get(A.ATTR_CALIBRATION_FREE)),
                    # 只看有没有资源类违规：有 = 装不下；没有 = 装得下（引擎判定）
                    "fits": not any(rule.get("type") == "resource" for rule in rules),
                }
            )
    return results


# =============================================================================
# 视图
# =============================================================================


@register("resources")
def build(ctx: ViewContext) -> dict:
    """资源视角：余量 / 可行性 / 槽位 / 挂点 / 无人机 / 货舱 / 升级空间。`flags` 恒为空。"""
    attrs = ctx.attrs(BASE)
    items = ctx.items(BASE)
    fitted = _fitted(items)

    slots = _slots(attrs, fitted)
    drones = [(position, item) for position, item in fitted if item.get("slot_type") == SLOT_DRONE_BAY]
    feasible, over_budget = _feasibility(attrs)
    # 炮位（T-54）：高槽 ≠ 炮位。`used` 从已装模块数出来，`free = total - used` 可为负
    hardpoint_block = hardpoints(attrs, fitted)

    overview = {
        "cpu": {
            "output": _fmt(_number(attrs, A.ATTR_CPU_OUTPUT)),
            "load": _fmt(_number(attrs, A.ATTR_CPU_LOAD)),
            "free": _fmt(_number(attrs, A.ATTR_CPU_FREE)),
        },
        "powergrid": {
            "output": _fmt(_number(attrs, A.ATTR_POWER_OUTPUT)),
            "load": _fmt(_number(attrs, A.ATTR_POWER_LOAD)),
            "free": _fmt(_number(attrs, A.ATTR_POWER_FREE)),
        },
        "calibration": {
            "output": _fmt(_number(attrs, A.ATTR_CALIBRATION_OUTPUT)),
            "load": _fmt(_number(attrs, A.ATTR_CALIBRATION_LOAD)),
            "free": _fmt(_number(attrs, A.ATTR_CALIBRATION_FREE)),
        },
        # 可行性事实（T-22，§0.6）：不降级模块、不改上面的数值，只把矛盾显式化
        "feasible": feasible,
        "over_budget": over_budget,
        "slots": slots,
        # 炮位（T-54）：和 `slots` 并列。**高槽 5 个 ≠ 5 个发射器挂点**，
        # 炮/发射器是不是超装看这里的 `free`（负数 = 非法装配）。
        "hardpoints": hardpoint_block,
        "drone": {
            "bandwidth": {
                "used": _drone_used(drones, ATTR_DRONE_BANDWIDTH_USED),
                "total": _fmt(_number(attrs, A.ATTR_DRONE_BANDWIDTH)),
            },
            "capacity": {
                "used": _drone_used(drones, A.ATTR_VOLUME),
                "total": _fmt(_number(attrs, A.ATTR_DRONE_CAPACITY)),
            },
        },
        "cargo": {
            "used": _cargo_used(ctx, items),
            "total": _fmt(_number(attrs, A.ATTR_CARGO_CAPACITY)),
        },
        "empty_slots": _empty_slots(slots),
        "upgrade_space": _upgrade_space(ctx, fitted),
    }
    return {"view": "resources", "overview": overview, "flags": []}
