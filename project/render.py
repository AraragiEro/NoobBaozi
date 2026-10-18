"""分级渲染层（L3）：把「六视角 overview + 诊断 + provenance + 版本元信息」
组装成对外可读的分级输出（字段规格 §0.1 / §0.2 / §0.3）。

两个入口：

```python
render_overview(ctx, *, views=None) -> dict   # 默认：六个视角全给
render_detail(ctx, *, view, fields=None) -> dict
```

渲染层的主职是**只组装，不计算**——唯一的例外是顶层 `modes` 块（T-17 追加的横切能力，
见下文「模式对比」）。四条具体含义（每条都对应一条红线）：

1. **诊断只算一次**（`diagnostics.evaluate(ctx)` 一次一调用），再按 `flag["view"]`
   归位到 `views[<view>]["flags"]`；不属于本次渲染视角的 flag 进顶层 `flags` 兜底，
   **绝不丢弃**（丢一条就少一条可疑点，且没有任何报错）。
   六个视角各自的 `flags` 恒为空（见各视角 docstring），所以这里不会重复计算。
2. **不碰视角数值、也不自己换算**（红线 N5）：视角给的已经是物理单位（秒 / 米 / GJ…），
   渲染层原样透传；`tests/test_render.py` 用「渲染结果 == 视角 `build_view()` 结果」
   把这条钉住（一旦渲染层偷偷乘除 1000，该断言立刻炸）。
   `modes` 块是唯一新增数值的地方，它**同样不另立缩放表**：换算走投影层同一个入口
   `views.base.ship_attributes()`（内部即 `units.physical()`），所以本文件依然
   **不 import `units`**，静态守卫（红线 N5）照旧成立。
3. **不重算、不改写假设**（红线 N9）：`meta.assumptions` 就是 `ctx.assumptions`
   本身（同一个对象，不是副本、不是重新推导）。
4. **provenance 逐字段打标**（红线 N3 / N4）：每个 overview 叶子都必须有一条三态，
   没有映射就是 `unknown`，不猜、不拿默认值冒充；**本次计算里值为 `None` 也是
   `unknown`**（T-27 实例级覆盖）；**本次配装里值随模块状态（online ↔ active）变化的
   属性也是 `assumed`**（T-30 实例级规则②，见下文「状态敏感性探测」）。

provenance 的三条约定（六视角**同一套口径**）
--------------------------------------------
- **点路径**：`dps.base` / `propulsion_states.off.sig_m` / `slots.high.total`；
- **数组元素约定**：`cap_by_module[].cap_use_gj_s` 表示「对列表每个元素都套同一来源」。
  列表整体也可以被一张**容器路径**覆盖（如 `weapons`），此时元素字段回退到容器路径
  的来源，见 `_tag()`；
- **整数键字典**（`constraints.skill_floor.count_by_level` = `{等级: 技能个数}`）：
  按等级逐条展开成 `skill_floor.count_by_level.<level>`。等级集合由**实际数据**决定
  （技能档里出现过的需求等级），**不硬编码等级列表**；容器路径上登记的来源（`None`）
  会逐级继承给每个 `<level>` 叶子，见 `_tag()`。

三个容易踩的点（本层显式处理，不在别处重复）

- **实例级先行（T-27）：值为 `None` 的叶子一律 `unknown`**。不管 `FIELD_SOURCES` 把它
  映射到哪个属性、静态表怎么判，只要**本次计算**里这个字段没有值，三态就落在 `unknown`
  ——「值不可信，三态却说可信」是本轮实测的根因性缺陷（`f4` 的 `dph` 是 `None`，
  旧口径却报 `assumed`：`-11` 在表里是 `assumed`，与这一次有没有值无关）。
  provenance 本质上是**按配装**的性质，不是按属性的常量（PROGRESS §3 第 167 条）。
  统一走 `provenance.resolve(来源属性, 值, state_sensitive=…)`；数组元素路径上只要
  **任一元素**缺值就按缺值处理（一条路径只有一个标签，不能比它最弱的那个出现更自信）。
- **`FIELD_SOURCES` 里值为 `None` 且字段有值 → `assumed`**：那是「多属性合成值」
  （弹匣时长、耗时差、升级空间是否装得下…）。它不是「无法推断」（`unknown` 的定义），
  而是「建立在假设之上算出来的」，所以判 `assumed`。**注意与上一条的区别**：`None` 在
  `FIELD_SOURCES` 里说的是「来源不是一个属性」，在 overview 里说的是「这次没有值」。
  （`firepower` / `survivability` 等模块的 docstring 写的是「合成值给 None →
  渲染层据此标 unknown」。本任务书明确裁决为 `assumed`，本层按任务书执行；
  两者只是措辞口径不同，都不是「用默认值冒充 unknown」。）
- **没有映射 → `unknown`**（红线 N4）：新加字段忘了登记 `FIELD_SOURCES` 时，
  输出会老实显示「无法推断」，而不是冒充 `measured`。
- **provenance 的键空间会随数据形态变形**（PROGRESS §3 第 84 条）：同一条路径在空列表下
  是 `hard_violations`、非空时是 `hard_violations[].rule.type`——下游按路径取值前先确认
  形状，别把它当稳定 schema。

状态敏感性探测（`state_sensitive_attrs()`，T-30 / 实例级规则②）
--------------------------------------------------------------
第一层静态表只能回答「同一属性在不同配装上是否稳定」，**按配装**的差异必须实测探测。
本层不再枚举任何「模块族名单」（那会随版本过时），只做两次对照计算：

1. **全 online**（`{"all": "online"}`）与 **全 active**（`{"all": "active"}`）各算一次，
   走 `ctx.calculate_variant()` → **复用 `ViewContext` 的实例缓存**（同一份 ctx 再渲染
   一次，一次引擎调用都不会多）；
2. 逐属性比较两次的 `ship.attributes`：**值不同（或只在一侧出现）→ 该属性进入
   `set[int]` 敏感性集合**；整船一次都没变 → **空集**；
3. 集合交给 `provenance.resolve(..., state_sensitive=…)`：只把 `measured` 升格成
   `assumed`，**不降级、不改判 `unknown`**，且「值缺失 ⇒ `unknown`」仍然优先。

三条成本护栏：

- **最多多算 2 次**（online / active），且这 2 次的结果留在 ctx 缓存里，不按视角重算；
- **没有 active 能力的模块 → 直接空集、零额外计算**：判据是基础变体里引擎给的
  `item.max_state`（`online`/`offline` 的模块即使把 `state` 写成 `active` 也会被夹回
  `online`，实测差异恒为空）——这是**引擎数据**，不是模块名单；
- 探测失败不吞异常：`fit` 缺 `ship` 之类的问题照常抛出（宁可炸，不要假装「没有敏感性」）。

为什么必须这样（而不是「识别盾抗模块」）：`271`–`274` 只是**当前样本里**能观察到的
一类；`4 mass` / `552 signatureRadius`（MWD 推力质量加成 / 信号膨胀）、`-47/-50`
（主动盾修）、`-44`（装甲修）全都吃同一个状态轴。「值变了没有」这一条判据把整类问题
一次性覆盖，且与模块清单解耦。

overview 的粒度改写（`OVERVIEW_DOWNGRADES`，软件设计 §7.5）
----------------------------------------------------------
视角给**事实**，渲染层决定**粒度**：逐条明细（如 `resources.upgrade_space` 的候选清单，
单项实测 4,135 chars）在 overview 里只留**摘要**，完整清单只在 `render_detail(view=...)`
里**原样**返回。改写规则集中在 `OVERVIEW_DOWNGRADES` 一张表里（视角名 → 路径 → 摘要
函数），便于以后扩展；**不改视角的输出、不改输出键名、不改任何数值**——摘要只是对
同一份列表做计数，故判 `assumed`（见上一条）。

模式对比（顶层 `modes`，T-17 / 字段规格 §10.2）
----------------------------------------------
T3D/T3C 的三种模式是**独立 type**（不是装备、也不是假设），差异大到不能省略：实测
Defense EHP **+35.9%**、Propulsion 对齐 **−66.7%**、Sharpshooter DPS **+33.3%**。
但六个视角**都不该知道「模式」这件事**（那是渲染层的横切能力，视角模块里一行都不加），
所以对比放在顶层：

- `modes` **恒存在**（无模式时 `[]`，schema 稳定，调用方不必判 `in`）；
- 每项 `{mode_id, mode_name, ehp_mixed, dps_base, align_time_s}`：取 `-43` EHP /
  `-12` DPS / `-1` alignTime，经投影层换算后 `round(..., 3)`；
- 每条模式都是「`fit` 的副本 + `ship["mode"] = mode_id`」算出来的，走
  `ctx.calculate_variant()` → **复用实例缓存**（同一份 ctx 渲染两次不重算）；
- **无模式就不空转**：`index is None` 或 `modes_for_ship()` 返回空 → 直接 `[]`，
  一艘非 T3D 船上不会产生任何额外的引擎调用；
- 模式只写进 `modes` 块，**绝不回流**到六个视角的数值（有测试钉住：把 `modes` 关掉，
  其余字段逐字节相同）。

可信度（`views[<view>]["confidence"]`，T-17 / 字段规格 §10.2）
-------------------------------------------------------------
与 `provenance` **正交**的第二个维度：provenance 答「这个值是否依赖假设」，
confidence 答「这个值的口径可信到什么程度（A / C / E）」——**未建模 / 不可用属可信度，
不得塞进 provenance 的三态**（T-17 裁决，见 `project/confidence.py`）。

**稀疏表（T-18 修正）**：缺省 = `"A"`，所以渲染层**只输出非 A 的叶子**
（`!= DEFAULT_CONFIDENCE`），未登记的路径不出现；空表输出 `{}`（键恒在，schema 稳定）。
因此 `confidence` 的键是 `provenance` 键（= overview 叶子）的**子集**，三条不变量合起来
才等价于「全量平行表」的强度：

1. `set(confidence) ⊆ set(provenance)`（非 A 项必须都是合法叶子）；
2. **每个非 A 的登记项都必须出现在 `confidence` 里**——由测试拿
   `confidence.CONFIDENCE` / `FIELDS_FROM_MAX_TARGETS` **反查**实际叶子来验，防止漏输出；
3. `provenance` 键集合 == overview 叶子集合（不变）。

消费方必须先按叶子路径补齐默认值再查表（见 `project/confidence.py` 的稀疏表约定）。

unknown 的原因（`views[<view>]["unknown_reasons"]`，T-32 / 字段规格 §0.2.1）
----------------------------------------------------------------------------
三态**不变**，但每个 `unknown` 叶子额外带一条原因（同一个 `unknown` 其实装着「输入不完整 /
不适用 / 无唯一答案 / 引擎永不提供」四种完全不同的情况，处置方式相反）：

```json
"views": {"firepower": {"overview": {...},
                        "provenance": {"dph": "unknown"},
                        "unknown_reasons": {"dph": "input_incomplete"}}}
```

**稀疏平行表**（与 `confidence` 同构）：键集合恒等于 `provenance` 里值为 `unknown` 的路径
集合——不多（非 unknown 不写）不少（每个 unknown 必有原因）；空表输出 `{}`，键恒在。
判定优先级与全部规则在 `project/reasons.py`（flag → 条件字段 → 引擎永不提供 → 多值并列 →
兜底 `unclassified`）；本层只负责**筛出 unknown 路径**、查出它的来源属性
（`_source_of()`，与三态同一个查表路径）、并把本视角**归位后的 flag** 递过去。

`unclassified` 是兜底且**出现即缺陷**：渲染层照实输出（不隐瞒），由
`tests/test_render.py` 对四个真实样本断言数量为 0。

预算护栏（软件设计 §7.5）
-------------------------
`budget.chars` = `len(json.dumps(views)) + len(json.dumps(modes))`（顶层
`flags` 不计入）；超过 `BUDGET_CHARS` 时按顺序降级：① 去掉 `flags` 的 `evidence`
（保留 id / view / message）② 把 `cap_by_module` / `cap_sensitivity` / `weapons`
截到前 5 项，并置 `degraded=True` ③ **最后**才清空 `modes`（键保留，仍 `[]`）。
**降级只删字段、不碰数值**：截断只是少几行，留下来的数一个都不改。
"""

