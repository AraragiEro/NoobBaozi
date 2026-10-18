"""L5 接口层（P4）：MCP / CLI / Skill **共用的唯一逻辑层**。

权威文档：`evefit/docs/输出分层与工具集-v1.md` §10（L5 接口层契约）。本模块只做
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
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Callable

from evefit.config import paths as _paths
from evefit.engine import adapter, normalize
from evefit.engine.assumptions import default_assumptions
from evefit.project import render
from evefit.project.views import ViewContext, available_views
from evefit.project.views.base import SLOT_CARGO
from evefit.sde.index import Index

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

CACHE_MAX_ENTRIES: int = 64
"""结果缓存的 LRU 容量（§10.6 要求 LRU；64 条覆盖一次战报的常见规模）。"""

USER_AGENT: str = "evefit/1.0 (L5 interface layer; +https://github.com/evefit)"
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
    "evefit/1.0 (L5 interface layer; zkb kill list only; +https://github.com/evefit)"
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
                        "先建库：python -c \"from evefit.sde.ingest import build_index; "
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
        raise _ApiFailure(
            "network_failed", f"{url} 返回的不是 UTF-8 JSON：{exc}", url=url
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
    """`meta.skill_tier` = `{name, count}`（**实际档名与实际技能数**，§10.2 硬要求）。"""
    return {"name": name, "count": len(skills)}


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
          state_policy: str, with_assumptions: bool = False) -> dict:
    """`meta` 块：版本三元组 + **实际**技能档 + 两个回显参数 + 输入形态。

    `with_assumptions=True` 时追加完整假设（**完整技能表只在这里**，§10.1 detail）。
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
    return meta


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


