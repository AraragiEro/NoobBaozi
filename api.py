"""L5 接口层（P4）：MCP / CLI / Skill **共用的唯一逻辑层**。

权威文档：`noobbaozi/docs/输出分层与工具集-v1.md` §10（L5 接口层契约）。本模块只做
「**参数 → 结果**」：不做 NL 理解、不做评分 / 排名 / 定性判词、**不二次换算任何数值**
（红线 N5）、不调用 LLM、**不用默认值填补缺失值**（红线 N4 —— 「不确定」只能走
`provenance` / `unknown_reasons` / `error` 出去）。

五个入口（签名与返回严格照 §10.1 / §10.3 / §10.4）：

```python
parse_fit(fit, *, skill_tier, damage_profile, state_policy)          -> dict  # 归一化成结构
fit_index(fit, *, ...)                                               -> dict  # L0 六维度摘要
fit_view(fit, view, *, ...)                                          -> dict  # L1 单视角全字段
fit_get(fit, path, *, ...)                                           -> dict  # 按路径取一个值
fit_detail(fit, view=None, path=None, *, ...)                        -> dict  # 列表型明细
```

第六个入口（T-49，**只接受战报**）：

```python
fit_killmail(fit, *, skill_tier, damage_profile, state_policy)       -> dict  # 战报上下文
```

`fit_killmail` 回答的是「谁打的、损失多少、挨了多少伤害」——损失价值 / 参战方 / 伤害构成
/ 最终一击 / 星系与安全等级全部由它给，agent **不需要再去打开 zkillboard 网页**。
输入没有战报来源（EFT / DNA / structured fit）时它诚实报 `kind: "not_a_killmail"`，
绝不拿配装猜一条战报。

第七个入口（T-50，**只查列表**）：

```python
zkb_search(*, character, corporation, alliance, ship, solar_system, region,
           past_seconds, page, side, limit, **common)                -> dict  # 战报列表
```

`zkb_search` 与 `fit_killmail` 是**同一件事的两层**，谁也不能替代谁：

| 层 | 谁给 | 能回答什么 | 拿不到什么 |
|---|---|---|---|
| 列表（`zkb_search`） | zkillboard `/api/kills|losses/…` | 谁在哪被谁打掉、值多少钱、solo/npc、一页 200 条 | **完整配装**（列表里没有 fitted 模块） |
| 单条（`fit_killmail`） | zkb `killID` → ESI killmail | 一条战报的完整上下文（参战方 / 伤害构成 / 损失明细） | 批量（一次一条） |

所以典型链路是 `zkb_search` 先筛出 `killmail_id`，再对感兴趣的那几条调 `fit_killmail`。
`zkb_search` **不进结果缓存**（列表数据时效性强、且带限流），只缓存「名字 → id」的解析结果。

统一信封
--------

成功 `{"ok": true, "result": {...}, "meta": {...}}`，失败
`{"ok": false, "error": {...}}`（**永不抛裸异常**，§10.4）。`meta` 一定回显
`spec_version` / `engine_version` / `sde_build` / `skill_tier: {name, count}` /
`damage_profile` / `state_policy` / `source_format`。

输入三形态（自动判别，不猜）
--------------------------

`fit` 接受四种写法，判别顺序是**先看「要不要联网」再看内容**：

1. `{"killmail_id": N, "hash": "..."}` —— **联网**（ESI killmail）；
2. zkillboard / ESI 的 killmail URL 字符串 —— **联网**（zkill 的 URL 只带 id，
   需要先向 zkillboard 查一次 `hash`，再取 ESI killmail；两步任一失败都是
   `network_failed`，绝不拿半成品继续算）；
3. `dict` / `list`（完整 killmail、structured fit、ESI fitting）—— `json.dumps`
   后交给 `engine.normalize.normalize_fit()`，**本层不解析任何配装语法**；
4. `str`（EFT 文本 / DNA / `%esf/1` / JSON 文本）—— 原样交给 `normalize_fit()`。

网络部分只用标准库 `urllib`，带描述性 `User-Agent`（`USER_AGENT`），超时
`NETWORK_TIMEOUT_S`；一切失败（DNS / 超时 / HTTP 状态 / JSON 坏 / 缺 hash）
都变成 `{"kind": "network_failed", ...}`。

技能档（为什么必须在接口层做）
----------------------------

`all4+min_required`（默认）由 L2 的 `engine.assumptions.derive_skill_tier()` 给
（全部已发布技能 4 级 + 逐模块补到 `max(4, required)`，实测 512 条）；
`all5` 是它的**单调提升**：每个技能取 `max(5, 该技能在 all4 档里的等级)`——
不重写那套违规反馈回路、不加一次引擎调用，也不会丢「某个技能最低要 5 级」的约束
（该技能在 all4 档里已经是 5，`max` 保持 5）。档名由
`assumptions["skill_tier_name"]` 回显（`all5` 档不叫 `all4+min_required`）。

**构造 `ViewContext` 时必须把技能注入 `fit["character"]["skills"]`**（PROGRESS §6
第 19 条 / §3 第 108 条）：`ViewContext` 只从 `ctx.fit` 建变体、**不读
`ctx.assumptions`**，漏注入就会按**零技能**算（EHP 7585.48 / 速度 1059.68），
看起来像一堆口径错误。见 `_context()`。

单例与缓存
----------

- `Engine` / `Index` 是**进程级单例**（`get_engine()` / `get_index()`）；`Index` 用
  **延迟初始化 + 线程锁**（首次调用才开库，失败不会缓存半成品）。索引打开后顺带
  `normalize.set_index()`，让「模块名写错」的失败行带上名称候选（红线 N2：
  **只报候选，绝不代填**，单候选也不代填）。
- 结果缓存：key = `(入口名, 入口附加参数, 输入内容哈希, skill_tier, damage_profile,
  state_policy)` 的 LRU（`CACHE_MAX_ENTRIES = 64`）。命中时**一次引擎调用都不发生**
  （`tests/test_api.py` 用计数桩钉住）。**只缓存成功结果**：失败不进缓存，否则修好
  网络 / 输错配装之后同一个调用还会一直拿到旧错误。
- 同一个 key 的 `fit_index` 与 `fit_view` 是两条缓存（入口名不同）：本层宁可多解析
  一次，也不做「跨入口共享中间对象」的优化——那会让一个入口的偶然就地修改污染另一个
  入口的结果。

已知边界（**不要试图在接口层补齐**）
----------------------------------

- `damage_profile` 目前只支持 `uniform`（25/25/25/25）。其它剖面需要在**投影层**算
  加权 EHP，接口层算它 = 二次换算（红线 N5），所以这里**报结构化错误**而不是静默按
  均匀剖面算（禁用默认值冒充）。同理 `state_policy` 只支持 `engine_default`
  （L2 只记录这一个来源）。
- `fit_get` 的「路径不存在」判据是 `get_path()` 返回里的 `hint` 键（见
  `project/summary.get_path`）：`value` 是 `None` 但**有** `provenance` 的路径是
  **合法答案**（如 `firepower.dph`），不能当 `invalid_path`。
- `fit_detail` 的 `meta.assumptions` 是**完整技能表**（512 条）：L0/L1 都压缩掉了它，
  「完整技能表」只在 detail 层给（§10.1「深度分析」）。
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import math
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from noobbaozi.config import paths as _paths
from noobbaozi.engine import adapter, normalize
from noobbaozi.engine.assumptions import default_assumptions
from noobbaozi.project import combat as _combat
from noobbaozi.project import explain as _explain
from noobbaozi.project import projection as _projection
from noobbaozi.project import reasons as _reasons
from noobbaozi.project import render
from noobbaozi.project import sensitivity as _sensitivity
from noobbaozi.project import summary as _summary
from noobbaozi.project.eft import EftError
from noobbaozi.project.eft import emit as eft_emit
from noobbaozi.project.views import ViewContext, available_views
from noobbaozi.project.views.base import SLOT_CARGO
from noobbaozi.sde.index import Index

# =============================================================================
# 一、常量（口径都在这里，别在函数里裸写）
# =============================================================================

SPEC_VERSION: str = "v1"
"""`meta.spec_version`：字段规格版本（与 `contracts.SPEC_VERSION` 同源，这里只透传）。"""

SKILL_TIER_ALL4: str = "all4+min_required"
"""默认技能档：全部已发布技能 4 级 + 逐模块补到 `max(4, required)`（§10.2）。"""

SKILL_TIER_ALL5: str = "all5"
"""第二个技能档：全部技能 5 级（+ 最低要求补丁）。"""

SKILL_TIERS: tuple[str, ...] = (SKILL_TIER_ALL4, SKILL_TIER_ALL5)
"""支持的技能档名（其它值 → `invalid_argument`，不静默退回默认档）。"""

SKILL_LEVEL_ALL5: int = 5
"""`all5` 档的等级（与 `engine.assumptions.DEFAULT_SKILL_LEVEL = 4` 对照着看）。"""

DAMAGE_PROFILE_UNIFORM: str = "uniform"
"""唯一支持的伤害剖面（25/25/25/25，见模块 docstring 的「已知边界」）。"""

DAMAGE_PROFILES: tuple[str, ...] = (DAMAGE_PROFILE_UNIFORM,)

STATE_POLICY_ENGINE_DEFAULT: str = "engine_default"
"""唯一支持的模块状态推定策略（L2 只记录这一个来源，§10.2）。"""

STATE_POLICIES: tuple[str, ...] = (STATE_POLICY_ENGINE_DEFAULT,)

# ---- 信封 `meta.glossary`：术语随返回体自带解释（T-52）----------------------
#
# 为什么要有它：**agent 永远不会读 `mcp/README.md` 和 `SKILL.md`**，它只看
# `tools/list` 的 description（≤600，塞不下）与返回的 JSON。实测过一个只接了 MCP 的
# agent：数字全读对了，但把 `assumed`（依赖假设）当成了「引擎实测」，也把
# `damage_profile=uniform` 当成了「这套配装的实际承伤剖面」。术语必须在**返回体里**解释。
#
# 两条硬约束（`tests/test_api.py` 的「信封术语表」一节盯着）：
# 1. **按需裁剪**：只解释本次返回里**真的出现**的术语（否则就是死条目）；
# 2. **枚举值必须写全**：每个术语的文案必须点出它全部合法取值 —— 这些取值从
#    `contracts.Provenance` / `reasons.UNKNOWN_REASONS` / explain 与 sensitivity 的
#    指标表**反查**，新增一个枚举值而没更新文案就会红。

PROVENANCE_STATES: tuple[str, ...] = ("measured", "assumed", "unknown")
"""三态取值（与 `contracts.Provenance` 同源；测试用 `typing.get_args()` 反查，不写第二份）。"""

COMBINE_VALUES: tuple[str, ...] = ("additive", "multiplicative", "single", "mixed")
"""`combine` 的全部取值（口径见 `project/explain.py` 模块 docstring 的 A / B / C 三类）。

- `additive`：A 可加类，`Σ sources[].contribution == value`；
- `multiplicative`：B 乘性 / C 投影类，给 `base + 有序因子链` 或公式两项，**因子不可相加**；
- `single`：单值指标（`sensitivity` 多出来的 5 个），既不是相加也不是相乘；
- `mixed`：**降级标记**（可加类里出现了非 `mod_add`/`mod_sub` 的项）—— 实测四个 A 类指标
  全是 `mod_add`，走不到这一支；真出现时它就是「别当可加」的意思。
"""

MINIMALITY_VALUES: tuple[str, ...] = ("filtered_noops", "not_minimal")
"""`minimality` 的两个取值（判据见 `project/explain.py::_minimality`）。"""

CONFIDENCE_OUTPUT_VALUES: tuple[str, ...] = ("A", "C", "E")
"""`confidence` 在**输出层**会出现的三档（口径见 `project/confidence.py` 的模块 docstring）。

`B`（需换算）/ `D`（需对拍）在输出层已不存在：换算是投影层做完的，而「半可信」没有
判据 —— 它们只留在 `confidence.CONFIDENCE_LEVELS` 的合法域里，不会被写进返回。
"""

GLOSSARY_ALWAYS: tuple[str, ...] = ("damage_profile", "state_policy")
"""**每个 `fit_*` 信封都带**的两个术语：它们由 `meta` 自己回显，必然出现在返回里。"""

GLOSSARY_ORDER: tuple[str, ...] = (
    "provenance",
    "confidence",
    "unknown_reasons",
    "combine",
    "minimality",
    "budget",
    "assumes",
    *GLOSSARY_ALWAYS,
)
"""术语的固定输出顺序（`GLOSSARY_TERMS` 的键集合必须与它**完全一致**）。"""

GLOSSARY_TERMS: dict[str, str] = {
    "provenance": (
        "measured=引擎直接给、换假设不变；assumed=依赖技能档/模块状态/伤害剖面，换假设会变；"
        "unknown=取不到（≠0，别用常识补数，看 unknown_reasons）"
    ),
    "confidence": (
        "口径可信度（与 provenance 正交）：A=已对拍可直接用；C=不可用（引擎不给值）；"
        "E=未建模（只给参数，结论自己建）；稀疏表，没写出的路径 = A"
    ),
    "unknown_reasons": (
        "unknown 的原因：input_incomplete=输入不足；not_applicable=本配装不适用（≠不知道）；"
        "engine_omits=引擎永不提供；ambiguous=无唯一答案；unclassified=缺规则（缺陷）"
    ),
    "combine": (
        "additive=各项相加（Σcontribution=value）；multiplicative=因子相乘（factor 不可相加）；"
        "single=单值不可组合；mixed=混入非可加项，别当可加"
    ),
    "minimality": (
        "filtered_noops=零贡献项已滤掉（滤掉几个见 folded_noops）；not_minimal=有截断或反向项"
    ),
    "budget": "chars=正文长度（不含 budget 块）；degraded=true 表示超出字符预算，只上报不裁剪",
    "assumes": (
        "某数值所依赖的前提：unlimited_charges=回复速率（repair_total_hp_s）按「充能无限」算；"
        "charge_limited=true 时那只是峰值，实际受充能/装填限制；"
        "cap_fed=靠电容维持，电容耗尽即中断"
    ),
    "damage_profile": "uniform=四系伤害均分 25/25/25/25；真实战况取决于对手",
    "state_policy": "engine_default=模块状态按引擎默认推定（不是实测状态）",
}
"""术语键 → 一句话解释（**只能解释、不能给结论**：本层不做判词，红线 N5）。

`zkb_*` 分支**不带** `glossary`：它的 `labels` / `npc` / `solo` / `efficiency` 已经在
工具 description 里写清，又不涉及本项目的 provenance / combine 口径。
"""

CACHE_MAX_ENTRIES: int = 64
"""结果缓存的 LRU 容量（§10.6 要求 LRU；64 条覆盖一次战报的常见规模）。"""

USER_AGENT: str = "noobbaozi/1.0 (L5 interface layer; +https://github.com/noobbaozi)"
"""联网请求的 `User-Agent`：**描述性**（谁在用、用来干什么），不是伪装浏览器。"""

NETWORK_TIMEOUT_S: float = 20.0
"""单次网络请求的超时（秒）。超时 → `network_failed`，不重试（重试属于调用方的策略）。"""

ZKILL_KILL_API: str = "https://zkillboard.com/api/killID/{killmail_id}/"
"""zkillboard：按 killmail id 取摘要（只为拿 `zkb.hash`，ESI 取原始 killmail 需要它）。"""

ESI_KILLMAIL_API: str = "https://esi.evetech.net/latest/killmails/{killmail_id}/{hash}/"
"""ESI killmail 原文（zkb 的附加字段引擎会忽略，实测可直传）。"""

KILLMAIL_ID_FIELD: str = "killmail_id"
KILLMAIL_HASH_FIELD: str = "hash"

ZKB_API_ROOT: str = "https://zkillboard.com/api"
"""zkillboard 的列表 API 根：`/api/<kills|losses>/<段名>/<值>/…/`（实测形态见第十三节）。"""

ZKB_USER_AGENT: str = (
    "noobbaozi/1.0 (L5 interface layer; zkb kill list only; +https://github.com/noobbaozi)"
)
"""zkb 请求的 `User-Agent`：**描述性**（zkb 按 UA 判断调用方，伪装浏览器没有好处）。"""

ZKB_PAGE_SIZE: int = 200
"""单页最多多少条（zkb 的硬上限，实测 200；要更多只能翻 `page`）。"""

ZKB_SIDE_KILLS: str = "kills"
ZKB_SIDE_LOSSES: str = "losses"
ZKB_SIDE_BOTH: str = "both"
"""`side` 的三个取值：`/api/kills/` 与 `/api/losses/` 是**两条独立路径**，both = 两次请求。"""

ZKB_SIDES: tuple[str, ...] = (ZKB_SIDE_KILLS, ZKB_SIDE_LOSSES, ZKB_SIDE_BOTH)

ZKB_MIN_INTERVAL_S: float = 1.2
"""两次出网请求之间的最小间隔（秒）：实测 zkb 要求 ≥1.2 s，否则会被限流。"""

ZKB_LIMIT_DEFAULT: int = 50
"""`limit` 的默认值（只截断 `kills` 数组，**不影响** `count` / `aggregates`）。"""

ZKB_RATE_LIMIT_STATUSES: tuple[int, ...] = (429, 403)
"""zkb 用这两个状态码表达「你太快了」→ `rate_limited`。"""

ZKB_ID_CACHE_MAX: int = 256
"""「名字 → id」解析缓存的 LRU 容量（只存**成功**解析）。"""

ZKB_SEARCH_NOTE: str = (
    "list 级数据来自 zkb（无 items）；要明细请对具体 killmail_id 调 fit_killmail"
)
"""成功返回里的固定说明（形状契约的一部分，调用方可以直接转述给用户）。"""

ESI_UNIVERSE_IDS_API: str = "https://esi.evetech.net/latest/universe/ids/"
"""ESI：名字数组 → id（`POST`，请求体是 `["Sleipnir", …]`）。"""

ZKB_FILTER_SEGMENTS: tuple[tuple[str, str], ...] = (
    ("character", "characterID"),
    ("corporation", "corporationID"),
    ("alliance", "allianceID"),
    ("ship", "shipTypeID"),
    ("solar_system", "solarSystemID"),
    ("region", "regionID"),
)
"""`zkb_search` 的过滤参数 → zkb 的 URL 段名，**顺序就是拼进 URL 的顺序**（AND 复合）。

顺序固定（不是随人的调用顺序）：身份 → 船型 → 空间 → 时间 → 分页。这样同一次检索
永远拼出同一个 URL —— 测试与日志才能逐字比对（`tests/test_api.py` 钉住这一点）。
"""

ESI_ID_CATEGORIES: dict[str, str] = {
    "character": "characters",
    "corporation": "corporations",
    "alliance": "alliances",
    "ship": "inventory_types",
    "solar_system": "systems",
    "region": "regions",
}
"""实体种类 → ESI `/universe/ids/` 响应里的分类键（**只认这一个对应关系**）。

实测：`["Sleipnir"]` 会同时命中 `characters`（真有个角色叫 Sleipnir）与
`inventory_types`（22444 船型）—— 所以解析必须**按分类取**，不能拿响应的第一个结果。
"""

ZKB_REJECTED_PARAMETERS: tuple[str, ...] = (
    "start_time",
    "end_time",
    "startTime",
    "endTime",
    "start",
    "end",
)
"""zkb 已停用的时间区间参数名（任一出现 → `invalid_argument`，**不发请求**）。"""

ZKB_IGNORED_PARAMETERS: tuple[str, ...] = ("skill_tier", "damage_profile", "state_policy")
"""`**common` 里允许出现但不影响结果的那三个配装参数（共用签名时会被带进来）。"""

INVALID_PATH_KIND: str = "invalid_path"
"""与 §10.4 对齐的五种 `error.kind` 之外，本层另外用到（**只增不改**）：

- `invalid_argument`：`skill_tier` / `damage_profile` / `state_policy` / `view` 取值不合法，
  以及 `zkb_search` 的时间区间参数 / 越界分页 / 一个过滤条件都不给；
- `unknown_entity`：`zkb_search` 的名字解析不出唯一答案（`candidates` 里给候选 + `kind`）；
- `rate_limited`：`zkb_search` 被 zkb 限流（429 / 403），间隔必须 ≥ `ZKB_MIN_INTERVAL_S`；
- `index_unavailable`：SDE 索引库缺失或打不开（**不退回旧技能档**，那会静默降级）；
- `internal_error`：兜底（任何未预料的异常都不许裸抛给 agent）。
"""

#: 输入形态（`meta.source_format` 会把 `normalize` 给的原始值透传：eft / dna / killmail …）。
INPUT_TEXT: str = "text"
INPUT_JSON: str = "json"
INPUT_NETWORK: str = "network"

_SHA256_DIGITS: int = 32
"""内容哈希保留的十六进制位数（128 bit，够区分，也不至于让 key 太长）。"""


# =============================================================================
# 二、信封：成功 / 失败（§10.3 / §10.4）
# =============================================================================


def _success(result: object, meta: dict) -> dict:
    """成功信封：`{"ok": true, "result": ..., "meta": {...}}`。"""
    return {"ok": True, "result": result, "meta": meta}


def _failure(payload: dict) -> dict:
    """失败信封：`{"ok": false, "error": {...}}`（**没有** `result` 键，形状恒定）。"""
    return {"ok": False, "error": payload}


class _ApiFailure(Exception):
    """内部可控失败：带一个「已经写好的人话 payload」，由 `_execute()` 变成失败信封。

    为什么不直接 `return _failure(...)`：解析 / 上下文构造 / 渲染三层都可能在中途
    失败，用异常**一次穿出**比每层都判 `returns-error` 干净，而且**只有这一种异常**
    会被当成「预期失败」，其它异常统一进 `internal_error`（保住「不吞异常」）。
    """

    def __init__(self, kind: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.extra = {key: value for key, value in extra.items() if value is not None}

    def payload(self) -> dict:
        """`error` 块（`kind` / `message` 恒在，其余按需，见 §10.4）。"""
        return {"kind": self.kind, "message": self.message, **self.extra}


# =============================================================================
# 三、进程级单例：Engine / Index（§10.6）
# =============================================================================

_ENGINE_LOCK = threading.Lock()
_ENGINE: adapter.Engine | None = None

_INDEX_LOCK = threading.Lock()
_INDEX: Index | None = None


def get_engine() -> adapter.Engine:
    """进程级 `Engine` 单例（构造只保证 SDE 已加载，重复构造不会二次加载）。"""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            _ENGINE = adapter.Engine()
    return _ENGINE


def get_index() -> Index:
    """进程级 `Index` 单例（**延迟初始化 + 线程锁**，见模块 docstring）。

    Raises:
        _ApiFailure: kind=`index_unavailable`（索引库没建 / 打不开）。**不**退回
            「只补最低要求」的旧技能档——那是静默降级（红线 N4）。
    """
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    with _INDEX_LOCK:
        if _INDEX is None:
            try:
                opened = Index.open(_paths.db_path())
            except (FileNotFoundError, OSError) as exc:
                raise _ApiFailure(
                    "index_unavailable",
                    f"SDE 索引库不可用：{exc}",
                    hint=(
                        "先建库：python -c \"from noobbaozi.sde.ingest import build_index; "
                        "build_index()\""
                    ),
                ) from exc
            # 候选增强：模块名写错时给名称候选（只报不填，红线 N2）。
            normalize.set_index(opened)
            _INDEX = opened
    return _INDEX


def _ensure_index() -> None:
    """确保索引单例就位（**只为副作用**）：技能档推导与「名称候选」都依赖它。"""
    get_index()


# =============================================================================
# 四、结果缓存（LRU，上限 CACHE_MAX_ENTRIES）
# =============================================================================

_CACHE_LOCK = threading.Lock()
_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CACHE_HITS: int = 0
_CACHE_MISSES: int = 0


def cache_stats() -> dict[str, int]:
    """缓存观测：`{entries, hits, misses, capacity}`（与 `Engine.cache_stats()` 同风格）。"""
    with _CACHE_LOCK:
        return {
            "entries": len(_CACHE),
            "hits": _CACHE_HITS,
            "misses": _CACHE_MISSES,
            "capacity": CACHE_MAX_ENTRIES,
        }


def clear_caches() -> None:
    """清空结果缓存与计数（**幂等**）。测试用来把「冷启动」造出来。

    顺带清空**星系名 / 实体名**的进程内永久缓存（`fit_killmail` / `zkb_search` 用）：那条
    缓存的失效条件只有「进程重启」或「显式清空」，所以必须挂在这里，否则测试之间会互相串味。
    限流闸门的「上一次请求时刻」也一起归零（否则换时钟的测试会等一个不该等的间隔）。
    """
    global _CACHE_HITS, _CACHE_MISSES, _UNIVERSE_NAMES_LAST_START
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_HITS = 0
        _CACHE_MISSES = 0
    with _UNIVERSE_LOCK:
        _UNIVERSE_NAMES.clear()
        _UNIVERSE_SYSTEMS.clear()
    with _UNIVERSE_NAMES_GATE_LOCK:
        _UNIVERSE_NAMES_LAST_START = None
    # `explain` 自带一层 LRU（key = fit 内容哈希 + metric + depth）。它**不在**上面这个
    # 缓存里，所以必须单独清 —— 否则「改完实现测试仍绿」的老坑（拿到上一条用例的旧信封）
    # 会在归因入口重演一遍。
    #
    # `sensitivity` **没有**自己的进程级 LRU（每个变体走 `engine.calculate`，命中引擎自己的
    # LRU；跨调用幂等靠上面的 api 结果缓存），所以这里**没有**第二层要清的对象。
    _explain.clear_cache()


def _cache_get(key: tuple) -> dict | None:
    """取缓存（命中时移动到最后 = LRU 的「最近用过」）并返回**深拷贝**。

    深拷贝是必需的：调用方（CLI / MCP / 测试）可能就地修改拿到的 dict，缓存里那份
    必须保持干净，否则「同一份输入两次调用结果相同」这条就不成立了。
    """
    global _CACHE_HITS
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is None:
            return None
        _CACHE.move_to_end(key)
        _CACHE_HITS += 1
        return copy.deepcopy(cached)


def _cache_put(key: tuple, envelope: dict) -> None:
    """存缓存（存深拷贝；超容量时丢最久未用的那条）。**只存成功结果**。"""
    global _CACHE_MISSES
    with _CACHE_LOCK:
        _CACHE_MISSES += 1
        _CACHE[key] = copy.deepcopy(envelope)
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_MAX_ENTRIES:
            _CACHE.popitem(last=False)


# =============================================================================
# 五、输入判别（四形态 → 内容哈希 / 网络取数）
# =============================================================================

_KILLMAIL_PATH_RE = re.compile(r"/killmails/(?P<id>\d+)/(?P<hash>[0-9a-fA-F]+)")
"""ESI 形态：`/killmails/<id>/<hash>/`（hash 是 40 位十六进制，这里只判形态）。"""

_ZKILL_PATH_RE = re.compile(r"/kill/(?P<id>\d+)")
"""zkillboard 形态：`/kill/<id>/`（**只带 id**，hash 得另外查一次）。"""


def _is_url(text: str) -> bool:
    """粗判（大小写不敏感）：`http(s)://…` 且不含空白。"""
    lowered = text.strip().lower()
    return lowered.startswith(("http://", "https://")) and not any(
        char.isspace() for char in text.strip()
    )


def killmail_ref_from_url(url: str) -> tuple[int, str | None] | None:
    """killmail URL → `(killmail_id, hash | None)`；不是 killmail URL 时返回 `None`。

    认得两种（**只认这两种**，别的 URL 一律当普通文本，不猜）：

    - `https://esi.evetech.net/latest/killmails/<id>/<hash>/` → `(id, hash)`；
    - `https://zkillboard.com/kill/<id>/` → `(id, None)` —— hash 需要查一次 zkill API。

    单独暴露成函数是为了**离线可测**（`tests/test_cli.py` 之外无需联网）。
    """
    if not isinstance(url, str) or not _is_url(url):
        return None
    matched = _KILLMAIL_PATH_RE.search(url)
    if matched is not None:
        return int(matched.group("id")), matched.group("hash").lower()
    matched = _ZKILL_PATH_RE.search(url)
    if matched is not None:
        return int(matched.group("id")), None
    return None


def _content_hash(payload: object) -> str | None:
    """内容哈希（`sha256` 前 32 位十六进制）；不可序列化时返回 `None`（该次不缓存）。"""
    try:
        if isinstance(payload, str):
            canonical = payload
        else:
            canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_SHA256_DIGITS]


def _is_network_ref(fit: object) -> bool:
    """`{"killmail_id": N, "hash": "..."}` 形态（**没有** `victim` 才是「要联网」）。

    完整 killmail（带 `victim`）即使同时带 `killmail_id` / `hash` 也是**本地内容**：
    调用方已经把数据给全了，再去联网就是多余的 I/O，还要求环境能上网。
    """
    if not isinstance(fit, dict):
        return False
    if "victim" in fit:
        return False
    return KILLMAIL_ID_FIELD in fit and KILLMAIL_HASH_FIELD in fit


def _network_key(fit: object) -> tuple[str, dict]:
    """网络输入 → `(缓存 key, 请求参数)`；形态不对时 `_ApiFailure`。"""
    if isinstance(fit, dict):
        killmail_id = fit.get(KILLMAIL_ID_FIELD)
        digest = fit.get(KILLMAIL_HASH_FIELD)
        source = f"id={killmail_id}"
    else:  # URL
        reference = killmail_ref_from_url(str(fit))
        if reference is None:
            raise _ApiFailure(
                "parse_failed",
                f"这个 URL 不是 killmail 链接：{fit!r}",
                hint="支持 /killmails/<id>/<hash>/（ESI）与 /kill/<id>/（zkillboard）",
            )
        killmail_id, digest = reference
        source = f"url={fit}"
    if not isinstance(killmail_id, int) or isinstance(killmail_id, bool):
        raise _ApiFailure(
            "parse_failed",
            f"{KILLMAIL_ID_FIELD} 必须是整数，收到 {killmail_id!r}",
            raw=str(killmail_id),
        )
    if digest is not None and not isinstance(digest, str):
        raise _ApiFailure(
            "parse_failed",
            f"{KILLMAIL_HASH_FIELD} 必须是字符串或 null，收到 {digest!r}",
            raw=str(digest),
        )
    key = f"network:{killmail_id}:{digest or ''}"
    params = {"killmail_id": killmail_id, "hash": digest, "source": source}
    return key, params


def _input_descriptor(fit: object) -> tuple[str, str | None, dict]:
    """输入 → `(形态, 缓存 key, 载荷)`。

    Returns:
        `(INPUT_TEXT, "text:<hash>", {"text": ...})` /
        `(INPUT_JSON, "json:<hash>", {"text": <规范化 JSON 文本>})` /
        `(INPUT_NETWORK, "network:<id>:<hash>", {"killmail_id", "hash"})`。

    Raises:
        _ApiFailure: 输入形态压根不是这四种之一（`parse_failed`）。
    """
    if _is_network_ref(fit) or (isinstance(fit, str) and _is_url(fit)):
        key, params = _network_key(fit)
        return INPUT_NETWORK, key, params

    if isinstance(fit, str):
        return INPUT_TEXT, f"text:{_content_hash(fit)}", {"text": fit}

    if isinstance(fit, (dict, list)):
        # 规范化文本交给 `normalize_fit()`（那才是配装语法的唯一解析处）。
        try:
            text = json.dumps(fit, ensure_ascii=False, sort_keys=False)
        except (TypeError, ValueError) as exc:
            raise _ApiFailure(
                "parse_failed",
                f"配装对象不是 JSON 可序列化的结构：{exc}",
                raw=repr(fit)[:200],
            ) from exc
        digest = _content_hash(text)  # 用规范化后的文本算，与「同一个对象」等价
        key = None if digest is None else f"json:{digest}"
        return INPUT_JSON, key, {"text": text}

    raise _ApiFailure(
        "parse_failed",
        f"fit 必须是 EFT / DNA 文本、killmail 对象或 killmail 链接，收到 {type(fit).__name__}",
        raw=repr(fit)[:200],
    )


# =============================================================================
# 六、联网（ESI killmail）：只用标准库 urllib，失败一律结构化
# =============================================================================


def _http_json(url: str, *, user_agent: str = USER_AGENT) -> object:
    """GET 一个 JSON 文档（标准库 `urllib`，描述性 UA + 超时）。

    Args:
        url: 完整 URL。
        user_agent: 覆盖 `User-Agent`（`zkb_search` 用 `ZKB_USER_AGENT`：它要说明
            「我们在拉列表，不是在伪装浏览器」）。

    Raises:
        _ApiFailure: kind=`network_failed`（DNS / 超时 / HTTP 状态 / 非 JSON / I/O）。
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "application/json"}
    )
    return _read_json(request, url)