from __future__ import annotations

import copy
import json
from typing import AbstractSet, Callable, Mapping

from noobbaozi.config import attributes as _a
from noobbaozi.engine import scenarios
from noobbaozi.project import confidence as _confidence
from noobbaozi.project import diagnostics as _diagnostics
from noobbaozi.project import provenance as _provenance
from noobbaozi.project import reasons as _reasons
from noobbaozi.project.views import ViewContext, available_views, build_view
from noobbaozi.project.views.base import BASE, ship_attributes
from noobbaozi.project.views.loader import VIEW_MODULES, load_views

BUDGET_CHARS: int = 19000
"""overview 的字符预算（`len(json.dumps(views)) + len(json.dumps(modes))`）。

**实测值，不是拍的**（T-15 / T-18 / 样本 `spike/kill.json`，六视角 + 全 4 技能档 512 技能）：

- `resources.upgrade_space` 换成摘要后、**不含**两个新维度时 T-15 实测 **13,519 chars**
  （此前 17,777；降级两档仍 16,528）；
- T-17 的 `confidence` 全量平行表（162 条，其中约 159 条是 `A`）额外 **+5,093 chars** →
  合计 18,954，预算被迫抬到 23,000；
- **T-18 稀疏化后**（缺省 `A` 不写出）：同一份样本 `views` **13,742 chars**——比 T-15 只多
  **+223**（就是 3 条 `E` 的 `firepower.application.*`，其余 159 条默认值不再出现）；
  再加顶层 `modes`（Hecate 三模式，**341 chars**）→ 合计 **14,083 chars**；
- 预算 = `ceil(实测 × 1.2 ÷ 1000) × 1000` = `ceil(14,083 × 1.2 ÷ 1000) × 1000` =
  `ceil(16.8996) × 1000` = **17,000**（20% 余量留给不同配装的 flag 数波动）；
- **为什么比 T-15 高一点**：公式落点与 T-15 同为 17,000（13,519 与 14,083 各自 ×1.2 都落在
  16.2k–16.9k，进不了 18,000 档），但真实增量是 **+564 chars** = 非 A 的 confidence
  **+223** 与 `modes` **+341**。这两块都是 T-17 承诺的出口（「未建模/不可用」与「模式对比」），
  不能再省——能省掉的只是「重复声明这个是 A」的 4,870 chars；
- 因此**默认路径 `degraded == False`**（14,083 < 17,000）；只有更大的配装才会触发降级。
  以上是 **T-18 的 `spike/kill.json` 单样本**实测，`tests/test_render.py::test_default_budget_fits_real_sample`
  至今仍钉着它（该样本确实不降级）。**但 17,000 已经装不下其它真实配装**，见 T-44 一段。
- **T-32 复测（本任务新增 `unknown_reasons` 之后，同一份样本 `spike/kill.json`）**：
  `views` **14,681 chars**（T-18 之后又长出 `fighter_bay.*` / `feasible` / `over_budget.*`
  等叶子，172 个叶子）→ 加上 `unknown_reasons`（18 条 `unknown`，**+979 chars**）为
  **15,660**，再加 `modes` **341** → 合计 **16,001 chars**，仍 < 17,000（余 **999**，6.2%）。
  **预算值本轮不上调**：17,000 依然装得下，抬阈值只会削弱护栏；但余量已从 20% 掉到 6%，
  下一次「再加一个出口」时**必须重新按 `ceil(实测 × 1.2 ÷ 1000) × 1000` 重定**，
  不要靠放宽断言过关。
- **T-44 裁决：17,000 → 19,000（有意为之的临时放宽）。** T-43 给 `propulsion_states`
  加了 `single` 分支（每个推进类模块一条）后，L1 原始体量实测（六视角 + 全 4 技能档、
  紧凑序列化 = `budget.chars` 口径）：
  `f47` 16,885 → **17,327**、`f67` 16,723 → **17,269**、`f12` 18,178 → **18,631**
  （f12 在 17,000 下**本来就降级**）。于是 f47 / f67 **新增降级**，代价是
  **`flags` 全部丢 `evidence`**（诊断证据整块消失，不是「少几行」）+ `cap_by_module` /
  `cap_sensitivity` 截到 5 项 —— 这是产品缺陷，不是未来风险（PROGRESS §3 第 256 条②）。
  T-44 的 evidence 合并又给每条 `state_exclusive` 加了一个 `sources` 数组，
  实测体量再 +23~45：`f47` **17,350** / `f67` **17,314** / `f12` **18,654**，
  19,000 下三者仍全部 `degraded == False`（最小余量 346 chars ≈ 1.9%）。
  - **取值依据**：`18,631 × 1.02 ≈ 19,004`，按千位取整落到 **19,000**（余量 ≈1.9%，
    仍只有约 2%）；按旧公式 `ceil(实测 × 1.2 ÷ 1000) × 1000` 在 f12 上会落 **23,000** 档
    （`18,631 × 1.2 = 22,357.2` → 23,000），19,000 明显更严格。
  - **为什么允许放宽**：agent 的默认入口已改为 L0（`summary.render_index`，约 2,000 字符），
    17,000 这条护栏服务的 `render_overview` 正逐渐变成「detail 转储层」；为保住
    「诊断证据不许消失」这条红线，临时把预算抬到刚好装下最大的实测样本（18,654）。
  - **撤销条件（P3 待办）**：等 overview 的 `provenance` / `unknown_reasons` 下沉到 detail 层
    （它们目前是 overview 的大头），预算**必须压回 17,000 以下**；届时按实测重新计算，
    不许把 19,000 当既成事实沿用。

测试用 `monkeypatch` 把它调小来验证降级路径（所以函数里必须**在调用时**读这个模块常量）。
"""