def _ship_block(ctx: ViewContext) -> dict:
    """`{type_id, name, group}`（名字 / 组名走 SDE 索引；查不到给 `None`，不编）。"""
    fit = ctx.fit if isinstance(ctx.fit, dict) else {}
    ship = fit.get("ship") if isinstance(fit.get("ship"), dict) else {}
    type_id = ship.get("type_id")
    row = None
    if isinstance(type_id, int) and ctx.index is not None:
        row = ctx.index.get_type(type_id)
    return {
        "type_id": type_id,
        "name": (row or {}).get("name"),
        "group": (row or {}).get("group_name"),
    }


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
) -> dict:
    """把输入（EFT / DNA / killmail）归一化成结构，并回显「我读懂了什么」。

    Args:
        fit: 四形态输入，见模块 docstring（自动判别，**不猜**）。
        skill_tier: `all4+min_required`（默认）/ `all5`。
        damage_profile: 只支持 `uniform`（见模块 docstring「已知边界」）。
        state_policy: 只支持 `engine_default`。

    Returns:
        成功时 `result` 是：

        ```json
        {"source_format": "eft",
         "fit": {...},                      // 引擎 fit 结构（原样，不加工）
         "warnings": ["module states are inferred by import rules"],
         "ship": {"type_id": 35683, "name": "Hecate", "group": "Tactical Destroyer"},
         "modules": {"fitted": 17, "cargo": 12, "by_slot": {"high": 6, ...}},
         "skills": {"name": "all4+min_required", "count": 512}}
        ```

        `fit` 是**引擎结构原样**：本层不加字段、不改值（下游 `fit_index` 等读的就是它）。
        注意它是**注入技能之前**的结构；本次真正用来计算的技能档在 `result.skills`
        （`{name, count}`），逐条技能表在 `fit_detail().meta.assumptions.skill_tier`
        （512 条，只在 detail 层给，§10.1）。
        `warnings` 一条不删（「舱内模块未计入」这类事实必须转达，见
        `engine/normalize.py` 模块 docstring 第 4 条）。
    """

    def compute(ctx: ViewContext, parsed: dict) -> tuple[object, bool]:
        assumptions = ctx.assumptions if isinstance(ctx.assumptions, dict) else {}
        result = {
            "source_format": parsed.get("source_format"),
            "fit": parsed["fit"],
            "warnings": list(parsed.get("warnings") or []),
            "ship": _ship_block(ctx),
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
        (),
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
        `result = {"ship", "meta", "views": {"<视角>": {...}}}`，形状恒定（`views` 恒为
        1–6 条）：

        - `meta` 比其它入口多一个 **`assumptions`**（含 512 条完整技能表）——
          这是「完整技能表只在 detail 层给」（§10.1）的落点；
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
        return {"ship": _ship_block(ctx), "views": entries}, True

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


def _system_block(system_id: object, warnings: list[str], names: dict[int, str]) -> dict:
    """`solar_system_id` → `{id, name, security: {status, band}}`（查不到给 `null` + `warnings`）。

    名字**不再单独发一次 names 请求**：`names` 是调用方一次批量查好的 `{id: name}`
    （星系 id 一起进了那一批），这里只取。取不到就退回 ESI 星系详情自带的 `name`。
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


def fetch_killmail_report(killmail_id: int, digest: str | None) -> dict:
    """取一条战报的**完整**上下文：zkillboard 的价值块 + ESI 的详情，合并成一份。

    与 `fetch_killmail()`（只给配装层要的那份 ESI 原文）并列存在，两者互不影响：

    1. 先 `GET zkillboard /api/killID/<id>/` —— 既拿 `zkb`（价值 / points / labels），
       也在 `digest is None` 时拿 `zkb.hash`（ESI 原文必须要它）；
    2. 再 `GET ESI /killmails/<id>/<hash>/` —— attackers / victim / `damage_taken`；
    3. 合并：**ESI 当底**（items 与 damage 以它为准），`zkb` 块原样附上。

    Raises:
        _ApiFailure: kind=`network_failed`（任一步失败都**不**拿半成品继续算）。
    """
    zkb_url = ZKILL_KILL_API.format(killmail_id=killmail_id)
    payload = _http_json(zkb_url)
    entry = _zkb_entry(payload)
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
    zkb_block = entry.get("zkb")
    if isinstance(zkb_block, dict):
        merged["zkb"] = zkb_block
    return merged


# ---- 十二之二：战报块的组装（只整理，不判断）--------------------------------


def _type_block(type_id: object, index: Index | None) -> dict:
    """`type_id` → `{type_id, name, group}`（名字 / 组名走本地 SDE；查不到给 `null`）。"""
    identifier = _as_int(type_id)
    row = None
    if identifier is not None and index is not None:
        row = index.get_type(identifier)
    return {
        "type_id": _as_int(type_id),
        "name": (row or {}).get("name"),
        "group": (row or {}).get("group_name"),
    }


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
) -> list[dict]:
    """按 `key_of` 分桶 → `[{<key_name>, count, damage, damage_pct}]`（按伤害降序）。"""
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
        }
        for key, bucket in grouped.items()
    ]
    rows.sort(key=lambda row: (-row["damage"], str(row[key_name] or "")))
    return rows


BY_PILOT_ROWS: int = 10
"""`attackers.by_pilot` 只给伤害最高的前 N 名（完整名单在原始 killmail 里，不值得刷屏）。"""


def _pilot_buckets(
    attackers: list[dict],
    damage_total: int | None,
    index: Index | None,
    names: dict[int, str],
) -> list[dict]:
    """按**驾驶员**聚合 → `[{character, corporation, alliance, ship, damage, …}]`（伤害降序前 10）。

    一个驾驶员在同一条战报里通常只有一条 attacker 记录，但模板化舰队战出现过同一角色
    多条目 —— 所以按 `character_id` 合并（合并的是**同一个人的伤害**，不是不同的人）。
    `character_id` 缺失时合并键是 `None`：那一 bucket 的 `character` 给 `null`，不编。
    """
    grouped: dict[object, dict] = {}
    for attacker in attackers:
        key = _as_int(attacker.get("character_id"))
        bucket = grouped.get(key)
        if bucket is None:
            bucket = grouped[key] = {
                "identity": _identity_blocks(attacker, names),
                "ship": _type_block(attacker.get("ship_type_id"), index),
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
) -> dict:
    """`attackers` 块：人数 / 总伤害 / 校验 / 最终一击 / 三个分桶 / 安全等级分布。"""
    damage_total = sum(_as_int(attacker.get("damage_done")) or 0 for attacker in attackers)
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
            "ship": _type_block(final.get("ship_type_id"), index),
            "weapon": _type_block(final.get("weapon_type_id"), index),
            "security_status": _as_float(final.get("security_status")),
        }

    statuses = [
        status
        for status in (_as_float(attacker.get("security_status")) for attacker in attackers)
        if status is not None
    ]

    block = {
        "count": len(attackers),
        "damage_total": damage_total,
        "damage_check": damage_check,
        "final_blow": final_block,
        "by_pilot": _pilot_buckets(attackers, damage_total, index, names),
        "by_ship_group": _buckets(
            attackers, lambda a: _type_block(a.get("ship_type_id"), index)["group"], "group", damage_total
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
) -> dict:
    """原始 killmail + `ViewContext` + 批量查好的 `names` → `result`（形状见 `fit_killmail`）。"""
    index = ctx.index
    victim = killmail.get("victim") if isinstance(killmail.get("victim"), dict) else {}
    if not isinstance(killmail.get("zkb"), dict):
        warnings.append("没有 zkb 价值块：value / flags / labels 给 null（不猜）")

    system = _system_block(killmail.get("solar_system_id"), warnings, names)
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
    return {
        "killmail": _killmail_block(killmail, system),
        "victim": {
            "damage_taken": victim_damage,
            "ship": _type_block(victim.get("ship_type_id"), index),
            **_identity_blocks(victim, names),
        },
        "attackers": _attacker_block(attackers, victim_damage, index, names),
        "context": _ehp_context(ctx, victim_damage),
        "killmail_source": killmail_source,
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


def _execute_killmail(
    fit: object,
    *,
    skill_tier: object,
    damage_profile: object,
    state_policy: object,
) -> dict:
    """`fit_killmail` 的执行骨架（与 `_execute()` 同构，只是载荷是**原始 killmail**）。

    Raises: 无 —— 任何异常都变成失败信封（§10.4）。
    """
    try:
        _check_params(skill_tier, damage_profile, state_policy)

        kind, content_key, payload = _killmail_descriptor(fit)
        cache_key = (
            None
            if content_key is None
            else (KILLMAIL_OP, (), content_key, skill_tier, damage_profile, state_policy)
        )
        if cache_key is not None:
            hit = _cache_get(cache_key)
            if hit is not None:
                return hit

        warnings: list[str] = []
        if kind == INPUT_NETWORK:
            killmail = fetch_killmail_report(payload["killmail_id"], payload["hash"])
            killmail_source = "network"
        else:
            killmail = _killmail_from_text(payload["text"])
            killmail_source = "local"

        # 索引先就位：船的 type/group 与武器族的判据都来自它（`_context()` 也要它）。
        _ensure_index()
        parsed = _parsed_fit({"text": json.dumps(killmail, ensure_ascii=False)})
        ctx = _context(parsed["fit"], str(skill_tier))

        # 名字**一次查完**：星系 + 受害者 + 全部攻击者（去重后一批 names 请求）。
        names = _resolve_identity_names(_killmail_name_ids(killmail), warnings)

        result = _killmail_report(killmail, ctx, killmail_source, warnings, names)
        meta = _meta(
            ctx,
            source_format=str(parsed.get("source_format")),
            damage_profile=str(damage_profile),
            state_policy=str(state_policy),
        )
        meta["killmail_source"] = killmail_source
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
         "attackers": {"count", "damage_total", "damage_check",
                       "final_blow": {"character": {"id", "name"},
                                      "corporation": {...}, "alliance": {...},
                                      "ship": {...}, "weapon": {...},
                                      "security_status"},
                       "by_pilot": [{"character", "corporation", "alliance", "ship",
                                     "damage", "damage_pct", "final_blow",
                                     "security_status"}],
                       "by_ship_group": [{"group", "count", "damage", "damage_pct"}],
                       "by_weapon_family": [{"family", "count", "damage", "damage_pct"}],
                       "security": {"min", "max", "criminal_count"}},
         "context": {"damage_taken_vs_ehp": {"damage_taken", "ehp_mixed", "ratio",
                                            "ehp_provenance"},
                     "note"},
         "killmail_source": "network" | "local",
         "warnings": [...]}
        ```

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
    kind: str, value: object, requester: ZkbRequester | None
) -> dict:
    """名字 / id → `{"id": int, "name": str|None}`（名字走 ESI，结果进进程内缓存）。

    判据（**不猜**）：

    - 整数（或纯数字字符串）→ 直接用，不联网；船型能顺手从 SDE 拿到名字；
    - 名字 → `POST /universe/ids/`，**只在该分类内**选：恰好一个精确同名 → 用它；
      没有精确同名但该分类只有一个结果 → 用它（ESI 已经给了唯一匹配）；其余
      （多个候选 / 一个都没有）→ `unknown_entity` + `candidates`（带 `kind`）。

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

    payload = _zkb_request(ESI_UNIVERSE_IDS_API, body=[text], requester=requester)
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
    values: dict[str, object], requester: ZkbRequester | None
) -> tuple[dict[str, dict], dict[str, int]]:
    """六个实体过滤参数 → `(resolved, filter_ids)`（`resolved` 回显给调用方）。"""
    resolved: dict[str, dict] = {}
    filter_ids: dict[str, int] = {}
    for field, _segment in ZKB_FILTER_SEGMENTS:
        raw = values.get(field)
        if raw is None:
            continue
        entry = _zkb_resolve_entity(field, raw, requester)
        resolved[field] = entry
        filter_ids[field] = int(entry["id"])
    return resolved, filter_ids