def _read_json(request: urllib.request.Request, url: str) -> object:
    """发出一个已经构造好的请求并解析 JSON（GET / POST 共用这一条错误通道）。

    为什么单独拆出来：`_post_json()`（`POST /universe/names/`）与 `_http_json()` 的错误
    **必须是同一种**（`network_failed` + `url` / `status`），否则 agent 看到的失败形状取决
    于「这次是哪一步联网」。异常类型只在这里列一次。

    Raises:
        _ApiFailure: kind=`network_failed`。
    """
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_S) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise _ApiFailure(
            "network_failed",
            f"HTTP {exc.code}：{url}",
            url=url,
            status=int(exc.code),
        ) from exc
    except urllib.error.URLError as exc:
        raise _ApiFailure(
            "network_failed", f"连不上 {url}：{exc.reason}", url=url
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise _ApiFailure(
            "network_failed", f"请求 {url} 失败：{exc}", url=url
        ) from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # `payload_bytes` 是给上游的**机读**线索：0 字节 ≠ 「返回了坏 JSON」。
        # zkb 的 `/api/stats/.../` 在 URL 少了结尾 `/` 时正是 `200` + **0 字节空体**
        # （实测），调用方（`zkb_stats`）据此把它判成「上游异常」而不是「网络不通」。
        raise _ApiFailure(
            "network_failed",
            f"{url} 返回的不是 UTF-8 JSON：{exc}",
            url=url,
            payload_bytes=len(raw),
        ) from exc


def _hash_from_zkill(payload: object) -> str | None:
    """zkill 的 `killID` 响应 → `zkb.hash`（响应可能是列表，也可能是单条对象）。"""
    entries = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        zkb = entry.get("zkb")
        if isinstance(zkb, dict) and isinstance(zkb.get("hash"), str):
            return str(zkb["hash"]).lower()
    return None


def fetch_killmail(killmail_id: int, digest: str | None) -> dict:
    """取一条 killmail 原文（`digest is None` 时先向 zkillboard 查 hash）。

    Raises:
        _ApiFailure: kind=`network_failed`（任何一步失败；**不**用残缺数据继续算）。
    """
    if not digest:
        payload = _http_json(ZKILL_KILL_API.format(killmail_id=killmail_id))
        digest = _hash_from_zkill(payload)
        if not digest:
            raise _ApiFailure(
                "network_failed",
                f"zkillboard 没有给出 killmail {killmail_id} 的 hash，无法向 ESI 取原文",
                url=ZKILL_KILL_API.format(killmail_id=killmail_id),
            )
    killmail = _http_json(
        ESI_KILLMAIL_API.format(killmail_id=killmail_id, hash=digest)
    )
    if not isinstance(killmail, dict) or "victim" not in killmail:
        raise _ApiFailure(
            "network_failed",
            f"ESI 返回的 killmail {killmail_id} 里没有 victim 块",
            url=ESI_KILLMAIL_API.format(killmail_id=killmail_id, hash=digest),
        )
    return killmail


# =============================================================================
# 七、归一化失败 → §10.4 的 error 块
# =============================================================================

_SHIP_LINE = "ship_name"
_MODULE_LINE = "module_name"
_REASON_UNKNOWN = "unknown"

_CANDIDATE_LIMIT: int = 3
"""最多给几个名称候选（与 `engine.normalize.CANDIDATE_LIMIT` 同量级）。"""

_UNKNOWN_TYPE_PREFIX: str = "unknown type "
"""引擎「名字认不出」那条消息的固定前缀（实测 `unknown type Damage Controll II`）。

只在这里用来**取出用户写错的那个名字**去查候选；不参与任何解析判断。
"""

_CANDIDATE_SEARCH_WORDS: int = 4
"""模糊兜底时最多拿几个词去索引里捞候选池（长的词优先：区分度最高）。"""

_CANDIDATE_WORD_MIN_CHARS: int = 4
"""参与兜底搜索的词最短长度（`II` / `x5` 这种短词只会把池子冲稀）。"""

_CANDIDATE_POOL_LIMIT: int = 40
"""候选池上限（`difflib` 在 40 条里挑最像的 3 条，够用且够快）。"""

_CANDIDATE_CUTOFF: float = 0.6
"""`difflib` 的相似度门槛（低于它宁可不给候选，也不指一个无关的名字）。"""


def _unknown_type_from_message(raw: str) -> str | None:
    """`unknown type Damage Controll II` → `Damage Controll II`（不是这种消息给 `None`）。"""
    position = raw.lower().find(_UNKNOWN_TYPE_PREFIX)
    if position < 0:
        return None
    name = raw[position + len(_UNKNOWN_TYPE_PREFIX) :].strip()
    return name or None


def _candidate_names(needle: str, index: Index | None) -> list[str]:
    """用户写错的名字 → **最多 3 个**最接近的类型名（只报候选，绝不代填，红线 N2）。

    两段式，先精确后模糊：

    1. 先走索引本来那套「精确 → 前缀 → 包含」查询（`Index.search_types`）：
       名字只错在大小写 / 少空格时这一条就命中；
    2. 一条都没有（**拼错**，如 `Damage Controll II`）时，拿名字里最长的几个词去索引
       里捞一个候选池，再用 `difflib` 按相似度挑前 3 条。

    为什么要模糊兜底：`search_types` 对拼写错误**天然一条都不给**（`Controll` 既不是
    `Control` 的前缀，也不是它的子串），而拼错恰恰是最高频的失败原因；不给候选时
    agent 只能自己猜名字，那正是红线 N2 要避免的事。判据（相似度）与候选（真实类型名）
    都来自数据，**不生成任何名字**；低于 `_CANDIDATE_CUTOFF` 时宁可空手。
    """
    text = (needle or "").strip()
    if not text or index is None:
        return []

    direct = [row["name"] for row in index.search_types(text, limit=_CANDIDATE_LIMIT)]
    if direct:
        return direct[:_CANDIDATE_LIMIT]

    words = sorted(
        {word for word in text.split() if len(word) >= _CANDIDATE_WORD_MIN_CHARS},
        key=len,
        reverse=True,
    )[:_CANDIDATE_SEARCH_WORDS]
    pool: list[str] = []
    for word in words:
        for row in index.search_types(word, limit=_CANDIDATE_POOL_LIMIT):
            name = row.get("name")
            if isinstance(name, str) and name and name not in pool:
                pool.append(name)
    if not pool:
        return []
    return difflib.get_close_matches(text, pool, n=_CANDIDATE_LIMIT, cutoff=_CANDIDATE_CUTOFF)


def _candidates(names: list, index: Index | None) -> list[dict]:
    """候选名字 → `[{"type_id", "name"}]`（§10.4 的形状）。

    **只报候选、绝不代填**（红线 N2）：即使索引只命中一个，也只出现在这里，
    不会替换进 `fit`。查不到 `type_id` 时给 `None`（不编一个 id 出来）。
    """
    rows: list[dict] = []
    for name in names[:_CANDIDATE_LIMIT]:
        type_id: int | None = None
        if isinstance(name, str) and index is not None:
            found = index.search_types(name, limit=1)
            if found:
                type_id = int(found[0]["type_id"])
        rows.append({"type_id": type_id, "name": name})
    return rows


def _kind_of(failed_at: object, reason: object) -> str:
    """`NormalizeError.failed_at` / `reason` → §10.4 的 `error.kind`。

    - 船名行读不出唯一一艘船（未知或结构坏）→ `ambiguous_ship`；
    - 模块名「未知」→ `unknown_module`（名字写错，给候选）；
    - 模块行「形态坏」（如 `]x[` 这种连名字都提不出来）→ `parse_failed`（给行号）；
    - 其余（格式 / JSON / 结构）→ `parse_failed`。
    """
    if failed_at == _SHIP_LINE:
        return "ambiguous_ship"
    if failed_at == _MODULE_LINE and reason == _REASON_UNKNOWN:
        return "unknown_module"
    return "parse_failed"


def _failure_from_normalize(result: dict) -> _ApiFailure:
    """`engine.normalize.normalize_fit()` 的失败结果 → 失败信封要用的 `_ApiFailure`。

    一个错误都不丢：第一条决定 `kind` / `line` / `raw`，**多条时**整份 `errors`
    挂在 `errors` 上（agent 能一次看全，而不是只看到第一条就去改）。
    """
    errors = [err for err in (result.get("errors") or []) if isinstance(err, dict)]
    first = errors[0] if errors else {}
    failed_at = first.get("failed_at")
    reason = first.get("reason")
    line = first.get("line")
    raw = first.get("raw") or ""
    kind = _kind_of(failed_at, reason)

    where = f"第 {line} 行" if isinstance(line, int) else "输入"
    if kind == "ambiguous_ship":
        message = f"读不出唯一一艘船（{where}）：{raw or reason}"
    elif kind == "unknown_module":
        message = f"认不出这个模块（{where}）：{raw or reason}"
    else:
        message = f"配装解析失败（{where}）：{raw or reason}"

    extra: dict[str, object] = {
        "line": line if isinstance(line, int) else None,
        "raw": raw,
        "failed_at": failed_at,
        "source_format": result.get("source_format"),
    }
    names = list(first.get("candidates") or [])
    if not names and kind in ("unknown_module", "ambiguous_ship"):
        # `normalize` 的候选走的是索引的「精确/前缀/包含」查询，**拼错时一条都不给**；
        # 这里补一层模糊兜底（只给候选，不代填）。
        names = _candidate_names(_unknown_type_from_message(raw) or raw, _try_index())
    chosen = _candidates(names, _try_index())
    if chosen:
        extra["candidates"] = chosen
    if len(errors) > 1:
        extra["errors"] = errors
    return _ApiFailure(kind, message, **extra)


def _try_index() -> Index | None:
    """拿索引（失败给 `None`）：候选是**增强项**，不能让它把主错误顶掉。"""
    try:
        return get_index()
    except _ApiFailure:
        return None


# =============================================================================
# 八、参数校验（不静默退回默认值）
# =============================================================================


def _check_choice(name: str, value: object, allowed: tuple[str, ...]) -> None:
    """取值必须在白名单里，否则 `invalid_argument`（附可用值）。

    **不**把不认识的值悄悄换成默认值：`skill_tier="all5"` 写错成 `"all-5"` 时
    静默按 all4 算，等于给出一个数字却标错档位（红线 N4）。
    """
    if value in allowed:
        return
    raise _ApiFailure(
        "invalid_argument",
        f"{name}={value!r} 不支持；可用值：{list(allowed)}",
        field=name,
        hint=list(allowed),
    )


def _check_params(skill_tier: object, damage_profile: object, state_policy: object) -> None:
    """三个共同参数一起校验（§10.2）。"""
    _check_choice("skill_tier", skill_tier, SKILL_TIERS)
    _check_choice("damage_profile", damage_profile, DAMAGE_PROFILES)
    _check_choice("state_policy", state_policy, STATE_POLICIES)


# =============================================================================
# 九、解析 → ViewContext（技能注入是硬要求）
# =============================================================================


def _parsed_fit(payload: dict) -> dict:
    """载荷文本 → `normalize_fit()` 的成功结果（失败时抛 `_ApiFailure`）。"""
    result = normalize.normalize_fit(payload["text"])
    if result.get("fit") is None:
        raise _failure_from_normalize(result)
    return result


def _skill_tier_block(name: str, skills: dict) -> dict:
    """`skill_tier` 块 = `{name, count, hash}`（**实际档名 + 实际技能数 + 稳定摘要**）。

    `hash` 由 `summary.skill_tier_hash()` 算 —— 与 L0 的 `result.meta.skill_tier.hash`
    **同一个函数**，所以两处可以直接比对（§10.2 的「同源、可校验」）。

    为什么信封也要 `hash`：`count` 相同**不代表内容相同**（同为 512 条、但某个技能
    4 级 vs 5 级，count 一模一样）。agent 判断「两次分析是不是同一个技能档」只能靠
    这个摘要 —— 只给 `{name, count}` 时它无从判断，只能重跑一遍。
    """
    payload = skills if isinstance(skills, dict) else {}
    return {
        "name": name,
        "count": len(payload),
        "hash": _summary.skill_tier_hash(payload) if payload else None,
    }


def _tier_skills(base: dict, skill_tier: str) -> dict[int, int]:
    """按档名给出技能表。

    `all4+min_required` 原样用 L2 的推导结果；`all5` 是它的**单调提升**
    （每项取 `max(5, 原等级)`）——不重写违规反馈回路、不加引擎调用，
    也不会丢「某技能最低要 5 级」这一约束。
    """
    if skill_tier == SKILL_TIER_ALL5:
        return {
            int(type_id): max(SKILL_LEVEL_ALL5, int(level))
            for type_id, level in base.items()
        }
    return {int(type_id): int(level) for type_id, level in base.items()}


def _context(fit: dict, skill_tier: str) -> ViewContext:
    """配装 + 技能档 → `ViewContext`（**六个视角的唯一入参**）。

    **这里是最容易踩的坑**（PROGRESS §6 第 19 条）：`ViewContext` 只从 `ctx.fit`
    建变体、不读 `ctx.assumptions`，所以技能必须**注入 `fit["character"]["skills"]`**，
    否则整船按零技能算（EHP 7585.48 / 速度 1059.68）。

    只做三件事：推导假设（L2）→ 把技能注入 fit 副本（不改入参）→ 组上下文。
    **不重算任何数值、不改假设口径**（红线 N9 / N5）。
    """
    engine = get_engine()
    index = get_index()
    assumptions = dict(default_assumptions(fit, engine, index))
    skills = _tier_skills(assumptions.get("skill_tier") or {}, skill_tier)
    assumptions["skill_tier"] = skills
    assumptions["skill_tier_name"] = skill_tier

    character = dict(fit.get("character") or {})
    character["skills"] = dict(skills)
    enriched = {**fit, "character": character}
    return ViewContext(
        engine=engine,
        index=index,
        fit=enriched,
        assumptions=assumptions,
        meta=engine.meta(),
    )


# =============================================================================
# 十、信封组装 + 执行骨架（缓存 / 异常 → 结构化错误）
# =============================================================================


def _meta(ctx: ViewContext, *, source_format: str, damage_profile: str,
          state_policy: str, with_assumptions: bool = False,
          result: object = None) -> dict:
    """`meta` 块：版本三元组 + **实际**技能档 + 两个回显参数 + 输入形态 + 术语表。

    `with_assumptions=True` 时追加完整假设（**完整技能表只在这里**，§10.1 detail）。
    `result` 只用来**裁剪** `meta.glossary`（本次返回里出现了哪些术语），不参与任何计算。
    """
    engine_meta = ctx.meta if isinstance(ctx.meta, dict) else {}
    assumptions = ctx.assumptions if isinstance(ctx.assumptions, dict) else {}
    meta = {
        "spec_version": engine_meta.get("spec_version", SPEC_VERSION),
        "engine_version": engine_meta.get("engine_version"),
        "sde_build": engine_meta.get("sde_build"),
        "skill_tier": _skill_tier_block(
            str(assumptions.get("skill_tier_name")),
            assumptions.get("skill_tier") or {},
        ),
        "damage_profile": damage_profile,
        "state_policy": state_policy,
        "source_format": source_format,
    }
    if with_assumptions:
        meta["assumptions"] = assumptions
    meta["glossary"] = _glossary(
        result,
        damage_profile=damage_profile,
        state_policy=state_policy,
    )
    return meta


def _term_keys(node: object) -> set[str]:
    """递归找出「本次返回里真的出现了术语载体」的术语键（`glossary` 的裁剪依据）。

    **判据是结构，不是字符串搜索**：`reason` 只在其值属于 `reasons.UNKNOWN_REASONS`
    时才算「unknown 的原因」—— `fit_engagement` 的单方向结果里也有一个 `reason`
    （`no_damage` / `out_of_missile_range` 这类自由文本），那是**交战事实**，不是三态原因，
    按名字命中会给出一个牛头不对马嘴的解释。
    """
    found: set[str] = set()
    stack: list[object] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key == "provenance_counts":
                    found.add("provenance")
                elif key == "provenance":
                    if isinstance(value, str) and value in PROVENANCE_STATES:
                        found.add("provenance")
                    elif isinstance(value, dict) and any(
                        state in PROVENANCE_STATES for state in value.values()
                    ):
                        found.add("provenance")
                elif key == "confidence":
                    found.add("confidence")
                elif key == "unknown_reasons":
                    found.add("unknown_reasons")
                elif (
                    key == "reason"
                    and isinstance(value, str)
                    and value in _reasons.UNKNOWN_REASONS
                ):
                    found.add("unknown_reasons")
                elif key == "combine" and isinstance(value, str) and value in COMBINE_VALUES:
                    found.add("combine")
                elif key in ("minimality", "folded_noops"):
                    found.add("minimality")
                elif key in ("assumes", "unlimited_charges"):
                    found.add("assumes")
                elif key in ("charge_limited", "repair_total_hp_s"):
                    # `survivability.repair.assumes` 的**同义载体**：L0 只给
                    # `repair_total_hp_s`（那个 269.5），前提不在同一层，但读它的人
                    # 必须在同一份返回里看到前提 —— 这正是本单要修的那个实证案例。
                    found.add("assumes")
                elif key == "budget" and isinstance(value, dict) and "degraded" in value:
                    found.add("budget")
                stack.append(value)
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return found


def _glossary(result: object, *, damage_profile: str, state_policy: str) -> dict[str, str]:
    """本次返回要带的术语表：**只解释真的出现了的术语**（没有死条目）。

    两个术语不可裁剪：`damage_profile` / `state_policy` 由 `meta` 自己回显，必然出现。
    """
    present = _term_keys(result)
    present.update(GLOSSARY_ALWAYS)
    if damage_profile not in DAMAGE_PROFILES:  # pragma: no cover - 参数已先校验
        present.discard("damage_profile")
    if state_policy not in STATE_POLICIES:  # pragma: no cover - 参数已先校验
        present.discard("state_policy")
    return {name: GLOSSARY_TERMS[name] for name in GLOSSARY_ORDER if name in present}


def _execute(
    op: str,
    fit: object,
    args: tuple,
    *,
    skill_tier: object,
    damage_profile: object,
    state_policy: object,
    compute: Callable[[ViewContext, dict], tuple[object, bool]],
) -> dict:
    """前五个入口共用的执行骨架：校验 → 缓存 → 解析 → 上下文 → 计算 → 信封。

    `fit_killmail` 走 `_execute_killmail()`（骨架同构，载荷是**原始 killmail**）。

    Args:
        op: 入口名（进缓存 key；不要把两个入口的 key 混在一起）。
        fit: 四形态输入（见模块 docstring）。
        args: 入口自己的附加参数（视角名 / 路径 / 字段列表），一起进缓存 key。
        compute: `(ctx, parsed) -> (result, with_assumptions)`：**只组装**的业务部分；
            `with_assumptions` 为真时 `meta` 带完整假设（detail 层要完整技能表）。

    Returns:
        成功或失败信封（**任何 Oracle 异常都不会穿出去**，§10.4）。

    缓存口径：key 含「输入内容哈希 + 三个参数 + 入口附加参数」，**只存成功**；
    第二条相同调用一次引擎都不会碰（命中即深拷贝返回）。
    """
    try:
        _check_params(skill_tier, damage_profile, state_policy)

        kind, content_key, payload = _input_descriptor(fit)
        cache_key = (
            None
            if content_key is None
            else (op, args, content_key, skill_tier, damage_profile, state_policy)
        )
        if cache_key is not None:
            hit = _cache_get(cache_key)
            if hit is not None:
                return hit

        if kind == INPUT_NETWORK:
            killmail = fetch_killmail(payload["killmail_id"], payload["hash"])
            payload = {"text": json.dumps(killmail, ensure_ascii=False)}

        # 索引必须**在归一化之前**就位：它既是技能档推导的前提，也是
        # `normalize.set_index()` 的注入点（模块名写错时要给名称候选）。
        # 放在归一化之后会得到「模块名未知但没有任何候选」——那等于让 agent 猜。
        _ensure_index()

        parsed = _parsed_fit(payload)
        ctx = _context(parsed["fit"], str(skill_tier))

        result, with_assumptions = compute(ctx, parsed)
        envelope = _success(
            result,
            _meta(
                ctx,
                source_format=str(parsed.get("source_format")),
                damage_profile=str(damage_profile),
                state_policy=str(state_policy),
                with_assumptions=with_assumptions,
                result=result,
            ),
        )
    except _ApiFailure as exc:
        return _failure(exc.payload())
    except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得裸抛给 agent
        return _failure(
            {
                "kind": "internal_error",
                "message": "接口层内部异常，本次没有可用结果",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )

    if cache_key is not None:
        _cache_put(cache_key, envelope)
    return envelope


# =============================================================================
# 十一、五个入口（§10.1）—— 第六个 `fit_killmail` 在第十二节
# =============================================================================


LOCALE_EN: str = "en"
"""英文（默认）：名字就是 SDE 的 `types.name`，即 EFT / ESI 口径。"""
LOCALE_ZH: str = "zh"
"""官方中文：名字取 SDE 的 `types.name_zh`（实测 53,288/53,288 全填）。"""
LOCALES: tuple[str, ...] = (LOCALE_EN, LOCALE_ZH)
"""`locale` 的合法取值（**只这两个**；别把 `zh-CN` / `zh_CN` 悄悄当 `zh` —— 不做宽松解析）。"""


def _check_locale(value: object) -> str:
    """校验 `locale`，返回规范值。

    Raises:
        _ApiFailure: `invalid_argument` + `field="locale"` + 合法取值列表。
    """
    if value is None:
        return LOCALE_EN
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in LOCALES:
            return normalized
    raise _ApiFailure(
        "invalid_argument",
        f"locale 只支持 {' / '.join(LOCALES)}，收到 {value!r}",
        field="locale",
        hint={"allowed": list(LOCALES), "default": LOCALE_EN},
    )


def _localized_type_name(row: object, locale: str) -> tuple[str | None, str | None]:
    """TypeRow → `(name, name_en)`。

    - `locale="en"`：`(英文名, None)` —— **不追加 `name_en`**，默认返回体形状与以前逐字节相同；
    - `locale="zh"`：`(中文名, 英文名)`，中文缺失时**回退英文**（契约允许 `name_zh=None`）。

    玩家 / 军团 / 联盟名**不走这里**：那是人名，不是 SDE 类型，永远不译。
    """
    if not isinstance(row, dict):
        return None, None
    english = row.get("name")
    if locale != LOCALE_ZH:
        return english, None
    chinese = row.get("name_zh")
    if isinstance(chinese, str) and chinese.strip():
        return chinese, english
    return english, english


def _ship_block(ctx: ViewContext, *, locale: str = LOCALE_EN) -> dict:
    """`{type_id, name, group}`（名字 / 组名走 SDE 索引；查不到给 `None`，不编）。

    `locale="zh"` 时多一个 `name_en`（英文原名，便于核对），`name` 是官方中文。
    """
    fit = ctx.fit if isinstance(ctx.fit, dict) else {}
    ship = fit.get("ship") if isinstance(fit.get("ship"), dict) else {}
    type_id = ship.get("type_id")
    row = None
    if isinstance(type_id, int) and ctx.index is not None:
        row = ctx.index.get_type(type_id)
    name, name_en = _localized_type_name(row, locale)
    block = {
        "type_id": type_id,
        "name": name,
        "group": (row or {}).get("group_name"),
    }
    if locale == LOCALE_ZH:
        block["name_en"] = name_en
    return block


def _module_counts(fit: dict) -> dict:
    """`{fitted, cargo, by_slot}`：只数「已装 / 货舱」与逐槽位件数（**只数不改数**）。

    「已装」的判据与投影层同一个常量（`views.base.SLOT_CARGO`）：`slot.type` 不是
    `cargo` 的条目。这不是配装语义判断，只是让人确认「我读到了几件东西」。
    """
    by_slot: dict[str, int] = {}
    fitted = 0
    cargo = 0
    for item in fit.get("items") or []:
        if not isinstance(item, dict):
            continue
        slot = item.get("slot") if isinstance(item.get("slot"), dict) else {}
        slot_type = slot.get("type")
        if slot_type == SLOT_CARGO:
            cargo += 1
            continue
        fitted += 1
        if isinstance(slot_type, str):
            by_slot[slot_type] = by_slot.get(slot_type, 0) + 1
    return {"fitted": fitted, "cargo": cargo, "by_slot": by_slot}


def parse_fit(
    fit: object,
    *,
    skill_tier: str = SKILL_TIER_ALL4,
    damage_profile: str = DAMAGE_PROFILE_UNIFORM,
    state_policy: str = STATE_POLICY_ENGINE_DEFAULT,
    locale: object = LOCALE_EN,
) -> dict:
    """把输入（EFT / DNA / killmail）归一化成结构，并回显「我读懂了什么」。

    Args:
        fit: 四形态输入，见模块 docstring（自动判别，**不猜**）。
        skill_tier: `all4+min_required`（默认）/ `all5`。
        damage_profile: 只支持 `uniform`（见模块 docstring「已知边界」）。
        state_policy: 只支持 `engine_default`。
        locale: `en`（默认）/ `zh`。`zh` 时 `ship.name` 给**官方中文**并多一个
            `name_en`（英文原名，便于核对）；`en` 时返回体与以前**逐字节相同**。
            **玩家 / 军团 / 联盟名永远不译**（那是人名，不是 SDE 类型）。
            非 `en`/`zh` → `invalid_argument` + `field="locale"`。

    Returns:
        成功时 `result` 是：

        ```json
        {"source_format": "eft",
         "fit": {...},                      // 引擎 fit 结构（原样，不加工）
         "eft": "[Osprey Navy Issue, Solo]\n\nDamage Control II\n...",
         "warnings": ["module states are inferred by import rules"],
         "ship": {"type_id": 35683, "name": "Hecate", "group": "Tactical Destroyer"},
         "modules": {"fitted": 17, "cargo": 12, "by_slot": {"high": 6, ...}},
         "skills": {"name": "all4+min_required", "count": 512}}
        ```

        `fit` 是**引擎结构原样**：本层不加字段、不改值（下游 `fit_index` 等读的就是它）。
        `eft`（T-54）是**可直接复制的规范 EFT 文本**（渲染规则见
        `project/eft.py` 的模块 docstring：低 → 中 → 高 → 改装 → 子系统、组间空行、
        同型模块每件一行、无人机 / 货舱带 `xN`、`\n` 换行、末尾无空行），且满足
        `parse(emit(fit))` 的已装模块集合（`type_id` + 数量）与原始一致。
        **渲染失败时是 `null`**（名字查不到 / 槽位类型没有 EFT 表示），原因写进
        `warnings`——**不编造任何一行**：要 EFT 文本就只读这个字段，不要自己拼。

        `fit` 是**引擎结构原样**：本层不加字段、不改值（下游 `fit_index` 等读的就是它）。
        注意它是**注入技能之前**的结构；本次真正用来计算的技能档在 `result.skills`
        （`{name, count}`），逐条技能表在 `fit_detail().meta.assumptions.skill_tier`
        （512 条，只在 detail 层给，§10.1）。
        `warnings` 一条不删（「舱内模块未计入」这类事实必须转达，见
        `engine/normalize.py` 模块 docstring 第 4 条）。
    """

    try:
        resolved_locale = _check_locale(locale)
    except _ApiFailure as exc:
        return _failure(exc.payload())

    def compute(ctx: ViewContext, parsed: dict) -> tuple[object, bool]:
        assumptions = ctx.assumptions if isinstance(ctx.assumptions, dict) else {}
        warnings = list(parsed.get("warnings") or [])
        eft_text: str | None
        try:
            # T-54：规范文本由**我们**给（此前只有解析、没有渲染，agent 只能自己拼，
            # 于是模块名 / 件数 / 槽位全靠记忆 → 实测拼出「5 门 RLML」这种不存在的配装）。
            eft_text = eft_emit(parsed["fit"], ctx.index)
        except EftError as exc:
            # **不猜、不半成品**：渲染失败就诚实给 null，并说明为什么（不编行）。
            eft_text = None
            warnings.append(f"EFT 渲染失败（eft 为 null）：{exc}")
        result = {
            "source_format": parsed.get("source_format"),
            "fit": parsed["fit"],
            "eft": eft_text,
            "warnings": warnings,
            "ship": _ship_block(ctx, locale=resolved_locale),
            "modules": _module_counts(parsed["fit"]),
            "skills": _skill_tier_block(
                str(assumptions.get("skill_tier_name")),
                assumptions.get("skill_tier") or {},
            ),
        }
        return result, False

    return _execute(
        "parse",
        fit,
        (resolved_locale,),
        skill_tier=skill_tier,
        damage_profile=damage_profile,
        state_policy=state_policy,
        compute=compute,
    )


def fit_index(
    fit: object,
    *,
    skill_tier: str = SKILL_TIER_ALL4,
    damage_profile: str = DAMAGE_PROFILE_UNIFORM,
    state_policy: str = STATE_POLICY_ENGINE_DEFAULT,
) -> dict:
    """六维度摘要（**L0**，默认入口，实测约 1.8–2.2k 字符）。

    `result` 就是 `project.render.render_index(ctx)` 的原样输出（字段清单见文档 §3）：
    `ship` / `meta` / 六视角各 3–5 个数 / `flags[].{id, hint}` /
    `provenance_counts` / `budget{chars, degraded}`。

    **本层一个字都不改**（不裁剪、不取整、不补字段）：L0 的预算与降级都由渲染层决定。
    """

    def compute(ctx: ViewContext, _parsed: dict) -> tuple[object, bool]:
        return render.render_index(ctx), False

    return _execute(
        "index",
        fit,
        (),
        skill_tier=skill_tier,
        damage_profile=damage_profile,
        state_policy=state_policy,
        compute=compute,
    )


def fit_view(
    fit: object,
    view: str,
    *,
    skill_tier: str = SKILL_TIER_ALL4,
    damage_profile: str = DAMAGE_PROFILE_UNIFORM,
    state_policy: str = STATE_POLICY_ENGINE_DEFAULT,
) -> dict:
    """单视角全字段（**L1**）：`{overview, provenance, unknown_reasons, confidence, flags}`。

    Args:
        fit: 四形态输入。
        view: 视角名（`firepower` / `survivability` / `mobility` / `resources` /
            `constraints` / `stability`；取值不合法 → `invalid_argument` + `hint`）。

    Returns:
        `result` 是 `project.render.render_view(ctx, view)` 的原样输出（含 `evidence`，
        不做预算裁剪——降到 L1 就是为了看全）。
    """
    views = tuple(available_views())

    def compute(ctx: ViewContext, _parsed: dict) -> tuple[object, bool]:
        return render.render_view(ctx, view), False

    if view not in views:
        return _failure(
            _ApiFailure(
                "invalid_argument",
                f"view={view!r} 未注册；可用视角：{list(views)}",
                field="view",
                hint=list(views),
            ).payload()
        )
    return _execute(
        "view",
        fit,
        (str(view),),
        skill_tier=skill_tier,
        damage_profile=damage_profile,
        state_policy=state_policy,
        compute=compute,
    )


def fit_get(
    fit: object,
    path: str,
    *,
    skill_tier: str = SKILL_TIER_ALL4,
    damage_profile: str = DAMAGE_PROFILE_UNIFORM,
    state_policy: str = STATE_POLICY_ENGINE_DEFAULT,
) -> dict:
    """按路径取一个值 + 它的 `provenance` / `reason`（**极小**，用于精确取数与二次加工）。

    Args:
        fit: 四形态输入。
        path: L0/L1 同一套路径表达式（`mobility.speed_mps` / `firepower.weapons[].name` /
            `meta.assumptions.skill_tier` / `flags[0].id` / `budget.chars`）。

    Returns:
        成功时 `result` = `{path, value, provenance, source_view, reason?}`：

        - `value`：取到的值；**`None` 是合法答案**（如 `firepower.dph` 无导弹时），
          这时 `provenance` 一般为 `unknown` 且可能带 `reason`；
        - `reason`：仅当三态是 `unknown` 时出现（`input_incomplete` /
          `not_applicable` / `ambiguous` / `engine_omits` / …）；
        - 路径真的不存在 → `{"ok": false, "error": {"kind": "invalid_path",
          "hint": [...可用前缀...]}}`（**不用 `value: null` 冒充**）。
    """
    text = str(path or "").strip()
    if not text:
        return _failure(
            _ApiFailure(
                INVALID_PATH_KIND,
                "path 不能为空",
                hint=_path_hint(),
            ).payload()
        )

    def compute(ctx: ViewContext, _parsed: dict) -> tuple[object, bool]:
        answer = render.get_path(ctx, text)
        if "hint" in answer:  # `get_path` 用 `hint` 表示「路径不存在」（见模块 docstring）
            hint = str(answer["hint"])
            # `get_path` 的 hint 以「路径 '…' 不存在；」开头，与本条的 message 重复 →
            # 只留后面那半句（可用前缀 / 该视角的 L0 字段名）。
            if hint.startswith("路径 ") and "；" in hint:
                hint = hint.split("；", 1)[1]
            raise _ApiFailure(
                INVALID_PATH_KIND,
                f"路径 {text!r} 不存在",
                path=text,
                hint=hint,
            )
        result = {
            "path": text,
            "value": answer.get("value"),
            "provenance": answer.get("provenance"),
            "source_view": answer.get("source_view"),
        }
        if "reason" in answer:
            result["reason"] = answer["reason"]
        return result, False

    return _execute(
        "get",
        fit,
        (text,),
        skill_tier=skill_tier,
        damage_profile=damage_profile,
        state_policy=state_policy,
        compute=compute,
    )


def _path_hint() -> list[str]:
    """`invalid_path` 的可用前缀列表（顶层块 + 六视角名）。"""
    return ["ship", "meta", "flags", "provenance_counts", "budget", *available_views()]


def _orphan_flags(ctx: ViewContext) -> list[dict]:
    """「**不属于任何已注册视角**」的诊断 flag → `fit_detail` 的顶层 `flags` 兜底桶。

    归位判据完全复用渲染层（`render._collect_flags(ctx, 全部视角)`）：`flag["view"]`
    是已注册视角 → 归那个视角；**其余**（视角名不存在 / 压根没有 `view`）→ residual。

    为什么必须有这个桶：`render_detail()` 只构建**被请求的**视角，它的 residual 是在
    渲染层内部丢掉的。结果是「同一个诊断在 L1 的视角条目里看得见、到 L2 深挖时**彻底
    消失**」—— 那正是「静默丢诊断」。（P2 红线：渲染层绝不丢弃 flag。）

    为什么归位目标用**全部视角**而不是本次 `targets`：若只给 `view="mobility"`，用 targets
    归位会把 firepower 等视角的 flag 也当成「不属于任何视角」塞进顶层桶 —— 那会撒谎。
    顶层桶的语义是**脱离视角的孤儿**，与本次查哪个视角无关（形状因此也恒定）。

    成本：多跑一次 `diagnostics.evaluate()`（渲染层每个视角各跑一次，这里是 +1）。
    实测 f1（Hecate）单次 < 5 ms，可接受；换来的是「一条诊断都不丢」。
    """
    _attributed, residual = render._collect_flags(ctx, tuple(available_views()))
    return [flag for flag in residual if isinstance(flag, dict)]


def _detail_targets(view: object, path: object) -> tuple[tuple[str, ...], list[str] | None]:
    """`fit_detail` 的 (视角列表, 字段列表)；入参不合法时抛 `_ApiFailure`。

    口径（形状恒定，调用方不必判类型）：

    - 都不给 → **六个视角全给**（每个视角一条完整明细）；
    - 只给 `view` → 该视角的完整明细；
    - 只给 `path` → 首段必须是**视角名**（`"stability.capacitor"`），否则
      `invalid_path`（顶层块走 `fit_get`，那不是「列表型明细」）；
    - 两个都给 → `path` 的首段必须等于 `view`（否则 `invalid_argument`）。
    """
    views = tuple(available_views())
    if path is None or str(path).strip() == "":
        if view is None:
            return views, None
        if view not in views:
            raise _ApiFailure(
                "invalid_argument",
                f"view={view!r} 未注册；可用视角：{list(views)}",
                field="view",
                hint=list(views),
            )
        return (str(view),), None

    text = str(path).strip()
    head, _dot, rest = text.partition(".")
    if head not in views:
        raise _ApiFailure(
            INVALID_PATH_KIND,
            f"path={text!r} 的首段 {head!r} 不是视角名（明细只能按视角取）",
            path=text,
            hint=_path_hint(),
        )
    if view is not None and str(view) != head:
        raise _ApiFailure(
            "invalid_argument",
            f"view={view!r} 与 path 的首段 {head!r} 不一致",
            field="view",
            hint=[head],
        )
    fields = [rest] if rest else None
    return (head,), fields


def fit_detail(
    fit: object,
    view: str | None = None,
    path: str | None = None,
    *,
    skill_tier: str = SKILL_TIER_ALL4,
    damage_profile: str = DAMAGE_PROFILE_UNIFORM,
    state_policy: str = STATE_POLICY_ENGINE_DEFAULT,
) -> dict:
    """列表型明细（升级候选、逐模块电容、**完整技能表**…）：`render_detail` 的组装。

    Args:
        fit: 四形态输入。
        view: 视角名；`None` 表示「六个视角全给」。
        path: `"<视角>.<字段路径>"`（如 `stability.capacitor` / `resources.upgrade_space`）：
            只给这些字段（找不到的进该视角的 `missing`，**不静默忽略**）。

    Returns:
        `result = {"ship", "flags", "views": {"<视角>": {...}}}`，形状恒定（`views` 恒为
        1–6 条，`flags` 恒为列表）：

        - **`meta` 不在 `result` 里**，它在**信封**上（`envelope["meta"]`）；detail 的信封
          `meta` 比其它入口多一个 **`assumptions`**（含 512 条完整技能表）——
          这是「完整技能表只在 detail 层给」（§10.1）的落点。`result` 自己**没有 `meta`**；
        - **顶层 `flags`**：装「**不属于任何已注册视角**」的诊断 flag（`flag["view"]` 是
          不存在的名字，或压根没有 `view`）。形状 = 完整 flag 对象（`id` / `view` /
          `message` / `evidence`），与 L1 视角条目里的 `flags` 同形。**没有就是 `[]`**。
          为什么要它：这些 flag 归不进任何视角，若不单独兜底，它们会在 L2 **彻底消失**
          —— 同一个诊断 L1 看得见、深挖时反而没了，那就是「静默丢诊断」，踩红线；
        - 每个视角条目 = `{overview, provenance, unknown_reasons, confidence, flags,
          missing}`，`overview` 是**未降级**的完整列表（`resources.upgrade_space`
          的全部候选在这里，不在 L0）。
    """
    try:
        targets, fields = _detail_targets(view, path)
    except _ApiFailure as exc:
        return _failure(exc.payload())

    def compute(ctx: ViewContext, _parsed: dict) -> tuple[object, bool]:
        entries: dict[str, dict] = {}
        for name in targets:
            entry = render.render_detail(ctx, view=name, fields=fields)
            entries[name] = {
                "overview": entry.get("overview"),
                "provenance": entry.get("provenance"),
                "unknown_reasons": entry.get("unknown_reasons"),
                "confidence": entry.get("confidence"),
                "flags": entry.get("flags"),
                "missing": entry.get("missing"),
            }
        return {
            "ship": _ship_block(ctx),
            "flags": _orphan_flags(ctx),
            "views": entries,
        }, True

    return _execute(
        "detail",
        fit,
        (targets, tuple(fields) if fields else None),
        skill_tier=skill_tier,
        damage_profile=damage_profile,
        state_policy=state_policy,
        compute=compute,
    )


# =============================================================================
# 十一之二、归因（P3 `explain`）：`fit_explain`
# =============================================================================
#
# 为什么参数校验放在 `_execute` 外面
# ----------------------------------
# `metric` / `depth` 的合法性**与配装无关**（登记表是模块级常量、depth 是 1..2），
# 先在入口判掉有三个好处：① 省一次归一化 + 一次引擎 `sources=True` 计算（实测 2.95 ms）；
# ② `invalid_path` 的 `hint` 必须是**当前**的 `supported_metrics()`，早判不会被缓存命中
# 绕过去；③ 与 `fit_get` 的「空 path 先判」保持同一条纪律。
#
# 为什么结果要 `deepcopy`
# -----------------------
# `explain.explain()` 返回的是**它自己 LRU 里的那个对象**（同一次调用第二次直接命中）。
# 本层的 `_cache_put` 会深拷贝，但**首次**返回的信封仍持有同一个引用 —— 调用方一旦
# 就地改一个字段，`explain` 的缓存就被污染，"同一指标两次调用结果相同" 立刻失效。
# 5–6 KB 的拷贝换掉这个隐患（`fit_detail` 的 10 KB 都不在乎这一次拷贝）。

EXPLAIN_MAX_DEPTH: int = _explain.MAX_DEPTH
"""`fit_explain` 的 `depth` 上限（= `explain.MAX_DEPTH`，目前 2：不解释到单条 modifier 之外）。"""

EXPLAIN_METRICS: tuple[str, ...] = _explain.supported_metrics()
"""可归因的指标路径（import 时快照；顺序 A 可加 → B 乘性 → C 投影）。"""


def fit_explain(
    fit: object,
    metric: str,
    *,
    depth: int = EXPLAIN_MAX_DEPTH,
    skill_tier: str = SKILL_TIER_ALL4,
    damage_profile: str = DAMAGE_PROFILE_UNIFORM,
    state_policy: str = STATE_POLICY_ENGINE_DEFAULT,
) -> dict:
    """某个指标**为什么**是这个数（`project/explain.py` 的组装层，**不改形状**）。

    Args:
        fit: 四形态输入。
        metric: 可归因的指标路径，见 `EXPLAIN_METRICS`：
            A 可加 `survivability.ehp_mixed` / `firepower.dps.base` /
            `resources.cpu.load` / `resources.powergrid.load`；
            B 乘性 `mobility.speed_mps` / `stability.capacitor.peak_recharge`；
            C 投影 `survivability.thinnest.{shield,armor,hull}.ehp`。
        depth: `1` 或 `2`（默认 2）。`1` = 只看直接贡献者；`2` = 再展开每个贡献者自己的源。

    Returns:
        成功时 `result` **就是** `explain.explain(ctx, metric, depth=depth)` 的返回，
        本层不加字段、不裁剪、不取整（字段清单见 `project/explain.py` 模块 docstring）：

        - 公共：`metric` / `view` / `value` / `unit` / `combine` / `includes_base` / `base` /
          `sources[]` / `folded_noops` / `unexplained` / `truncated_at_depth` /
          `truncated_nodes` / `minimality` / `has_offsets` / `tolerance` / `provenance`；
        - A 可加（`combine == "additive"`）**另有** `sum_of_contributions` / `additive_only`，
          且 **`Σ sources[].contribution == value`**（`unexplained` 是真 0）；
        - B 乘性 / C 投影（`combine == "multiplicative"`）**另有** `replayed` / `replay_ok`；
          C 类还带 `formula` / `damage_type` / `tied`。B 类的 `factor` **不是贡献量**，
          不能相加；C 类的两条 source 是「层 HP」与「该层共振率」。
        - **`value` 是全精度**（不是 L1 的 3 位小数）：A 类的加和判据只能在全精度上成立，
          要展示请自己舍入。`dps.base` / `cpu.load` 的源链体量 5–6 KB。

        业务失败：

        - `metric` 不在 `EXPLAIN_METRICS` 里 → `invalid_path`，`hint` 给可用指标全集；
        - `metric` 在登记表里但当前配装取不到值（该属性没有源）→ 同样 `invalid_path`；
        - `depth` ∉ 1..2（含 `True` / `"2"` 这类）→ `invalid_argument`，`field="depth"`。

    Note:
        返回值已从 `explain` 的缓存对象**深拷贝**出来，调用方可以放心就地改；但本层缓存
        与其它入口一样**只存成功结果**。
    """
    text = str(metric or "").strip()
    metrics = _explain.supported_metrics()

    # `depth` 先判：它是**参数**错误（invalid_argument），与「指标不存在」（invalid_path）
    # 是两类事。`bool` 是 `int` 的子类，必须显式排除，否则 `True` 会被当成 depth=1。
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= EXPLAIN_MAX_DEPTH:
        return _failure(
            _ApiFailure(
                "invalid_argument",
                f"depth 只能是 1..{EXPLAIN_MAX_DEPTH}，收到 {depth!r}",
                field="depth",
                hint=list(range(1, EXPLAIN_MAX_DEPTH + 1)),
            ).payload()
        )
    if not text:
        return _failure(
            _ApiFailure(INVALID_PATH_KIND, "metric 不能为空", hint=list(metrics)).payload()
        )
    if text not in metrics:
        return _failure(
            _ApiFailure(
                INVALID_PATH_KIND,
                f"metric {text!r} 不在可归因指标表里（只归因登记过的叶子指标，不做通用路径）",
                path=text,
                hint=list(metrics),
            ).payload()
        )

    def compute(ctx: ViewContext, _parsed: dict) -> tuple[object, bool]:
        result = _explain.explain(ctx, text, depth=depth)
        if result is None:
            # 登记表里有、但这个上下文取不到值（属性没有源）—— 不拿空壳冒充。
            raise _ApiFailure(
                INVALID_PATH_KIND,
                f"metric {text!r} 在当前配装上下文里取不到源（该指标对本配装可能不适用）",
                path=text,
                hint=list(metrics),
            )
        return copy.deepcopy(result), False

    return _execute(
        "explain",
        fit,
        (text, depth),
        skill_tier=skill_tier,
        damage_profile=damage_profile,
        state_policy=state_policy,
        compute=compute,
    )


# =============================================================================
# 十一之三、灵敏度（P3 `sensitivity`）：`fit_sensitivity`
# =============================================================================
#
# 与 `fit_explain` 的分工
# ----------------------
# `explain` 回答「这个数**由什么构成**」（一次计算的源链）；`sensitivity` 回答
# 「**改一个输入**，这个数怎么变」（每个 op 重新进引擎算一遍，实测 0.1–0.33 ms/次）。
# 两者共用同一套指标语言，但 `sensitivity` 的指标表**更大**（+5 个单值指标，见下）。

SENSITIVITY_MAX_OPS: int = _sensitivity.MAX_OPS
"""`fit_sensitivity` 的 `ops` 默认上限（= `sensitivity.MAX_OPS`，目前 32：每个 op 一次重算）。"""

SENSITIVITY_METRICS: tuple[str, ...] = _sensitivity.supported_metrics()
"""可算灵敏度的指标（import 时快照）：`explain` 的 9 个 + 5 个单值指标（`combine == "single"`）。"""


def _json_key(value: object) -> str | None:
    """任意 JSON 可序列化值 → **确定的**缓存 key 片段（`None` 原样返回 `None`）。

    为什么要它：`ops` / `effects` / `buffs` 都是「对象数组」，直接放进 key 会
    `TypeError: unhashable`。按 JSON 规范化（`sort_keys` + 紧凑分隔符）之后，同一份列表
    的两种键序得到**同一个** key，不同列表绝不串味。不可序列化的值给一个唯一标记
    （那次调用照样能跑，只是不共享缓存）。
    """
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return f"<unserializable:{id(value)}>"


def _sensitivity_ops_key(ops: object) -> str | None:
    """`ops` → 缓存 key 片段（`None` = 自动计划，不与任何显式列表撞）。"""
    return _json_key(ops)


def fit_sensitivity(
    fit: object,
    metric: str,
    *,
    ops: object = None,
    max_ops: int = SENSITIVITY_MAX_OPS,
    skill_tier: str = SKILL_TIER_ALL4,
    damage_profile: str = DAMAGE_PROFILE_UNIFORM,
    state_policy: str = STATE_POLICY_ENGINE_DEFAULT,
) -> dict:
    """某指标对「逐件关闭 / 状态切换 / 模式切换」的灵敏度（`project/sensitivity.py` 的组装层）。

    Args:
        fit: 四形态输入。
        metric: 可算灵敏度的指标，见 `SENSITIVITY_METRICS`（`explain` 的 9 个 + 5 个单值：
            `stability.capacitor.{stable_pct,peak_load,capacity}` /
            `resources.cpu.free` / `resources.powergrid.free`，后 5 个 `combine == "single"`）。
        ops: 显式 op 列表（`[{"kind": "disable_item", "item_index": N}` /
            `{"kind": "set_state", "item_index": N, "state": "overload"}` /
            `{"kind": "set_mode", "mode_id": M}]`）；`None` = **自动计划**（每件已装模块
            `disable_item` 一次 + 每个非当前模式 `set_mode` 一次）。
        max_ops: 最多执行几个 op（默认 32）。非正整数 → `invalid_argument`。

    Returns:
        成功时 `result` **就是** `sensitivity.sensitivity(...)` 的返回（本层不改形状）：

        - `{metric, combine, baseline, changes[], warnings[], ops_planned, ops_executed,
          skipped, truncated, max_ops, provenance, tolerance}`；
        - 每条 `changes[i] = {op: {kind, item_index|mode_id, label, state?, mode_name?},
          value, delta, delta_pct, note}`；`delta = value - baseline`、
          `delta_pct = delta / baseline`（**基线为 0 时是 `null`**，不是 0 也不是 inf）；
        - `combine` 多一个枚举值 **`"single"`**（单值指标：既不是相加也不是相乘）；
        - **计数恒等式：`ops_planned == ops_executed + skipped`**（恒成立）。
          `ops_planned` 是**考虑过**的 op 总数（**含空操作** —— 否则「计划 20 / 执行 3 /
          跳过 0」三个数互相打架，少掉的 17 条无处交代）；`ops_executed` 是**真产出一条
          `changes[]`** 的 op 数；`skipped` 是**没产出**的总数，由四部分组成：
          **空操作**（模块已 `offline`、状态/模式已是目标值、重复 op、`mode_not_available`）
          + 被 `max_ops` **砍掉**的 + `variant_error` + `metric_missing_after_change`。
          **`skipped` 只是总数，逐条原因看 `warnings[].kind`**（`noop` 带 `count`，
          一个被跳过的 op 可能同时计入 `noop.count` 与某个具体原因条目 —— 别把各 kind
          的条数直接相加当 `skipped`）；`invalid_op` / `invalid_ops` 是**没成为计划**的坏
          输入，不计入三个计数。
        - `modes_unknown` 表示「模式这一维**没覆盖**」（T3C 的模式在本地索引里枚举不到），
          不等于「切模式没有影响」；`no_index` 同理（没有索引，只能逐件 op）。

        业务失败：

        - `metric` 不在 `SENSITIVITY_METRICS` 里（含空串）→ `invalid_path` +
          `hint` = 可用指标全集；
        - `metric` 在表里但基线取不到（该属性没有值）→ 同样 `invalid_path`；
        - `max_ops` 不是正整数（`0` / `-1` / `true` / `"8"`）→ `invalid_argument` +
          `field="max_ops"`。

    Note:
        返回的 dict 是**每次调用新建的**（`sensitivity` **没有自己的进程级 LRU**，每个变体
        走 `engine.calculate` 并命中引擎自己的 LRU），所以本层不需要像 `fit_explain` 那样
        深拷贝。跨调用的幂等靠 **api 的结果缓存**（key 含 `metric` / 规范化后的 `ops` /
        `max_ops`）；`api.clear_caches()` 清的是**api 结果缓存 + `explain` 的 LRU**，
        没有「sensitivity 的 LRU」这个东西。
    """
    text = str(metric or "").strip()
    metrics = _sensitivity.supported_metrics()

    # `max_ops` 先判：它是参数错误（invalid_argument），与「指标不支持」（invalid_path）
    # 是两类事。`bool` 是 `int` 的子类，必须显式排除（否则 `True` 会被当成 1）。
    if isinstance(max_ops, bool) or not isinstance(max_ops, int) or max_ops < 1:
        return _failure(
            _ApiFailure(
                "invalid_argument",
                f"max_ops 必须是正整数，收到 {max_ops!r}",
                field="max_ops",
                hint={"min": 1, "default": SENSITIVITY_MAX_OPS},
            ).payload()
        )
    if not text:
        return _failure(
            _ApiFailure(INVALID_PATH_KIND, "metric 不能为空", hint=list(metrics)).payload()
        )
    if text not in metrics:
        return _failure(
            _ApiFailure(
                INVALID_PATH_KIND,
                f"metric {text!r} 不在可算灵敏度的指标表里（只算登记过的指标）",
                path=text,
                hint=list(metrics),
            ).payload()
        )

    ops_key = _sensitivity_ops_key(ops)

    def compute(ctx: ViewContext, _parsed: dict) -> tuple[object, bool]:
        # `ops` 原样递进去：坏 op（未知 kind / 越界下标 / 货舱件 / 非法 state）由模块写进
        # `warnings` 并跳过 —— 那是**数据问题**，不是参数错误，不该在接口层 500 掉。
        result = _sensitivity.sensitivity(ctx, text, ops=ops, max_ops=max_ops)
        if result is None:
            raise _ApiFailure(
                INVALID_PATH_KIND,
                f"metric {text!r} 在当前配装上下文里取不到基线（该指标对本配装可能不适用）",
                path=text,
                hint=list(metrics),
            )
        return result, False

    return _execute(
        "sensitivity",
        fit,
        (text, ops_key, max_ops),
        skill_tier=skill_tier,
        damage_profile=damage_profile,
        state_policy=state_policy,
        compute=compute,
    )


# =============================================================================
# 十一之四、投射影响（P6-A projection）：`fit_project`
# =============================================================================
#
# 为什么不复用 `_execute`
# ----------------------
# `_execute` 只吃**一个** `fit`；project 是「一个目标 + N 个来源」，每个来源都要独立归一化，
# 而且来源解析失败必须报 `invalid_argument` + `index`（笼统的 `parse_failed` 会让调用方
# 不知道是哪一项写错了）。缓存 key 因此要含「目标内容哈希 + 每个来源的内容哈希 +
# effects / buffs / strict_state」。
#
# 三个错误归属（写在这里，免得将来「顺手统一」成一种）
# ------------------------------------------------
# - `sources[i]` 解析失败 / 形态不对 → `invalid_argument` + `index=i`；
# - `effects` 里 `effect_id` 未知 → **不是错误**：`projection` 给 `unknown_effect_id`
#   warning 并跳过（引擎对未知 id 静默忽略，我们不能跟着静默）；
# - 目标超载 / 非法配装 → **照常返回数值** + 现有 warnings（那是事实，不是调用错误）。

PROJECT_STRICT_STATE_DEFAULT: bool = _projection.STRICT_STATE_DEFAULT
"""`fit_project` 的 `strict_state` 默认值（= `projection.STRICT_STATE_DEFAULT`，照 EVE 过滤）。"""


def _project_source_items(sources: object) -> list[tuple[int, object, str | None]]:
    """`sources` → `[(index, 配装输入, 显式 label | None)]`；形态不对 → `invalid_argument`。

    每项可以是：
    - 四形态配装输入本身（EFT / DNA / killmail JSON / 链接）；
    - `{"fit": <四形态输入>, "label": "指挥舰 A"}`（标签只影响输出里「谁给的」）。
    """
    if sources is None:
        return []
    if isinstance(sources, (str, bytes)) or not isinstance(sources, (list, tuple)):
        raise _ApiFailure(
            "invalid_argument",
            "sources 必须是数组（每项是一个配装输入：EFT / DNA / killmail JSON / 链接）",
            field="sources",
            hint="sources: []",
        )
    rows: list[tuple[int, object, str | None]] = []
    for index, raw in enumerate(sources):
        label: str | None = None
        item = raw
        if isinstance(raw, dict) and "fit" in raw and "victim" not in raw:
            item = raw.get("fit")
            label_value = raw.get("label")
            if label_value is not None and str(label_value).strip():
                label = str(label_value)
        rows.append((index, item, label))
    return rows


UNKNOWN_BUFF_ID_WARNING: str = (
    "buffs 里有 SDE 的 dbuffCollections 不认识的 id（{ids}）—— 原样保留 id/value，不编名字"
)
"""buff 名查不到时的 warning 模板（**翻译失败不报错**，但要说出来）。"""


def _buff_name_block(buff_id: object, index: Index | None) -> dict | None:
    """buff id → `{name, name_zh}`；查不到 / 没有索引 → `None`（**不编**）。"""
    identifier = _related_int(buff_id)
    if identifier is None or index is None:
        return None
    row = index.buff(identifier)
    if row is None:
        return None
    return {"name": row.get("name"), "name_zh": row.get("name_zh")}


def _decorate_buffs(result: dict, index: Index | None) -> dict:
    """给 buff 条目补上 `name` / `name_zh`（**形状只加不删**）。

    为什么在 api 层做：`project/projection.py` 只负责算，不认识 SDE 的名字；翻译属于
    **组装层**的活，而且这样 `project/**` 一行都不用改。

    覆盖两处（都在真实返回里核对过）：
    1. `applied.buffs[]` = `{id, value, from}` → 追加 `name` / `name_zh`；
    2. `delta.attributes[].attributed_to[]` **如果**某行带 `buff_id` 也一并翻
       （当前版本那里只有 `effect_index` / `operator` / `value` / `penalty` / `source`，
       **没有 buff id** —— 所以这是防御性分支，不是主路径）。

    `id` / `value` / `from` 一个都不动：测试、README、agent 的既有读法都依赖它们。
    查不到名字时不加键、不报错，只在 `warnings` 里说一句。
    """
    applied = result.get("applied")
    buffs = applied.get("buffs") if isinstance(applied, dict) else None
    unknown: list[int] = []

    def decorate(entry: dict) -> None:
        named = _buff_name_block(entry.get("id"), index)
        if named is None:
            identifier = _related_int(entry.get("id"))
            if identifier is not None:
                unknown.append(identifier)
            return
        entry["name"] = named["name"]
        entry["name_zh"] = named["name_zh"]

    if isinstance(buffs, list):
        for entry in buffs:
            if isinstance(entry, dict) and "id" in entry:
                decorate(entry)

    delta = result.get("delta")
    attributes = delta.get("attributes") if isinstance(delta, dict) else None
    if isinstance(attributes, list):
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            attribution = attribute.get("attributed_to")
            if not isinstance(attribution, list):
                continue
            for row in attribution:
                if isinstance(row, dict) and "buff_id" in row:
                    named = _buff_name_block(row.get("buff_id"), index)
                    if named is not None:
                        row["name"] = named["name"]
                        row["name_zh"] = named["name_zh"]

    if unknown:
        warning = UNKNOWN_BUFF_ID_WARNING.format(ids=sorted(set(unknown)))
        warnings = result.get("warnings")
        if isinstance(warnings, list):
            warnings.append(warning)
    return result


def fit_project(
    target: object,
    sources: object = None,
    effects: object = None,
    buffs: object = None,
    *,
    strict_state: bool = PROJECT_STRICT_STATE_DEFAULT,
    skill_tier: str = SKILL_TIER_ALL4,
    damage_profile: str = DAMAGE_PROFILE_UNIFORM,
    state_policy: str = STATE_POLICY_ENGINE_DEFAULT,
) -> dict:
    """把来源船的投射影响投到目标上（`project/projection.py` 的组装层，**不改形状**）。

    **「一给一」而不是「编队」**：一次调用 = 一个目标 + N 个来源的**并集**。投射**不链式
    传递**（目标收到的 incoming 不会变成它的 outgoing），所以编队要由调用方**并联**：
    对每个目标各调一次，边自己枚举。

    Args:
        target: 目标配装（四形态输入）。
        sources: 来源船数组，每项是四形态输入，或 `{"fit": <输入>, "label": "指挥舰 A"}`。
            `None` / `[]` = 只看显式 `effects` / `buffs`（都没有时结果是恒等 delta）。
        effects: 显式 `ProjectedEffect` 数组（`{type_id, effect_id, attributes}`）。
            `effect_id` 不在该 type 的编号目录里 → `unknown_effect_id` warning 并跳过。
        buffs: 显式 `ProjectedBuff` 数组（`{id, value}`）。
        strict_state: `True`（默认，照 EVE）= **非 `active`** 的模块不提供加成；
            `False` = 引擎原样（引擎对 buffs 不看 state，offline 的指挥爆发器也照样给）。
            两种取值都会在 `warnings` 里说明差异（`buff_state_filtered` /
            `buff_state_not_filtered`），不静默。

    Returns:
        成功时 `result` **就是** `projection.project(...)` 的返回（本层不改形状）：

        - `{strict_state, target, applied{effects[], buffs[]}, sources[], delta, repaired,
          warnings[]}`；
        - `delta.metrics[]`：`{path, before, after, delta, delta_pct, unit}`，固定 10 条
          （EHP 混合 + 三层 / 速度 / 电容六项）；
        - `delta.attributes[]`：每个**真的变了**的船体属性 + `attributed_to`（谁造成的）；
        - `repaired`：遥修落点（`rate` / `effective` 的前后与增量）—— **它改的是「回血
          速度」，不是 EHP**，两者在返回里是分开的；
        - `warnings[]`：`buff_state_filtered` / `buff_state_not_filtered` /
          `unknown_effect_id` / `self_buff_included` / `no_outgoing` / `invalid_effect` /
          `invalid_buff` / `effect_catalog_unavailable`。

        业务失败：

        - `sources[i]` 解析失败 / 形态不对 → `invalid_argument` + `index=i`
          （原错误放在 `cause` 里，便于区分「写错模块名」还是「不是配装」）；
        - `strict_state` 不是布尔 → `invalid_argument` + `field="strict_state"`；
        - **目标超载 / 非法配装不报错**：照常给数值 + 该配装自己的 warnings。

    Note:
        成本实测 **5–7 ms**（目标基线 1 次 + 投影 1 次 + 每个有 buffs 的来源 1 次 strict
        变体）；返回对象每次新建，不需要像 `fit_explain` 那样深拷贝。
    """
    try:
        _check_params(skill_tier, damage_profile, state_policy)

        if not isinstance(strict_state, bool):
            raise _ApiFailure(
                "invalid_argument",
                f"strict_state 必须是布尔，收到 {strict_state!r}",
                field="strict_state",
                hint=[True, False],
            )

        source_rows = _project_source_items(sources)
        target_kind, target_key, target_payload = _input_descriptor(target)

        specs: list[dict] = []
        for index, item, label in source_rows:
            try:
                kind, key, payload = _input_descriptor(item)
            except _ApiFailure as exc:
                raise _ApiFailure(
                    "invalid_argument",
                    f"sources[{index}] 无法识别：{exc.message}",
                    field=f"sources[{index}]",
                    index=index,
                    cause=exc.payload(),
                ) from exc
            specs.append(
                {"index": index, "kind": kind, "key": key, "payload": payload, "label": label}
            )

        effects_key = _json_key(effects)
        buffs_key = _json_key(buffs)
        cache_key: tuple | None = None
        if target_key is not None and all(spec["key"] is not None for spec in specs):
            cache_key = (
                "project",
                (
                    tuple(spec["key"] for spec in specs),
                    effects_key,
                    buffs_key,
                    strict_state,
                ),
                target_key,
                skill_tier,
                damage_profile,
                state_policy,
            )
        if cache_key is not None:
            hit = _cache_get(cache_key)
            if hit is not None:
                return hit

        if target_kind == INPUT_NETWORK:
            killmail = fetch_killmail(target_payload["killmail_id"], target_payload["hash"])
            target_payload = {"text": json.dumps(killmail, ensure_ascii=False)}
        for spec in specs:
            if spec["kind"] == INPUT_NETWORK:
                killmail = fetch_killmail(spec["payload"]["killmail_id"], spec["payload"]["hash"])
                spec["payload"] = {"text": json.dumps(killmail, ensure_ascii=False)}

        _ensure_index()
        target_parsed = _parsed_fit(target_payload)
        target_ctx = _context(target_parsed["fit"], str(skill_tier))

        project_sources: list[object] = []
        for spec in specs:
            try:
                parsed = _parsed_fit(spec["payload"])
            except _ApiFailure as exc:
                raise _ApiFailure(
                    "invalid_argument",
                    f"sources[{spec['index']}] 解析失败：{exc.message}",
                    field=f"sources[{spec['index']}]",
                    index=spec["index"],
                    cause=exc.payload(),
                ) from exc
            ctx = _context(parsed["fit"], str(skill_tier))
            entry: dict[str, object] = {"ctx": ctx}
            if spec["label"]:
                entry["label"] = spec["label"]
            project_sources.append(entry)

        result = _decorate_buffs(
            _projection.project(
                target_ctx,
                project_sources,
                effects,
                buffs,
                strict_state=strict_state,
            ),
            target_ctx.index,
        )
        envelope = _success(
            result,
            _meta(
                target_ctx,
                source_format=str(target_parsed.get("source_format")),
                damage_profile=str(damage_profile),
                state_policy=str(state_policy),
                result=result,
            ),
        )
    except _ApiFailure as exc:
        return _failure(exc.payload())
    except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得裸抛给 agent
        return _failure(
            {
                "kind": "internal_error",
                "message": "接口层内部异常，本次没有可用结果",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )

    if cache_key is not None:
        _cache_put(cache_key, envelope)
    return envelope


# =============================================================================
# 十一之五、交战数学（P6-B combat）：`fit_engagement`
# =============================================================================
#
# 三个 mode 的语义（本层只分派，数学全在 `project/combat.py`）
# ----------------------------------------------------------
# - `exchange`（默认）：A ↔ B 双向换血（一次 `exchange` 里各有一次单方向 `engagement`）；
# - `timeline`：双方同时开火、按有效 DPS 线性扣血的简化时间线；
# - `anchor`：战报锚点 —— 模型能否在 `window_s` 内打出 `observed_hp`（`explain_anchor`）。
#
# `fleet` 与三个 mode 正交，但**只在 `exchange` 下允许**：它是「同型 × 数量」的量级模型
# （`fleet_engagement`），不是个体配装，也不是链式编队。
#
# `target_velocity` 的归属（别猜）
# ------------------------------
# `exchange` / `timeline` **不接受**它（那两条按双方 maxVelocity 自动填，是模块的既定假设）；
# 给了非 0 值就 `invalid_argument` + `field="target_velocity"`（明确拒绝 > 静默忽略）——
# 要指定速度请用 `anchor` 或 `fleet`。

ENGAGEMENT_MODES: tuple[str, ...] = ("exchange", "timeline", "anchor")
"""`fit_engagement` 的 `mode` 取值（顺序 = 推荐顺序）。"""

ENGAGEMENT_MODE_EXCHANGE: str = "exchange"
"""默认 mode：双向换血比（`combat.exchange`）。"""

ENGAGEMENT_MODE_TIMELINE: str = "timeline"
ENGAGEMENT_MODE_ANCHOR: str = "anchor"


def _engagement_number(
    value: object,
    field: str,
    *,
    positive: bool = False,
    required: bool = True,
    label: str = "非负数",
) -> float | None:
    """`distance_m` / `angular_vel` / `observed_hp` … 的数值校验（失败 → `invalid_argument`）。"""
    if value is None:
        if not required:
            return None
        raise _ApiFailure(
            "invalid_argument",
            f"{field} 必填（{label}）；距离 / 角速度是**输入**，本层不做几何假设",
            field=field,
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _ApiFailure(
            "invalid_argument",
            f"{field} 必须是数值（{label}），收到 {value!r}",
            field=field,
        )
    number = float(value)
    if not math.isfinite(number):
        raise _ApiFailure(
            "invalid_argument",
            f"{field} 必须是有限数值（收到 {value!r}：nan / inf 不能参与几何计算）",
            field=field,
        )
    if number < 0.0 or (positive and number <= 0.0):
        raise _ApiFailure(
            "invalid_argument",
            f"{field} 必须是{'正数' if positive else '非负数'}，收到 {value!r}",
            field=field,
        )
    return number


def _engagement_fleet(fleet: object) -> dict | None:
    """`fleet` → `{"count": N}` / `{"killmail_attackers": {...}}`；`None` 表示不聚合。

    数量**不得编造**：要么调用方给 `count`，要么直接给 `fit_killmail()["attackers"]`
    （模块内部按模板 ship group 取计数）。
    """
    if fleet is None:
        return None
    if isinstance(fleet, bool):
        raise _ApiFailure(
            "invalid_argument",
            "fleet 必须是正整数或对象（count / killmail_attackers），收到 bool",
            field="fleet",
        )
    if isinstance(fleet, int):
        if fleet <= 0:
            raise _ApiFailure(
                "invalid_argument", f"fleet 数量必须是正整数，收到 {fleet}", field="fleet"
            )
        return {"count": fleet}
    if isinstance(fleet, dict):
        if "killmail_attackers" in fleet:
            return {"killmail_attackers": fleet["killmail_attackers"]}
        count = fleet.get("count")
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            return {"count": count}
        raise _ApiFailure(
            "invalid_argument",
            f"fleet.count 必须是正整数，收到 {count!r}",
            field="fleet",
        )
    raise _ApiFailure(
        "invalid_argument",
        f"fleet 必须是正整数或对象（count / killmail_attackers），收到 {type(fleet).__name__}",
        field="fleet",
    )


def _engagement_input(
    fit_input: object, field: str
) -> tuple[str, str | None, dict]:
    """一侧配装 → `(形态, 缓存 key, 载荷)`；形态不对 → `invalid_argument` + `field`。

    **只做判别，不解析**：缓存命中时连归一化都不该发生（两次配装各 10–20 ms）。
    """
    try:
        return _input_descriptor(fit_input)
    except _ApiFailure as exc:
        raise _ApiFailure(
            "invalid_argument",
            f"{field} 无法识别：{exc.message}",
            field=field,
            cause=exc.payload(),
        ) from exc


def _engagement_side(
    kind: str, payload: dict, field: str, skill_tier: str
) -> tuple[dict, ViewContext]:
    """一侧配装 → `(归一化结果, ViewContext)`；联网 / 解析失败 → `invalid_argument` + `field`。"""
    if kind == INPUT_NETWORK:
        killmail = fetch_killmail(payload["killmail_id"], payload["hash"])
        payload = {"text": json.dumps(killmail, ensure_ascii=False)}
    try:
        parsed = _parsed_fit(payload)
    except _ApiFailure as exc:
        raise _ApiFailure(
            "invalid_argument",
            f"{field} 解析失败：{exc.message}",
            field=field,
            cause=exc.payload(),
        ) from exc
    return parsed, _context(parsed["fit"], skill_tier)


def fit_engagement(
    attacker: object,
    defender: object,
    *,
    distance_m: object = None,
    angular_velocity: object = 0.0,
    target_velocity: object = 0.0,
    include_wrecking: bool = True,
    mode: str = ENGAGEMENT_MODE_EXCHANGE,
    fleet: object = None,
    observed_hp: object = None,
    window_s: object = None,
    duration_s: object = None,
    points: object = 6,
    skill_tier: str = SKILL_TIER_ALL4,
    damage_profile: str = DAMAGE_PROFILE_UNIFORM,
    state_policy: str = STATE_POLICY_ENGINE_DEFAULT,
) -> dict:
    """交战数学：A 打 B 的命中率 / 有效 DPS / 击穿时间（`project/combat.py` 的组装层）。

    Args:
        attacker / defender: 两份四形态配装输入。
        distance_m: **必填**，面到面距离（m，非负）。距离与角速度是**输入**，本层不做
            几何假设。
        angular_velocity: 角速度（rad/s）。`0` 是乐观上界（迎头/静止）。
        target_velocity: 守方绝对速度（m/s）。**只对 `anchor` / `fleet` 生效**；
            `exchange` / `timeline` 按双方 maxVelocity 自动填 —— 在它们上面给非 0 值会
            `invalid_argument`（明确拒绝，不静默忽略）。
        include_wrecking: 炮台伤害是否含 wrecking（默认 True，与 pyfa 一致）；False 时
            归一到纸面口径（完美应用 = 1.0）。
        mode: `exchange`（默认，双向换血比）/ `timeline`（线性时间线）/ `anchor`
            （战报锚点：能否在 `window_s` 内打出 `observed_hp`）。
        fleet: 编队量级：正整数，或 `{"count": N}` / `{"killmail_attackers": {...}}`。
            **只在 `exchange` 下允许**。返回带 `fleet_assumption: true` 与
            `order_of_magnitude_only` flag —— 它是「同型 × 数量」的量级模型，不是个体配装。
        observed_hp / window_s: `anchor` 必填（正数）；`fleet` 下可选（给量级校验）。
        duration_s / points: `timeline` 的时间窗与采样点数（默认 6，至少 2）。
        skill_tier / damage_profile / state_policy: 三个共同参数。

    Returns:
        成功时 `result` **就是** 对应子函数的返回（本层不改形状）：

        - `exchange` → `{kind:"exchange", inputs, a_on_b, b_on_a, race}`；
        - `timeline` → `{kind:"timeline", inputs, rates, endpoints, samples, assumptions}`；
        - `anchor` → `{kind:"anchor_check", observed_hp, window_s, required_hp_dps,
          explainable, region, best, reasons, caveats, …}`；
        - `fleet` → `{kind:"fleet_engagement", fleet_assumption:true, fleet_note, count,
          totals, defender_survival, window, flags:[…,"order_of_magnitude_only"]}`。

        **两个血量口径别混**（每个单方向结果里都有，各带 `compare_with` 说明用途）：
        `hp_pool`（盾+甲+结构，和战报 `damage_taken` 同量纲）与 `ehp`（原始伤害口径，
        用来算击穿时间）。

        业务失败：

        - `distance_m` 缺失 / 非数 / 负数 → `invalid_argument` + `field="distance_m"`；
        - `attacker` / `defender` 解析失败 → `invalid_argument` + `field`；
        - `mode` 不认识 / `fleet` 形态不对 / `anchor` 缺 `observed_hp` 或 `window_s` →
          `invalid_argument` + `field`；
        - **一方没有对敌武器不是错误**：`ttk` 为 `None`，`flags` 里给
          `no_weapons: …`，`unsupported` 里给未建模武器的下界 DPS。
    """
    try:
        _check_params(skill_tier, damage_profile, state_policy)

        mode_text = str(mode or "").strip()
        if mode_text not in ENGAGEMENT_MODES:
            raise _ApiFailure(
                "invalid_argument",
                f"mode {mode!r} 不支持",
                field="mode",
                hint=list(ENGAGEMENT_MODES),
            )
        distance = _engagement_number(distance_m, "distance_m")
        angular = _engagement_number(angular_velocity, "angular_velocity")
        target_speed = _engagement_number(target_velocity, "target_velocity")
        if not isinstance(include_wrecking, bool):
            raise _ApiFailure(
                "invalid_argument",
                f"include_wrecking 必须是布尔，收到 {include_wrecking!r}",
                field="include_wrecking",
                hint=[True, False],
            )

        fleet_spec = _engagement_fleet(fleet)
        if fleet_spec is not None and mode_text != ENGAGEMENT_MODE_EXCHANGE:
            raise _ApiFailure(
                "invalid_argument",
                f"fleet 只在 mode={ENGAGEMENT_MODE_EXCHANGE} 下允许（收到 mode={mode_text!r}）",
                field="fleet",
                hint={"modes": [ENGAGEMENT_MODE_EXCHANGE]},
            )

        observed = _engagement_number(
            observed_hp, "observed_hp", positive=True, required=False, label="正数"
        )
        window = _engagement_number(
            window_s, "window_s", positive=True, required=False, label="正数"
        )
        duration = _engagement_number(
            duration_s, "duration_s", positive=True, required=False, label="正数"
        )

        if mode_text == ENGAGEMENT_MODE_ANCHOR:
            if observed is None or window is None:
                raise _ApiFailure(
                    "invalid_argument",
                    "mode=anchor 必须给 observed_hp 与 window_s（都是正数）",
                    field="observed_hp" if observed is None else "window_s",
                )
        if mode_text == ENGAGEMENT_MODE_TIMELINE:
            if isinstance(points, bool) or not isinstance(points, int) or points < 2:
                raise _ApiFailure(
                    "invalid_argument",
                    f"points 必须是 >=2 的整数，收到 {points!r}",
                    field="points",
                )
        if (
            target_speed != 0.0
            and mode_text != ENGAGEMENT_MODE_ANCHOR
            and fleet_spec is None
        ):
            raise _ApiFailure(
                "invalid_argument",
                "target_velocity 只对 mode=anchor / fleet 生效：exchange/timeline 按双方 "
                "maxVelocity 自动填（要指定速度请用 anchor 或 fleet）",
                field="target_velocity",
            )

        attacker_kind, attacker_key, attacker_payload = _engagement_input(attacker, "attacker")
        defender_kind, defender_key, defender_payload = _engagement_input(defender, "defender")

        args = (
            mode_text,
            distance,
            angular,
            target_speed,
            include_wrecking,
            observed,
            window,
            duration,
            points,
            _json_key(fleet_spec),
        )
        cache_key: tuple | None = None
        if attacker_key is not None and defender_key is not None:
            cache_key = (
                "engagement",
                args,
                attacker_key,
                defender_key,
                skill_tier,
                damage_profile,
                state_policy,
            )
        if cache_key is not None:
            hit = _cache_get(cache_key)
            if hit is not None:
                return hit

        # 缓存未命中才归一化两份配装（各 10–20 ms；命中路径一个字节都不解析）
        attacker_parsed, attacker_ctx = _engagement_side(
            attacker_kind, attacker_payload, "attacker", str(skill_tier)
        )
        defender_parsed, defender_ctx = _engagement_side(
            defender_kind, defender_payload, "defender", str(skill_tier)
        )

        if fleet_spec is not None:
            fleet_kwargs: dict[str, object] = {
                "range_m": distance,
                "angular_vel": angular,
                "target_velocity": target_speed,
                "include_wrecking": include_wrecking,
            }
            if "count" in fleet_spec:
                fleet_kwargs["count"] = fleet_spec["count"]
            else:
                fleet_kwargs["killmail_attackers"] = fleet_spec["killmail_attackers"]
            if window is not None:
                fleet_kwargs["window_s"] = window
            if observed is not None:
                fleet_kwargs["observed_hp"] = observed
            result = _combat.fleet_engagement(attacker_ctx, defender_ctx, **fleet_kwargs)
        elif mode_text == ENGAGEMENT_MODE_TIMELINE:
            result = _combat.timeline(
                attacker_ctx,
                defender_ctx,
                range_m=distance,
                angular_vel=angular,
                duration_s=duration,
                points=int(points),
                include_wrecking=include_wrecking,
            )
        elif mode_text == ENGAGEMENT_MODE_ANCHOR:
            result = _combat.explain_anchor(
                attacker_ctx,
                defender_ctx,
                observed_hp=observed,
                window_s=window,
                target_velocity=target_speed,
                include_wrecking=include_wrecking,
            )
        else:
            result = _combat.exchange(
                attacker_ctx,
                defender_ctx,
                range_m=distance,
                angular_vel=angular,
                include_wrecking=include_wrecking,
            )

        meta = _meta(
            attacker_ctx,
            source_format=str(attacker_parsed.get("source_format")),
            damage_profile=str(damage_profile),
            state_policy=str(state_policy),
            result=result,
        )
        meta["mode"] = mode_text
        meta["defender_source_format"] = str(defender_parsed.get("source_format"))
        envelope = _success(result, meta)
    except _ApiFailure as exc:
        return _failure(exc.payload())
    except ValueError as exc:
        # 模块自己的参数校验（points/ammo/count 之类）—— 那是调用方参数问题，不是内部 bug
        return _failure(
            {"kind": "invalid_argument", "message": str(exc), "field": "engagement"}
        )
    except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得裸抛给 agent
        return _failure(
            {
                "kind": "internal_error",
                "message": "接口层内部异常，本次没有可用结果",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )

    if cache_key is not None:
        _cache_put(cache_key, envelope)
    return envelope


# =============================================================================
# 十二、战报上下文（T-49）：`fit_killmail`
# =============================================================================
#
# 为什么单独一条执行骨架
# ----------------------
# `_execute()` 只把「归一化后的 fit」交给 `compute`，而战报上下文要的是**原始 killmail**
# （`attackers` / `zkb` 价值块 / `victim.damage_taken` / `solar_system_id`）。所以这里另起
# `_execute_killmail()`：校验 / 缓存 / 上下文 / 信封**照样共用**，只把「取 killmail」与
# 「组装战报块」换成这一节的实现。归一化仍然只有一处（`_parsed_fit()`）。
#
# 两个 API 各给一半（实测，原始响应存在 `tests/fixtures/killmail/`）
# ----------------------------------------------------------------
#
# - zkillboard `/api/killID/<id>/`：**有** `zkb{...}`（价值 / points / labels），
#   `attackers` 里**没有** `items`（items 只有 ESI 有）；
# - ESI `/killmails/<id>/<hash>/`：**有** attackers / victim 详情与
#   `victim.damage_taken`，**没有** `zkb` 价值块。
#
# 联网路径 = 两个都拉、合并成一份（ESI 当底、`zkb` 块附上去）；本地 killmail 只用调用方
# 给的那份，缺 zkb 块时价值 / flags / labels 一律 `null` 并写进 `warnings`（不猜）。
#
# 三条不做的事
# ------------
#
# 1. **不编造**：星系名 / 安全等级 / 武器族拿不到就给 `null` / `other`，绝不用「看着像」
#    的值填。`security.band` 只在**真的拿到** `security_status` 时才给；
# 2. **不重算 EHP**：`context.ehp_mixed` 直接读投影层（`survivability.ehp_mixed`），本层
#    只做一次除法给出 `ratio`；
# 3. **不替用户下结论**：`damage_taken_vs_ehp` 只给「挨了多少 / 自身多少」与一句固定说明，
#    不给生死判词。

KILLMAIL_OP: str = "killmail"
"""`fit_killmail` 的入口名：**一定要进缓存 key**，否则会和 `fit_index` 的缓存串味。"""

NOT_A_KILLMAIL_KIND: str = "not_a_killmail"
"""「输入没有战报来源」的 `error.kind`（EFT / DNA / structured fit 都是这种）。

与 §10.4 的五种 kind **只增不改**：宁可诚实报错，也不拿配装去猜一条战报。
"""

KILLMAIL_ORIGIN_ZKILLBOARD: str = "zkillboard"
"""这条战报的**原文**来自 zkb `/api/killID/<id>/`（它本身就带整条 killmail，见下）。"""

KILLMAIL_ORIGIN_ESI: str = "esi"
"""原文来自 ESI `/killmails/<id>/<hash>/`（zkb 没带 items 时的回落路径）。"""

KILLMAIL_ORIGIN_LOCAL: str = "local"
"""原文是调用方直接贴进来的 killmail JSON（不经网络）。"""

ZKB_ITEMS_FALLBACK_WARNING: str = (
    "zkb 的响应里**没有 `victim.items` 这个键**（可能版本 / 裁剪差异），已回落到 ESI "
    "取原文（多一次 1.7–2.6 s 的请求）"
)
"""回落 ESI 时写进 `warnings` 的说明（**沉默不行**：多花了一次时间要交代）。

注意措辞：回落只在「键不存在」时发生；`items: []` 是另一个分支（走
`ZKB_EMPTY_ITEMS_WARNING`），别把两者混成一句话。
"""

ZKB_EMPTY_ITEMS_WARNING: str = "该战报没有已装模块（zkb 与 ESI 均为空）"
"""`victim.items` 是 `[]` 时短路的那条约战报，**必须显式说出来**。

为什么：`items: []` 与「我们漏了模块数据」在返回里长得一模一样。取证已证明这两种来源
在该字段上一致（16/16），所以空数组是**事实**，不是缺失 —— 但 agent 看不到我们的取证，
一句 warning 就是它唯一的判断依据。
"""

ZKB_COMPLETE_KILLMAIL_NOTE: str = (
    "zkb /api/killID/ 返回的是**整条完整 killmail**（实测 16/16 与 ESI 逐字段一致："
    "attackers 条数与每个字段值、victim 全字段、items 逐项），所以带 items 时不必再请求 ESI"
)
"""数据源原则的实测依据（写在常量里，改这条之前先看它）。"""

ESI_UNIVERSE_NAMES_API: str = "https://esi.evetech.net/latest/universe/names/"
"""ESI 批量 id → 名字（`POST`，请求体是 id 数组）。星系名本地 SDE 索引里**没有**。"""

ESI_UNIVERSE_SYSTEM_API: str = "https://esi.evetech.net/latest/universe/systems/{system_id}/"
"""ESI 单星系详情：`name` / `security_status` / `security_class`（安全等级只有这里有）。"""

UNIVERSE_NAMES_MAX_IDS: int = 1000
"""`POST /universe/names/` 单次最多 1000 个 id（ESI 的硬上限，超了整批 400）。"""

SECURITY_BAND_HIGHSEC: str = "highsec"
SECURITY_BAND_LOWSEC: str = "lowsec"
SECURITY_BAND_NULLSEC: str = "nullsec"
SECURITY_BAND_WORMHOLE: str = "wormhole"

HIGHSEC_THRESHOLD: float = 0.5
"""`security_status >= 0.5` → highsec；`0 < s < 0.5` → lowsec；`s <= 0` → nullsec。"""

LOWSEC_THRESHOLD: float = 0.0

CRIMINAL_SECURITY_THRESHOLD: float = -5.0
"""security status ≤ -5.0 的角色带 **Criminal** flag —— 这是游戏规则，不是本层的评分。

`attackers.security.criminal_count` 用的就是它：一个可被玩家独立复核的判据，
而不是「看起来坏不坏」的判断。
"""

WEAPON_FAMILY_OTHER: str = "other"
"""分不清的一律 `other`（**不猜**）。族名固定 8 个，顺序即 `by_weapon_family` 的族清单。"""

WEAPON_FAMILIES: tuple[str, ...] = (
    "projectile",
    "lazors",
    "hybrid",
    "missile",
    "drone",
    "fighter",
    "smartbomb",
    WEAPON_FAMILY_OTHER,
)

_WEAPON_FAMILY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 顺序有意义：先判「一定不是炮/导弹」的族，再判炮，最后判导弹。
    ("smartbomb", ("smartbomb", "smart bomb")),
    ("fighter", ("fighter",)),
    ("drone", ("drone",)),
    ("projectile", ("projectile", "autocannon", "artillery", "cannon")),
    ("hybrid", ("hybrid", "railgun", "blaster")),
    ("lazors", ("laser", "pulse", "beam", "energy weapon", "disintegrator")),
    ("missile", ("launcher", "missile", "torpedo", "rocket", "bomb")),
)
"""`weapon_type_id` 的**名字 / 组名** → 武器族（启发式，命中第一条就停）。

判据只来自 SDE 的名字与组名，**没有**任何「这族应该打多少」的推断；都命中不了就是
`other`。实测覆盖：`Mega Pulse Laser II`→lazors、`Hexa 2500mm Repeating Cannon II`→
projectile、`Mjolnir Rage Torpedo`→missile、`Garde II`/`Bouncer II`→drone、
`Warp Scrambler II`（不是武器）→other。
"""

KILLMAIL_EHP_PATH: str = "survivability.ehp_mixed"
"""EHP 的唯一来源（投影层路径）：本层只读它，**绝不自己算**。"""

KILLMAIL_EHP_NOTE: str = "这条只说「挨了多少伤害 / 自身 EHP 多少」，不给生死结论"

_SHARE_DIGITS: int = 6
"""`damage_pct` 保留的小数位（推导值；6 位足以让 79 项之和仍是 1.0）。"""

_RATIO_DIGITS: int = 3
"""`damage_taken_vs_ehp.ratio` 保留的小数位（四舍五入；任务书的 3.065 是截断值）。"""

DAMAGE_CHECK_MATCH: str = "matches_victim"
DAMAGE_CHECK_MISMATCH: str = "mismatch"

CAPSULE_TYPE_ID: int = 670
"""`Capsule`（蛋）的 `type_id` —— **蛋不能装任何装备**，所以蛋不可能打出武器伤害。

实测（killmail 139239127，2026-10-19）：79 个 attackers 里有 **3 个** `ship_type_id == 670`，
伤害 1086009 / 76069 / 843（合计 1162921）。其中 `character_id 2121910483` 的损失记录显示
2026-10-17T22:21:40Z 在星系 30002537 就丢了 ship 29340（Osprey Navy Issue），而受害者战报
时间是 22:31:32Z、**同一星系** —— 即他在这场交火里先丢了自己的船，之后以蛋的状态还在场；
ESI 把他那一刻的「记录船」写成了蛋，那 108 万伤害实际来自他**已被摧毁的船**。

所以口径是：**蛋的伤害数字是真的，但船归属不可信**；蛋出现在 attackers 里 = 该玩家在这场
交火中已经失去了自己的船。
"""

CAPSULE_GROUP: str = "Capsule"
"""SDE 里 `CAPSULE_TYPE_ID` 所属组的组名（`by_ship_group` 就是按组名分桶的）。

**坑（实测 139239127）**：`Capsule` 组里有**两种** type —— `670`（`Capsule`）与 `33328`
（`Capsule - Genolution 'Auroral' 197-variant`）。所以 `by_ship_group` 的 Capsule 行
`count=4`（多出来的那个是 33328、伤害 0），而 `capsule_count` 严格按 `type_id == 670` 是
**3** —— 两个数字都对，只是数的是**条目**与**严格同型**。`ship_attribution` 按**组**判定，
所以 33328 那一行也会被标成 `capsule`（它同样是蛋，同样装不了武器）。
"""

SHIP_ATTRIBUTION_NORMAL: str = "normal"
SHIP_ATTRIBUTION_CAPSULE: str = "capsule"
"""`by_ship_group[].ship_attribution` 的两个取值（**全表一致**：每行都给这个键）。

选「每行都带、默认 `normal`」而不是「只有 capsule 行才有」：`by_ship_group` 是列表，
形状稳定才不需要调用方 `in` 判断（多一个键的代价远小于「有时有有时没有」）。
"""

CAPSULE_ATTACKER_WARNING: str = (
    "{count} 名攻击者的记录船是 Capsule（蛋）。蛋无法装备任何武器，其伤害来自该玩家在同一次"
    "交火中被摧毁的船；出现蛋说明这些玩家在这场交火里已经失去了自己的船。按船型统计时请勿把"
    "蛋当作实际输出船。"
)
"""`attackers.capsule_count > 0` 时追加到 `result.warnings` 的那一条（三个事实全部写明）。"""

_UNIVERSE_LOCK = threading.Lock()
_UNIVERSE_NAMES: dict[int, str | None] = {}
_UNIVERSE_SYSTEMS: dict[int, dict] = {}
"""星系名 / 星系详情的**进程内永久缓存**（选永久而不是 TTL，理由见 `universe_names`）。"""

UNIVERSE_NAMES_MIN_INTERVAL_S: float = 0.05
"""两批 `POST /universe/names/` 之间的**最小间隔**（秒）。

ESI 比 zkb 宽松得多，但「一次调用最多 1000 个 id」这条约束下，扫描几十条战报时仍然
会出现连续多批 —— 0.05 s 是刻意很小的值：足以避免背靠背爆冲，又不会让批量变慢。
"""

_UNIVERSE_NAMES_GATE_LOCK = threading.Lock()
_UNIVERSE_NAMES_LAST_START: float | None = None
"""上一次 names 请求的开始时刻（`time.monotonic`；限流闸门用，测试可注入 clock）。"""

UniverseNamesRequester = Callable[[str, object], object]
"""`POST /universe/names/` 的请求替身签名：`(url, body)`（测试注入点；生产走 urllib）。"""


def _as_float(value: object) -> float | None:
    """`int` / `float` → `float`（**排除 `bool`**）；其它一律 `None`（不猜 0）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_int(value: object) -> int | None:
    """`int` → `int`（排除 `bool`）；其它一律 `None`。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _as_bool(value: object) -> bool | None:
    """`bool` → `bool`；其它一律 `None`（`zkb` 缺字段时**不是** `False`）。"""
    return value if isinstance(value, bool) else None


def _share(damage: int, total: int | None) -> float | None:
    """占比（分母为 0 / 未知时给 `None`，不给 0.0 冒充）。"""
    if not total:
        return None
    return round(damage / total, _SHARE_DIGITS)


# ---- 十二之一：联网（POST /universe/names/ 与星系详情）----------------------


def _post_json(url: str, payload: object) -> object:
    """`POST` 一个 JSON 文档（请求体 = `payload` 的 JSON），失败一律 `network_failed`。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    return _read_json(request, url)


def _universe_names_gate(
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> None:
    """names 请求的限流闸门：与上一次请求至少隔 `UNIVERSE_NAMES_MIN_INTERVAL_S`。

    等待量**上下都夹住**（`[0, 0.05]`）：注入的虚拟时钟若比真实 `monotonic` 小（测试里
    1000.0 vs 十几万秒），不夹就会算出一个几万秒的 sleep 把进程挂死。夹住之后语义仍然
    是「别背靠背」，只是不会因为时钟不单调而自伤。
    """
    global _UNIVERSE_NAMES_LAST_START
    now_fn = clock or time.monotonic
    sleep_fn = sleeper or time.sleep
    with _UNIVERSE_NAMES_GATE_LOCK:
        now = float(now_fn())
        last = _UNIVERSE_NAMES_LAST_START
        if last is not None:
            wait = UNIVERSE_NAMES_MIN_INTERVAL_S - (now - last)
            wait = min(max(wait, 0.0), UNIVERSE_NAMES_MIN_INTERVAL_S)
            if wait > 0:
                sleep_fn(wait)
                now = float(now_fn())
        _UNIVERSE_NAMES_LAST_START = now


def universe_names(
    ids: "list[int] | tuple[int, ...]",
    *,
    requester: UniverseNamesRequester | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[int, str]:
    """批量 `id → 名字`（走 ESI `POST /universe/names/`，**单次 ≤ 1000**，进程内永久缓存）。

    Args:
        ids: 想要的 id（重复的只查一次；不是整数的直接忽略）。
        requester: 请求替身 `(url, body) → payload`（测试注入点；生产不传 = urllib）。
        clock / sleeper: 限流闸门的时钟 / 睡眠替身（测试用虚拟时钟，避免真的等 0.05 s）。

    Returns:
        `{id: name}`，**只含 ESI 真的给了名字的那些 id**（查不到就不在字典里 ——
        调用方自己决定给 `null` 还是用别的来源，这里不编）。

    为什么**永久缓存**而不是 TTL：星系名 / 驾驶员名的失效条件只有「改名 / 删号」，
    而战报分析是批量读同一批实体 —— TTL 只会让「同一批战报的第 N 条」再付一次网络费，
    正确性一点都不多。**未命中（`None`）也进缓存**：删号 / NPC 的 id 不能每轮再问一次。
    进程重启即失效，`clear_caches()` 也会一并清空（换环境 / 测试用）。

    多批时的节奏：每批之间至少隔 `UNIVERSE_NAMES_MIN_INTERVAL_S`（0.05 s）。
    """
    wanted: list[int] = []
    for value in ids or []:
        candidate = _as_int(value)
        if candidate is not None and candidate not in wanted:
            wanted.append(candidate)
    if not wanted:
        return {}

    fetch = requester or _post_json
    result: dict[int, str] = {}
    missing: list[int] = []
    with _UNIVERSE_LOCK:
        for type_id in wanted:
            if type_id in _UNIVERSE_NAMES:
                name = _UNIVERSE_NAMES[type_id]
                if name is not None:
                    result[type_id] = name
            else:
                missing.append(type_id)

    for start in range(0, len(missing), UNIVERSE_NAMES_MAX_IDS):
        chunk = missing[start : start + UNIVERSE_NAMES_MAX_IDS]
        _universe_names_gate(clock=clock, sleeper=sleeper)
        payload = fetch(ESI_UNIVERSE_NAMES_API, chunk)
        entries = payload if isinstance(payload, list) else []
        found: dict[int, str] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = _as_int(entry.get("id"))
            name = entry.get("name")
            if entry_id is not None and isinstance(name, str) and name:
                found[entry_id] = name
        with _UNIVERSE_LOCK:
            for type_id in chunk:
                _UNIVERSE_NAMES[type_id] = found.get(type_id)
        result.update({key: value for key, value in found.items() if key in chunk})
    return result


def universe_system(system_id: int) -> dict:
    """单个星系详情（`name` / `security_status` / `security_class`），**进程内永久缓存**。

    Returns:
        `{"name": str|None, "security_status": float|None, "security_class": ...,
        "has_class": bool}`。`has_class` 用来区分「ESI 给了 `security_class: null`」与
        「这个老版本响应里根本没有这个键」—— 前者是**虫洞空间的判据**（实测 Thera /
        J220438 / Sentinel MZ 都是 `security_class: null` + `security_status: -0.99`，
        而 k 空间一律给 `'B'` / `'H6'` / `'E'` 这样的类名）。

    Raises:
        _ApiFailure: kind=`network_failed`（调用方决定降级还是整条失败）。
    """
    key = int(system_id)
    with _UNIVERSE_LOCK:
        cached = _UNIVERSE_SYSTEMS.get(key)
    if cached is not None:
        return dict(cached)

    url = ESI_UNIVERSE_SYSTEM_API.format(system_id=key)
    payload = _http_json(url)
    data = payload if isinstance(payload, dict) else {}
    name = data.get("name")
    block = {
        "name": name if isinstance(name, str) and name else None,
        "security_status": _as_float(data.get("security_status")),
        "security_class": data.get("security_class"),
        "has_class": "security_class" in data,
    }
    with _UNIVERSE_LOCK:
        _UNIVERSE_SYSTEMS[key] = dict(block)
    return block


def _security_band(
    status: float | None, security_class: object, has_class: bool
) -> str | None:
    """`security_status` → `highsec` / `lowsec` / `nullsec` / `wormhole`（拿不到给 `None`）。

    虫洞**只按 ESI 的 `security_class` 判**：k 空间每个星系都有类名，w 空间是 `null`
    （实测 Thera / J220438 / Sentinel MZ）。没有这个键（老响应）时不认定虫洞，退回数值
    规则 —— 宁可按数值说 nullsec，也不凭 id 段猜。
    """
    if status is None:
        return None
    if has_class and security_class is None:
        return SECURITY_BAND_WORMHOLE
    if status >= HIGHSEC_THRESHOLD:
        return SECURITY_BAND_HIGHSEC
    if status > LOWSEC_THRESHOLD:
        return SECURITY_BAND_LOWSEC
    return SECURITY_BAND_NULLSEC


def _system_block(
    system_id: object,
    warnings: list[str],
    names: dict[int, str],
    *,
    prefetched: "dict | None" = None,
    prefetch_error: str | None = None,
) -> dict:
    """`solar_system_id` → `{id, name, security: {status, band}}`（查不到给 `null` + `warnings`）。

    名字**不再单独发一次 names 请求**：`names` 是调用方一次批量查好的 `{id: name}`
    （星系 id 一起进了那一批），这里只取。取不到就退回 ESI 星系详情自带的 `name`。

    `prefetched` / `prefetch_error` 是**并行取数的结果**：`_execute_killmail()` 会把
    `universe_system()` 放到工作线程里与 `names` 同时跑（两者互不依赖），再把结果递进来。
    没给就走原来的同步路径 —— 调用点不必关系并行与否。

    Args:
        prefetched: 线程里已经取到的星系详情（`None` = 没预取）。
        prefetch_error: 预取失败时的人话原因（与同步路径的 warning 文案一致）。
    """
    identifier = _as_int(system_id)
    if identifier is None:
        warnings.append("killmail 没有 solar_system_id：星系名与安全等级给 null（不猜）")
        return {"id": _as_int(system_id), "name": None, "security": {"status": None, "band": None}}

    name: str | None = names.get(identifier)
    info: dict = {
        "name": None,
        "security_status": None,
        "security_class": None,
        "has_class": False,
    }
    if prefetched is not None:
        info = dict(prefetched)
    elif prefetch_error is not None:
        warnings.append(f"星系安全等级查不到（{prefetch_error}）：security 给 null")
    else:
        try:
            info = universe_system(identifier)
        except _ApiFailure as exc:
            warnings.append(f"星系安全等级查不到（{exc.message}）：security 给 null")

    if name is None:
        # 同一个 ESI 的星系详情也带 name（权威来源相同），不额外多花一次请求。
        name = info.get("name")
    if name is None:
        warnings.append("星系名查不到：solar_system.name 给 null（不猜）")
    status = _as_float(info.get("security_status"))
    return {
        "id": identifier,
        "name": name,
        "security": {
            "status": status,
            "band": _security_band(status, info.get("security_class"), bool(info.get("has_class"))),
        },
    }


def _zkb_entry(payload: object) -> dict:
    """zkill 的 `killID` 响应 → 单条战报（响应可能是列表，也可能是单条对象）。"""
    entries = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        if isinstance(entry, dict):
            return entry
    return {}


def fetch_killmail_report(
    killmail_id: int, digest: str | None
) -> tuple[dict, str]:
    """取一条战报的**原文** → `(killmail, origin)`，`origin` ∈ `zkillboard` / `esi`。

    数据源策略（**实测依据见 `ZKB_COMPLETE_KILLMAIL_NOTE`**）：

    1. 先 `GET zkillboard /api/killID/<id>/` —— 这一步本来就要做：它给 `zkb`（价值 /
       points / labels）与 `hash`，**而且它返回的就是整条 killmail**（attackers / victim /
       items 全在，16/16 样本与 ESI 逐字段一致）；
    2. 再看 `victim.items`，**按「键在不在」而不是「真不真」分三种**：

       | `victim.items` | 处理 | `origin` |
       |---|---|---|
       | **键不存在** | ❌ 可疑（可能是 zkb 版本 / 裁剪差异）→ **回落 ESI** | `esi` |
       | **键在但为 `[]`** | ✅ 短路，不请求 ESI（这条战报本来就没模块） | `zkillboard` |
       | **键在且非空** | ✅ 短路 | `zkillboard` |

    为什么「空数组」可以短路而「键不存在」不行：取证里那 3 条 items 为空的样本，在
    **zkb 与 ESI 里都是空**（蛋 / 空船）—— 空数组是**事实**；而键整个不见，说明这份响应
    可能不是完整的那一版，**拿不准就回落**，宁可多花一次时间也不猜。

    **可见性**：空数组短路时调用方会补一条 `ZKB_EMPTY_ITEMS_WARNING`，免得 agent 以为
    我们漏了模块数据。

    Args:
        killmail_id: 战报 id。
        digest: ESI 要求的 hash；`None` 时向 zkb 要。

    Returns:
        `(合并后的 killmail, 来源)`。`zkb` 块会原样附在文档里（ESI 路径也附）。

    Raises:
        _ApiFailure: kind=`network_failed`（任一步失败都**不**拿半成品继续算）。
    """
    zkb_url = ZKILL_KILL_API.format(killmail_id=killmail_id)
    payload = _http_json(zkb_url)
    entry = _zkb_entry(payload)
    zkb_block = entry.get("zkb")

    victim = entry.get("victim") if isinstance(entry.get("victim"), dict) else {}
    items = victim.get("items")
    # **键存在性**判定，不是真假判定：`[]` 是「这条战报本来没模块」（事实，可短路），
    # 而键整个不见是「这份响应可能不完整」（可疑，回落）。用 `if items:` 会把 `[]` 也
    # 判成可疑，等于白白多花一次 1.7–2.6 s 的 ESI 请求。
    if isinstance(items, list):
        merged = dict(entry)
        if isinstance(zkb_block, dict):
            merged["zkb"] = zkb_block
        return merged, KILLMAIL_ORIGIN_ZKILLBOARD

    if not digest:
        digest = _hash_from_zkill(payload)
        if not digest:
            raise _ApiFailure(
                "network_failed",
                f"zkillboard 没有给出 killmail {killmail_id} 的 hash，无法向 ESI 取原文",
                url=zkb_url,
            )

    esi_url = ESI_KILLMAIL_API.format(killmail_id=killmail_id, hash=digest)
    killmail = _http_json(esi_url)
    if not isinstance(killmail, dict) or not isinstance(killmail.get("victim"), dict):
        raise _ApiFailure(
            "network_failed",
            f"ESI 返回的 killmail {killmail_id} 里没有 victim 块",
            url=esi_url,
        )

    merged = dict(killmail)
    if isinstance(zkb_block, dict):
        merged["zkb"] = zkb_block
    return merged, KILLMAIL_ORIGIN_ESI


# ---- 十二之二：战报块的组装（只整理，不判断）--------------------------------


def _type_block(type_id: object, index: Index | None, *, locale: str = LOCALE_EN) -> dict:
    """`type_id` → `{type_id, name, group}`（名字 / 组名走本地 SDE；查不到给 `null`）。

    `locale="zh"` 时 `name` 换成官方中文，并多一个 `name_en` 便于核对。
    """
    identifier = _as_int(type_id)
    row = None
    if identifier is not None and index is not None:
        row = index.get_type(identifier)
    name, name_en = _localized_type_name(row, locale)
    block = {
        "type_id": _as_int(type_id),
        "name": name,
        "group": (row or {}).get("group_name"),
    }
    if locale == LOCALE_ZH:
        block["name_en"] = name_en
    return block


# ---- 十二之一半：身份（驾驶员 / 军团 / 联盟）的名字解析 ------------------------

_ENTITY_KEYS: tuple[tuple[str, str], ...] = (
    ("character", "character_id"),
    ("corporation", "corporation_id"),
    ("alliance", "alliance_id"),
)
"""战报里的三种身份：输出键 ←→ killmail 原始键（顺序固定，批量去重时可预期）。"""

UNRESOLVED_NAMES_WARNING: str = (
    "{count} 个实体名未解析（ESI `/universe/names/` 没给名字：删号 / NPC / 已迁移）："
    "这些 name 给 null"
)
"""名字查不到时**唯一**该说的话（`null` 就是 `null`，不编一个占位名顶上去）。"""


def _id_name_block(identifier: object, names: dict[int, str]) -> dict | None:
    """一个实体 id → `{"id", "name"}`。

    **id 缺失 → 整个对象 `null`**（没有联盟就是 `null`，不是 `{"id": null, "name": null}`）；
    id 有但 ESI 没给名字 → `name: null`（查不到就老实说查不到）。
    """
    value = _as_int(identifier)
    if value is None:
        return None
    return {"id": value, "name": names.get(value)}


def _identity_blocks(block: dict, names: dict[int, str]) -> dict:
    """`character_id` / `corporation_id` / `alliance_id` → `{character, corporation, alliance}`。"""
    return {key: _id_name_block(block.get(raw), names) for key, raw in _ENTITY_KEYS}


def _identity_ids(blocks: "list[object]") -> list[int]:
    """若干身份块里所有**存在的** id（去重、保序：一次 `POST /universe/names/` 查完）。"""
    ids: list[int] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for _key, raw in _ENTITY_KEYS:
            value = _as_int(block.get(raw))
            if value is not None and value not in ids:
                ids.append(value)
    return ids


def _resolve_identity_names(
    ids: "list[int]",
    warnings: list[str],
    *,
    requester: UniverseNamesRequester | None = None,
) -> dict[int, str]:
    """一批 id → `{id: name}`（**一次批量请求**；失败 / 缺项都降级成 `null` + `warnings`）。

    这里的取舍：名字是**附加信息**，拿不到不该让整条战报失败 —— 所以网络失败只记一条
    warning，实体块的 `name` 全是 `null`。但**沉默不行**：缺了几个必须说出来。
    """
    if not ids:
        return {}
    try:
        names = (
            universe_names(ids)
            if requester is None
            else universe_names(ids, requester=requester)
        )
    except _ApiFailure as exc:
        warnings.append(
            f"{len(ids)} 个实体名未解析（{exc.message}）：character / corporation / alliance "
            "的 name 给 null"
        )
        return {}
    unresolved = sum(1 for identifier in ids if identifier not in names)
    if unresolved:
        warnings.append(UNRESOLVED_NAMES_WARNING.format(count=unresolved))
    return names


def _fill_identity_names(block: object, names: dict[int, str]) -> None:
    """就地把身份块里的 `name` 填上（`id` 为 `null` 时保持 `null`，不编）。"""
    if not isinstance(block, dict):
        return
    for key, _raw in _ENTITY_KEYS:
        entry = block.get(key)
        if isinstance(entry, dict):
            identifier = _as_int(entry.get("id"))
            entry["name"] = None if identifier is None else names.get(identifier)


def _killmail_name_ids(killmail: dict) -> list[int]:
    """一条战报里所有要查名字的 id：**星系 + 受害者 + 全部攻击者**（一次查完，不逐条查）。

    攻击者通常有几十个，但 `character / corporation / alliance` 高度重复（同一支舰队就是
    同一批军团），去重后一条战报常常只剩十几个 —— 所以「一次 fit_killmail = 一次请求」。
    """
    victim = killmail.get("victim") if isinstance(killmail.get("victim"), dict) else {}
    attackers = [
        attacker
        for attacker in (killmail.get("attackers") or [])
        if isinstance(attacker, dict)
    ]
    ids = _identity_ids([victim, *attackers])
    system = _as_int(killmail.get("solar_system_id"))
    if system is not None and system not in ids:
        ids.insert(0, system)
    return ids


def weapon_family(type_id: object, index: Index | None) -> str:
    """`weapon_type_id` → 武器族（`WEAPON_FAMILIES` 之一，分不清给 `other`）。

    只用 SDE 里的**名字 + 组名**做启发式（`_WEAPON_FAMILY_KEYWORDS`）：命中不了就是
    `other`。这里**不猜**——ESI 常把 `weapon_type_id` 填成攻击者自己的船（如 `642`
    Apocalypse），那种情况老实给 `other` 比硬塞一个族更有用。
    """
    identifier = _as_int(type_id)
    if identifier is None or index is None:
        return WEAPON_FAMILY_OTHER
    row = index.get_type(identifier)
    if not isinstance(row, dict):
        return WEAPON_FAMILY_OTHER
    haystack = " ".join(
        str(row.get(field) or "") for field in ("name", "group_name")
    ).lower()
    if not haystack.strip():
        return WEAPON_FAMILY_OTHER
    for family, keywords in _WEAPON_FAMILY_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return family
    return WEAPON_FAMILY_OTHER


def _buckets(
    attackers: list[dict],
    key_of: Callable[[dict], object],
    key_name: str,
    damage_total: int | None,
    row_extra: Callable[[object], dict] | None = None,
) -> list[dict]:
    """按 `key_of` 分桶 → `[{<key_name>, count, damage, damage_pct}]`（按伤害降序）。

    `row_extra` 给每一行补字段（入参是**桶键**，所以同一桶的补法必然一致）；不传就不补。
    """
    grouped: dict[object, dict] = {}
    for attacker in attackers:
        key = key_of(attacker)
        bucket = grouped.setdefault(key, {"count": 0, "damage": 0})
        bucket["count"] += 1
        bucket["damage"] += _as_int(attacker.get("damage_done")) or 0
    rows = [
        {
            key_name: key,
            "count": bucket["count"],
            "damage": bucket["damage"],
            "damage_pct": _share(bucket["damage"], damage_total),
            **(row_extra(key) if row_extra is not None else {}),
        }
        for key, bucket in grouped.items()
    ]
    rows.sort(key=lambda row: (-row["damage"], str(row[key_name] or "")))
    return rows


def _ship_attribution(key: object, capsule_groups: "set[object]") -> str:
    """`by_ship_group` 的一行是不是「蛋」桶（**船归属不可信**）。

    `key` 是组名：正常情况下 SDE 给的就是 `CAPSULE`（`Capsule`）；`capsule_groups` 是
    「由 `ship_type_id == 670` 反查出来的组名」兜底（SDE 索引不可用时组名全是 `None`，
    这时**只有**组名真的为 `None` 的桶才可能命中，不会波及有名字的桶）。
    """
    if key == CAPSULE_GROUP or (key is not None and key in capsule_groups):
        return SHIP_ATTRIBUTION_CAPSULE
    return SHIP_ATTRIBUTION_NORMAL


def _attacker_counts(attackers: list[dict]) -> dict:
    """`attackers` 里的四个计数（都是**人数**，不是伤害）。

    - `count`：攻击者条目数（= 挂名人数）；
    - `damaging_count`：`damage_done > 0` 的人数（**真的打出过伤害**的人）；
    - `zero_damage_count`：`damage_done == 0` 的人数（挂名没输出；实测 139239127 有 22 个）；
    - `capsule_count`：`ship_type_id == 670` 的人数（**不管伤害是否为 0**）。

    `damage_done` 缺失按 0 计（与 `damage_total` 同一口径）。ESI 的 `damage_done` 非负，
    所以 `damaging_count + zero_damage_count == count`；万一来个负值，它既不算「有伤害」也
    不算「零伤害」，等式会**露馅**而不是被吞掉（不静默改数据）。
    """
    damages = [_as_int(attacker.get("damage_done")) or 0 for attacker in attackers]
    return {
        "count": len(attackers),
        "damaging_count": sum(1 for damage in damages if damage > 0),
        "zero_damage_count": sum(1 for damage in damages if damage == 0),
        "capsule_count": sum(
            1
            for attacker in attackers
            if _as_int(attacker.get("ship_type_id")) == CAPSULE_TYPE_ID
        ),
    }


BY_PILOT_ROWS: int = 10
"""`attackers.by_pilot` 只给伤害最高的前 N 名（完整名单在原始 killmail 里，不值得刷屏）。"""


def _pilot_buckets(
    attackers: list[dict],
    damage_total: int | None,
    index: Index | None,
    names: dict[int, str],
    *,
    locale: str = LOCALE_EN,
) -> list[dict]:
    """按**驾驶员**聚合 → `[{character, corporation, alliance, ship, damage, …}]`（伤害降序前 10）。

    一个驾驶员在同一条战报里通常只有一条 attacker 记录，但模板化舰队战出现过同一角色
    多条目 —— 所以按 `character_id` 合并（合并的是**同一个人的伤害**，不是不同的人）。
    `character_id` 缺失时合并键是 `None`：那一 bucket 的 `character` 给 `null`，不编。

    `recorded_ship_is_capsule` 描述的是**这一行打出来的 `ship` 块**（`type_id == 670`）：
    蛋装不了武器，所以这种行的伤害来自该玩家已被摧毁的船 —— 船归属不可信，但**伤害不扣**。
    合并多条记录时 `ship` 取第一条，标记跟着同一个 `ship` 走（永远与显示的那块一致）。
    """
    grouped: dict[object, dict] = {}
    for attacker in attackers:
        key = _as_int(attacker.get("character_id"))
        bucket = grouped.get(key)
        if bucket is None:
            bucket = grouped[key] = {
                "identity": _identity_blocks(attacker, names),
                "ship": _type_block(attacker.get("ship_type_id"), index, locale=locale),
                "damage": 0,
                "final_blow": None,
                "security_status": None,
            }
        bucket["damage"] += _as_int(attacker.get("damage_done")) or 0
        flag = _as_bool(attacker.get("final_blow"))
        if flag is True:
            bucket["final_blow"] = True
        elif bucket["final_blow"] is None:
            bucket["final_blow"] = flag
        if bucket["security_status"] is None:
            bucket["security_status"] = _as_float(attacker.get("security_status"))

    rows = [
        {
            "character": bucket["identity"]["character"],
            "corporation": bucket["identity"]["corporation"],
            "alliance": bucket["identity"]["alliance"],
            "ship": bucket["ship"],
            "recorded_ship_is_capsule": bucket["ship"].get("type_id") == CAPSULE_TYPE_ID,
            "damage": bucket["damage"],
            "damage_pct": _share(bucket["damage"], damage_total),
            "final_blow": bucket["final_blow"],
            "security_status": bucket["security_status"],
        }
        for bucket in grouped.values()
    ]
    rows.sort(
        key=lambda row: (-row["damage"], str((row["character"] or {}).get("name") or ""))
    )
    return rows[:BY_PILOT_ROWS]


def _attacker_block(
    attackers: list[dict],
    victim_damage: int | None,
    index: Index | None,
    names: dict[int, str],
    *,
    locale: str = LOCALE_EN,
) -> dict:
    """`attackers` 块：人数 / 有伤害人数 / 零伤害人数 / 蛋人数 / 总伤害 / 校验 / 三个分桶。"""
    damage_total = sum(_as_int(attacker.get("damage_done")) or 0 for attacker in attackers)
    counts = _attacker_counts(attackers)
    if victim_damage is None:
        damage_check: str | None = None
        damage_diff: int | None = None
    elif damage_total == victim_damage:
        damage_check = DAMAGE_CHECK_MATCH
        damage_diff = None
    else:
        damage_check = DAMAGE_CHECK_MISMATCH
        damage_diff = damage_total - victim_damage

    final = next(
        (attacker for attacker in attackers if attacker.get("final_blow") is True), None
    )
    final_block: dict | None = None
    if final is not None:
        final_block = {
            "character": _id_name_block(final.get("character_id"), names),
            "corporation": _id_name_block(final.get("corporation_id"), names),
            "alliance": _id_name_block(final.get("alliance_id"), names),
            "ship": _type_block(final.get("ship_type_id"), index, locale=locale),
            "weapon": _type_block(final.get("weapon_type_id"), index, locale=locale),
            "security_status": _as_float(final.get("security_status")),
        }

    statuses = [
        status
        for status in (_as_float(attacker.get("security_status")) for attacker in attackers)
        if status is not None
    ]

    capsule_groups = {
        _type_block(attacker.get("ship_type_id"), index)["group"]
        for attacker in attackers
        if _as_int(attacker.get("ship_type_id")) == CAPSULE_TYPE_ID
    }
    block = {
        **counts,
        "damage_total": damage_total,
        "damage_check": damage_check,
        "final_blow": final_block,
        "by_pilot": _pilot_buckets(attackers, damage_total, index, names, locale=locale),
        "by_ship_group": _buckets(
            attackers,
            lambda a: _type_block(a.get("ship_type_id"), index)["group"],
            "group",
            damage_total,
            row_extra=lambda key: {
                "ship_attribution": _ship_attribution(key, capsule_groups)
            },
        ),
        "by_weapon_family": _buckets(
            attackers, lambda a: weapon_family(a.get("weapon_type_id"), index), "family", damage_total
        ),
        "security": {
            "min": min(statuses) if statuses else None,
            "max": max(statuses) if statuses else None,
            "criminal_count": sum(
                1 for status in statuses if status <= CRIMINAL_SECURITY_THRESHOLD
            ),
        },
    }
    if damage_diff is not None:
        block["damage_diff"] = damage_diff
    return block


def _killmail_block(killmail: dict, system: dict) -> dict:
    """`killmail` 块（价值 / flags / labels 全部来自 `zkb`，没有就是 `null`）。"""
    zkb = killmail.get("zkb") if isinstance(killmail.get("zkb"), dict) else None
    value: dict[str, float | None] = {
        "fitted": None,
        "dropped": None,
        "destroyed": None,
        "total": None,
    }
    flags: dict[str, object] = {"npc": None, "solo": None, "awox": None, "points": None}
    labels: list | None = None
    if zkb is not None:
        value = {
            "fitted": _as_float(zkb.get("fittedValue")),
            "dropped": _as_float(zkb.get("droppedValue")),
            "destroyed": _as_float(zkb.get("destroyedValue")),
            "total": _as_float(zkb.get("totalValue")),
        }
        flags = {
            "npc": _as_bool(zkb.get("npc")),
            "solo": _as_bool(zkb.get("solo")),
            "awox": _as_bool(zkb.get("awox")),
            "points": _as_int(zkb.get("points")),
        }
        raw_labels = zkb.get("labels")
        if isinstance(raw_labels, list):
            labels = [label for label in raw_labels if isinstance(label, str)]
    return {
        "killmail_id": _as_int(killmail.get("killmail_id")),
        "killmail_time": (
            killmail.get("killmail_time")
            if isinstance(killmail.get("killmail_time"), str)
            else None
        ),
        "solar_system": {"id": system["id"], "name": system["name"]},
        "security": system["security"],
        "value": value,
        "flags": flags,
        "labels": labels,
    }


def _ehp_context(ctx: ViewContext, damage_taken: int | None) -> dict:
    """`context` 块：`{damage_taken_vs_ehp: {...}, note}`（EHP **只读**投影层，本层只做一次除法）。"""
    answer = render.get_path(ctx, KILLMAIL_EHP_PATH)
    ehp = _as_float(answer.get("value"))
    ratio = None
    if damage_taken is not None and ehp:
        ratio = round(damage_taken / ehp, _RATIO_DIGITS)
    return {
        "damage_taken_vs_ehp": {
            "damage_taken": damage_taken,
            "ehp_mixed": ehp,
            "ratio": ratio,
            "ehp_provenance": answer.get("provenance"),
        },
        "note": KILLMAIL_EHP_NOTE,
    }


def _killmail_report(
    killmail: dict,
    ctx: ViewContext,
    killmail_source: str,
    warnings: list[str],
    names: dict[int, str],
    *,
    killmail_origin: str = KILLMAIL_ORIGIN_LOCAL,
    system_prefetched: "dict | None" = None,
    system_error: str | None = None,
    locale: str = LOCALE_EN,
) -> dict:
    """原始 killmail + `ViewContext` + 批量查好的 `names` → `result`（形状见 `fit_killmail`）。

    `killmail_source`（`network` / `local`）说的是**输入形态**，`killmail_origin`
    （`zkillboard` / `esi` / `local`）说的是**原文从哪来** —— 两者维度不同，别混。
    """
    index = ctx.index
    victim = killmail.get("victim") if isinstance(killmail.get("victim"), dict) else {}
    if not isinstance(killmail.get("zkb"), dict):
        warnings.append("没有 zkb 价值块：value / flags / labels 给 null（不猜）")

    system = _system_block(
        killmail.get("solar_system_id"),
        warnings,
        names,
        prefetched=system_prefetched,
        prefetch_error=system_error,
    )
    victim_damage = _as_int(victim.get("damage_taken"))
    if victim_damage is None:
        warnings.append("killmail 没有 victim.damage_taken：damage_check 给 null（不猜）")
    attackers = [
        attacker
        for attacker in (killmail.get("attackers") or [])
        if isinstance(attacker, dict)
    ]
    untracked = sum(
        1
        for attacker in attackers
        if attacker.get("weapon_type_id") is not None
        and attacker.get("weapon_type_id") == attacker.get("ship_type_id")
    )
    if untracked:
        warnings.append(
            f"{untracked} 个攻击者的 weapon_type_id 与自己的 ship_type_id 相同"
            "（ESI 没有记录武器）：这些按 other 计入 by_weapon_family"
        )
    attacker_block = _attacker_block(attackers, victim_damage, index, names, locale=locale)
    if attacker_block["capsule_count"]:
        warnings.append(
            CAPSULE_ATTACKER_WARNING.format(count=attacker_block["capsule_count"])
        )
    return {
        "killmail": _killmail_block(killmail, system),
        "victim": {
            "damage_taken": victim_damage,
            "ship": _type_block(victim.get("ship_type_id"), index, locale=locale),
            **_identity_blocks(victim, names),
        },
        "attackers": attacker_block,
        "context": _ehp_context(ctx, victim_damage),
        "killmail_source": killmail_source,
        "killmail_origin": killmail_origin,
        "warnings": warnings,
    }


# ---- 十二之三：输入判别（必须是战报）----------------------------------------


def _killmail_descriptor(fit: object) -> tuple[str, str | None, dict]:
    """输入 → `(形态, 缓存 key, 载荷)`；比 `_input_descriptor()` 多认一种网络形态。

    多认的那一种：`{"killmail_id": N}`（**没有** hash）。它不是「本地内容」（没有
    `victim` 就不是一份完整 killmail），hash 可以向 zkillboard 要 —— 所以直接按网络
    输入处理，而不是判成 `not_a_killmail`。
    """
    if (
        isinstance(fit, dict)
        and "victim" not in fit
        and KILLMAIL_ID_FIELD in fit
    ):
        key, params = _network_key(fit)
        return INPUT_NETWORK, key, params
    return _input_descriptor(fit)


def _killmail_from_text(text: str) -> dict:
    """本地输入文本 → killmail 对象；不是战报就 `not_a_killmail`（**不猜**）。

    EFT / DNA / structured fit / ESI fitting 都在这里被挡住：它们没有 `attackers` /
    `zkb` / `damage_taken`，硬拿配装凑一条战报是编造。
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise _ApiFailure(
            NOT_A_KILLMAIL_KIND,
            "这段输入没有战报来源（不是 killmail JSON，也不带攻击方 / 价值块）",
            hint=[
                "killmail JSON（zkillboard 或 ESI 的原文）",
                '{"killmail_id": N, "hash": "..."}',
                "https://zkillboard.com/kill/<id>/ 或 ESI 的 /killmails/<id>/<hash>/ 链接",
            ],
            raw=text.strip()[:200],
        ) from None

    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if isinstance(data, dict) and isinstance(data.get("victim"), dict):
        return data
    raise _ApiFailure(
        NOT_A_KILLMAIL_KIND,
        "这段输入里没有 victim 块，不是一条 killmail（EFT / DNA / 结构化配装都没有战报信息）",
        hint=[
            "看配装用 fit_parse / fit_index",
            "要战报上下文就给 killmail JSON、{killmail_id, hash} 或 killmail 链接",
        ],
        raw=text.strip()[:200],
    )


def _prefetch_system_info(system_id: object) -> dict:
    """取星系详情，**把两种结局都收成返回值**（供工作线程用，绝不抛到线程外）。

    Returns:
        `{"info": dict | None, "error": str | None}`。`id` 不是整数时两者都是 `None`
        （`_system_block()` 会自己给 warning）。

    为什么要在工作线程里跑：它与 `POST /universe/names/` **互不依赖**（一个要 system_id、
    一个要 id 集合），串行时白等 0.66–0.71 s。但**只有它**进线程 —— names 有批间闸门
    （`_UNIVERSE_NAMES_MIN_INTERVAL_S`）与进程内缓存字典，留在主线程最省心（见
    `_execute_killmail` 的注释）。
    """
    identifier = _as_int(system_id)
    if identifier is None:
        return {"info": None, "error": None}
    try:
        return {"info": universe_system(identifier), "error": None}
    except _ApiFailure as exc:
        return {"info": None, "error": exc.message}
    except Exception as exc:  # noqa: BLE001 - 线程里绝不能裸抛，否则会掩盖 names 的结果
        return {"info": None, "error": f"{type(exc).__name__}: {exc}"}


def _execute_killmail(
    fit: object,
    *,
    skill_tier: object,
    damage_profile: object,
    state_policy: object,
    locale: str = LOCALE_EN,
) -> dict:
    """`fit_killmail` 的执行骨架（与 `_execute()` 同构，只是载荷是**原始 killmail**）。

    冷路径的两次「互不依赖」请求在这里并行：`universe_system()` 进工作线程，`names`
    留在主线程，join 之后再装配（`_system_block()` 要用 names 给星系名）。

    Raises: 无 —— 任何异常都变成失败信封（§10.4）。
    """
    try:
        _check_params(skill_tier, damage_profile, state_policy)
        locale = _check_locale(locale)

        kind, content_key, payload = _killmail_descriptor(fit)
        cache_key = (
            None
            if content_key is None
            else (KILLMAIL_OP, (locale,), content_key, skill_tier, damage_profile, state_policy)
        )
        if cache_key is not None:
            hit = _cache_get(cache_key)
            if hit is not None:
                return hit

        warnings: list[str] = []
        if kind == INPUT_NETWORK:
            killmail, killmail_origin = fetch_killmail_report(
                payload["killmail_id"], payload["hash"]
            )
            killmail_source = "network"
            if killmail_origin == KILLMAIL_ORIGIN_ESI:
                # 多花了一次 ESI 请求就要交代（沉默不行）。
                warnings.append(ZKB_ITEMS_FALLBACK_WARNING)
            else:
                # 短路来自 zkb：若 items 是空数组，**这是事实**（这条战报本来没模块），
                # 但返回里与「我们漏了数据」长得一模一样，必须说出来。
                short_items = (
                    (killmail.get("victim") or {}).get("items")
                    if isinstance(killmail.get("victim"), dict)
                    else None
                )
                if isinstance(short_items, list) and not short_items:
                    warnings.append(ZKB_EMPTY_ITEMS_WARNING)
        else:
            killmail = _killmail_from_text(payload["text"])
            killmail_source = "local"
            killmail_origin = KILLMAIL_ORIGIN_LOCAL

        # 索引先就位：船的 type/group 与武器族的判据都来自它（`_context()` 也要它）。
        _ensure_index()
        parsed = _parsed_fit({"text": json.dumps(killmail, ensure_ascii=False)})
        ctx = _context(parsed["fit"], str(skill_tier))

        # 冷路径最后两步**互不依赖**：names 要的是 id 集合，systems 要的是 system_id。
        # 把 systems 丢进工作线程、names 留在主线程，join 后再装配（systems 的星系名
        # 要用 names 的结果，所以「装配」不能并行，只能「取数」并行）。
        #
        # 为什么 names 不进线程：它有批间闸门（`_UNIVERSE_NAMES_MIN_INTERVAL_S`，靠模块级
        # `_UNIVERSE_NAMES_LAST_START` 记账）与进程内缓存字典。放进线程虽不坏（缓存有锁），
        # 但闸门的「与上一次请求至少隔 0.05 s」在并发下就不再是全局串行了 —— 为了省这点
        # 复杂度，让最娇的那条留在主线程。
        system_prefetch: dict = {"info": None, "error": None}
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="noobbaozi-sys") as pool:
            pending = pool.submit(_prefetch_system_info, killmail.get("solar_system_id"))
            # 名字**一次查完**：星系 + 受害者 + 全部攻击者（去重后一批 names 请求）。
            names = _resolve_identity_names(_killmail_name_ids(killmail), warnings)
            system_prefetch = pending.result()

        result = _killmail_report(
            killmail,
            ctx,
            killmail_source,
            warnings,
            names,
            killmail_origin=killmail_origin,
            system_prefetched=system_prefetch["info"],
            system_error=system_prefetch["error"],
            locale=locale,
        )
        meta = _meta(
            ctx,
            source_format=str(parsed.get("source_format")),
            damage_profile=str(damage_profile),
            state_policy=str(state_policy),
            result=result,
        )
        meta["killmail_source"] = killmail_source
        meta["killmail_origin"] = killmail_origin
        envelope = _success(result, meta)
    except _ApiFailure as exc:
        return _failure(exc.payload())
    except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得裸抛给 agent
        return _failure(
            {
                "kind": "internal_error",
                "message": "接口层内部异常，本次没有可用结果",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )

    if cache_key is not None:
        _cache_put(cache_key, envelope)
    return envelope


def fit_killmail(
    fit: object,
    *,
    skill_tier: str = SKILL_TIER_ALL4,
    damage_profile: str = DAMAGE_PROFILE_UNIFORM,
    state_policy: str = STATE_POLICY_ENGINE_DEFAULT,
    locale: object = LOCALE_EN,
) -> dict:
    """**战报上下文**：谁打的、损失多少、挨了多少伤害（一项都不是配装数值的复述）。

    输入与其它入口一致（killmail JSON / `{"killmail_id", "hash"}` / zkillboard URL），
    但**只接受战报**：EFT / DNA / structured fit 这类没有战报来源的输入返回
    `{"ok": false, "error": {"kind": "not_a_killmail", ...}}`（诚实报错，不猜）。

    Args:
        fit: 四形态输入，见模块 docstring；其中三形态必须是 killmail。
        skill_tier: 同其它入口（只影响 `context.ehp_mixed`）。
        damage_profile: 只支持 `uniform`。
        state_policy: 只支持 `engine_default`。
        locale: `en`（默认）/ `zh`。`zh` 时**所有 SDE 类型名**（`victim.ship`、
            `attackers.final_blow.ship` / `.weapon`、`attackers.by_pilot[*].ship`）
            给官方中文并多一个 `name_en`；**玩家 / 军团 / 联盟名永远不译**
            （`_id_name_block` 走的是 ESI 名字，不是 SDE 类型）。

    Returns:
        成功时 `result` 是（形状固定）：

        ```json
        {"killmail": {"killmail_id", "killmail_time",
                      "solar_system": {"id", "name"},
                      "security": {"status", "band"},
                      "value": {"fitted", "dropped", "destroyed", "total"},
                      "flags": {"npc", "solo", "awox", "points"},
                      "labels": [...]},
         "victim": {"damage_taken",
                    "ship": {"type_id", "name", "group"},
                    "character": {"id", "name"},
                    "corporation": {"id", "name"},
                    "alliance": {"id", "name"}},
         "attackers": {"count", "damaging_count", "zero_damage_count", "capsule_count",
                       "damage_total", "damage_check",
                       "final_blow": {"character": {"id", "name"},
                                      "corporation": {...}, "alliance": {...},
                                      "ship": {...}, "weapon": {...},
                                      "security_status"},
                       "by_pilot": [{"character", "corporation", "alliance", "ship",
                                     "recorded_ship_is_capsule",
                                     "damage", "damage_pct", "final_blow",
                                     "security_status"}],
                       "by_ship_group": [{"group", "count", "damage", "damage_pct",
                                          "ship_attribution"}],
                       "by_weapon_family": [{"family", "count", "damage", "damage_pct"}],
                       "security": {"min", "max", "criminal_count"}},
         "context": {"damage_taken_vs_ehp": {"damage_taken", "ehp_mixed", "ratio",
                                            "ehp_provenance"},
                     "note"},
         "killmail_source": "network" | "local",
         "killmail_origin": "zkillboard" | "esi" | "local",
         "warnings": [...]}
        ```

    `killmail_source` 与 `killmail_origin` **是两个维度**，别混：

    - `killmail_source`：**输入形态** —— `network`（链接 / id / id+hash，要联网）或
      `local`（调用方直接贴的 killmail JSON）；
    - `killmail_origin`：**原文从哪来** —— `zkillboard`（zkb `/api/killID/` 自带整条
      killmail 且带 items，因此**没有**请求 ESI）、`esi`（zkb 没带 items，回落 ESI）、
      `local`（不经网络）。

    回落时会往 `warnings` 里写一条 `ZKB_ITEMS_FALLBACK_WARNING` —— 多花了一次 1.7–2.6 s
    的请求必须交代，不许静默。反过来，**短路时 `items` 是 `[]`** 会写一条
    `ZKB_EMPTY_ITEMS_WARNING`（「该战报没有已装模块」）—— 空数组是事实，但它与
    「我们漏了模块数据」在返回里长得一样，所以必须说出来。

    `null` 的语义（**别当 0 / 别当 false**）：

    - `security.status` / `band`：ESI 查不到安全等级时是 `null`（**不伪造**）；
      `band` 只在真的拿到 status 时才出现；
    - `value.*` / `flags.*` / `labels`：没有 `zkb` 价值块时全是 `null`（本地 ESI 形态的
      killmail 就是这样），原因写在 `warnings` 里；
    - `damage_check`：`victim.damage_taken` 缺失时是 `null`；两组数字不等时是
      `"mismatch"` **并且**多给一个 `damage_diff`（= Σattackers − victim，数据质量信号，
      绝不静默）；
    - `by_weapon_family` 里分不清的一律 `other`（`weapon_family()` 的启发式只认名字 /
      组名，认不出就不猜）。

    **战报口径**（T-53）：`count` 是**挂名人数**，`damaging_count` / `zero_damage_count` 是
    「真打出伤害 / 挂名零伤害」的人数（二者之和恒等于 `count`，`damage_done` 缺失按 0 计）。
    `capsule_count` 是记录船为 `Capsule`（蛋，`type_id == 670`）的人数：**蛋装不了任何武器**，
    所以蛋的伤害数字是真的、**船归属不可信** —— 蛋出现在攻击者里说明该玩家在这场交火中
    已经失去了自己的船（其伤害来自被摧毁的那条船）。因此 `by_ship_group` 每行都带
    `ship_attribution`（`"capsule"` / `"normal"`，全表一致），蛋行**不扣伤害**；`capsule_count > 0`
    时 `warnings` 会追加一条说明。`by_pilot[].recorded_ship_is_capsule` 标出那些「记录船是蛋」
    的驾驶员（伤害照旧保留）。

    **身份**（驾驶员 / 军团 / 联盟）：`character` / `corporation` / `alliance` 都是
    `{"id", "name"}`；**id 缺失 → 整个对象 `null`**（没有联盟就是 `null`）；
    **id 有但名字查不到 → `name: null`**，并在 `warnings` 里给「N 个实体名未解析」。
    名字走 ESI `POST /universe/names/`，一条战报的**所有 id 合成一批**只查一次
    （进程内缓存；同一份输入第二次调用一次请求都不发）。`attackers.by_pilot` 是
    按伤害降序的前 `BY_PILOT_ROWS`（10）名 —— 完整名单在原始 killmail 里。

    `context.damage_taken_vs_ehp.ratio = damage_taken / ehp_mixed`，EHP 直接读投影层
    （`survivability.
    ehp_mixed`，`provenance` 一起回显），**本层不重算**；这一块只陈述「挨了多少 / 自身
    多少」，不给生死结论。
    """
    return _execute_killmail(
        fit,
        skill_tier=skill_tier,
        damage_profile=damage_profile,
        state_policy=state_policy,
        locale=locale,
    )


# =============================================================================
# 十三、zkb 战报检索（T-50）：`zkb_search` —— 列表级数据 + 限流 + 名字解析
# =============================================================================
#
# 这里的每条口径都是**实测**的（2026-10-19，python urllib 直连 zkb；本机 curl /
# Invoke-WebRequest 的 TLS 不可用，只有 urllib 能出网）：
#
# | 查询 | URL 形态 | 实测 |
# |---|---|---|
# | 按船型 + 近 7 天 | `/api/losses/shipTypeID/22444/pastSeconds/604800/` | 200，**170 条** |
# | 复合（AND）：星系 + 船型 | `/api/kills/solarSystemID/30002537/shipTypeID/22444/` | 200，**200 条**（7.65 s，重查询） |
# | 按角色 + 近 1 天 | `/api/kills/characterID/2121910483/pastSeconds/86400/` | 200，9 条 |
# | 分页 | `/api/losses/shipTypeID/22444/page/2/` | 200，200 条 |
# | 时间区间 | `/startTime/20261010/endTime/20261012/` | ❌ `{"error": "startTime/endTime no longer supported in api because of abuse"}` |
#
# 由此定下四条口径：
#
# 1. **`startTime` / `endTime` 已被 zkb 停用**：时间维度只能用 `pastSeconds/<n>` +
#    `page/<n>` 回溯。调用方硬塞起止时间参数 → `invalid_argument`（**不发请求**，
#    既省一次配额，也不给 agent 一条看不出所以然的上游 error）。
# 2. **单页最多 200 条**：`limit` 只截断返回的 `kills` 数组，`count` 与 `aggregates`
#    仍按**整页**算（并在 `note` 里说清楚，别让 agent 以为「一共就 50 条」）。
# 3. **多过滤器串联即 AND**，`/kills/` 与 `/losses/` 是两条独立路径（`side="both"` =
#    两次请求，然后按 `killmail_time` 倒序合并去重）。
# 4. **限流是真的**：所有出网请求过同一个全局锁 + 最小间隔 `ZKB_MIN_INTERVAL_S`
#    （1.2 s）；被限流（429 / 403）→ `rate_limited`。
#
# 列表里**没有完整配装**（只有被弹出的模块 `victim.items`，实测确实存在，但本工具
# 不返回它 —— 那是单条明细的活）；要明细请对具体 `killmail_id` 调 `fit_killmail`。

ZkbRequester = Callable[[str, "object | None"], object]
"""请求替身的签名：`(url, body)`；GET 时 `body is None`，POST 时是请求体对象。"""

_ZKB_LOCK = threading.Lock()
"""出网闸门的**全局锁**：持锁期间不放第二个请求进来（串行 + 最小间隔）。"""

_ZKB_LAST_START: float | None = None
"""上一次出网请求的**开始**时刻（`time.monotonic`；测试可注入 clock）。"""

_ZKB_ID_CACHE_LOCK = threading.Lock()
_ZKB_ID_CACHE: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
"""名字 → `{"id", "name"}` 的进程内 LRU（只存**成功**解析，失败不缓存）。"""


def _reset_zkb_state() -> None:
    """清空「限流时间戳 + 名字解析缓存」（**测试/换环境用**，生产进程不需要调用）。"""
    global _ZKB_LAST_START
    with _ZKB_LOCK:
        _ZKB_LAST_START = None
    with _ZKB_ID_CACHE_LOCK:
        _ZKB_ID_CACHE.clear()


def zkb_url(
    *,
    side: str,
    filters: "dict[str, int] | None" = None,
    past_seconds: int | None = None,
    page: int = 1,
) -> str:
    """拼一条 zkb 列表 URL（**纯函数**，离线可断言；`page <= 1` 时不拼 `page`）。

    形态：`<root>/<side>/[<段名>/<值>/]…[pastSeconds/<n>/][page/<n>/]`，段的顺序就是
    `ZKB_FILTER_SEGMENTS` 的顺序（AND 复合，顺序固定才可比对）。

    Args:
        side: `kills` / `losses`（**不接受** `both`：那是两次请求，不是一条 URL）。
        filters: `{参数名: id}`，键必须来自 `ZKB_FILTER_SEGMENTS`（不认识的键忽略）。
        past_seconds: 时间窗口；`None` = 不限制（zkb 已停用起止时间）。
        page: 页码；`1` 时省略（`/page/1/` 与不带 page 等价，少一段更干净）。
    """
    parts: list[str] = [ZKB_API_ROOT, side]
    table = filters or {}
    for field, segment in ZKB_FILTER_SEGMENTS:
        value = table.get(field)
        if value is None:
            continue
        parts.append(segment)
        parts.append(str(value))
    if past_seconds is not None:
        parts.append("pastSeconds")
        parts.append(str(past_seconds))
    if page and page > 1:
        parts.append("page")
        parts.append(str(page))
    return "/".join(parts) + "/"


def _zkb_default_requester(url: str, body: object | None = None) -> object:
    """生产用的请求器：GET 走 zkb 的 UA，POST 走 ESI 的 UA（错误通道同一个 `_read_json`）。"""
    if body is None:
        return _http_json(url, user_agent=ZKB_USER_AGENT)
    return _post_json(url, body)


def _zkb_request(
    url: str,
    *,
    body: object | None = None,
    requester: ZkbRequester | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> object:
    """一次出网请求：**全局限流**（串行 + ≥ `ZKB_MIN_INTERVAL_S`）→ 取 JSON。

    Args:
        url: 完整 URL。
        body: `None` = GET；否则按 JSON `POST`（只有 ESI `/universe/ids/` 走这条）。
        requester: 请求替身（测试注入；生产走 urllib）。
        clock: 单调时钟替身（测试用虚拟时钟，避免真的等 1.2 s）。
        sleeper: 睡眠替身（与 `clock` 配套）。

    Raises:
        _ApiFailure: `rate_limited`（429 / 403）、或上游透传的 `network_failed`。
    """
    global _ZKB_LAST_START
    now_fn = clock or time.monotonic
    sleep_fn = sleeper or time.sleep
    fetch = requester or _zkb_default_requester
    with _ZKB_LOCK:
        now = float(now_fn())
        if _ZKB_LAST_START is not None:
            wait = ZKB_MIN_INTERVAL_S - (now - _ZKB_LAST_START)
            if wait > 0:
                sleep_fn(wait)
                now = float(now_fn())
        _ZKB_LAST_START = now
        try:
            return fetch(url, body)
        except _ApiFailure as exc:
            status = exc.extra.get("status")
            if status in ZKB_RATE_LIMIT_STATUSES:
                raise _ApiFailure(
                    "rate_limited",
                    f"被 zkb 限流（HTTP {status}）：{url}；请求间隔必须 "
                    f"≥ {ZKB_MIN_INTERVAL_S} s，稍后重试",
                    url=url,
                    status=status,
                    hint={"min_interval_s": ZKB_MIN_INTERVAL_S},
                ) from exc
            raise


def _zkb_integer(
    name: str,
    value: object,
    *,
    default: int | None,
    minimum: int,
    maximum: int | None = None,
) -> int:
    """把一个参数收成整数（`None` → `default`；不合法 → `invalid_argument`）。

    **不静默夹取**：超出 `maximum` 就报错并给 `hint.max`。夹取等于偷偷改了调用方的
    请求（他说要 500 条，你给 200 条却说成功了），比直接报错更坏。
    """
    if value is None:
        if default is None:
            raise _ApiFailure("invalid_argument", f"{name} 不能为空", field=name)
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _ApiFailure(
            "invalid_argument",
            f"{name} 必须是整数，收到 {type(value).__name__}",
            field=name,
        )
    if isinstance(value, int):
        number = value
    else:
        text = value.strip()
        if not text.isdigit():
            raise _ApiFailure(
                "invalid_argument", f"{name} 必须是整数，收到 {value!r}", field=name
            )
        number = int(text)
    if number < minimum:
        raise _ApiFailure(
            "invalid_argument",
            f"{name} 必须 ≥ {minimum}，收到 {number}",
            field=name,
            hint={"min": minimum},
        )
    if maximum is not None and number > maximum:
        raise _ApiFailure(
            "invalid_argument",
            f"{name} 必须 ≤ {maximum}（zkb 单页上限），收到 {number}",
            field=name,
            hint={"max": maximum},
        )
    return number


def _zkb_check_common(common: dict) -> None:
    """`**common` 只允许三项「无影响」的配装参数；时间区间类参数直接报错。

    Raises:
        _ApiFailure: `invalid_argument`（时间区间参数 / 不认识的参数名）。
    """
    accepted = [field for field, _segment in ZKB_FILTER_SEGMENTS] + [
        "past_seconds",
        "page",
        "side",
        "limit",
    ]
    for name in common:
        if name in ZKB_REJECTED_PARAMETERS:
            raise _ApiFailure(
                "invalid_argument",
                f"{name} 不受支持：zkb 已停用 startTime/endTime（abuse 防护），"
                "时间窗口只能用 past_seconds + page 回溯",
                field=name,
                hint={"use": ["past_seconds", "page"]},
            )
        if name not in ZKB_IGNORED_PARAMETERS:
            raise _ApiFailure(
                "invalid_argument",
                f"不认识的参数 {name!r}",
                field=name,
                hint={"accepted": accepted},
            )


def _zkb_id_cache_get(kind: str, key: str) -> dict | None:
    """取「名字 → id」缓存的一条（命中时移到末尾 = LRU 的最近用过）。"""
    with _ZKB_ID_CACHE_LOCK:
        found = _ZKB_ID_CACHE.get((kind, key))
        if found is None:
            return None
        _ZKB_ID_CACHE.move_to_end((kind, key))
        return dict(found)


def _zkb_id_cache_put(kind: str, key: str, entry: dict) -> None:
    """存一条解析结果（超容量丢最久未用的那条）。"""
    with _ZKB_ID_CACHE_LOCK:
        _ZKB_ID_CACHE[(kind, key)] = dict(entry)
        _ZKB_ID_CACHE.move_to_end((kind, key))
        while len(_ZKB_ID_CACHE) > ZKB_ID_CACHE_MAX:
            _ZKB_ID_CACHE.popitem(last=False)


def _esi_id_candidates(payload: object) -> list[dict]:
    """ESI `/universe/ids/` 响应 → `[{"kind", "id", "name"}]`（只收本层认得的分类）。"""
    if not isinstance(payload, dict):
        return []
    reverse = {category: kind for kind, category in ESI_ID_CATEGORIES.items()}
    rows: list[dict] = []
    for category, entries in payload.items():
        kind = reverse.get(category)
        if kind is None or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            identity = _as_int(entry.get("id"))
            name = entry.get("name")
            if identity is None or not isinstance(name, str) or not name:
                continue
            rows.append({"kind": kind, "id": identity, "name": name})
    return rows


def _zkb_ship_block(type_id: int | None) -> dict:
    """`{type_id, name, group}`：名字 / 组名走 SDE 索引，查不到就是 `None`（**不编**）。"""
    row = None
    if type_id is not None:
        index = _try_index()
        if index is not None:
            row = index.get_type(int(type_id))
    return {
        "type_id": type_id,
        "name": (row or {}).get("name"),
        "group": (row or {}).get("group_name"),
    }


def _zkb_resolve_entity(
    kind: str,
    value: object,
    requester: ZkbRequester | None,
    *,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict:
    """名字 / id → `{"id": int, "name": str|None}`（名字走 ESI，结果进进程内缓存）。

    判据（**不猜**）：

    - 整数（或纯数字字符串）→ 直接用，不联网；船型能顺手从 SDE 拿到名字；
    - 名字 → `POST /universe/ids/`，**只在该分类内**选：恰好一个精确同名 → 用它；
      没有精确同名但该分类只有一个结果 → 用它（ESI 已经给了唯一匹配）；其余
      （多个候选 / 一个都没有）→ `unknown_entity` + `candidates`（带 `kind`）。

    `clock` / `sleeper` 会**透传**给 `_zkb_request`（限流闸门用）。不透传的后果很实在：
    名字解析那次 POST 会用**真实**时钟写 `_ZKB_LAST_START`，随后任何用虚拟时钟的调用都
    会算出一个巨大的等待量 —— 「虚拟时钟 + 名字解析」的测试全线失真。这是实测踩过的坑。

    Raises:
        _ApiFailure: `invalid_argument`（形态不对）/ `unknown_entity`（给不出唯一答案）。
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _ApiFailure(
            "invalid_argument",
            f"{kind} 必须是名字或整数 id，收到 {type(value).__name__}",
            field=kind,
        )
    if isinstance(value, int):
        return {"id": int(value), "name": _zkb_ship_block(value).get("name")}
    text = value.strip()
    if not text:
        raise _ApiFailure("invalid_argument", f"{kind} 不能为空", field=kind)
    if text.isdigit():
        number = int(text)
        return {"id": number, "name": _zkb_ship_block(number).get("name")}

    key = text.casefold()
    cached = _zkb_id_cache_get(kind, key)
    if cached is not None:
        return cached

    payload = _zkb_request(
        ESI_UNIVERSE_IDS_API,
        body=[text],
        requester=requester,
        clock=clock,
        sleeper=sleeper,
    )
    rows = _esi_id_candidates(payload)
    expected = [row for row in rows if row["kind"] == kind]
    exact = [row for row in expected if row["name"].casefold() == key]
    chosen: dict | None = None
    if len(exact) == 1:
        chosen = exact[0]
    elif not exact and len(expected) == 1:
        chosen = expected[0]
    if chosen is None:
        candidates = exact or expected or rows
        reason = "有多个同名候选" if len(candidates) > 1 else "ESI 认不出这个名字"
        raise _ApiFailure(
            "unknown_entity",
            f"解析不出唯一的 {kind}：{reason}（{text!r}）；请改用整数 id",
            field=kind,
            value=text,
            candidates=[
                {"id": row["id"], "name": row["name"], "kind": row["kind"]}
                for row in candidates[:5]
            ],
        )
    entry = {"id": int(chosen["id"]), "name": str(chosen["name"])}
    _zkb_id_cache_put(kind, key, entry)
    return entry


def _zkb_filters(
    values: dict[str, object],
    requester: ZkbRequester | None,
    *,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> tuple[dict[str, dict], dict[str, int]]:
    """六个实体过滤参数 → `(resolved, filter_ids)`（`resolved` 回显给调用方）。

    `clock` / `sleeper` 透传给名字解析那次 `POST /universe/ids/`（见 `_zkb_resolve_entity`）。
    """
    resolved: dict[str, dict] = {}
    filter_ids: dict[str, int] = {}
    for field, _segment in ZKB_FILTER_SEGMENTS:
        raw = values.get(field)
        if raw is None:
            continue
        entry = _zkb_resolve_entity(
            field, raw, requester, clock=clock, sleeper=sleeper
        )
        resolved[field] = entry
        filter_ids[field] = int(entry["id"])
    return resolved, filter_ids


ZKB_ERROR_UNKNOWN_ENTITY: str = "Invalid type or id"
"""zkb 表达「id 不存在 / id 与类型不匹配」的正文（实测，**HTTP 状态仍是 200**）。

`/api/stats/` 与列表接口都用这一句：`characterID/999999999999/`、`allianceID/<公司 id>/`
实测都返回 `{"error": "Invalid type or id"}`。
"""

ZKB_RATE_LIMIT_TOKENS: tuple[str, ...] = ("too many", "rate limit", "rate-limit", "throttl")
"""正文里出现这些词 → 按「上游在限我们」归类。

**防御性归类，未实测**：我们确实没能抓到 zkb 用正文表达限流（6 次快速连发实测全部
`200` + 完整体）。真正可证的限流通道是 HTTP 429 / 403，由 `_zkb_request` 直接映射成
`rate_limited`。这里留一手只是为了「万一上游换了表达方式」时不要把 `rate_limited`
误报成 `network_failed`。
"""


def _zkb_error_kind(text: object) -> tuple[str, str]:
    """zkb 的 `{"error": ...}` 正文 → `(kind, 人话解释)`。**两个工具共用这一份判定。**

    为什么必须共用：同一份上游正文，`zkb_search` 曾经判 `network_failed`、`zkb_stats`
    曾经判 `unknown_entity` —— 同一类错误在两个工具里两个 kind，agent 无从判断该「换个
    参数重试」还是「等一会儿重试」。

    判据（**只看正文，不看状态码**：zkb 的错误体都是 `200`）：

    - 含 `Invalid type or id` → `unknown_entity`（换 id / 换类型可解）；
    - 含限流词 → `rate_limited`（等一会儿重试）；
    - 其余 → `network_failed`（上游拒绝了这次查询，但我们不知道是为什么，**不猜成实体问题**）。
    """
    raw = str(text if text is not None else "")
    folded = raw.casefold()
    if ZKB_ERROR_UNKNOWN_ENTITY.casefold() in folded:
        return ("unknown_entity", "id 不存在，或 id 与 entity_type 不匹配")
    if any(token in folded for token in ZKB_RATE_LIMIT_TOKENS):
        return ("rate_limited", "上游说我们请求太快（正文里的说法，不是 HTTP 状态码）")
    return ("network_failed", "上游拒绝了这次查询，但没说为什么")


def _zkb_payload_rows(payload: object, url: str) -> list[dict]:
    """一次 zkb 响应 → 列表项字典（形状不对时给结构化错误，**不猜**）。

    Raises:
        _ApiFailure: `unknown_entity` / `rate_limited` / `network_failed`
            （`{"error": ...}` 的归类走共用的 `_zkb_error_kind()`；不是列表也是
            `network_failed`）。
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and payload.get("error"):
        kind, why = _zkb_error_kind(payload.get("error"))
        raise _ApiFailure(
            kind,
            f"zkb 拒绝了这次查询（{why}）：{payload.get('error')}",
            url=url,
            raw=str(payload.get("error")),
        )
    raise _ApiFailure(
        "network_failed",
        f"zkb 返回的不是列表：{type(payload).__name__}",
        url=url,
    )


def _zkb_kill_row(item: dict) -> dict | None:
    """一条 zkb 列表项 → 稳定形状（缺字段给 `None`，**绝不用 0 / false 冒充**）。

    连 `killmail_id` 都没有的项返回 `None`（那不是一条战报，也不该被数进 `count`）。

    `victim` 块这里只放 **id**（`name` 全是 `None`）：列表一页 200 条，整页查名字是浪费；
    名字等 `limit` 截断之后由 `zkb_search` 对**真正返回的那几行**一次补齐。
    """
    killmail_id = _as_int(item.get("killmail_id"))
    if killmail_id is None:
        return None
    zkb = item.get("zkb") if isinstance(item.get("zkb"), dict) else {}
    victim = item.get("victim") if isinstance(item.get("victim"), dict) else {}
    ship = _zkb_ship_block(_as_int(victim.get("ship_type_id")))
    labels = zkb.get("labels")
    return {
        "killmail_id": killmail_id,
        "killmail_time": (
            item.get("killmail_time")
            if isinstance(item.get("killmail_time"), str)
            else None
        ),
        "ship": ship,
        "victim": {
            **_identity_blocks(victim, {}),
            "ship": dict(ship),
        },
        "value": _as_float(zkb.get("totalValue")),
        "labels": [label for label in (labels or []) if isinstance(label, str)]
        if isinstance(labels, list)
        else [],
        "npc": _as_bool(zkb.get("npc")),
        "solo": _as_bool(zkb.get("solo")),
    }


def _zkb_row_name_ids(rows: "list[dict]") -> list[int]:
    """若干 zkb 行 → 要查名字的 id（去重、保序；只给**真的要返回的**那几行用）。"""
    ids: list[int] = []
    for row in rows:
        victim = row.get("victim") if isinstance(row.get("victim"), dict) else {}
        for key, _raw in _ENTITY_KEYS:
            entry = victim.get(key)
            value = _as_int(entry.get("id")) if isinstance(entry, dict) else None
            if value is not None and value not in ids:
                ids.append(value)
    return ids


def _zkb_median(values: list[float]) -> float | None:
    """中位数（**空集给 `None`**：中位数无定义，不能用 0 冒充）。"""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _zkb_aggregates(rows: list[dict]) -> dict:
    """整页的聚合：总值 / 中位数 / 按船型分组 / 时间跨度（**原样相加，不取整**）。"""
    values = [
        float(row["value"])
        for row in rows
        if isinstance(row["value"], (int, float)) and not isinstance(row["value"], bool)
    ]
    buckets: dict[object, dict] = {}
    for row in rows:
        ship = row["ship"]
        key = ship.get("type_id")
        bucket = buckets.get(key)
        if bucket is None:
            bucket = buckets[key] = {"name": ship.get("name"), "count": 0, "value": 0.0}
        bucket["count"] += 1
        value = row["value"]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            bucket["value"] += float(value)
    by_ship = sorted(
        buckets.values(), key=lambda bucket: (-bucket["count"], -bucket["value"], str(bucket["name"]))
    )
    times = [
        row["killmail_time"]
        for row in rows
        if isinstance(row["killmail_time"], str)
    ]
    return {
        "total_value": float(sum(values)),
        "median_value": _zkb_median(values),
        "by_ship": by_ship,
        "time_span": {
            "earliest": min(times) if times else None,
            "latest": max(times) if times else None,
        },
    }


def zkb_search(
    *,
    character: object = None,
    corporation: object = None,
    alliance: object = None,
    ship: object = None,
    solar_system: object = None,
    region: object = None,
    past_seconds: object = None,
    page: object = 1,
    side: object = ZKB_SIDE_BOTH,
    limit: object = ZKB_LIMIT_DEFAULT,
    requester: ZkbRequester | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    **common: object,
) -> dict:
    """查战报**列表**（谁 / 哪个公司 / 哪个联盟 / 哪种船 / 哪个星系 / 哪个区域）。

    六个实体条件**可以同时给**（zkb 的 URL 段串联即 AND），例如「Sleipnir 在 Jita 附近
    最近的损失」。时间维度**只能用 `past_seconds`**（zkb 已停用 startTime/endTime，
    见第十三节开头的实测表）；更早的数据靠 `page` 往回翻。

    Args:
        character: 角色（名字或 id）。
        corporation: 公司（名字或 id）。
        alliance: 联盟（名字或 id）。
        ship: 船型（名字或 id；名字走 ESI `/universe/ids/` 的 `inventory_types`）。
        solar_system: 星系（名字或 id）。
        region: 区域（名字或 id）。
        past_seconds: 时间窗口（秒，正整数）；**只在显式给定时**才拼进 URL。
        page: 页码（≥1；1 = 最新一页；单页最多 `ZKB_PAGE_SIZE` = 200 条）。
        side: `kills` / `losses` / `both`（both = 两条路径各请求一次，合并去重）。
        limit: 只截断返回的 `kills` 数组（1..200）；`count` / `aggregates` 仍按整页算。
        requester / clock / sleeper: 测试注入点（生产不传）。
        **common: 允许 `skill_tier` / `damage_profile` / `state_policy`（无影响）；
            时间区间类参数（`startTime` 等）会被明确拒绝。

    Returns:
        成功时 `result`：

        ```json
        {"query": {"resolved": {"ship": {"id": 22444, "name": "Sleipnir"}},
                   "side": "both", "past_seconds": 604800, "page": 1},
         "count": 170,
         "page": 1,
         "kills": [{"killmail_id": 139265742, "killmail_time": "2026-10-18T19:29:03Z",
                    "ship": {"type_id": 22444, "name": "Sleipnir", "group": "Command Ship"},
                    "victim": {"character": {"id": 1, "name": "..."},
                               "corporation": {"id": 2, "name": "..."},
                               "alliance": {"id": 3, "name": "..."},
                               "ship": {"type_id": 22444, "name": "Sleipnir",
                                        "group": "Command Ship"}},
                    "value": 397729064.05, "labels": ["pvp"], "npc": false, "solo": false}],
         "aggregates": {"total_value": 123.4, "median_value": 5.6,
                        "by_ship": [{"name": "Sleipnir", "count": 3, "value": 1.2}],
                        "time_span": {"earliest": "...", "latest": "..."}},
         "note": "list 级数据来自 zkb（无 items）；要明细请对具体 killmail_id 调 fit_killmail",
         "warnings": []}
        ```

        `kills[i].victim` 的 `character` / `corporation` / `alliance` 是 `{"id", "name"}`
        （**id 缺失 → 整个对象 `null`；名字查不到 → `name: null`**）。名字只解析
        **`limit` 之后真正返回的那些行**，并且所有 id 合成**一批** `POST /universe/names/`
        （去重 + 进程内缓存）—— 不会为整页 200 条都查一次。

        `meta` 里给 `source` / `page_size` / `min_interval_s` 与**本次真正请求过的 URL**
        （排查「为什么是这些结果」时不用猜）。

    失败时 `error.kind` ∈ `invalid_argument` / `unknown_entity` / `network_failed` /
    `rate_limited` / `internal_error`（见 `INVALID_PATH_KIND` 的说明）。
    """
    try:
        _zkb_check_common(common)
        _check_choice("side", side, ZKB_SIDES)
        selected_side = str(side)
        page_number = _zkb_integer("page", page, default=1, minimum=1)
        row_limit = _zkb_integer(
            "limit", limit, default=ZKB_LIMIT_DEFAULT, minimum=1, maximum=ZKB_PAGE_SIZE
        )
        window = (
            None
            if past_seconds is None
            else _zkb_integer("past_seconds", past_seconds, default=None, minimum=1)
        )

        resolved, filter_ids = _zkb_filters(
            {
                "character": character,
                "corporation": corporation,
                "alliance": alliance,
                "ship": ship,
                "solar_system": solar_system,
                "region": region,
            },
            requester,
            clock=clock,
            sleeper=sleeper,
        )
        if not filter_ids:
            # 裸的 /api/kills/ 是全服流式数据（重查询 + 无意义），不是一次检索。
            raise _ApiFailure(
                "invalid_argument",
                "至少要给一个实体过滤条件（character / corporation / alliance / "
                "ship / solar_system / region）",
                field="filters",
                hint={"accepted": [field for field, _segment in ZKB_FILTER_SEGMENTS]},
            )

        sides = (
            (ZKB_SIDE_KILLS, ZKB_SIDE_LOSSES)
            if selected_side == ZKB_SIDE_BOTH
            else (selected_side,)
        )
        urls: list[str] = []
        rows: list[dict] = []
        seen: set[int] = set()
        for one_side in sides:
            url = zkb_url(
                side=one_side,
                filters=filter_ids,
                past_seconds=window,
                page=page_number,
            )
            urls.append(url)
            payload = _zkb_request(
                url, requester=requester, clock=clock, sleeper=sleeper
            )
            for item in _zkb_payload_rows(payload, url):
                row = _zkb_kill_row(item)
                if row is None or row["killmail_id"] in seen:
                    continue  # 去重：side=both 时同一条可能同时出现在 kills / losses
                seen.add(row["killmail_id"])
                rows.append(row)

        # 按时间倒序（zkb 自己也是倒序，但合并两条路径后必须自己排一次）；
        # 时间缺失的行排到最后，用 killmail_id 兜底 —— id 单调递增，顺序仍然确定。
        rows.sort(
            key=lambda row: (row["killmail_time"] or "", row["killmail_id"]),
            reverse=True,
        )
        aggregates = _zkb_aggregates(rows)
        shown = rows[:row_limit]
        # 名字只解析**真的要返回的行**（limit 之后）：整页 200 条全查是浪费且慢。
        warnings: list[str] = []
        names = _resolve_identity_names(
            _zkb_row_name_ids(shown), warnings, requester=requester
        )
        for row in shown:
            _fill_identity_names(row.get("victim"), names)
        note = ZKB_SEARCH_NOTE
        if len(shown) < len(rows):
            note = (
                f"{ZKB_SEARCH_NOTE}（kills 已按 limit={row_limit} 截断到 {len(shown)} 条；"
                f"count 与 aggregates 仍按整页 {len(rows)} 条计算）"
            )

        result = {
            "query": {
                "resolved": resolved,
                "side": selected_side,
                "past_seconds": window,
                "page": page_number,
            },
            "count": len(rows),
            "page": page_number,
            "kills": shown,
            "aggregates": aggregates,
            "note": note,
            "warnings": warnings,
        }
        meta = {
            "spec_version": SPEC_VERSION,
            "source": "zkillboard",
            "page_size": ZKB_PAGE_SIZE,
            "min_interval_s": ZKB_MIN_INTERVAL_S,
            "urls": urls,
        }
        return _success(result, meta)
    except _ApiFailure as exc:
        return _failure(exc.payload())
    except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得裸抛给 agent
        return _failure(
            {
                "kind": "internal_error",
                "message": "接口层内部异常，本次没有可用结果",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )


# =============================================================================
# 十四、实体画像（T-55）：`zkb_stats` —— 战斗总量 / 风格指标 / 榜单
# =============================================================================
#
# 全部口径都是**实测**的（2026-10-19，python urllib 直连 zkb；本机 curl /
# Invoke-WebRequest 的 TLS 不可用，只有 urllib 能出网）：
#
# | 请求 | 实测 |
# |---|---|
# | `stats/characterID/2117515414/` | 200，48,666 B，**59** 个顶层键 |
# | `stats/corporationID/98821127/` | 200，64,835 B，**48** 键 |
# | `stats/allianceID/1354830081/` | 200，167,789 B，**49** 键 |
# | `stats/characterID/2117515414`（**少了结尾 `/`**） | 200 + **0 字节空体** |
# | `stats/characterID/999999999999/`（id 不存在） | 200 + `{"error":"Invalid type or id"}` |
# | `stats/allianceID/98821127/`（拿公司 id 当联盟） | 200 + `{"error":"Invalid type or id"}` |
# | `stats/characterID/1/`（**存在但没名字**） | 200 + 702 B **空壳**（5 键，`info` 无 name） |
#
# 由此定下六条口径：
#
# 1. **错误体也是 HTTP 200**：判成败只能看正文 —— `{"error": ...}` → `unknown_entity`。
# 2. **URL 结尾的 `/` 不能省**：省了是 200 + 空体；空体 → `internal_error` + warning
#    （`_read_json` 已经带上 `payload_bytes`，这里按「0 字节」判，不靠字符串匹配）。
# 3. **空壳实体**（`info.name` 缺失 **且** 战斗字段全缺）也判 `unknown_entity`。
# 4. **zkb 会省略空块**（三份样本顶层键 59/48/49 各不相同）→ **本层补空值**，
#    返回形状里所有键永远存在。缺失 ≠ 零，不适用 ≠ 不知道。
# 5. **四个 ratio 的公式 zkb 未公开，实测也反推不出来**：
#    `dangerRatio` 与 ships / points / isk 三个候选式**都不吻合**（N=10 实体，
#    max|diff| = 22.18 / 7.47 / 38.34 个百分点）；`avgGangSize` 的三个候选式也都不吻合
#    （30.70 / 64.95 / 29.08）→ 一律 `formula: null`，只转述数值，**不给威胁等级判词**。
# 6. **`soloRatio` 是 0–100 的百分数（1 位小数）**，`avgGangSize` 是「人/次」的实数均值
#    ——**都不是 0–1 比例**；且 `gangRatio + soloRatio ≠ 100`（实测 100.0–103.7），
#    两者不互补，不能互相反推。
#
# 体积（默认形状实测，紧凑 JSON）：角色 1,586 B / 公司 2,371 B / 联盟 2,876 B —— 都 ≤ 4 KB。
# 大块（`months` / `rankings` / `top_all_time` / `groups`）只能由调用方显式 `include`，
# 单块上限 `ZKB_STATS_BLOCK_MAX_BYTES`，超了才截断；整个响应超 64 KB 只加软警告、不截断
# （**隐式截断比大响应更坏**）。

ZKB_STATS_API: str = "https://zkillboard.com/api/stats/{segment}/{entity_id}/"
"""实体画像 URL 模板。**结尾的 `/` 不可省**：省了 zkb 回 `200` + **0 字节空体**（实测）。"""

ZKB_STATS_ENTITY_KINDS: tuple[str, ...] = ("character", "corporation", "alliance")
"""`entity_type` 的三个合法值。**必填、不靠 id 猜**：同一串数字在三个命名空间里都可能存在。"""

ZKB_STATS_SEGMENTS: dict[str, str] = {
    "character": "characterID",
    "corporation": "corporationID",
    "alliance": "allianceID",
}
"""实体类型 → zkb 的 URL 段名（**单一真源**，别在函数里裸写字符串）。"""

ZKB_STATS_ERROR_BODY: str = ZKB_ERROR_UNKNOWN_ENTITY
"""`zkb` 表达「实体不存在 / 类型不匹配」的正文 —— **就是** `ZKB_ERROR_UNKNOWN_ENTITY`
（两个工具共用同一个常量，不再各写一份字面量）。"""

ZKB_STATS_TOP_LIMIT: int = 5
"""`top.*` 每类取前几条（实测 6 类各 5 条约 1.5 KB，撑得住 4 KB 预算）。"""

ZKB_STATS_TOP_LISTS: tuple[tuple[str, str], ...] = (
    ("character", "characters"),
    ("corporation", "corporations"),
    ("alliance", "alliances"),
    ("shipType", "ships"),
    ("solarSystem", "systems"),
    ("location", "locations"),
)
"""zkb `topLists[].type` → 我们返回的键。

**就是这 6 个，没有 `regions`** —— zkb 没有区域榜（实测）。`locations` 是「天体 / 站点」级，
不是星系，别按名字望文生义。
"""

ZKB_STATS_LOCATIONS_NOTE: str = (
    "top.locations 是「天体 / 站点」级（行星、月球、小行星带、恒星、星门、玩家建筑），"
    "**不是星系**；要看星系请用 top.systems"
)

ZKB_STATS_BLOCKS: tuple[str, ...] = ("months", "rankings", "top_all_time", "groups")
"""`include` 的白名单。**没有「全量开关」**：大块必须逐个点名，免得一次灌进 121 KB。"""

ZKB_STATS_BLOCK_MAX_BYTES: int = 64 * 1024
"""单块上限（实测最大块 = 联盟 `months` 33.6 KB，正常永远不触发）。"""

ZKB_STATS_RESPONSE_SOFT_BYTES: int = 64 * 1024
"""整个响应的**软**阈值：超了不截断，只加一条 `large_response` 警告。"""

ZKB_STATS_ACTIVITY_KEYS: tuple[str, ...] = (
    "ships",
    "systems",
    "regions",
    "characters",
    "corporations",
    "kills",
)
"""`activity_7d` 的固定 6 键（zkb 的 `activepvp` 键集随实体类型变，本层归一）。"""

ZKB_STATS_ACTIVITY_NOT_APPLICABLE: dict[str, tuple[str, ...]] = {
    "character": ("characters", "corporations"),
    "corporation": ("corporations",),
    "alliance": (),
}
"""**本类型天生没有**的 `activepvp` 键 → `null` 且**不产生 warning**（`not_applicable` 不是「不知道」）。

每次查角色都报「characters/corporations 不适用」是噪音而不是信号 —— 天天报警的告警等于没有告警。
"""

ZKB_STATS_MONTH_FIELDS: tuple[str, ...] = (
    "iskDestroyed",
    "iskLost",
    "pointsDestroyed",
    "pointsLost",
    "shipsDestroyed",
    "shipsLost",
)
"""`months` 块里每月的固定 6 个数值键（zkb 把 0 值键**整个省略**，本层补 0）。"""

ZKB_STATS_STYLE_NOTE: str = (
    "dangerRatio / gangRatio / soloRatio / avgGangSize 均为 zkb 自有指标，公式未公开；"
    "soloRatio 是 0-100 百分数、avgGangSize 是人/次均值，都不是 0-1 比例；"
    "gangRatio + soloRatio != 100（两者不互补，不可互相反推）；本工具不据此给出威胁等级判定"
)

ZKB_STATS_EFFICIENCY_NOTE: str = "我们派生，与 style.danger_ratio 不是同一个东西"

ZKB_STATS_NOTE: str = (
    "战斗总量与风格指标来自 zkb /api/stats/（原样转述）；只有 efficiency 是本层派生的。"
    "大块数据需显式 include（months / rankings / top_all_time / groups）"
)


def _stats_size(payload: object) -> int:
    """紧凑 JSON 的字节数（**和 MCP 实际发出的量级一致**，不带缩进）。"""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return len(text.encode("utf-8"))


def _stats_number(value: object) -> int | float | None:
    """**原样透传** zkb 的数值：`int` 保持 `int`、`float` 保持 `float`。

    为什么不做任何取整：zkb 这些指标（`dangerRatio` / `gangRatio` / `soloKills` …）
    **没有承诺是整数**，我们也不理解它们的口径。`int(12.5)` 得到 `12` 是**静默的精度损失**
    —— 调用方看不出少了半个点。纪律：**只转述，不加工**；要加工就必须显式声明
    （`efficiency` 就是那样：派生 + 公式 + 精度都写在返回里）。

    `bool` 不算数值（`True` 是 `1`，那是类型混淆）；非数值给 `None`，**不拿 0 冒充**。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _stats_number_or(value: object, default: object) -> object:
    """原样透传；缺失（`None` / 非数值）才退回 `default`。

    不用 `x or default`：那会把合法的 `0` / `0.0` 也换成 `default`，还会把 `0.0` 变成
    `int 0`（类型漂移）。这里只判 `None`。
    """
    number = _stats_number(value)
    return default if number is None else number


def _stats_top_row(entry: dict) -> dict:
    """`topLists[].values[i]` → 统一形状 `{id, name, count, isk}`。

    `count` 就是 zkb 的 `kills`（改名只为 6 类形状一致，**不是「数量」的意思**）。
    实测这 4 个键在 6 个 type 里**恒有**，所以不做「若有」分支。

    **四个值全部原样透传**：`isk` 不四舍五入（实测联盟某行原始是
    `57388505453.520004`，`round(x, 2)` 会把它改写成 `…53.52` —— 那是我们编的 2 位精度，
    zkb 没给这个承诺）。
    """
    return {
        "id": _stats_number(entry.get("id")),
        "name": entry.get("name"),
        "count": _stats_number(entry.get("kills")),
        "isk": _stats_number(entry.get("isk")),
    }


def _stats_entity_block(payload: dict, kind: str, resolved_name: str | None) -> dict:
    """`entity` 块：类型 / id / 名字 / ticker / 安全等级 / 成立日期（**所有键恒在**）。

    `security_status` **只对角色有意义**：公司 / 联盟一律 `None`（不拿 0 冒充「安全等级 0」）。
    成立日期：角色读 `info.birthday`，公司 / 联盟读 `info.date_founded`。
    """
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    sec = info.get("security_status")
    founded = info.get("birthday") if kind == "character" else info.get("date_founded")
    identifier = payload.get("id")
    if identifier is None:
        identifier = info.get("id")
    return {
        "type": kind,
        "id": _stats_number(identifier),
        "name": info.get("name") or resolved_name,
        "ticker": info.get("ticker"),
        # `security_status` 原样透传（不做 `float()` 强转：zkb 给 4 就给 int 4，给 4.3 就给
        # 4.3 —— 强转会把 int 变 float，那是我们改写了类型）。
        "security_status": (
            _stats_number(sec) if kind == "character" else None
        ),
        "founded": founded if isinstance(founded, str) else None,
    }


def _stats_combat_block(payload: dict) -> dict:
    """`combat` 块：六个原始总量 + 一个**明确标注派生**的 `efficiency`。

    `efficiency` 用的是 isk 口径，和 zkb 的 `dangerRatio` **不是同一个东西**
    （同一角色实测 82.9% vs 69）—— 所以它带 `kind: derived` + `formula` + `note`，
    并且**显式声明精度**（`decimals`）：它是我们做的加工，加工就必须说清到什么位。
    其余六个数是 zkb 原样，**一位都不动**。
    """
    isk_destroyed = _stats_number(payload.get("iskDestroyed"))
    isk_lost = _stats_number(payload.get("iskLost"))
    total = (isk_destroyed or 0) + (isk_lost or 0)
    return {
        "ships_destroyed": _stats_number(payload.get("shipsDestroyed")),
        "ships_lost": _stats_number(payload.get("shipsLost")),
        "isk_destroyed": isk_destroyed,
        "isk_lost": isk_lost,
        "points_destroyed": _stats_number(payload.get("pointsDestroyed")),
        "points_lost": _stats_number(payload.get("pointsLost")),
        "efficiency": {
            "value": round((isk_destroyed or 0) / total, 4) if total else None,
            "kind": "derived",
            "formula": "isk_destroyed / (isk_destroyed + isk_lost)",
            "decimals": 4,
            "note": ZKB_STATS_EFFICIENCY_NOTE,
        },
    }


def _stats_style_block(payload: dict) -> dict:
    """`style` 块：四个 zkb 自有指标 + solo 计数，**公式一律 `null`**。

    为什么不填公式：实测反推不出来（见本节开头第 5 条）。写一个「看着像」的公式，
    比写 `null` 危险得多 —— 调用方会拿它去算别的实体。

    为什么四个数**全部原样透传**：zkb 没有承诺这几个指标是整数。`dangerRatio: 12.5`
    被 `int()` 成 `12` 是静默丢精度（实测过）。`solo_ratio` 是 0–100 的百分数、
    `avg_gang_size` 是人/次均值，两者本来就可能带小数 —— 不加工才谈得上「只转述」。
    """
    return {
        "danger_ratio": _stats_number(payload.get("dangerRatio")),
        "gang_ratio": _stats_number(payload.get("gangRatio")),
        "solo_ratio": _stats_number(payload.get("soloRatio")),
        "avg_gang_size": _stats_number(payload.get("avgGangSize")),
        "solo_kills": _stats_number(payload.get("soloKills")),
        "solo_losses": _stats_number(payload.get("soloLosses")),
        "source": "zkillboard",
        "formula": None,
        "notes": ZKB_STATS_STYLE_NOTE,
    }


def _stats_activity_block(payload: dict, kind: str) -> tuple[dict, dict, list[str]]:
    """`activity_7d`（固定 6 键）+ `field_reasons` + `warnings`。

    **`not_applicable` 与「数据缺失」分开**：本类型天生没有的键 → `null` 且静默；
    本该有却被 zkb 省略 → `null` + 进 `field_reasons`。

    数值原样透传：`activepvp.<k>.count` 是 zkb 的计数，我们不改写。
    """
    pvp = payload.get("activepvp") if isinstance(payload.get("activepvp"), dict) else {}
    not_applicable = ZKB_STATS_ACTIVITY_NOT_APPLICABLE.get(kind, ())
    block: dict = {}
    reasons: dict = {}
    warnings: list[str] = []
    for key in ZKB_STATS_ACTIVITY_KEYS:
        if key in not_applicable:
            block[key] = None
            continue
        cell = pvp.get(key)
        count = _stats_number(cell.get("count")) if isinstance(cell, dict) else None
        block[key] = count
        if count is None:
            reasons[key] = "zkb_response_missing"
    if not pvp:
        warnings.append("insufficient_activity: 该实体 7 天内无战斗记录（activepvp 为空）")
    return block, reasons, warnings


def _stats_top_block(payload: dict) -> dict:
    """`top` 块：**6 类全部保留**，空榜给 `[]`（键不许省略 —— 形状是契约）。"""
    by_type: dict[object, list] = {}
    for entry in payload.get("topLists") or []:
        if isinstance(entry, dict) and isinstance(entry.get("values"), list):
            by_type[entry.get("type")] = entry["values"]
    return {
        out_key: [
            _stats_top_row(row)
            for row in (by_type.get(zkb_type) or [])[:ZKB_STATS_TOP_LIMIT]
            if isinstance(row, dict)
        ]
        for zkb_type, out_key in ZKB_STATS_TOP_LISTS
    }


def _stats_months_data(payload: dict) -> dict:
    """`months` 补齐**只填内部空洞**，不向两端外扩。

    为什么这件事必须说清楚：「这个月他没活动」和「这个月他还不存在」是两件事。
    把区间外扩到首末观测月之外等于**编造不存在的零活动历史**；
    `range` 给的是**观测区间**（首次 / 末次有数据），`observed_months` 是 zkb 真正给了
    数据的月份数，`filled_internal_gaps` 只数区间内的空洞。
    """
    raw = payload.get("months") if isinstance(payload.get("months"), dict) else {}
    observed = sorted(int(key) for key in raw if str(key).isdigit())
    if not observed:
        return {
            "range": {"first": None, "last": None},
            "observed_months": 0,
            "filled_internal_gaps": 0,
            "data": {},
        }
    first, last = observed[0], observed[-1]
    data: dict = {}
    filled = 0
    year, month = first // 100, first % 100
    while (year * 100 + month) <= last:
        key = f"{year:04d}{month:02d}"
        source = raw.get(key)
        if not isinstance(source, dict):
            filled += 1
            row = {"year": year, "month": month}
            row.update({field: 0 for field in ZKB_STATS_MONTH_FIELDS})
        else:
            # 原样透传；缺失才用「这个键对应的年月」/0 兜底。
            # 不用 `x or default`：那会把合法的 0 / 0.0 也换掉，还会让 0.0 变成 int 0。
            row = {
                "year": _stats_number_or(source.get("year"), year),
                "month": _stats_number_or(source.get("month"), month),
            }
            row.update(
                {
                    field: _stats_number_or(source.get(field), 0)
                    for field in ZKB_STATS_MONTH_FIELDS
                }
            )
        data[key] = row
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return {
        "range": {
            "first": f"{first // 100:04d}-{first % 100:02d}",
            "last": f"{last // 100:04d}-{last % 100:02d}",
        },
        "observed_months": len(observed),
        "filled_internal_gaps": filled,
        "data": data,
    }


def _stats_block(kind: str, payload: dict) -> dict:
    """一个 `include` 块的内容（**不含**外壳的 `bytes` / `truncated`）。"""
    if kind == "months":
        return _stats_months_data(payload)
    if kind == "rankings":
        data = payload.get("rankings")
        return {"data": data if isinstance(data, dict) else {}}
    if kind == "top_all_time":
        data = payload.get("topAllTime")
        return {"data": data if isinstance(data, list) else []}
    data = payload.get("groups")
    return {"data": data if isinstance(data, dict) else {}}


def _stats_truncate(block: dict) -> tuple[dict, int]:
    """把超 `ZKB_STATS_BLOCK_MAX_BYTES` 的块按**原顺序保留前若干项**截断。

    Returns:
        `(块, 丢掉几项)`。没超就原样返回、丢 0 项（**绝大多数情况走这条**）。
    """
    if _stats_size(block) <= ZKB_STATS_BLOCK_MAX_BYTES:
        return block, 0
    data = block.get("data")
    dropped = 0
    while isinstance(data, (list, dict)) and data and _stats_size(block) > ZKB_STATS_BLOCK_MAX_BYTES:
        if isinstance(data, list):
            data.pop()
        else:
            data.popitem()
        dropped += 1
    return block, dropped


def _stats_include(include: object) -> tuple[str, ...]:
    """校验 `include`：`None` / 空 → 空元组；否则必须是白名单内 `str` 组成的列表。

    Raises:
        _ApiFailure: `invalid_argument`（类型不对 / 含未知块名，**不静默忽略**）。
    """
    if include is None:
        return ()
    if isinstance(include, str) or not isinstance(include, (list, tuple)):
        raise _ApiFailure(
            "invalid_argument",
            f"include 必须是字符串列表，收到 {type(include).__name__}",
            field="include",
            hint={"accepted": list(ZKB_STATS_BLOCKS)},
        )
    selected: list[str] = []
    for item in include:
        if not isinstance(item, str):
            raise _ApiFailure(
                "invalid_argument",
                f"include 的元素必须是字符串，收到 {type(item).__name__}",
                field="include",
                hint={"accepted": list(ZKB_STATS_BLOCKS)},
            )
        name = item.strip()
        if name not in ZKB_STATS_BLOCKS:
            raise _ApiFailure(
                "invalid_argument",
                f"不认识的 include 块 {item!r}",
                field="include",
                hint={"accepted": list(ZKB_STATS_BLOCKS)},
            )
        if name not in selected:
            selected.append(name)
    return tuple(selected)


def _stats_entity_type(entity_type: object) -> str:
    """校验 `entity_type`（必填；三个值之一，**不靠 id 猜**）。

    Raises:
        _ApiFailure: `invalid_argument`。
    """
    if entity_type is None:
        raise _ApiFailure(
            "invalid_argument",
            "entity_type 不能为空：同一串数字在角色 / 公司 / 联盟三个命名空间里都可能存在，"
            "必须显式指定",
            field="entity_type",
            hint={"accepted": list(ZKB_STATS_ENTITY_KINDS)},
        )
    if not isinstance(entity_type, str) or entity_type.strip() not in ZKB_STATS_ENTITY_KINDS:
        raise _ApiFailure(
            "invalid_argument",
            f"entity_type 必须是 {' / '.join(ZKB_STATS_ENTITY_KINDS)} 之一，收到 {entity_type!r}",
            field="entity_type",
            hint={"accepted": list(ZKB_STATS_ENTITY_KINDS)},
        )
    return entity_type.strip()


def zkb_stats_url(entity_type: str, entity_id: int) -> str:
    """拼一条实体画像 URL（**纯函数**：可离线断言，且保证结尾有 `/`）。"""
    segment = ZKB_STATS_SEGMENTS[entity_type]
    return ZKB_STATS_API.format(segment=segment, entity_id=entity_id)


def _stats_payload(url: str, payload: object, warnings: list[str]) -> dict:
    """校验响应正文 → 画像文档（**判成败只看正文，不看状态码**）。

    Raise:
        _ApiFailure: `unknown_entity`（`{"error": "Invalid type or id"}` / 空壳）/
            `rate_limited` / `network_failed`（`{"error": ...}` 的归类走**共用**的
            `_zkb_error_kind()`，与 `zkb_search` 同一份判定）/ `network_failed`（形状不对）。
    """
    if not isinstance(payload, dict):
        raise _ApiFailure(
            "network_failed",
            f"zkb 返回的不是对象：{type(payload).__name__}",
            url=url,
        )
    error = payload.get("error")
    if error:
        kind, why = _zkb_error_kind(error)
        raise _ApiFailure(
            kind,
            f"zkb 不认这次画像查询（{why}）：{error}",
            url=url,
            raw=str(error),
        )
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    if not info.get("name") and "shipsDestroyed" not in payload:
        # 实测 `stats/characterID/1/`：200 + 702 B 空壳（5 个键、info 无 name、战斗字段全缺）。
        warnings.append(
            "empty_entity: zkb 只返回了空壳（没有 name、没有战斗字段），"
            "这个 id 存在但没有任何可报告的画像"
        )
        raise _ApiFailure(
            "unknown_entity",
            "zkb 返回了空壳画像（没有 name、也没有任何战斗字段），这个实体没有可用的统计",
            url=url,
            warnings=list(warnings),
        )
    return payload


def zkb_stats(
    entity: object,
    *,
    entity_type: object = None,
    include: object = None,
    requester: ZkbRequester | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    **common: object,
) -> dict:
    """取一个实体（角色 / 公司 / 联盟）的**画像**：战斗总量、风格指标、7 天活动、6 类榜单。

    「这个人在 zkb 上是什么水平、打得多不多 / 亏不亏、主要在哪儿活动」。**只给事实**：
    四个 ratio 的公式 zkb 未公开，本层也不反推（`formula: null`），**不给「威胁等级」判词**。

    Args:
        entity: 名字或整数 id。名字走 ESI `/universe/ids/`（复用 `_zkb_resolve_entity`，
            带进程内缓存、与 `zkb_search` 同一套解析）。
        entity_type: **必填**，`character` / `corporation` / `alliance`。不靠 id 猜类型：
            zkb 自己会校验 id 与类型，错配会返回 `unknown_entity`。
        include: 可选大块白名单 `months` / `rankings` / `top_all_time` / `groups`。
            **没有「全量」开关** —— 全量联盟响应 121 KB，必须逐个点名。
        requester / clock / sleeper: 测试注入点（生产不传）。
        **common: 允许 `skill_tier` / `damage_profile` / `state_policy`（无影响）。

    Returns:
        成功时 `result`（**所有键恒在**）：

        ```json
        {"entity": {"type": "character", "id": 2117515414, "name": "SixtyEverSnake",
                    "ticker": null, "security_status": 4.3, "founded": "2020-09-03T21:05:25Z"},
         "combat": {"ships_destroyed": 823, "ships_lost": 384,
                    "isk_destroyed": 222758096336, "isk_lost": 45961938028,
                    "points_destroyed": 3693, "points_lost": 1622,
                    "efficiency": {"value": 0.829, "kind": "derived",
                                   "formula": "isk_destroyed / (isk_destroyed + isk_lost)",
                                   "note": "..."}},
         "style": {"danger_ratio": 69, "gang_ratio": 99, "solo_ratio": 1.8,
                   "avg_gang_size": 3.6, "solo_kills": 14, "solo_losses": 50,
                   "source": "zkillboard", "formula": null, "notes": "..."},
         "activity_7d": {"ships": 4, "systems": 5, "regions": 4,
                         "characters": null, "corporations": null, "kills": 8},
         "top": {"characters": [...], "corporations": [...], "alliances": [],
                 "ships": [...], "systems": [...], "locations": [...]},
         "warnings": []}
        ```

        `top` 的 6 类各取前 `ZKB_STATS_TOP_LIMIT` 条，元素恒为
        `{"id", "name", "count", "isk"}`（`count` 是 zkb 的 `kills`）。
        **空榜给 `[]`，键不省略** —— 形状是契约。

        `include` 的块挂在 `result.blocks.<name>`，每块带 `bytes`（该块去掉
        `bytes`/`truncated` 之后的紧凑 JSON 字节数）与 `truncated`；
        超 `ZKB_STATS_BLOCK_MAX_BYTES` 才截断并追加 warning。

        `meta` 给 `source` / `min_interval_s` / `urls` / `bytes`（本次结果体积）。

    失败时 `error.kind` ∈ `invalid_argument` / `unknown_entity` / `network_failed` /
    `rate_limited` / `internal_error`（空响应体）。
    """
    try:
        # 时间区间类参数先在这里拦：`_zkb_check_common()` 的提示是给列表接口写的
        # （「用 past_seconds + page 回溯」），而 `/api/stats/` 两个参数都不接受
        # —— 它返回的是全时期累计。照抄那条提示只会把人带偏。
        rejected = [name for name in common if name in ZKB_REJECTED_PARAMETERS]
        if rejected:
            raise _ApiFailure(
                "invalid_argument",
                f"{rejected[0]} 不受支持：/api/stats/ 不接受时间区间参数，"
                "它返回的是全时期累计（没有 past_seconds / page 这类窗口）",
                field=rejected[0],
                hint={"accepted": list(ZKB_IGNORED_PARAMETERS)},
            )
        _zkb_check_common(common)
        if entity is None:
            raise _ApiFailure(
                "invalid_argument",
                "entity 不能为空：给角色 / 公司 / 联盟的名字或整数 id",
                field="entity",
                hint={"accepted": ["实体名字", "整数 id"]},
            )
        kind = _stats_entity_type(entity_type)
        blocks = _stats_include(include)
        resolved = _zkb_resolve_entity(
            kind, entity, requester, clock=clock, sleeper=sleeper
        )
        url = zkb_stats_url(kind, int(resolved["id"]))
        warnings: list[str] = []
        try:
            payload = _zkb_request(url, requester=requester, clock=clock, sleeper=sleeper)
        except _ApiFailure as exc:
            if exc.kind == "network_failed" and exc.extra.get("payload_bytes") == 0:
                raise _ApiFailure(
                    "internal_error",
                    f"zkb 返回了**空响应体**（0 字节）：{url}；本层 URL 恒以 `/` 结尾，"
                    "空体说明上游异常（实测少写结尾斜杠就是这个现象），请稍后重试",
                    url=url,
                    hint={"url_ends_with_slash": True},
                    warnings=[
                        "empty_body: 上游返回 0 字节；本层 URL 构造恒以 `/` 结尾，"
                        "这不是参数问题"
                    ],
                ) from exc
            raise

        document = _stats_payload(url, payload, warnings)
        activity, reasons, activity_warnings = _stats_activity_block(document, kind)
        warnings.extend(activity_warnings)
        result: dict = {
            "entity": _stats_entity_block(document, kind, resolved.get("name")),
            "combat": _stats_combat_block(document),
            "style": _stats_style_block(document),
            "activity_7d": activity,
            "top": _stats_top_block(document),
            "note": ZKB_STATS_NOTE,
            "warnings": warnings,
        }
        if reasons:
            result["field_reasons"] = reasons

        if blocks:
            packed: dict = {}
            for name in blocks:
                block = _stats_block(name, document)
                block, dropped = _stats_truncate(block)
                size = _stats_size(block)
                block["bytes"] = size
                block["truncated"] = dropped > 0
                if dropped:
                    block["dropped_items"] = dropped
                    warnings.append(
                        f"truncated: {name} 超过 {ZKB_STATS_BLOCK_MAX_BYTES} B 上限，"
                        f"按原顺序截掉 {dropped} 项"
                    )
                packed[name] = block
            result["blocks"] = packed
            result["warnings"] = warnings
            total = _stats_size(result)
            if total > ZKB_STATS_RESPONSE_SOFT_BYTES:
                warnings.append(
                    f"large_response: 响应 {total / 1024:.1f} KB（>64 KB）；"
                    "建议按需只 include 1-2 块"
                )
                result["warnings"] = warnings

        meta: dict = {
            "spec_version": SPEC_VERSION,
            "source": "zkillboard",
            "min_interval_s": ZKB_MIN_INTERVAL_S,
            "top_limit": ZKB_STATS_TOP_LIMIT,
            "blocks": list(blocks),
            "urls": [url],
            "bytes": _stats_size(result),
        }
        return _success(result, meta)
    except _ApiFailure as exc:
        return _failure(exc.payload())
    except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得裸抛给 agent
        return _failure(
            {
                "kind": "internal_error",
                "message": "接口层内部异常，本次没有可用结果",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )


# =============================================================================
# 十五、相关战报（T-1/5）：`zkb_related` —— 一个星系在某个时间窗内的「双方战况」
# =============================================================================
#
# 为什么要有它
# ------------
# zkb 的列表接口**已停用** `startTime` / `endTime`（`because of abuse`），只能
# `pastSeconds` + `page` 往回翻 —— 等于**查不了任意历史窗口**。而 `/api/related/` 把时间
# 放在**路径里**：`/api/related/<systemID>/<YYYYMMDDHHMM>/`，于是任意历史窗口都能查。
#
# 它还给两样别处拿不到的东西：
# 1. **阵营划分**（`summary.teamA` / `teamB`）—— 谁和谁在打；
# 2. 每条 kill 的**完整明细块**（含 `zkb.hash`）—— 所以摘要里给出 `killmail_ids` 之后，
#    可以直接把它们丢给 `fit_killmail` 深挖。
#
# 实测（2026-10-20，python urllib 直连）：
#
# | 请求 | 结果 |
# |---|---|
# | `/api/related/30004614/202609091400/` | 200，**212,286 B**（AV-VB6 / Fountain；23v23，10+9 kill） |
# | `/api/related/30000142/202610191200/` | 200，38,778 B（Jita / The Forge；4v3，4+1 kill） |
#
# **原样返回会炸 context**（212 KB），所以本层只给摘要；`killmail_ids` 给全，
# 逐 kill 的明细只给前 `limit` 条并显式 `truncated`。
#
# 两条实测出来的「容易读错」的点（写进返回的 `notes`）：
# - **窗口不对称**：`exHours=1` 时实测 `startTime = time − 1h`、`endTime = time + 2h`
#   （`14:00` → `13:00..16:00`），**不是**对称的 ±exHours；
# - 相邻星系**没有**被统计：`adjacentSystems` 只给名字，`summary` 只含 `systemID` 一个星系。

ZKILL_RELATED_API: str = "https://zkillboard.com/api/related/{system_id}/{stamp}/"
"""相关战报接口模板。**时间在路径里**（`stamp` 固定 12 位 `YYYYMMDDHHMM`）。"""

ZKB_RELATED_PATH_RE: re.Pattern[str] = re.compile(
    r"/related/(?P<system>\d+)/(?P<stamp>\d{12})/?(?:\?.*)?$"
)
r"""认 `https://zkillboard.com/related/<sys>/<stamp>/` 与 `.../api/related/...` 两种链接。

`(?:\?.*)?` 吃掉查询串：主人贴的网页链接常带 `?page=1` 之类，那不该让整条解析失败。
"""

ZKB_RELATED_STAMP_RE: re.Pattern[str] = re.compile(r"^\d{12}$")
"""`time` 参数只认 12 位纯数字（`202609091400`）。

`2026-09-09 14:00` / `2026090914` / 13 位都要判 `invalid_argument` —— **不做宽松解析**：
把「我猜你是 14 点」当成用户意图，比直接报错危险得多。
"""

ZKB_RELATED_KILL_LIMIT: int = 20
"""每边最多给几条逐 kill 明细（`limit` 的默认值）。**`killmail_ids` 不受它限制**。"""

ZKB_RELATED_SIDES: tuple[str, ...] = ("teamA", "teamB")
"""阵营键名（zkb 原样）。"""


def zkb_related_url(system_id: int, stamp: str) -> str:
    """拼一条相关战报 URL（**纯函数**，可离线断言）。"""
    return ZKILL_RELATED_API.format(system_id=int(system_id), stamp=stamp)


def _related_stamp(value: object) -> str:
    """校验时间戳：只接受 12 位纯数字。"""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _ApiFailure(
            "invalid_argument",
            f"time 必须是 12 位数字串（YYYYMMDDHHMM），收到 {type(value).__name__}",
            field="time",
            hint={"format": "YYYYMMDDHHMM", "example": "202609091400"},
        )
    text = str(value).strip()
    if not ZKB_RELATED_STAMP_RE.match(text):
        raise _ApiFailure(
            "invalid_argument",
            f"time 必须是 12 位数字（YYYYMMDDHHMM），收到 {text!r}"
            f"（{len(text)} 位）",
            field="time",
            hint={"format": "YYYYMMDDHHMM", "example": "202609091400"},
        )
    return text


def _related_int(value: object) -> int | None:
    """zbk 的 id / 时间戳**有时是 `int`、有时是字符串**（JSON 对象的 key、`$numberLong`）。

    `_as_int()` 只收 `int`，直接拿它去读 `$numberLong`（`"1788963082000"`）或
    `kills` 的 key（`"138324530"`）会**静默得到 `None`** —— 那两个字段就永远丢了。
    这个助手把「整数或纯数字串」都收下，其它一律 `None`（不猜）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
    return None


def _related_target(
    url: object,
    system: object,
    time: object,
    *,
    requester: ZkbRequester | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> tuple[int, str, dict]:
    """`(url | system + time)` → `(system_id, stamp, resolved)`。

    规则（**不静默取一个**）：

    - 给了 `url` → 只认 `url`；**同时**给 `system` / `time` → `invalid_argument`
      （两套来源打架时猜一个，等于悄悄忽略另一个）；
    - 没给 `url` → 必须给 `system`（名字或 id，走 `_zkb_resolve_entity`）+ `time`。

    Returns:
        `(星系 id, 12 位时间戳, 回显用的 resolved 块)`。`url` 形态下 resolved 是
        `{"url": ..., "system_id": ..., "time": ...}`。

    Raises:
        _ApiFailure: `invalid_argument` / `unknown_entity`。
    """
    if url is not None:
        if system is not None or time is not None:
            raise _ApiFailure(
                "invalid_argument",
                "url 与 system/time 是两套来源，只能给一套（给 url 时不要再给 system/time）",
                field="url",
                hint={"either": ["url"], "or": ["system", "time"]},
            )
        if not isinstance(url, str) or not url.strip():
            raise _ApiFailure(
                "invalid_argument",
                f"url 必须是非空字符串，收到 {type(url).__name__}",
                field="url",
            )
        matched = ZKB_RELATED_PATH_RE.search(url.strip())
        if matched is None:
            raise _ApiFailure(
                "invalid_argument",
                f"这不是一条 related 链接：{url!r}；要 "
                "https://zkillboard.com/related/<systemID>/<YYYYMMDDHHMM>/ "
                "或 .../api/related/... 形态",
                field="url",
                hint={
                    "pattern": "/related/<systemID>/<YYYYMMDDHHMM>/",
                    "example": "https://zkillboard.com/related/30004614/202609091400/",
                },
            )
        system_id = int(matched.group("system"))
        stamp = matched.group("stamp")
        return system_id, stamp, {"url": url.strip(), "system_id": system_id, "time": stamp}

    if system is None:
        raise _ApiFailure(
            "invalid_argument",
            "要么给 url，要么给 system + time",
            field="system",
            hint={
                "example": {"system": "AV-VB6", "time": "202609091400"},
                "url_example": "https://zkillboard.com/related/30004614/202609091400/",
            },
        )
    entry = _zkb_resolve_entity(
        "solar_system", system, requester, clock=clock, sleeper=sleeper
    )
    stamp = _related_stamp(time)
    return int(entry["id"]), stamp, {"solar_system": entry, "time": stamp}


def _related_time(value: object) -> str | None:
    """`kills[*].dttm` = `{"$date": {"$numberLong": "<ms>"}}` → ISO 8601（UTC）。

    与 `fit_killmail` 的 `killmail_time` 同格式（`2026-09-09T14:00:00Z`），调用方不必
    为两处准备两种时间写法。解析不出来给 `None`（**不猜**）。
    """
    if not isinstance(value, dict):
        return None
    date = value.get("$date")
    if not isinstance(date, dict):
        return None
    milliseconds = _related_int(date.get("$numberLong"))
    if milliseconds is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(milliseconds / 1000.0))


def _related_side(block: object, limit: int) -> tuple[dict, list[int], int]:
    """一边（`summary.teamA` / `teamB`）→ `(摘要, killmail_id 列表, 被省略条数)`。

    人数 / 公司 / 联盟都是**去重计数**（同一个驾驶员可能出现在名单里多次：他有多条 kill）。
    `ships` 按「船名 + 舰级」聚合。
    """
    data = block if isinstance(block, dict) else {}
    roster = [entry for entry in (data.get("list") or []) if isinstance(entry, dict)]

    pilots: set[int] = set()
    corporations: set[int] = set()
    alliances: set[int] = set()
    ships: dict[tuple[str, str], int] = {}
    for entry in roster:
        pilot = _as_int(entry.get("characterID"))
        if pilot is not None:
            pilots.add(pilot)
        corporation = _as_int(entry.get("corporationID"))
        if corporation is not None:
            corporations.add(corporation)
        alliance = _as_int(entry.get("allianceID"))
        if alliance is not None and alliance != 0:  # 0 = 无联盟，不算一个「联盟」
            alliances.add(alliance)
        ship_name = entry.get("shipName")
        group_name = entry.get("groupName")
        if isinstance(ship_name, str) and ship_name:
            key = (ship_name, group_name if isinstance(group_name, str) else "")
            ships[key] = ships.get(key, 0) + 1

    ship_rows = [
        {"ship": ship, "group": group or None, "count": count}
        for (ship, group), count in sorted(
            ships.items(), key=lambda item: (-item[1], item[0][0])
        )
    ]

    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    isk = _stats_number(totals.get("total_price"))

    kills_raw = data.get("kills") if isinstance(data.get("kills"), dict) else {}
    kills: list[dict] = []
    for _key, kill in kills_raw.items():
        if not isinstance(kill, dict):
            continue
        kills.append(
            {
                # `killID` 缺失时回退到 `kills` 的 key（JSON 对象 key 是字符串）
                "killmail_id": _related_int(kill.get("killID")) or _related_int(_key),
                "damage_taken": _stats_number(kill.get("damage_taken")),
                "attacker_count": _stats_number(kill.get("attackerCount")),
                "time": _related_time(kill.get("dttm")),
            }
        )
    # 新的在前（与 `zkb_search` 的排序一致）；时间缺失的排最后，用 id 兜底。
    kills.sort(key=lambda row: (row["time"] or "", row["killmail_id"] or 0), reverse=True)
    all_ids = [row["killmail_id"] for row in kills if row["killmail_id"] is not None]

    summary = {
        "pilots": len(pilots),
        "corporations": len(corporations),
        "alliances": len(alliances),
        "ships": ship_rows,
        "isk": isk,
        "kills": kills[:limit],
        "kills_total": len(kills),
        # 明细被截了要说出来；`killmail_ids` 仍然是全的。
        "kills_truncated": len(kills) > limit,
    }
    return summary, all_ids, max(0, len(kills) - limit)


def _related_notes(document: dict, system_id: int,
                   omitted: dict[str, int], adjacent: dict) -> list[str]:
    """`notes`：**至少三条**「读之前必须知道」的口径。"""
    start = document.get("startTime")
    end = document.get("endTime")
    moment = document.get("time")
    ex_hours = _stats_number(document.get("exHours"))
    notes = [
        f"exHours={ex_hours} 是**窗口半径**，窗口并不对称：实测 startTime = time − exHours、"
        f"endTime = time + 2×exHours（time={moment} → {start}..{end}）。"
        "它是「这个时间点附近有多热闹」，**不等于整场战斗的时长**。",
        f"complete={document.get('complete')} 只是 zkb 自己的判断，"
        "`true` 不代表没有遗漏（它不解释判据）。",
    ]
    if adjacent:
        names = "、".join(str(name) for name in list(adjacent.values())[:4])
        notes.append(
            f"相邻星系**没有**被统计：adjacentSystems 只给名字（{names}…），"
            f"summary 只含 systemID={system_id} 这一个星系；要邻星系得各自再查一次。"
        )
    else:
        notes.append("zkb 没给 adjacentSystems：本窗口没有可列出的相邻星系。")
    if any(omitted.values()):
        detail = "、".join(f"{side} 省略 {count} 条" for side, count in omitted.items() if count)
        notes.append(
            f"逐 kill 明细按 limit 截断（{detail}），但 killmail_ids 是**全的** —— "
            "要明细就对里面的 id 调 fit_killmail。"
        )
    excluded = (
        document.get("summary", {}).get("excluded")
        if isinstance(document.get("summary"), dict)
        else None
    )
    if isinstance(excluded, list) and excluded:
        notes.append(
            f"zkb 排除了 {len(excluded)} 个实体（summary.excluded），它们**不在**上面两边的统计里。"
        )
    return notes


def zkb_related(
    url: object = None,
    system: object = None,
    time: object = None,
    *,
    limit: object = ZKB_RELATED_KILL_LIMIT,
    requester: ZkbRequester | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    **common: object,
) -> dict:
    """一个星系在**指定时间窗**内的相关战报：双方阵营、舰船构成、ISK、killmail id 清单。

    为什么需要它：zkb 的列表接口**已停用** `startTime` / `endTime`（abuse 防护），只能
    `pastSeconds` + `page` 往回翻；而 `/api/related/` 把时间放在**路径里**，所以
    **任意历史窗口**都能查。它还给阵营划分（`teamA` / `teamB`）—— 那是别处拿不到的。

    Args:
        url: 首选。`https://zkillboard.com/related/<systemID>/<YYYYMMDDHHMM>/`
            或 `.../api/related/...`。**给了它就别再给 `system` / `time`**（两套来源打架
            时猜一个等于悄悄忽略另一个 → `invalid_argument`）。
        system: 星系（名字或 id）；`url=None` 时必填。名字走 ESI `/universe/ids/`。
        time: 时间戳，**固定 12 位** `YYYYMMDDHHMM`（如 `202609091400`）。
            `2026-09-09 14:00` / `2026090914` / 13 位都会被拒 —— **不做宽松解析**。
        limit: 每边最多给几条逐 kill 明细（1..200，默认 20）。**不影响 `killmail_ids`**。
        requester / clock / sleeper: 测试注入点（生产不传）。

    Returns:
        成功时 `result`（原响应 212 KB → 摘要约 5 KB）：

        ```json
        {"system": {"id": 30004614, "name": "AV-VB6"},
         "region": "Fountain",
         "window": {"startTime": "2026-09-09 13:00", "endTime": "2026-09-09 16:00",
                    "exHours": 1, "complete": true},
         "sides": {"teamA": {"pilots": 23, "corporations": 12, "alliances": 4,
                             "ships": [{"ship": "Phoenix Navy Issue",
                                        "group": "Dreadnought", "count": 3}],
                             "isk": 1585949032.3,
                             "kills": [{"killmail_id": 138324530, "damage_taken": 450,
                                        "attacker_count": 4,
                                        "time": "2026-09-09T14:11:22Z"}],
                             "kills_total": 10, "kills_truncated": false},
                   "teamB": {"...": "同上"}},
         "killmail_ids": {"teamA": [138324530, "..."], "teamB": ["..."]},
         "notes": ["exHours 是窗口半径，…"],
         "truncated": {"kills": false, "limit": 20, "omitted": {"teamA": 0, "teamB": 0}}}
        ```

        命名约定（**故意混排，有规则**）：**回显 zkb 原生字段的地方保持它的拼写**
        （`window.startTime` / `exHours` / `complete`），**我们派生的字段一律 snake_case**
        （`killmail_id` / `damage_taken` / `attacker_count`）。

        `killmail_ids` 每边**给全**（按时间倒序）—— 下一步就是拿它调 `fit_killmail`。
        `sides.<side>.kills` 只给前 `limit` 条，被截时 `kills_truncated=true` 且
        `truncated.omitted` 给出每边省略了几条。

        `window.startTime` / `endTime` 是 **zkb 原文的字符串**（`YYYY-MM-DD HH:MM`），
        而 `kills[].time` 是本层转成的 **ISO 8601 UTC**（与 `fit_killmail` 的
        `killmail_time` 同格式）。

    失败时 `error.kind` ∈ `invalid_argument`（url 形态 / 时间戳 / 参数互斥）/
    `unknown_entity`（星系认不出）/ `rate_limited` / `network_failed`
    （`{"error": ...}` 的归类走**共用**的 `_zkb_error_kind()`）。
    """
    try:
        _zkb_check_common(common)
        base_limit = _zkb_integer(
            "limit", limit, default=ZKB_RELATED_KILL_LIMIT, minimum=1, maximum=ZKB_PAGE_SIZE
        )
        system_id, stamp, resolved = _related_target(
            url, system, time, requester=requester, clock=clock, sleeper=sleeper
        )
        target_url = zkb_related_url(system_id, stamp)
        payload = _zkb_request(
            target_url, requester=requester, clock=clock, sleeper=sleeper
        )
        if not isinstance(payload, dict):
            raise _ApiFailure(
                "network_failed",
                f"zkb 返回的不是对象：{type(payload).__name__}",
                url=target_url,
            )
        error = payload.get("error")
        if error:
            kind, why = _zkb_error_kind(error)
            raise _ApiFailure(
                kind,
                f"zkb 不认这次 related 查询（{why}）：{error}",
                url=target_url,
                raw=str(error),
            )
        summary_block = payload.get("summary")
        if not isinstance(summary_block, dict):
            raise _ApiFailure(
                "network_failed",
                "zkb 的 related 响应里没有 summary 块（可能是这个窗口没有任何战报）",
                url=target_url,
                raw=str(sorted(payload))[:200],
            )

        sides: dict[str, dict] = {}
        killmail_ids: dict[str, list[int]] = {}
        omitted: dict[str, int] = {}
        for side in ZKB_RELATED_SIDES:
            side_summary, side_ids, side_omitted = _related_side(
                summary_block.get(side), base_limit
            )
            sides[side] = side_summary
            killmail_ids[side] = side_ids
            omitted[side] = side_omitted

        adjacent = (
            payload.get("adjacentSystems")
            if isinstance(payload.get("adjacentSystems"), dict)
            else {}
        )
        notes = _related_notes(payload, system_id, omitted, adjacent)
        result = {
            "system": {"id": system_id, "name": payload.get("systemName")},
            "region": payload.get("regionName"),
            "window": {
                "startTime": payload.get("startTime"),
                "endTime": payload.get("endTime"),
                "exHours": _stats_number(payload.get("exHours")),
                "complete": payload.get("complete"),
            },
            "sides": sides,
            "killmail_ids": killmail_ids,
            "notes": notes,
            "truncated": {
                "kills": any(omitted.values()),
                "limit": base_limit,
                "omitted": omitted,
            },
        }
        meta = {
            "spec_version": SPEC_VERSION,
            "source": "zkillboard",
            "min_interval_s": ZKB_MIN_INTERVAL_S,
            "limit": base_limit,
            "resolved": resolved,
            "urls": [target_url],
            "note": "related 把时间放在路径里，所以能查任意历史窗口（列表接口的 startTime 已被停用）",
        }
        return _success(result, meta)
    except _ApiFailure as exc:
        return _failure(exc.payload())
    except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得裸抛给 agent
        return _failure(
            {
                "kind": "internal_error",
                "message": "接口层内部异常，本次没有可用结果",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )


# =============================================================================
# 十六、名字反查（T-3/5）：`sde_search` —— 中英文都能搜的 SDE 类型检索
# =============================================================================
#
# 为什么要有它：agent 经常只拿到用户口语里的名字（「损伤控制 II」/「vulcan 220」），
# 不知道确切英文名就调不了 `fit_parse`（那是 EFT 口径）。本工具把「人类说法 → type_id」
# 这件事变成本地一步，而不是让 agent 猜。
#
# 实测（本机索引库 noobbaozi-3607705.sqlite3）：
#
# - `types.name_zh` **53,288/53,288 全填**（占位类型也带中文，`CharacterType` 一族同理）；
# - 四级检索：`"autocannon vulcan 220"` 前三级 **0 命中**、第四级（词全命中）命中
#   `220mm Vulcan AutoCannon II`（2897）；
# - `kind` 按 `groups.category_id` 过滤（Ship=6 / Module=7 / Charge=8）：
#   `"Hawk"` → `kind=ship` 只有 `Hawk` / `Nighthawk`（涂装与蓝图被正确排除，**不靠名字猜**）。

SDE_SEARCH_KIND_ALL: str = "all"
"""`kind` 默认值：不过滤。"""

SDE_SEARCH_KINDS: dict[str, tuple[int, ...]] = {
    "ship": (6,),  # SDE category `Ship`
    "module": (7,),  # `Module`
    "charge": (8,),  # `Charge`
}
"""`kind` → SDE **category id**（`categories` 表实测：6 Ship / 7 Module / 8 Charge）。

**按 category 过滤，不靠名字猜**：名字里带 `Hawk` 的还有涂装（SKINs）和蓝图（Blueprint），
靠关键字筛会把它们当成船。
"""

SDE_SEARCH_LIMIT_DEFAULT: int = 10
SDE_SEARCH_LIMIT_MAX: int = 50
"""`limit` 的默认值与上限（上限 50：再多对 agent 没意义，只会把 context 撑爆）。"""

SDE_SEARCH_DID_YOU_MEAN_CHARS: int = 4
"""0 命中时把每个词截成前 N 字符再试（`autocannon vulcan 220` → `auto vulc 220`）。"""


def _sde_kind_categories(kind: object) -> tuple[int, ...] | None:
    """校验 `kind` → category id 元组（`all`/None → 不过滤）。

    Raises:
        _ApiFailure: `invalid_argument` + `field="kind"` + 合法取值。
    """
    if kind is None:
        return None
    if not isinstance(kind, str):
        raise _ApiFailure(
            "invalid_argument",
            f"kind 必须是字符串，收到 {type(kind).__name__}",
            field="kind",
            hint={"allowed": [SDE_SEARCH_KIND_ALL, *SDE_SEARCH_KINDS]},
        )
    normalized = kind.strip().lower()
    if normalized in ("", SDE_SEARCH_KIND_ALL):
        return None
    categories = SDE_SEARCH_KINDS.get(normalized)
    if categories is None:
        raise _ApiFailure(
            "invalid_argument",
            f"kind 只支持 {SDE_SEARCH_KIND_ALL} / {' / '.join(SDE_SEARCH_KINDS)}，收到 {kind!r}",
            field="kind",
            hint={"allowed": [SDE_SEARCH_KIND_ALL, *SDE_SEARCH_KINDS]},
        )
    return categories


def _sde_match_rank(row: dict, needle: str) -> int:
    """0 = 精确 / 1 = 前缀 / 2 = 其它（第四级或无命中前缀）。用于 `ambiguous` 判定。"""
    lowered = needle.strip().lower()
    for value in (row.get("name"), row.get("name_zh")):
        if isinstance(value, str) and value.strip():
            candidate = value.strip().lower()
            if candidate == lowered:
                return 0
            if candidate.startswith(lowered):
                return 1
    return 2


def _sde_match_row(row: dict, index: Index | None, locale: str) -> dict:
    """一行 `TypeRow` → `sde_search` 的 match 形状（含 category 与三态无关的元信息）。"""
    name, name_en = _localized_type_name(row, locale)
    block = {
        "type_id": row.get("type_id"),
        "name": name,
        "name_zh": row.get("name_zh"),
        "group": row.get("group_name"),
        "category": index.category_name(row.get("group_id")) if index else None,
        "meta_level": row.get("meta_level"),
        "published": row.get("published"),
    }
    if locale == LOCALE_ZH:
        block["name_en"] = name_en
    return block


def sde_search(
    query: object,
    *,
    kind: object = None,
    limit: object = SDE_SEARCH_LIMIT_DEFAULT,
    locale: object = LOCALE_EN,
) -> dict:
    """**名字反查**：自由文本（中英文都认）→ SDE 类型（`type_id` / 中英文名 / 组 / 类别）。

    为什么需要它：agent 常只拿到用户口语里的名字（「损伤控制 II」/「vulcan 220」），
    不知道确切英文名就调不了 `fit_parse`。本工具把「人类说法 → `type_id`」变成本地一步。

    Args:
        query: 自由文本。**中英文都认**、大小写不敏感、`%` / `_` 按**字面**匹配。
            多词查询按「**每个词都出现**」（顺序不限）匹配 —— `"autocannon vulcan 220"`
            能命中 `220mm Vulcan AutoCannon II`。
            **纯数字**时按 `type_id` 精确查（拿到 id 后想知道中文名时用）。
        kind: `all`（默认）/ `ship` / `module` / `charge`。按 SDE **category** 过滤
            （6 / 7 / 8），**不靠名字猜** —— 名字带 `Hawk` 的还有涂装和蓝图，关键字筛会误判。
            无人机 / 植入体等用 `kind=all`（本参数只覆盖这三类）。
        limit: 1..50，默认 10。
        locale: `en`（默认）/ `zh`。`zh` 时 `matches[].name` 是官方中文并多一个 `name_en`；
            `name_zh` **两种 locale 都给**（它是「中文名」这个字段本身，不是 locale 的产物）。

    Returns:
        成功时 `result`：

        ```json
        {"query": "损伤控制",
         "count": 3,
         "matches": [{"type_id": 2048, "name": "Damage Control II",
                      "name_zh": "损伤控制 II", "group": "Damage Control",
                      "category": "Module", "meta_level": 5, "published": true}],
         "ambiguous": false,
         "notes": ["…"]}
        ```

        - **`ambiguous: true`** = 有多于一个候选是**精确或前缀**命中 → 该追问用户，别挑第一个；
        - **0 命中**时给 `did_you_mean`（每个词截前 4 字符重试的结果，显式标注「近似结果，非精确」）；
        - `notes` 至少三条口径（中文名来源 / kind 口径 / ambiguous 语义）。

    失败时 `error.kind` ∈ `invalid_argument`（`query` 空、`kind` 非法、`locale` 非法、
    `limit` 越界）/ `index_unavailable`（索引库没就绪）。
    """
    try:
        index = get_index()
        if not isinstance(query, str) or not query.strip():
            raise _ApiFailure(
                "invalid_argument",
                "query 必须是非空字符串（中英文都行，如 '损伤控制' 或 'Damage Control II'）",
                field="query",
                hint={"examples": ["损伤控制", "Damage Control II", "autocannon vulcan 220"]},
            )
        needle = query.strip()
        resolved_kind = _sde_kind_categories(kind)
        resolved_locale = _check_locale(locale)
        resolved_limit = _zkb_integer(
            "limit",
            limit,
            default=SDE_SEARCH_LIMIT_DEFAULT,
            minimum=1,
            maximum=SDE_SEARCH_LIMIT_MAX,
        )
        kind_label = SDE_SEARCH_KIND_ALL if resolved_kind is None else next(
            name for name, ids in SDE_SEARCH_KINDS.items() if ids == resolved_kind
        )
        meta = {
            "spec_version": SPEC_VERSION,
            "kind": kind_label,
            "locale": resolved_locale,
            "limit": resolved_limit,
            "note": "sde_search 只查本地 SDE 索引，不联网",
        }

        # 纯数字 → 按 type_id 精确查（拿到 id 想知道中文名 / 类别时用）。
        if needle.isdigit():
            row = index.get_type(int(needle))
            matches = [] if row is None else [_sde_match_row(dict(row), index, resolved_locale)]
            return _success(
                {
                    "query": needle,
                    "count": len(matches),
                    "matches": matches,
                    "ambiguous": False,
                    "notes": [
                        f"query 是纯数字 → 按 type_id={int(needle)} 精确查（不是名字检索）。",
                        "中文名来自 SDE `types.name_zh`（本机索引 53,288/53,288 全填）。",
                        "id 不存在时 count=0 —— **不是**名字没匹配到，别混淆。",
                    ],
                },
                meta,
            )

        rows = index.search_types(needle, resolved_limit, category_ids=resolved_kind)
        matches = [_sde_match_row(dict(row), index, resolved_locale) for row in rows]
        ranks = [_sde_match_rank(dict(row), needle) for row in rows]
        prefix_hits = sum(1 for rank in ranks if rank <= 1)
        ambiguous = prefix_hits > 1

        notes = [
            "中文名来自 SDE `types.name_zh`（官方译名，不是机翻）；`name` 随 locale，"
            "`name_zh` 两种 locale 都给。",
            "`kind` 按 SDE category 过滤（ship=6 / module=7 / charge=8），**不靠名字猜**："
            "名字里带 `Hawk` 的还有涂装（SKINs）与蓝图（Blueprint），关键字筛会把它们当船。"
            "无人机 / 植入体等本参数不覆盖，用 `kind=all`。",
        ]
        if ambiguous:
            notes.append(
                f"**ambiguous: true** —— {prefix_hits} 个候选都是精确/前缀命中，"
                "该追问用户是哪一个，别挑第一个。"
            )
        if len(matches) >= resolved_limit:
            notes.append(
                f"结果达到 limit={resolved_limit}，**可能还有更多**：调大 limit 或用更具体的词。"
            )

        suggestion: dict | None = None
        if not matches:
            retry = " ".join(
                word[:SDE_SEARCH_DID_YOU_MEAN_CHARS] for word in needle.lower().split()
            )
            if retry and retry != needle.lower():
                retry_rows = index.search_types(
                    retry, resolved_limit, category_ids=resolved_kind
                )
                if retry_rows:
                    suggestion = {
                        "query": retry,
                        "count": len(retry_rows),
                        "matches": [
                            _sde_match_row(dict(row), index, resolved_locale)
                            for row in retry_rows
                        ],
                        "note": "近似结果，非精确 —— 用户说的可能不是这些，别直接当答案。",
                    }
        if not matches:
            if suggestion is not None:
                notes.append(
                    f"0 命中：把每个词截到 {SDE_SEARCH_DID_YOU_MEAN_CHARS} 字符重试才有结果，"
                    "见 did_you_mean（**近似结果，非精确**）。"
                )
            else:
                notes.append(
                    "0 命中：连「每词截 4 字符」的近似重试也没有结果 —— "
                    "这个名字可能不在 SDE 里（拼错 / 是外号 / 是玩家名，不是类型名）。"
                )

        result = {
            "query": needle,
            "count": len(matches),
            "matches": matches,
            "ambiguous": ambiguous,
            "notes": notes,
        }
        if suggestion is not None:
            result["did_you_mean"] = suggestion
        return _success(result, meta)
    except _ApiFailure as exc:
        return _failure(exc.payload())
    except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得裸抛给 agent
        return _failure(
            {
                "kind": "internal_error",
                "message": "接口层内部异常，本次没有可用结果",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )


# =============================================================================
# 十七、类型详情与可装性（T-4/5）：`sde_type` + `can_fit`
# =============================================================================
#
# 数据来自 v2 索引库的 dogma 表（`type_attributes` / `type_effects` / `effects` /
# `type_bonuses` / `buffs`）。**本层只查、不换算**：SDE 原值进出，单位名照抄。
#
# 实测锚点（type 4403 Reactive Armor Hardener，已写进测试）：
#   armorEmDamageResonance(267)=0.85 · duration(73)=10000 · resistanceShiftAmount(1849)=6.0
#   maxGroupFitted(1544)=1 · maxGroupActive(763)=1 · effect 4928=adaptiveArmorHardener

SDE_TYPE_ATTRIBUTES_DEFAULT: int = 60
"""`sde_type` 默认最多给几条属性（SDE 里有的类型有 200+ 条，全给会把 context 撑爆）。"""

SDE_TYPE_ATTRIBUTES_MAX: int = 300
"""`limit_attributes` 上限。"""

SDE_TYPE_INCLUDE_ALL: str = "all"
"""`include="all"` = 连「值是 0 且没有中文名」的噪声属性也给。"""

SDE_TYPE_REQUIRED_SKILL_ATTRS: tuple[tuple[int, int], ...] = (
    (182, 277),  # requiredSkill1 / requiredSkill1Level
    (183, 278),
    (184, 279),
    (185, 280),
    (186, 281),
    (187, 282),
)
"""`(requiredSkillN, requiredSkillNLevel)` 的属性 id —— 六级，SDE 的上限就是 6。"""

SDE_TYPE_LIMIT_ATTRS: dict[str, int] = {
    "max_group_fitted": 1544,
    "max_group_active": 763,
    "charge_group": 604,  # chargeGroup1（2/3/4 取第一个非零的）
}
"""`limits` 用到的属性 id（按 id 取，不按名字匹配 —— 名字在 SDE 里可能改）。"""

CAN_FIT_PROBE_FIT_NAME: str = "noobbaozi-can_fit-probe"
"""探针配装的 EFT 标题（只是给人看的，不参与计算）。"""

CAN_FIT_ASSUMPTIONS: tuple[str, ...] = (
    "技能档 all4+min_required（全部已发布技能 4 级，再按模块要求补高）",
    "探针配装：只有这艘船 + 这一个模块，`quantity=1`",
    "**不含已装模块的挤占** —— 真实配装里 CPU/PG 已被别的东西吃掉，可能更紧",
)


def _resolve_probe_type(value: object, field: str, *, kind: str) -> dict:
    """`type_id` 或名字 → TypeRow（`can_fit` 的船 / 模块解析）。

    Raises:
        _ApiFailure: `invalid_argument`（查不到 / 有歧义），带 `field` 与候选。
    """
    index = get_index()
    # `_related_int` 收「整数或纯数字串」—— CLI / MCP 传进来的 id 往往是字符串，
    # 用 `_as_int` 会静默当成「名字」去搜，然后 0 命中。
    identifier = None if isinstance(value, bool) else _related_int(value)
    if identifier is not None:
        row = index.get_type(identifier)
        if row is None:
            raise _ApiFailure(
                "invalid_argument",
                f"{field} 的 type_id={identifier} 不在 SDE 里",
                field=field,
                hint={"tip": "用 sde_search 先查名字"},
            )
        return dict(row)
    if isinstance(value, str) and value.strip():
        needle = value.strip()
        rows = index.search_types(needle, 5)
        exact = [
            row for row in rows
            if str(row.get("name", "")).casefold() == needle.casefold()
            or str(row.get("name_zh") or "").casefold() == needle.casefold()
        ]
        chosen = exact[0] if len(exact) == 1 else (rows[0] if len(rows) == 1 else None)
        if chosen is None:
            raise _ApiFailure(
                "invalid_argument",
                f"{field} 认不出唯一类型：{needle!r}"
                + (f"（{len(rows)} 个候选）" if rows else "（0 命中）"),
                field=field,
                hint={
                    "candidates": [
                        {"type_id": row.get("type_id"), "name": row.get("name"),
                         "name_zh": row.get("name_zh")}
                        for row in rows[:5]
                    ],
                    "tip": "先用 sde_search 把名字落实成 type_id，再传整数 id",
                },
            )
        return dict(chosen)
    raise _ApiFailure(
        "invalid_argument",
        f"{field} 必须是 type_id（整数）或名字（字符串），收到 {type(value).__name__}",
        field=field,
    )


def _sde_int(value: object) -> int | None:
    """SDE 的 id 类属性值常以 **REAL** 存（`182.0`）—— `_as_int()` 只收 `int`，会静默丢。

    这个助手接受「整数值的 int/float」，其余 `None`（不猜、不四舍五入）。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return int(number) if number.is_integer() else None


def _sde_type_noise(row: dict) -> bool:
    """「噪声属性」判据：值为 0 **且** 没有中文名 **且** 没有英文 display_name。

    默认过滤它们（SDE 里大量 `_unused` / 内部占位属性，值是 0、名字是内部标识符）；
    `include="all"` 时不过滤。
    """
    value = row.get("value")
    is_zero = not isinstance(value, (int, float)) or float(value) == 0.0
    has_zh = bool(str(row.get("display_name_zh") or "").strip())
    return is_zero and not has_zh


def sde_type(
    query: object,
    *,
    locale: object = LOCALE_ZH,
    include: object = None,
    limit_attributes: object = None,
) -> dict:
    """**类型详情**：属性 + effect + 装配限制 + 技能要求 + 船体加成（SDE dogma 原值）。

    想知道「这个模块到底有哪些属性 / 值是多少 / 怎么装 / 要什么技能 / 船有什么加成」时用它。
    `query` 可以是 `type_id` 或名字（名字走与 `sde_search` 同一套检索，**有歧义就报错不猜**）。

    Args:
        query: `type_id`（整数或纯数字串）或名字（中英文都认）。
        locale: `zh`（默认，属性名 / 组名给官方中文）/ `en`。
        include: `"all"` = 连噪声属性一起给；默认只给「值非 0 或 有中文名」的。
        limit_attributes: 最多几条属性，默认 60、上限 300；被截时 `truncated.attributes=true`。

    Returns:
        ```json
        {"type": {"id", "name", "name_zh", "group", "category", "meta_level", "published", "volume"},
         "attributes": [{"id", "name", "name_zh", "value", "unit"}],
         "effects": [{"id", "name", "name_zh", "duration_ms"}],
         "limits": {"max_group_fitted", "max_group_active", "slot", "charge_group"},
         "required_skills": [{"skill", "skill_id", "level"}],
         "bonuses": [{"skill", "text", "value", "kind"}],
         "truncated": {"attributes": false, "shown": 0, "total": 0},
         "notes": ["…"]}
        ```

        `attributes[].value` 是 **SDE 原值**（毫秒还是毫秒，不换算）；`unit` 是 SDE 的量纲名。
    """
    try:
        index = get_index()
        resolved_locale = _check_locale(locale)
        row = _resolve_probe_type(query, "query", kind="type")
        type_id = int(row["type_id"])

        default_limit = SDE_TYPE_ATTRIBUTES_DEFAULT
        limit = _zkb_integer(
            "limit_attributes",
            default_limit if limit_attributes is None else limit_attributes,
            default=default_limit,
            minimum=1,
            maximum=SDE_TYPE_ATTRIBUTES_MAX,
        )
        include_all = isinstance(include, str) and include.strip().lower() == SDE_TYPE_INCLUDE_ALL
        if include is not None and not include_all:
            raise _ApiFailure(
                "invalid_argument",
                f'include 只支持 "{SDE_TYPE_INCLUDE_ALL}"（或不给），收到 {include!r}',
                field="include",
                hint={"allowed": [None, SDE_TYPE_INCLUDE_ALL]},
            )

        raw_attributes = index.type_attributes(type_id)
        kept = raw_attributes if include_all else [r for r in raw_attributes if not _sde_type_noise(r)]
        shown = kept[:limit]

        def pick(name: str) -> float | None:
            for item in raw_attributes:
                if item.get("name") == name:
                    value = item.get("value")
                    return value if isinstance(value, (int, float)) else None
            return None

        by_attribute_id = {item.get("attribute_id"): item for item in raw_attributes}
        attributes = [
            {
                "id": item.get("attribute_id"),
                "name": item.get("name"),
                "name_zh": item.get("display_name_zh"),
                "value": item.get("value"),
                "unit": item.get("unit"),
            }
            for item in shown
        ]

        effects = []
        for effect in index.type_effects(type_id):
            duration_id = effect.get("duration_attribute_id")
            duration_value = by_attribute_id.get(duration_id, {}).get("value")
            effects.append(
                {
                    "id": effect.get("effect_id"),
                    "name": effect.get("name"),
                    "name_zh": effect.get("display_name_zh"),
                    # 库里是 SDE 原值（毫秒），字段名写明单位，免得被当秒读
                    "duration_ms": duration_value if isinstance(duration_value, (int, float)) else None,
                }
            )

        limits = {
            "max_group_fitted": pick("maxGroupFitted"),
            "max_group_active": pick("maxGroupActive"),
            "slot": pick("slot") if pick("slot") is not None else None,
            "charge_group": next(
                (
                    value
                    for value in (pick("chargeGroup1"), pick("chargeGroup2"), pick("chargeGroup3"))
                    if value
                ),
                None,
            ),
        }

        required_skills = []
        for skill_attr, level_attr in SDE_TYPE_REQUIRED_SKILL_ATTRS:
            skill_row = by_attribute_id.get(skill_attr)
            if skill_row is None:
                continue
            skill_id = _sde_int(skill_row.get("value"))
            level_row = by_attribute_id.get(level_attr)
            level = _sde_int(level_row.get("value")) if level_row else None
            skill_name = None
            skill_name_zh = None
            if skill_id is not None:
                skill_type = index.get_type(skill_id)
                if skill_type is not None:
                    skill_name = skill_type.get("name")
                    skill_name_zh = skill_type.get("name_zh")
            required_skills.append(
                {
                    "skill": skill_name_zh if resolved_locale == LOCALE_ZH and skill_name_zh else skill_name,
                    "skill_id": skill_id,
                    "level": level,
                    "name_en": skill_name,
                }
            )

        bonuses = []
        for bonus in index.type_bonuses(type_id):
            text = bonus.get("text_zh") if resolved_locale == LOCALE_ZH else bonus.get("text_en")
            skill_label = bonus.get("skill_name_zh") if resolved_locale == LOCALE_ZH else bonus.get("skill_name")
            bonuses.append(
                {
                    "skill": skill_label or bonus.get("skill_name"),
                    "skill_id": bonus.get("skill_type_id"),
                    "value": bonus.get("value"),
                    "kind": bonus.get("bonus_type"),
                    "text": text or bonus.get("text_en"),
                }
            )

        notes = [
            "属性值 / 单位都是 **SDE 原值**（`duration` 这类是毫秒，本层不换算）。",
            f"默认只给「值非 0 或 有中文名」的属性（过滤掉 {len(raw_attributes) - len(kept)} 条噪声）；"
            f'要全量传 include="{SDE_TYPE_INCLUDE_ALL}"。',
            "部分属性的 `name_zh` 为 **null**：SDE 的 `dogmaAttributes` 本身就没有该属性的 "
            "displayName（连英文 `display_name` 都没有，实测 1,620/2,871 条如此），"
            "**不是我们漏了** —— 这时请用 `name`（camelCase 内部名，如 `maxGroupActive`）"
            "判断含义，别把 null 当空串。本层**不维护任何手写中文映射表**（SDE 一更新就会过期）。",
            "`bonuses` 里 `kind=type` 是**每级**加成（值 = 每级 +N，**没乘技能等级**）；"
            "`kind=role` 是角色加成、不挂技能。",
        ]
        if not bonuses:
            notes.append("SDE 的 typeBonus 里没有这个类型的加成条目（不是我们丢了）。")

        result = {
            "type": {
                "id": type_id,
                "name": row.get("name") if resolved_locale == LOCALE_EN else (row.get("name_zh") or row.get("name")),
                "name_zh": row.get("name_zh"),
                "group": row.get("group_name"),
                "category": index.category_name(row.get("group_id")),
                "meta_level": row.get("meta_level"),
                "published": row.get("published"),
                "volume": row.get("volume"),
            },
            "attributes": attributes,
            "effects": effects,
            "limits": limits,
            "required_skills": required_skills,
            "bonuses": bonuses,
            "truncated": {
                "attributes": len(kept) > limit,
                "shown": len(shown),
                "total": len(kept),
            },
            "notes": notes,
        }
        if resolved_locale == LOCALE_ZH:
            result["type"]["name_en"] = row.get("name")
        return _success(
            result,
            {
                "spec_version": SPEC_VERSION,
                "locale": resolved_locale,
                "schema_version": "2",
                "note": "sde_type 只查本地 SDE 索引（v2 dogma 表），不联网",
            },
        )
    except _ApiFailure as exc:
        return _failure(exc.payload())
    except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得裸抛给 agent
        return _failure(
            {
                "kind": "internal_error",
                "message": "接口层内部异常，本次没有可用结果",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )


def _can_fit_blocker(violation: dict, locale: str) -> dict:
    """引擎违规 → `{kind, detail}`（**认识的就翻译，不认识的原样透传 kind**）。"""
    rule = violation.get("rule") if isinstance(violation.get("rule"), dict) else {}
    kind = rule.get("type")
    target = violation.get("target") if isinstance(violation.get("target"), dict) else {}
    if kind == "resource":
        resource = rule.get("resource")
        used = rule.get("used")
        available = rule.get("available")
        name = {"cpu": "CPU", "powergrid": "能量栅格", "calibration": "校准值"}.get(
            str(resource), str(resource)
        )
        detail = f"{name}不足：需要 {used}，可用 {available}"
        return {"kind": "resource", "resource": resource, "used": used,
                "available": available, "detail": detail}
    if kind == "skill":
        skill_id = rule.get("type_id")
        required = rule.get("required")
        skill_name = None
        if isinstance(skill_id, int):
            row = get_index().get_type(skill_id)
            if row is not None:
                skill_name = (
                    row.get("name_zh") if locale == LOCALE_ZH and row.get("name_zh") else row.get("name")
                )
        detail = f"技能不足：{(skill_name or skill_id)} 需要 {required} 级"
        return {"kind": "skill", "skill": skill_name, "skill_id": skill_id,
                "required": required, "detail": detail}
    if kind == "capital_item":
        return {
            "kind": "capital_item",
            "detail": "这是旗舰级装备，普通舰船装不上",
            "target": target,
        }
    # 不认识的规则：**原样透传**，不编中文
    return {"kind": kind, "detail": None, "rule": rule, "target": target}


def can_fit(
    ship: object,
    module: object,
    *,
    locale: object = LOCALE_EN,
    skill_tier: str = SKILL_TIER_ALL4,
) -> dict:
    """**「这船能装这个吗」**：探针配装 → 引擎 `validate` → 把违规翻成可读原因。

    做法（**假设写在返回的 `assumptions` 里，别当成真实配装结论**）：
    拼一条只含「这艘船 + 这一个模块」的 EFT，走引擎的归一化 + 校验，
    再把 `violations` 翻成中文；引擎给的规则类型我们不认识时**原样透传 kind**（不编）。

    Args:
        ship: 船（`type_id` 或名字）。名字走 SDE 检索，**有歧义就报错不猜**。
        module: 模块（`type_id` 或名字）。
        locale: `en`（默认）/ `zh` —— 影响 `blockers.detail` 与技能名。
        skill_tier: 技能档（默认 all4+min_required）。

    Returns:
        ```json
        {"fits": false,
         "blockers": [{"kind": "resource", "resource": "powergrid",
                       "used": 125000.0, "available": 56.25,
                       "detail": "能量栅格不足：需要 125000.0，可用 56.25"}],
         "remaining": {"cpu": 40.0, "powergrid": -124943.75, "calibration": 400.0},
         "slot_used": "high",
         "assumptions": ["技能档 all4+min_required（…）", "探针配装：…", "不含已装模块的挤占…"],
         "notes": ["…"]}
        ```

        `remaining` 是**探针配装**的余量（负数 = 超了），不是真实配装的余量。
    """
    try:
        _check_params(skill_tier, DAMAGE_PROFILE_UNIFORM, STATE_POLICY_ENGINE_DEFAULT)
        resolved_locale = _check_locale(locale)
        ship_row = _resolve_probe_type(ship, "ship", kind="ship")
        module_row = _resolve_probe_type(module, "module", kind="module")
        if ship_row.get("type_id") == module_row.get("type_id"):
            raise _ApiFailure(
                "invalid_argument",
                "ship 与 module 是同一个类型 —— 装不了，也不该拿它当探针",
                field="module",
            )

        eft = f"[{ship_row.get('name')}, {CAN_FIT_PROBE_FIT_NAME}]\n{module_row.get('name')}\n"
        resources_envelope = fit_index(eft, skill_tier=skill_tier)
        if resources_envelope.get("ok") is not True:
            error = resources_envelope.get("error") or {}
            raise _ApiFailure(
                "invalid_argument",
                f"这条探针配装引擎读不懂：{error.get('message')}",
                field="module",
                hint={"probe_eft": eft, "engine_kind": error.get("kind")},
            )
        resources = resources_envelope["result"].get("resources") or {}

        def free_of(name: str) -> object:
            block = resources.get(name)
            return block.get("free") if isinstance(block, dict) else None

        parsed = _parsed_fit({"text": eft})
        ctx = _context(parsed["fit"], str(skill_tier))
        violations = get_engine().validate(
            {**parsed["fit"], "character": {"skills": ctx.assumptions["skill_tier"]}}
        )
        blockers = [
            _can_fit_blocker(violation, resolved_locale)
            for violation in violations
            if isinstance(violation, dict)
        ]
        items = parsed["fit"].get("items") or []
        slot_used = None
        if items and isinstance(items[0].get("slot"), dict):
            slot_used = items[0]["slot"].get("type")

        notes = [
            "`fits: true` 只代表**这个探针配装**装得下 —— 真实配装里 CPU/PG 已被别的东西吃掉，"
            "可能装不下；反过来 `fits: false` 的 `blockers` 是确定的。",
        ]
        if not blockers:
            notes.append("没有违规：引擎对这艘船 + 这一个模块的组合没有报任何问题。")
        unknown = [b["kind"] for b in blockers if b.get("detail") is None]
        if unknown:
            notes.append(
                f"有 {len(unknown)} 条违规我们**不认识**（{unknown}）—— 已原样透传 `rule`，没有翻译。"
            )
        return _success(
            {
                "ship": {"type_id": ship_row.get("type_id"),
                         "name": ship_row.get("name_zh") if resolved_locale == LOCALE_ZH else ship_row.get("name"),
                         "name_en": ship_row.get("name")},
                "module": {"type_id": module_row.get("type_id"),
                           "name": module_row.get("name_zh") if resolved_locale == LOCALE_ZH else module_row.get("name"),
                           "name_en": module_row.get("name")},
                "fits": not blockers,
                "blockers": blockers,
                "remaining": {
                    "cpu": free_of("cpu"),
                    "powergrid": free_of("powergrid"),
                    "calibration": free_of("calibration"),
                },
                "slot_used": slot_used,
                "assumptions": list(CAN_FIT_ASSUMPTIONS),
                "notes": notes,
            },
            {
                "spec_version": SPEC_VERSION,
                "locale": resolved_locale,
                "probe_eft": eft,
                "note": "can_fit 走本地引擎 + 本地 SDE，不联网",
            },
        )
    except _ApiFailure as exc:
        return _failure(exc.payload())
    except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得裸抛给 agent
        return _failure(
            {
                "kind": "internal_error",
                "message": "接口层内部异常，本次没有可用结果",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )


__all__ = [
    "BY_PILOT_ROWS",
    "CACHE_MAX_ENTRIES",
    "CRIMINAL_SECURITY_THRESHOLD",
    "DAMAGE_CHECK_MATCH",
    "DAMAGE_CHECK_MISMATCH",
    "DAMAGE_PROFILES",
    "DAMAGE_PROFILE_UNIFORM",
    "ESI_ID_CATEGORIES",
    "ESI_UNIVERSE_IDS_API",
    "ESI_UNIVERSE_NAMES_API",
    "ESI_UNIVERSE_SYSTEM_API",
    "ENGAGEMENT_MODES",
    "ENGAGEMENT_MODE_ANCHOR",
    "ENGAGEMENT_MODE_EXCHANGE",
    "ENGAGEMENT_MODE_TIMELINE",
    "EXPLAIN_MAX_DEPTH",
    "EXPLAIN_METRICS",
    "HIGHSEC_THRESHOLD",
    "KILLMAIL_EHP_NOTE",
    "KILLMAIL_EHP_PATH",
    "KILLMAIL_ORIGIN_ESI",
    "KILLMAIL_ORIGIN_LOCAL",
    "KILLMAIL_ORIGIN_ZKILLBOARD",
    "LOWSEC_THRESHOLD",
    "LOCALE_EN",
    "LOCALE_ZH",
    "LOCALES",
    "NETWORK_TIMEOUT_S",
    "NOT_A_KILLMAIL_KIND",
    "PROJECT_STRICT_STATE_DEFAULT",
    "SDE_SEARCH_DID_YOU_MEAN_CHARS",
    "SDE_SEARCH_KINDS",
    "SDE_SEARCH_KIND_ALL",
    "SDE_SEARCH_LIMIT_DEFAULT",
    "SDE_SEARCH_LIMIT_MAX",
    "SECURITY_BAND_HIGHSEC",
    "SECURITY_BAND_LOWSEC",
    "SECURITY_BAND_NULLSEC",
    "SECURITY_BAND_WORMHOLE",
    "SENSITIVITY_MAX_OPS",
    "SENSITIVITY_METRICS",
    "SKILL_TIERS",
    "SKILL_TIER_ALL4",
    "SKILL_TIER_ALL5",
    "SPEC_VERSION",
    "STATE_POLICIES",
    "STATE_POLICY_ENGINE_DEFAULT",
    "UNIVERSE_NAMES_MAX_IDS",
    "UNIVERSE_NAMES_MIN_INTERVAL_S",
    "UNRESOLVED_NAMES_WARNING",
    "USER_AGENT",
    "WEAPON_FAMILIES",
    "WEAPON_FAMILY_OTHER",
    "ZKB_COMPLETE_KILLMAIL_NOTE",
    "ZKB_EMPTY_ITEMS_WARNING",
    "ZKB_ITEMS_FALLBACK_WARNING",
    "ZKB_API_ROOT",
    "ZKB_ERROR_UNKNOWN_ENTITY",
    "ZKB_FILTER_SEGMENTS",
    "ZKB_ID_CACHE_MAX",
    "ZKB_IGNORED_PARAMETERS",
    "ZKB_LIMIT_DEFAULT",
    "ZKB_MIN_INTERVAL_S",
    "ZKB_PAGE_SIZE",
    "ZKB_RATE_LIMIT_STATUSES",
    "ZKB_RATE_LIMIT_TOKENS",
    "ZKB_REJECTED_PARAMETERS",
    "ZKB_RELATED_KILL_LIMIT",
    "ZKB_RELATED_PATH_RE",
    "ZKB_RELATED_SIDES",
    "ZKB_RELATED_STAMP_RE",
    "ZKB_SEARCH_NOTE",
    "ZKB_SIDES",
    "ZKB_SIDE_BOTH",
    "ZKB_SIDE_KILLS",
    "ZKB_SIDE_LOSSES",
    "ZKB_STATS_ACTIVITY_KEYS",
    "ZKB_STATS_ACTIVITY_NOT_APPLICABLE",
    "ZKB_STATS_API",
    "ZKB_STATS_BLOCKS",
    "ZKB_STATS_BLOCK_MAX_BYTES",
    "ZKB_STATS_EFFICIENCY_NOTE",
    "ZKB_STATS_ENTITY_KINDS",
    "ZKB_STATS_ERROR_BODY",
    "ZKB_STATS_LOCATIONS_NOTE",
    "ZKB_STATS_MONTH_FIELDS",
    "ZKB_STATS_NOTE",
    "ZKB_STATS_RESPONSE_SOFT_BYTES",
    "ZKB_STATS_SEGMENTS",
    "ZKB_STATS_STYLE_NOTE",
    "ZKB_STATS_TOP_LIMIT",
    "ZKB_STATS_TOP_LISTS",
    "ZKB_USER_AGENT",
    "cache_stats",
    "clear_caches",
    "fetch_killmail",
    "fetch_killmail_report",
    "fit_detail",
    "fit_engagement",
    "fit_explain",
    "fit_get",
    "fit_index",
    "fit_killmail",
    "fit_project",
    "fit_sensitivity",
    "fit_view",
    "get_engine",
    "get_index",
    "killmail_ref_from_url",
    "parse_fit",
    "sde_search",
    "universe_names",
    "universe_system",
    "weapon_family",
    "zkb_related",
    "zkb_related_url",
    "zkb_search",
    "zkb_stats",
    "zkb_stats_url",
    "zkb_url",
]