TRUNCATABLE_LISTS: tuple[str, ...] = ("cap_by_module", "cap_sensitivity", "weapons")
"""降级时可以截断的列表（都是「逐条明细」，前 5 项已能表达主要来源）。"""

TRUNCATE_TO: int = 5
"""降级后每个可截断列表保留几项。"""

ARRAY_MARKER: str = "[]"
"""数组元素约定里的标记：`cap_by_module[].cap_use_gj_s`。"""

PROVENANCE_STATES: tuple[str, ...] = ("measured", "assumed", "unknown")
"""三态取值（与 `contracts.Provenance` 同源，这里只为自检与文档）。"""


# =============================================================================
# 一、叶子路径（provenance / confidence 的键空间）
# =============================================================================


def _unique(items: list[str]) -> list[str]:
    """去重且保序（数组里每个元素的字段路径相同，只需一条）。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _leaf_values(
    node: object, prefix: str, out: dict[str, list[object]]
) -> dict[str, list[object]]:
    """叶子点路径 → **该路径上所有出现过的值**（数组元素的每个元素各算一次出现）。

    与 `_leaf_paths()` 同一套寻址规则（同一份语义只写一次，避免两套 walker 漂移）：
    - 对象（dict）→ 逐键递归 `prefix.key`；**整数键也展开**（`str(key)`）；
    - 非空且元素全是对象的列表 → 逐元素递归 `prefix[].key`（数组元素约定）；
    - 其余（标量 / 字符串 / `None` / 空列表 / 空对象 / 标量列表）→ 记 `prefix` 本身。

    为什么要收集**列表**而不是一个值：`weapons[].ammo_name` 这类数组元素路径只产生一条
    点路径，但可能对应多个元素（f4：一门炮有弹药、另一门没装）。T-27 的实例级规则
    （值缺失 ⇒ `unknown`）必须看到全部出现，见 `_occurrence_value()`。
    """
    if isinstance(node, dict) and node:
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            _leaf_values(value, child, out)
        return out
    if isinstance(node, list) and node and all(isinstance(item, dict) for item in node):
        for item in node:
            _leaf_values(item, f"{prefix}{ARRAY_MARKER}", out)
        return out
    out.setdefault(prefix, []).append(node)
    return out


def _leaf_paths(node: object, prefix: str) -> list[str]:
    """收集 overview 的叶子点路径（渲染层与测试共用同一套语义）。

    寻址规则见 `_leaf_values()`；这里只取路径（去重且保序），供只关心键空间的调用方用。
    """
    return _unique(list(_leaf_values(node, prefix, {})))


def _occurrence_value(occurrences: list[object]) -> object:
    """一条路径的多个出现 → 用来判三态的**一个**值（保守方向：任一缺值就算缺值）。"""
    if any(value is None for value in occurrences):
        return None
    return occurrences[0]


def _container_of(path: str) -> str | None:
    """`cap_by_module[].cap_use_gj_s` → `cap_by_module`；不含 `[]` 时返回 `None`。"""
    head, marker, _rest = path.partition(ARRAY_MARKER)
    return head if marker else None


def _int_key_container_of(path: str) -> str | None:
    """`skill_floor.count_by_level.4` → `skill_floor.count_by_level`。

    尾段不是整数（或没有父路径）时返回 `None`：只有「整数键字典的元素路径」才允许
    向上继承容器来源，避免把普通点路径误当成容器覆盖。
    """
    head, _dot, tail = path.rpartition(".")
    return head if head and tail.isdigit() else None


_VALUE_NOT_GIVEN: object = object()
"""`_tag()` 的哨兵：调用方**没传实例值**（保持 T-27 之前的纯静态口径）。

