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

INVALID_PATH_KIND: str = "invalid_path"
"""与 §10.4 对齐的五种 `error.kind` 之外，本层另外用到（**只增不改**）：

- `invalid_argument`：`skill_tier` / `damage_profile` / `state_policy` / `view` 取值不合法；
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
    """清空结果缓存与计数（**幂等**）。测试用来把「冷启动」造出来。"""
    global _CACHE_HITS, _CACHE_MISSES
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_HITS = 0
        _CACHE_MISSES = 0


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


def _http_json(url: str) -> object:
    """GET 一个 JSON 文档（标准库 `urllib`，描述性 UA + 超时）。

    Raises:
        _ApiFailure: kind=`network_failed`（DNS / 超时 / HTTP 状态 / 非 JSON / I/O）。
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
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
    """五个入口共用的执行骨架：校验 → 缓存 → 解析 → 上下文 → 计算 → 信封。

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
# 十一、五个入口（§10.1）
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


__all__ = [
    "CACHE_MAX_ENTRIES",
    "DAMAGE_PROFILES",
    "DAMAGE_PROFILE_UNIFORM",
    "NETWORK_TIMEOUT_S",
    "SKILL_TIERS",
    "SKILL_TIER_ALL4",
    "SKILL_TIER_ALL5",
    "SPEC_VERSION",
    "STATE_POLICIES",
    "STATE_POLICY_ENGINE_DEFAULT",
    "USER_AGENT",
    "cache_stats",
    "clear_caches",
    "fetch_killmail",
    "fit_detail",
    "fit_get",
    "fit_index",
    "fit_view",
    "get_engine",
    "get_index",
    "killmail_ref_from_url",
    "parse_fit",
]