def _zkb_payload_rows(payload: object, url: str) -> list[dict]:
    """一次 zkb 响应 → 列表项字典（形状不对时给结构化错误，**不猜**）。

    Raises:
        _ApiFailure: `network_failed`（zkb 返回 `{"error": ...}` / 不是列表）。
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and payload.get("error"):
        raise _ApiFailure(
            "network_failed",
            f"zkb 拒绝了这次查询：{payload.get('error')}",
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
    "HIGHSEC_THRESHOLD",
    "KILLMAIL_EHP_NOTE",
    "KILLMAIL_EHP_PATH",
    "LOWSEC_THRESHOLD",
    "NETWORK_TIMEOUT_S",
    "NOT_A_KILLMAIL_KIND",
    "SECURITY_BAND_HIGHSEC",
    "SECURITY_BAND_LOWSEC",
    "SECURITY_BAND_NULLSEC",
    "SECURITY_BAND_WORMHOLE",
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
    "ZKB_API_ROOT",
    "ZKB_FILTER_SEGMENTS",
    "ZKB_ID_CACHE_MAX",
    "ZKB_IGNORED_PARAMETERS",
    "ZKB_LIMIT_DEFAULT",
    "ZKB_MIN_INTERVAL_S",
    "ZKB_PAGE_SIZE",
    "ZKB_RATE_LIMIT_STATUSES",
    "ZKB_REJECTED_PARAMETERS",
    "ZKB_SEARCH_NOTE",
    "ZKB_SIDES",
    "ZKB_SIDE_BOTH",
    "ZKB_SIDE_KILLS",
    "ZKB_SIDE_LOSSES",
    "ZKB_USER_AGENT",
    "cache_stats",
    "clear_caches",
    "fetch_killmail",
    "fetch_killmail_report",
    "fit_detail",
    "fit_get",
    "fit_index",
    "fit_killmail",
    "fit_view",
    "get_engine",
    "get_index",
    "killmail_ref_from_url",
    "parse_fit",
    "universe_names",
    "universe_system",
    "weapon_family",
    "zkb_search",
    "zkb_url",
]