不能用 `None` 当哨兵——`None` 正是「值缺失」这个要判的信号。
"""

_MISSING_SOURCE: object = object()
"""`FIELD_SOURCES` 查不到映射的哨兵（与「映射到 `None`（合成值）」区分开）。"""


def _state_of(attribute_id: int | None) -> str:
    """属性 ID → 三态；`None`（合成值）→ `assumed`（**不看实例值**的旧口径）。

    只给「没传值」的调用路径用（`_tag()` 的默认分支）。需要实例级覆盖时走
    `provenance.resolve()`（`_tag()` 收到值时自动走那边）。
    """
    if attribute_id is None:
        return "assumed"
    return _provenance.tag(int(attribute_id))


def _source_of(sources: Mapping[str, int | None], path: str) -> object:
    """叶子路径 → `FIELD_SOURCES` 里的来源（属性 ID / `None` / `_MISSING_SOURCE`）。

    查表顺序（与 T-27 之前完全一致，只把「返回三态」换成「返回来源」）：
    1. 整条路径命中；
    2. 数组元素路径退回**容器**路径（`weapons: None` 覆盖 `weapons[].name`）；
    3. 整数键字典的元素路径退回容器路径（`count_by_level` 覆盖 `count_by_level.4`）；
    4. 都没有 → `_MISSING_SOURCE`。
    """
    if path in sources:
        return sources[path]
    container = _container_of(path)
    if container is not None and container in sources:
        return sources[container]
    int_key_container = _int_key_container_of(path)
    if int_key_container is not None and int_key_container in sources:
        return sources[int_key_container]
    return _MISSING_SOURCE


def _tag(
    sources: Mapping[str, int | None],
    path: str,
    value: object = _VALUE_NOT_GIVEN,
    state_sensitive: AbstractSet[int] = frozenset(),
) -> str:
    """一个叶子路径（+ 本次计算里的值）→ 三态。

    查表顺序：
    0. **没传值** → 旧口径（纯静态表，保持既有调用方语义不变）；**传了值** → 交
       `provenance.resolve(来源, 值, state_sensitive=…)` 判，于是「值缺失 ⇒ `unknown`」
       与「状态敏感 ⇒ `assumed`」两条实例级规则同时生效；
    1. 整条路径命中 `FIELD_SOURCES`（含 `[]` 元素约定路径）→ 用它的来源；
    2. 否则若路径在数组元素里，退回**容器**路径的来源（容器路径能覆盖整张列表，
       例如 `weapons: None` 指的是「整张武器清单都是合成值」）；
    3. 否则若路径是**整数键字典的元素路径**（`count_by_level.4`），退回容器路径的来源
       ——等级集合由数据决定，登记在容器上即可逐级继承，不硬编码等级列表；
    4. 都没有 → `unknown`（红线 N4：不猜、不用默认值冒充）。

    **没有映射时永远 `unknown`，与值无关**：缺映射是「这个字段没登记来源」，比缺值更
    根本；不能因为碰巧有值就冒充实测。

    `state_sensitive` 是**本次配装**状态敏感性探测的产物（`state_sensitive_attrs()`），
    只有来源属性落在集合里才置位：`measured` 会被升格成 `assumed`，其它态不动
    （见 `provenance.resolve()`）。
    """
    source = _source_of(sources, path)
    if source is _MISSING_SOURCE:
        return "unknown"
    if value is _VALUE_NOT_GIVEN:
        return _state_of(source)
    sensitive = source is not None and int(source) in state_sensitive
    return _provenance.resolve(source, value, state_sensitive=sensitive)


def _field_sources(view: str) -> dict[str, int | None]:
    """某视角的 `FIELD_SOURCES`（没有声明/未加载时返回空表 → 全 `unknown`）。"""
    module = VIEW_MODULES.get(view)
    sources = getattr(module, "FIELD_SOURCES", None) if module is not None else None
    return dict(sources) if isinstance(sources, dict) else {}


def _provenance_block(
    overview: dict, view: str, state_sensitive: AbstractSet[int] = frozenset()
) -> dict[str, str]:
    """逐字段打标：**每个 overview 叶子**都有一条（覆盖率 100%）。

    T-27 起把**该路径本次的值**一并交给 `_tag()` → `provenance.resolve()`：值缺失的叶子
    强制 `unknown`，覆盖静态表的结论（实例级规则①）。数组元素路径的「缺值」口径见
    `_occurrence_value()`。

    T-30 起再把**本次配装的状态敏感性集合**传下去（实例级规则②）：来源属性在集合里的
    叶子，`measured` 升格成 `assumed`。集合由 `state_sensitive_attrs(ctx)` 在每次渲染里
    **只算一次**，六个视角共用，不按视角重算。
    """
    sources = _field_sources(view)
    return {
        path: _tag(sources, path, _occurrence_value(occurrences), state_sensitive)
        for path, occurrences in _leaf_values(overview, "", {}).items()
    }


def _confidence_block(overview: dict, view: str) -> dict[str, str]:
    """可信度**稀疏表**：只输出**非默认档**（`!= DEFAULT_CONFIDENCE`）的叶子路径。

    缺省 = `"A"`：`confidence_of()` 语义不变（未登记仍返回 `A`），但渲染层**不把 A 写进
    输出**——默认值不需要被显式写出来（T-18：T-17 的全量平行表 162 条里约 159 条是 `A`，
    实测 `+5,093 chars` 只为重复声明「这个是 A」，不值）。

    于是本表的键是 `provenance` 键的**子集**（整数键 / 数组元素的继承仍由
    `confidence_of()` 负责，不在这里另写一套匹配）。空表输出 `{}`——**键恒在**，schema
    稳定，调用方不必判 `in`；消费方必须先按叶子路径补齐默认值再查表
    （见 `project/confidence.py` 的稀疏表约定）。
    """
    default = _confidence.DEFAULT_CONFIDENCE
    sparse: dict[str, str] = {}
    for path in _leaf_paths(overview, ""):
        level = _confidence.confidence_of(view, path)
        if level != default:
            sparse[path] = level
    return sparse


# =============================================================================
# 一·C、unknown 的原因（T-32：稀疏平行表，只在 unknown 路径上出现）
# =============================================================================


def _reason_block(
    view: str,
    provenance: Mapping[str, str],
    flags: list[dict],
) -> dict[str, str]:
    """`unknown_reasons` **稀疏表**：只给 `provenance == "unknown"` 的叶子写原因。

    口径（与 `_confidence_block()` 的稀疏约定同构，见 `project/reasons.py`）：

    - **键集合 == `provenance` 里值为 `unknown` 的路径集合**（不多不少）：每个 `unknown`
      都必须有原因，非 `unknown` 的路径**不出现**（默认情况不需要被写出来）；
    - 判定顺序由 `reasons.reason_for()` 负责（flag → 条件字段 → 引擎永不提供 → 并列 → 兜底），
      本层只负责把三样输入递过去：**路径**、**该路径在 `FIELD_SOURCES` 里的来源属性**
      （与 `_tag()` 用同一个 `_source_of()`，所以容器 / 整数键回退也一致）、
      **本视角归位后的 flag id**（`weapon_damage_zero` 这类 flag 直接解释某字段）；
    - `_MISSING_SOURCE`（没登记映射）与 `None`（合成值）都按「没有来源属性」传下去，
      于是这类路径只能靠路径规则或兜底，不会凭空得到 `engine_omits`。

    空表输出 `{}`——**键恒在**，schema 稳定；消费方按 `provenance` 的 unknown 路径查表。
    """
    sources = _field_sources(view)
    flag_ids = [str(flag["id"]) for flag in flags if isinstance(flag, dict) and flag.get("id")]
    reasons: dict[str, str] = {}
    for path, state in provenance.items():
        if state != "unknown":
            continue  # 稀疏：只有 unknown 才需要原因
        source = _source_of(sources, path)
        attribute_id = None if source is _MISSING_SOURCE or source is None else int(source)
        reasons[path] = _reasons.reason_for(path, view=view, source=attribute_id, flags=flag_ids)
    return reasons


# =============================================================================
# 一·B、状态敏感性探测（T-30：实例级规则②，按配装实测，不枚举模块族）
# =============================================================================

STATE_ONLINE: str = "online"
"""`{"all": "online"}` 变体的状态名（`engine/scenarios.py` 的合法 `state`）。"""

STATE_ACTIVE: str = "active"
"""`{"all": "active"}` 变体的状态名（同上）。"""

STATE_VARIANT_PREFIX: str = "state:"
"""临时变体的缓存 key 前缀（`ViewContext.calculate_variant` 的槽名）。

**不能**与 `views.base.VARIANTS` 的预置名冲突（`base`/`overload`/`offline`），
`calculate_variant()` 会直接 `ValueError` 拒绝重名。
"""

ACTIVE_CAPABLE_MAX_STATES: frozenset[str] = frozenset({"active", "overload"})
"""引擎回 `item.max_state` 落在这些值上，模块才**可能**受 active 状态影响。

实测依据（本 SDE）：`state` 会被引擎按 `max_state` 夹住——一块 `Damage Control II`
在输入里写 `active`，计算结果里仍是 `online`；而 `online`-only 配装的
「全 online」与「全 active」两次属性表**逐键相等**。所以这是「有没有 active 能力」
的**引擎数据判据**，不是模块族名单（名单会随版本过时，这条不会）。
"""


def _state_variant_attrs(ctx: ViewContext, state: str) -> dict[int, float]:
    """「所有已装模块统一设为 `state`」这一次计算的船体属性（物理值）。

    - 变体由 `engine/scenarios.build_variant()` 造（`{"all": state}` 语义只在 L2 定义，
      本层不另写一套状态改写规则）；
    - 结果走 `ctx.calculate_variant()` → **留在 `ViewContext` 的实例缓存里**，同一份 ctx
      再渲染一次不会重算；也正因为如此，六个视角共用这一次结果。
    """
    key = f"{STATE_VARIANT_PREFIX}{state}"
    spec = {"name": key, "states": {scenarios.ALL_KEY: state}, "mode_id": None}
    fit = scenarios.build_variant(ctx.fit, spec)
    return ship_attributes(ctx.calculate_variant(key, fit))


def _has_active_capable_module(ctx: ViewContext) -> bool:
    """整船有没有「可能受 active 影响」的模块（只看引擎回的 `max_state`）。

    基础变体本来就会被视角算出来（`ctx.variant(BASE)` 命中同一份缓存），所以这个前置
    检查**不产生额外引擎调用**；它挡掉的是「纯被动配装上还去算 online/active」这种
    纯浪费。返回 `False` 时敏感性集合必然是空集（模块被 `max_state` 夹住，两次结果相同）。
    """
    calculation = ctx.variant(BASE)
    for item in calculation.get("items") or []:
        if isinstance(item, dict) and item.get("max_state") in ACTIVE_CAPABLE_MAX_STATES:
            return True
    return False


def state_sensitive_attrs(ctx: ViewContext) -> set[int]:
    """本次配装里「值随模块状态（online ↔ active）变化」的属性 ID 集合（T-30）。

    做法就是**实测探测**，不含任何模块族名单：

    1. 全 online / 全 active 各算一次（最多 **2** 次额外计算，且进 ctx 缓存）；
    2. 逐属性比较：两次的值不同（**或只在一侧出现**——例如护盾回充 `-47` 只在 active
       那一侧存在）→ 该属性在**这次配装**里是状态敏感的；
    3. 整船一次都没变 → **空集**（静态表结论原样生效）。

    比较用精确不等（不做容差）：判「敏感」只会把 `measured` 推向更保守的 `assumed`，
    所以宁可多判；反过来才是有害的方向。

    整船没有 active 能力的模块 → 前置检查直接返回空集，**零额外计算**
    （见 `_has_active_capable_module()`）。

    **已知范围**：只比较**船体**属性（`ship.attributes`），装备条目级属性不参与。实测
    36 个 KB 样本里「只在装备条目里变」的属性只有 `-45/-47/158/160`，**四个都是
    `assumed`**，所以当前不漏判任何 `measured`；要覆盖到「装备条目级 measured 属性」时，
    把 `items[*].attributes` 一起并进比较即可（不增加引擎调用）。

    Returns:
        属性 ID 集合（`set[int]`）。空集 = 本次配装看不到任何状态敏感性。
    """
    if not _has_active_capable_module(ctx):
        return set()

    online = _state_variant_attrs(ctx, STATE_ONLINE)
    active = _state_variant_attrs(ctx, STATE_ACTIVE)
    return {
        attribute_id
        for attribute_id in set(online) | set(active)
        if online.get(attribute_id) != active.get(attribute_id)
    }


# =============================================================================
# 二、顶层 modes 块（T3D/T3C 的横切能力）
# =============================================================================

MODE_EHP_ATTR: int = _a.ATTR_EHP
"""`-43` 混合 EHP（与 `survivability.ehp_mixed` 同一个派生值；常量表 `ATTR_EHP`）。"""

MODE_DPS_ATTR: int = _a.ATTR_DAMAGE_DPS
"""`-12` 秒伤（与 `firepower.dps.base` 同一个派生值；常量表 `ATTR_DAMAGE_DPS`）。"""

MODE_ALIGN_ATTR: int = _a.ATTR_ALIGN_TIME
"""`-1` 对齐时间（秒；与 `mobility.align_time_s` 同一个派生值；常量表 `ATTR_ALIGN_TIME`）。

三条都是**派生负 ID**（引擎 pass 4 输出，`Index.attribute()` 查不到 → 「派生，SDE 无记录」）。
T-32 起不再裸写数字（红线 N12），一律从 `config/attributes.py` 引用；这里保留具名常量，
是因为它们表达的是「`modes` 块取哪三个数」这件事，比在字典字面量里写 `_a.ATTR_EHP` 更好读。
"""

MODE_DIGITS: int = 3
"""模式数值的小数位（字段规格 §10.2：对比看趋势，3 位足够且省预算）。"""

MODE_NAME_SUFFIX: str = " mode"
"""模式类型名尾巴（SDE 里是 `Hecate Defense Mode` / `Anhinga Primary Mode`）。"""


def _rounded(value: float | None) -> float | None:
    """取整到 `MODE_DIGITS` 位；缺值仍给 `None`（不补零冒充 0）。"""
    return None if value is None else round(float(value), MODE_DIGITS)


def _mode_name(raw_name: object, ship_name: object) -> str:
    """`Hecate Defense Mode` + 船名 `Hecate` → `Defense`（只去前缀/后缀，不改写词）。"""
    name = str(raw_name or "").strip()
    if not name:
        return ""
    if name.lower().endswith(MODE_NAME_SUFFIX):
        name = name[: -len(MODE_NAME_SUFFIX)].strip()
    ship = str(ship_name or "").strip()
    if ship and name.lower().startswith(ship.lower()):
        name = name[len(ship) :].strip()
    return name or str(raw_name)


def _modes_block(ctx: ViewContext) -> list[dict]:
    """三模式对比（`[{"mode_id", "mode_name", "ehp_mixed", "dps_base", "align_time_s"}]`）。

    - `index is None` / 没有 `ship.type_id` / 该船没有模式 → `[]`（**不空转**：
      非 T3D 船上不会发生任何额外引擎调用）；
    - 每条模式都是 `fit` 的深拷贝 + `ship["mode"] = mode_id`，计算走
      `ctx.calculate_variant()`，**复用实例缓存**（同 key 只算一次）；
    - 换算走 `views.base.ship_attributes()`（内部即 `units.physical()`）：本层不另立
      缩放表，也不 import `units`（红线 N5）；
    - 只写 `modes` 块，**不回流**到六个视角的数值。
    """
    index = ctx.index
    if index is None:
        return []

    fit = ctx.fit if isinstance(ctx.fit, dict) else {}
    ship = fit.get("ship")
    type_id = ship.get("type_id") if isinstance(ship, dict) else None
    if not isinstance(type_id, int):
        return []

    rows = index.modes_for_ship(type_id)
    if not rows:  # 无模式船（绝大多数）：一次引擎调用都不发生
        return []

    ship_row = index.get_type(type_id) or {}
    entries: list[dict] = []
    for row in rows:
        mode_id = int(row["type_id"])
        variant = copy.deepcopy(fit)
        variant_ship = variant.get("ship")
        if not isinstance(variant_ship, dict):
            variant_ship = {}
            variant["ship"] = variant_ship
        variant_ship["mode"] = mode_id

        attributes = ship_attributes(ctx.calculate_variant(f"mode:{mode_id}", variant))
        entries.append(
            {
                "mode_id": mode_id,
                "mode_name": _mode_name(row.get("name"), ship_row.get("name")),
                "ehp_mixed": _rounded(attributes.get(MODE_EHP_ATTR)),
                "dps_base": _rounded(attributes.get(MODE_DPS_ATTR)),
                "align_time_s": _rounded(attributes.get(MODE_ALIGN_ATTR)),
            }
        )
    return entries


# =============================================================================
# 三、overview 的粒度改写（`OVERVIEW_DOWNGRADES`）
# =============================================================================

SUMMARY_PROVENANCE: str = "assumed"
"""摘要叶子的三态：摘要 = 对视角同一份列表的**计数聚合**（合成值）→ `assumed`。

既不是「无法推断」（`unknown` 的定义），也不是单一属性的实测（`measured`）；
与 `FIELD_SOURCES` 里登记 `None` 的语义**完全一致**（见模块 docstring）。
"""

SummaryFn = Callable[[list], dict]
"""`OVERVIEW_DOWNGRADES` 里摘要函数的签名：`list -> dict`（纯函数，只数不改数）。"""


def _summarize_upgrade_space(rows: list) -> dict:
    """`resources.upgrade_space` → 摘要（候选清单只在 `render_detail` 里给全）。

    三个字段都只是**数着同一份列表**，不重算、不取整、不改任何视角数值：

    - `slots_checked`：已完成的「槽位 × 候选」检查条数（= 列表长度）；
    - `upgradeable_count`：其中 `fits is True` 的条数（装得下几个）；
    - `blocked_by_resource`：有候选、但**一条都装不下**（说明卡在 CPU/PG/标定上；
      没有候选时是 `False`——「没有升级空间」不等于「被资源卡住」）。
    """
    checked = [row for row in rows if isinstance(row, dict)]
    upgradeable = sum(1 for row in checked if row.get("fits") is True)
    return {
        "slots_checked": len(checked),
        "upgradeable_count": upgradeable,
        "blocked_by_resource": bool(checked) and upgradeable == 0,
    }


OVERVIEW_DOWNGRADES: dict[str, dict[str, "SummaryFn"]] = {
    "resources": {"upgrade_space": _summarize_upgrade_space},
}
"""overview 的**粒度改写表**（视角名 → overview 路径 → 摘要函数）。

约定：

- 路径指向 overview 里的一个 `list`；命中时整段替换成摘要函数返回的 `dict`（键名由
  摘要函数决定，**摘要叶子判 `assumed`**）；
- 只在 `render_overview` 生效；`render_detail` **原样返回完整列表**；
- 视角本身不改（`resources.build()` 仍然产出完整候选清单）——「视角给事实，渲染层
  决定粒度」；
- 以后要再压别的明细项，只在这里加一行 + 一个纯函数，不碰视角、不碰预算降级逻辑。
"""


def _apply_overview_downgrades(overview: dict, view: str) -> tuple[dict, dict[str, str]]:
    """按 `OVERVIEW_DOWNGRADES` 改写 overview，返回 `(新 overview, 摘要叶子的三态覆盖)`。

    视角的 overview 是 `build_view()` 每次新建的对象，这里仍然**先浅拷贝再替换**，
    避免调用方拿到被就地修改的视角结果。不是 `list` 的路径（空/形状变了）**跳过**，
    不猜形状、不伪造摘要。
    """
    rules = OVERVIEW_DOWNGRADES.get(view) or {}
    if not rules:
        return overview, {}

    downgraded = dict(overview)
    overrides: dict[str, str] = {}
    for path, summarize in rules.items():
        items = overview.get(path)
        if not isinstance(items, list):
            continue
        summary = summarize(items)  # 表里只放 list -> dict 的纯函数
        downgraded[path] = summary
        for key in summary:
            overrides[f"{path}.{key}"] = SUMMARY_PROVENANCE
    return downgraded, overrides


# =============================================================================
# 四、Meta 与逐视角组装
# =============================================================================


def _meta(ctx: ViewContext, scope: str) -> dict:
    """版本三元组 + 假设 + 分级范围（规范 §4.5 / 红线 N9）。"""
    engine_meta = ctx.meta if isinstance(ctx.meta, dict) else {}
    return {
        "sde_build": engine_meta.get("sde_build"),
        "engine_version": engine_meta.get("engine_version"),
        "spec_version": engine_meta.get("spec_version"),
        # 红线 N9：直接取 `ctx.assumptions`（同一个对象，不重算、不复制、不补默认值）
        "assumptions": ctx.assumptions,
        "scope": scope,
    }


def _collect_flags(
    ctx: ViewContext, built: tuple[str, ...]
) -> tuple[dict[str, list[dict]], list[dict]]:
    """`diagnostics.evaluate(ctx)` **只调一次**，再按 `flag["view"]` 归位。

    Args:
        ctx: 视角上下文。
        built: 本次要渲染的视角名（归位目标）。

    Returns:
        `(attributed, residual)`：`{视角名: [flag, ...]}` 与「不属于本次渲染视角」的
        flag 列表（进顶层 `flags` 兜底，绝不丢）。
    """
    attributed: dict[str, list[dict]] = {}
    residual: list[dict] = []
    targets = set(built)

    for flag in _diagnostics.evaluate(ctx):
        view = flag.get("view") if isinstance(flag, dict) else None
        if isinstance(view, str) and view in targets:
            attributed.setdefault(view, []).append(flag)
        else:
            residual.append(flag)
    return attributed, residual


def _view_entry(
    name: str,
    ctx: ViewContext,
    attributed: dict[str, list[dict]],
    *,
    scope: str,
    state_sensitive: AbstractSet[int] = frozenset(),
) -> dict:
    """一个视角的 `{overview, provenance, unknown_reasons, confidence, flags}`。

    视角自己回 `flags`（契约允许，当前六视角恒为空）；这里与归位来的诊断合并，
    视角自己的在前——诊断只在渲染层算一次，视角层不许重复算。

    `scope="overview"` 时先按 `OVERVIEW_DOWNGRADES` 把逐条明细换成摘要，**再**按改写过
    的 overview 打 provenance / unknown_reasons / confidence（三张表的键空间必须与返回的
    overview 逐叶子一致；后两张都是**稀疏表**，只含其中非默认 / 非全量的项）；摘要叶子用
    `SUMMARY_PROVENANCE` 覆盖三态。`scope="detail"` 时原样返回完整列表。

    `unknown_reasons` 必须**在 flags 归位之后**算：`weapon_damage_zero` 这类 flag 是
    `dph` 判 `input_incomplete` 的**唯一**依据（规则 1 优先），拿不到它就只剩路径推断。

    `state_sensitive` 是**本次配装**的状态敏感性属性集合（`state_sensitive_attrs()`），
    由调用方算一次后传给每个视角（T-30 实例级规则②）。
    """
    view = build_view(name, ctx)
    overview = view.get("overview") or {}
    own_flags = list(view.get("flags") or [])

    overrides: dict[str, str] = {}
    if scope == "overview":
        overview, overrides = _apply_overview_downgrades(overview, name)

    provenance = _provenance_block(overview, name, state_sensitive)
    provenance.update(overrides)
    flags = own_flags + attributed.get(name, [])

    return {
        "overview": overview,
        "provenance": provenance,
        "unknown_reasons": _reason_block(name, provenance, flags),
        "confidence": _confidence_block(overview, name),
        "flags": flags,
    }


# =============================================================================
# 五、预算护栏（只删字段，不碰数值）
# =============================================================================


def _measure(views: Mapping[str, dict], modes: list[dict] | None = None) -> int:
    """按 `len(json.dumps(...))` 估算字符数：`views` 子树 + 顶层 `modes`。

    顶层 `flags` 不计入（与 T-15 同口径）；`modes` 是 T-17 新增的输出，必须计入预算，
    否则「砍掉 modes 换预算」这件事根本没有触发条件。
    """
    chars = len(json.dumps(views, ensure_ascii=False))
    if modes:
        chars += len(json.dumps(modes, ensure_ascii=False))
    return chars


def _strip_evidence(flags: list[dict]) -> None:
    """降级①：去掉每条 flag 的 `evidence`（保留 id / view / message）。原地修改。"""
    for flag in flags:
        if isinstance(flag, dict):
            flag.pop("evidence", None)


def _truncate_lists(views: dict[str, dict]) -> None:
    """降级②：把三个明细列表截到前 `TRUNCATE_TO` 项。原地修改。

    只切片、不重算：留下来的元素与降级前**逐字节相同**。
    """
    for entry in views.values():
        overview = entry.get("overview")
        if not isinstance(overview, dict):
            continue
        for key in TRUNCATABLE_LISTS:
            value = overview.get(key)
            if isinstance(value, list) and len(value) > TRUNCATE_TO:
                overview[key] = value[:TRUNCATE_TO]


def _apply_budget(views: dict[str, dict], residual: list[dict], modes: list) -> dict:
    """按顺序降级直到装进制预算内，返回 `{"chars": int, "degraded": bool}`。

    预算按 `views` + `modes` 度量（顶层 `flags` 不计入，但降级时一并去 evidence——
    否则「同样一条 flag 在两处形状不同」会让调用方困惑）。

    降级顺序（**modes 最后**，T-17 裁决：先砍别的）：① 去 flag 的 `evidence`
    ② 截断三个明细列表 ③ 清空 `modes`（原地 `del modes[:]`：键保留、schema 稳定，
    调用方拿到的仍是同一个 list 对象）。
    """
    limit = BUDGET_CHARS  # 调用时读模块常量：测试要能 monkeypatch 它
    chars = _measure(views, modes)
    degraded = False

    if chars > limit:
        degraded = True
        for entry in views.values():
            _strip_evidence(entry.get("flags") or [])
        _strip_evidence(residual)
        chars = _measure(views, modes)

    if chars > limit:
        _truncate_lists(views)
        chars = _measure(views, modes)

    if chars > limit and modes:
        del modes[:]  # 最后才砍 modes（只清空内容，不换对象）
        chars = _measure(views, modes)

    return {"chars": chars, "degraded": degraded}


# =============================================================================
# 六、detail 的字段挑选（点路径 / 数组元素路径）
# =============================================================================


def _resolve(node: object, path: str) -> tuple[bool, object]:
    """按点路径取值；段尾的 `[]` 表示「对列表每个元素套用余下路径」。"""
    return _walk(node, path.split("."))


def _walk(node: object, parts: list[str]) -> tuple[bool, object]:
    if not parts:
        return True, node

    head, rest = parts[0], parts[1:]
    if head.endswith(ARRAY_MARKER):
        key = head[: -len(ARRAY_MARKER)]
        if not isinstance(node, dict) or key not in node:
            return False, None
        items = node[key]
        if not isinstance(items, list):
            return False, None
        values: list[object] = []
        for item in items:
            found, value = _walk(item, rest)
            if not found:
                return False, None
            values.append(value)
        return True, values

    if isinstance(node, dict) and head in node:
        return _walk(node[head], rest)
    return False, None


def _merge(target: dict, parts: list[str], value: object) -> None:
    """把 `_resolve` 取到的值按同一套路径装回（多字段请求时合并不冲突）。"""
    head, rest = parts[0], parts[1:]
    if not rest:
        target[head] = copy.deepcopy(value)  # 深拷贝：调用方拿到的不是视角内部对象
        return

    if head.endswith(ARRAY_MARKER):
        key = head[: -len(ARRAY_MARKER)]
        bucket = target.get(key)
        if not isinstance(bucket, list):
            bucket = []
            target[key] = bucket
        items = value if isinstance(value, list) else []
        for index, item_value in enumerate(items):
            while len(bucket) <= index:
                bucket.append({})
            if not isinstance(bucket[index], dict):
                bucket[index] = {}
            _merge(bucket[index], rest, item_value)
        return

    bucket = target.get(head)
    if not isinstance(bucket, dict):
        bucket = {}
        target[head] = bucket
    _merge(bucket, rest, value)


def _select(overview: dict, fields: list[str]) -> tuple[dict, list[str]]:
    """只保留指定点路径（找不到的字段名进 `missing`，不静默忽略）。"""
    picked: dict = {}
    missing: list[str] = []
    for field in fields:
        found, value = _resolve(overview, field)
        if not found:
            missing.append(field)
            continue
        _merge(picked, field.split("."), value)
    return picked, missing


def _covered(path: str, fields: list[str]) -> bool:
    """provenance 键是否属于被请求的字段（等于它、或挂在它下面）。"""
    for field in fields:
        if path == field:
            return True
        if path.startswith(f"{field}.") or path.startswith(f"{field}{ARRAY_MARKER}"):
            return True
    return False


# =============================================================================
# 七、对外入口
# =============================================================================


def render_overview(ctx: ViewContext, *, views: tuple[str, ...] | None = None) -> dict:
    """分级渲染的 `overview` 层（默认六个视角全给）。

    Args:
        ctx: 视角上下文（六视角共用同一个；变体缓存在它身上）。
        views: 只要这些视角（默认 `load_views()` = `VIEW_ORDER` 顺序的六个）。

    Returns:
        `{"meta", "views", "modes", "flags", "budget"}`：

        - `meta`：`sde_build` / `engine_version` / `spec_version` /
          `assumptions`（= `ctx.assumptions`）/ `scope="overview"`；
        - `views`：`{视角名: {"overview", "provenance", "unknown_reasons", "confidence",
          "flags"}}`，顺序 = 传入顺序（默认 `VIEW_ORDER`）；overview 里的逐条明细按
          `OVERVIEW_DOWNGRADES` 换成**摘要**（如 `resources.upgrade_space`），
          完整清单走 `render_detail`；`unknown_reasons` 与 `confidence` 都是**稀疏表**
          （前者只在 `provenance == "unknown"` 的路径上出现，后者缺省 `A`、只含非 A 项，
          键集都是 `provenance` 的子集）；
        - `modes`：T3D/T3C 的三模式对比（**恒存在**，无模式时 `[]`）；每项
          `{mode_id, mode_name, ehp_mixed, dps_base, align_time_s}`；
        - `flags`：**不属于本次渲染视角**的诊断兜底桶（通常为空；不丢诊断）；
        - `budget`：`{"chars", "degraded"}`，`chars` 计入 `views` + `modes`。

    Raises:
        KeyError: `views` 里有未注册的视角名（不返回空壳）。
    """
    targets = tuple(views) if views is not None else load_views()
    registered = set(available_views())
    unknown = [name for name in targets if name not in registered]
    if unknown:
        raise KeyError(f"这些视角未注册：{unknown}；已注册：{list(available_views())}")

    attributed, residual = _collect_flags(ctx, targets)
    # T-30：状态敏感性只探测一次（最多多算 2 次），六个视角共用同一个集合。
    state_sensitive = state_sensitive_attrs(ctx)
    rendered = {
        name: _view_entry(name, ctx, attributed, scope="overview", state_sensitive=state_sensitive)
        for name in targets
    }
    modes = _modes_block(ctx)
    budget = _apply_budget(rendered, residual, modes)

    return {
        "meta": _meta(ctx, "overview"),
        "views": rendered,
        "modes": modes,
        "flags": residual,
        "budget": budget,
    }


def render_detail(
    ctx: ViewContext, *, view: str, fields: list[str] | None = None
) -> dict:
    """分级渲染的 `detail` 层：单视角的完整 overview + provenance + confidence（可按字段裁剪）。

    与 overview 的唯一区别是**粒度**：`OVERVIEW_DOWNGRADES` 里登记的路径在这里
    **原样返回完整列表**（例如 `resources.upgrade_space` 的全部候选），不截断、不摘要。

    Args:
        ctx: 视角上下文。
        view: 视角名（必须已注册）。
        fields: 非空时只返回这些点路径（支持 `a.b` 与 `cap_by_module[].cap_use_gj_s`）；
            找不到的字段名原样进 `missing`，**不静默忽略**。`provenance` 与
            `confidence` 同口径裁剪（confidence 只保留被请求字段下的**非 A 项**）。

    Returns:
        `{"meta", "view", "overview", "provenance", "unknown_reasons", "confidence", "flags",
        "missing"}`。
        `missing` 恒存在（没有缺字段时是空列表），便于调用方稳定解析。

    Raises:
        KeyError: 视角未注册。
    """
    if view not in set(available_views()):
        raise KeyError(f"视角 {view!r} 未注册；已注册：{list(available_views())}")

    attributed, _residual = _collect_flags(ctx, (view,))
    entry = _view_entry(
        view,
        ctx,
        attributed,
        scope="detail",
        state_sensitive=state_sensitive_attrs(ctx),
    )

    overview = entry["overview"]
    provenance = entry["provenance"]
    unknown_reasons = entry["unknown_reasons"]
    confidence = entry["confidence"]
    missing: list[str] = []

    if fields:
        requested = [str(field) for field in fields]
        overview, missing = _select(overview, requested)
        provenance = {
            path: state for path, state in provenance.items() if _covered(path, requested)
        }
        unknown_reasons = {
            path: reason
            for path, reason in unknown_reasons.items()
            if _covered(path, requested)
        }
        confidence = {
            path: level for path, level in confidence.items() if _covered(path, requested)
        }

    return {
        "meta": _meta(ctx, "detail"),
        "view": view,
        "overview": overview,
        "provenance": provenance,
        "unknown_reasons": unknown_reasons,
        "confidence": confidence,
        "flags": entry["flags"],
        "missing": missing,
    }


# =============================================================================
# 八、输出分层（L0 / L1 / 按路径取数）—— **实现在 `project/summary.py`**
# =============================================================================
#
# 为什么是「薄封装 + 函数内 import」而不是在本文件里实现：
#
# 1. 三个入口全部**复用上面已有的构建路径**（`_collect_flags` / `_view_entry` /
#    `_meta` / `state_sensitive_attrs`），所以 L0/L1 与 `render_overview` 的数值
#    不可能漂移；本文件不新增任何计算。
# 2. 组装逻辑放在 `project/summary.py`（第一阶段唯一的新文件），本文件只**追加导出**，
#    `render_overview` / `render_detail` 的行为一个字都没改（验收基线不得漂移）。
# 3. 函数内 import：`summary` 在模块级 import 本文件，模块级互相 import 会形成环
#    （谁先被 import 谁不完整）。放函数体内只在实际调用时解析，两个方向都安全。


def render_index(ctx: ViewContext) -> dict:
    """L0 摘要（实测 1,884–1,965 字符）：六视角各 3–5 个数 + 精简 flag + provenance 计数。

    契约与字段清单见 `noobbaozi/docs/输出分层与工具集-v1.md` §3，实现见
    `noobbaozi.project.summary.render_index`。**不含** `evidence` / 逐叶子三张平行表 /
    逐模块列表 / 512 条技能明细；`meta` 压缩成 `{sde_build, engine_version,
    spec_version, skill_tier, module_states, damage_profile, mode}`（`skill_tier` 只给
    `{name, count, hash}`）。模式对比（`modes`）不在 L0，走 `render_overview()["modes"]`。

    Args:
        ctx: 视角上下文（与 `render_overview` 同一份；变体缓存共用）。

    Returns:
        `{"ship", "meta", "<六视角>", "flags", "provenance_counts", "budget"}`。
    """
    from noobbaozi.project.summary import render_index as _impl

    return _impl(ctx)


def render_view(ctx: ViewContext, view_name: str) -> dict:
    """L1 单视角：`{overview, provenance, unknown_reasons, confidence, flags}`（含 evidence）。

    **只构建被请求的那一个视角**，不重算六视角再丢弃；返回值与
    `render_overview(ctx)["views"][view_name]` 逐字节相同（overview 未降级时）。
    实现见 `noobbaozi.project.summary.render_view`。

    Raises:
        ValueError: 视角名未注册（消息里列出可用视角）。
    """
    from noobbaozi.project.summary import render_view as _impl

    return _impl(ctx, view_name)


def get_path(ctx: ViewContext, path: str) -> dict:
    """按稳定路径取数：`{value, provenance, reason?, source_view}`。

    路径表达式在 L0/L1 里一致（`mobility.speed_mps` / `firepower.weapons[].name` /
    `constraints.tier_violations[0].kind`）；先按 L0 语义、再落回 L1；
    **路径不存在返回 `value=None` + `hint`**（不抛异常）。实现见
    `noobbaozi.project.summary.get_path`。
    """
    from noobbaozi.project.summary import get_path as _impl

    return _impl(ctx, path)


__all__ = [
    "BUDGET_CHARS",
    "OVERVIEW_DOWNGRADES",
    "PROVENANCE_STATES",
    "SUMMARY_PROVENANCE",
    "TRUNCATABLE_LISTS",
    "TRUNCATE_TO",
    "get_path",
    "render_detail",
    "render_index",
    "render_overview",
    "render_view",
    "state_sensitive_attrs",
]
