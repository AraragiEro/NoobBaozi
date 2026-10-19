"""输入归一化（L2）：任意文本 → 引擎 `fit` 结构 + **可修复错误**。

输入形态：EFT 文本 / DNA / ESF（`%esf/1`）/ JSON（structured fit、ESI fitting、killmail）。

三条纪律：

1. **不猜歧义**（红线 N2）。判定依据不足就返回 `source_format="unknown"` 加一条错误，
   绝不"挑一个最像的"。
2. **不抛裸异常**（规范 §4.3）。引擎的 `ValueError` 一律转成 `NormalizeError`，
   原始消息原样留在 `raw` 里，方便定位。
3. **状态是引擎补的**。导入规则（武器/推进/修复 → active）由引擎在 `calculate`
   阶段套用，本层不做任何 state 推断，只用 `warnings` 明示这一事实。
4. **丢了什么要说出来**（T-22 / 字段规格 §0.6）。killmail 里落在**非槽位 flag**
   （至少覆盖 177 `SubSystemBay`）的模块，引擎根本不会装进 `fit`；本层**不改还原结果**，
   只在 `warnings` 里追加一条「舱内另有 N 件模块，其效果未计入」。静默丢失会让
   `resources` 把「真实不可能在线」的模块当实占用（f4：`cpu.load 10572.1` vs 可飞 472.1）。

`EFT` 额外做一次**结构预扫描**（自己按行解析，不依赖引擎），目的是在引擎报错之前
给出「第几行、原文、失败位置」这种可读错误；预扫描只判断行的**形态**，不校验名字。
名字候选（`candidates`）是**可选增强**：调用方用 `set_index()` 注入索引后，模块名
失败行会带 `index.search_types(原文, limit=3)` 的名字候选；**未注入时候选恒为空列表，
`normalize_fit` 的行为与 P1 完全一致**（不得让归一化依赖全局可变状态完成基本功能）。

已知边界（不要在 P1 之后又猜回来）：

- 引擎的 EFT 解析器不认 `Offline` 后缀（实测 `unknown type Damage Control II Offline`）。
  预扫描把它当作合法模块行，于是这条会以「模块名未知」的形式报出来，而不是静默当成在线。
- `load_esf` 只吃 `%esf/1` 文本，**不是** EVEShip.fit 的 base64 链接。
"""

from __future__ import annotations

import json
import re

from noobbaozi import contracts
from noobbaozi.engine import adapter

STATE_WARNING = "module states are inferred by import rules"
"""成功导入时的固定提示：state 由引擎导入规则推定，不是输入里给出的。"""

SOURCE_FORMATS = ("eft", "dna", "esf", "esi_fitting", "killmail", "structured")

# EFT 首行：[船名, 配装名]（逗号是必须的，否则 `[Empty High slot]` 会被误判成船名行；
# 配装名可以为空，且允许包含逗号）
_EFT_HEADER_RE = re.compile(r"^\[(?P<ship>[^,\[\]]+),(?P<fit_name>.*)\]$")
# 首行无配装名：`[Rifter]`。真实 EFT 文件两种写法都存在（EFT 自己也能读无逗号版），
# 而我们的 `eft` 渲染器对「无名配装」正是写 `[Rifter]` —— 解析器不认就等于
# `parse_fit()["eft"]` 的输出自己解析不了（DNA / ESF / killmail 输入都没有配装名）。
# 只在不命中 `_EMPTY_SLOT_RE` 时才当首行，避免把 `[Empty High slot]` 认成船。
_EFT_HEADER_NO_NAME_RE = re.compile(r"^\[(?P<ship>[^,\[\]]+)\]$")
# [Empty High slot] / [Empty Rig slot] / [Empty Subsystem slot] ...
_EMPTY_SLOT_RE = re.compile(r"^\[Empty [^\[\]]+\]$", re.IGNORECASE)


def _match_eft_header(line: str) -> re.Match[str] | None:
    """匹配 EFT 首行（`[Ship, name]` 或 `[Ship]`），排除 `[Empty X slot]`。

    两种写法都接受：有逗号的优先；无逗号的必须不是空槽占位行。
    """
    matched = _EFT_HEADER_RE.match(line)
    if matched is not None:
        return matched
    if _EMPTY_SLOT_RE.match(line):
        return None
    return _EFT_HEADER_NO_NAME_RE.match(line)


# 尾部 Offline 标记（引擎不认，预扫描只负责识别形态）
_OFFLINE_SUFFIX_RE = re.compile(r"(?:^|(?<=[\s\]\)]))offline\s*$", re.IGNORECASE)
# 尾部数量：`Warrior II x5` / `Antimatter Charge S x100`
_QUANTITY_RE = re.compile(r"^(?P<base>.*?)\s+x(?P<quantity>\d+)\s*$", re.IGNORECASE)
# DNA 数值形态：`35683:2048;1::`
_NUMERIC_DNA_RE = re.compile(r"^\d+(?::\d+(?:;\d+)?)+:*$")
# base64-ish（EVEShip.fit 之类的长串）
_BASE64ISH_RE = re.compile(r"^[A-Za-z0-9+/=_-]{20,}$")


# ---- 可选索引注入：失败行的名称候选（契约 `NormalizeError.candidates`）----
#
# 索引是**可选增强**，不是解析前提：
# - 未注入 → `candidates` 恒为空列表，`normalize_fit` 的行为与本层 P1 完全一致；
# - 注入后 → 只给「模块名」失败行补最多 `CANDIDATE_LIMIT` 个最接近的类型名，
#   **绝不自动替换**（红线 N2：歧义报错不猜，哪怕只有一个候选）；
# - 船名行 / 格式本身的失败**不给候选**（那两类没有"最接近的类型名"这回事）。

CANDIDATE_LIMIT = 3
"""失败行最多给几个名称候选（`NormalizeError.candidates` 的长度上限）。"""

#: 引擎未知类型消息的固定前缀（实测：`unknown type Damage Controll II`）。
_UNKNOWN_TYPE_PREFIX = "unknown type "

_INDEX: object | None = None
"""已注入的索引（鸭子类型：有 `search_types(query, limit=...)` 即可）。"""


def set_index(index: object) -> None:
    """注入索引，用于给失败行补名称候选。

    Args:
        index: `noobbaozi.sde.index.Index` 实例，或任何具备
            `search_types(query, limit=...)` 方法的对象（便于测试替身）。

    Raises:
        TypeError: 对象没有可调用的 `search_types`。

    说明：只在注入后**增强**诊断信息，`normalize_fit` 的基本功能不依赖它
    （未注入时候选为空列表）。由组合根（API / CLI 启动处）在打开索引后调用一次，
    用完 `clear_index()`。
    """
    if not callable(getattr(index, "search_types", None)):
        raise TypeError(
            "set_index() 需要具备 search_types(query, limit=...) 方法的对象，"
            f"实际收到 {type(index).__name__}"
        )
    global _INDEX
    _INDEX = index


def clear_index() -> None:
    """撤销注入，回到"候选恒为空"的原始行为。**幂等**。"""
    global _INDEX
    _INDEX = None


def _candidate_names(query: str | None) -> list[str]:
    """向注入的索引要最多 `CANDIDATE_LIMIT` 个名称；没有索引就返回空列表。

    索引查询的任何异常（库已 `close()`、替身实现不全等）都**不得**让归一化失败，
    因此这里吞掉异常并退化成"无候选"——候选是诊断信息，不是解析前提。
    """
    index = _INDEX
    if index is None or not query:
        return []
    try:
        rows = index.search_types(query, limit=CANDIDATE_LIMIT)  # type: ignore[attr-defined]
        names: list[str] = []
        for row in rows or []:
            name = row.get("name") if isinstance(row, dict) else None
            if not isinstance(name, str) or not name or name in names:
                continue
            names.append(name)
            if len(names) >= CANDIDATE_LIMIT:
                break
        return names
    except Exception:  # 边界：候选增强失败不能影响主流程
        return []


def _unknown_type_name(message: str) -> str | None:
    """从引擎的 `unknown type X` 消息里取出名字 X；不是这种消息返回 `None`。"""
    position = message.lower().find(_UNKNOWN_TYPE_PREFIX)
    if position < 0:
        return None
    name = message[position + len(_UNKNOWN_TYPE_PREFIX) :].strip()
    return name or None


def _failing_name(error: contracts.NormalizeError) -> str | None:
    """失败行的「原文」类型名，用来查候选；取不到返回 `None`。

    - 引擎消息（`unknown type X`）：取 X，就是用户写错的那个名字；
    - 预扫描的形态错误：`raw` 就是那一行原文，尝试按模块行解析出名字
      （`[]` 这类连名字都提取不出的格式问题 → `None`，即不给候选）。
    """
    unknown = _unknown_type_name(error.get("raw") or "")
    if unknown:
        return unknown
    return _parse_module_line((error.get("raw") or "").strip())


def _enrich_errors(
    errors: list[contracts.NormalizeError], ship_name: str | None
) -> list[contracts.NormalizeError]:
    """给失败补 `candidates`（并纠正被引擎误标成模块名的船名行）。

    Args:
        errors: 原始失败列表（**不改入参**，返回新的 dict 列表）。
        ship_name: EFT 头里的船名；`None` 表示这不是带头的 EFT 文本 →
            不做名称候选（DNA / JSON 只有 ID，名字候选没有意义）。

    红线 N2 的落点：即使索引只给回一个候选，也只写进 `candidates`，绝不代填。
    """
    if ship_name is None:
        return list(errors)

    enriched: list[contracts.NormalizeError] = []
    for error in errors:
        fixed: contracts.NormalizeError = dict(error)  # type: ignore[assignment]
        if fixed["failed_at"] == "module_name" and not fixed["candidates"]:
            name = _failing_name(fixed)
            if name is not None and name.casefold() == ship_name.strip().casefold():
                # 引擎把"船名未知"也报成 `unknown type X`：这是船名行，不是模块行。
                fixed["failed_at"] = "ship_name"
            else:
                fixed["candidates"] = _candidate_names(name)
        enriched.append(fixed)
    return enriched


# ---- 结果与错误的构造 ----


def _error(
    *,
    failed_at: str,
    reason: str,
    raw: str = "",
    line: int | None = None,
    candidates: list[str] | None = None,
) -> contracts.NormalizeError:
    return {
        "line": line,
        "raw": raw,
        "failed_at": failed_at,
        "candidates": list(candidates or []),
        "reason": reason,
    }


def _ok(
    fit: dict, source_format: str, *, states_inferred: bool = True
) -> contracts.NormalizeResult:
    return {
        "fit": fit,
        "source_format": source_format,
        "errors": [],
        "warnings": [STATE_WARNING] if states_inferred else [],
    }


def _fail(
    source_format: str,
    errors: list[contracts.NormalizeError],
    *,
    ship_name: str | None = None,
) -> contracts.NormalizeResult:
    """失败结果。`ship_name` 只在 EFT 分支传入（见 `_enrich_errors`）。"""
    return {
        "fit": None,
        "source_format": source_format,
        "errors": _enrich_errors(errors, ship_name),
        "warnings": [],
    }


# ---- 形态判定 ----


def _first_line(text: str) -> str:
    lines = text.splitlines()
    return lines[0].strip() if lines else ""


def _looks_like_dna(text: str) -> bool:
    compact = "".join(text.split())
    if not compact:
        return False
    if text[: len("fitting:")].lower() == "fitting:":
        return True
    if _NUMERIC_DNA_RE.match(compact):
        return True
    # 长串 base64-ish：没有空白、只含 base64 字符，且不是纯字母单词
    if " " not in text.strip() and "\n" not in text.strip():
        if _BASE64ISH_RE.match(compact) and not compact.isalpha():
            return True
    return False


def _detect_format(text: str) -> str:
    """返回 `eft` / `dna` / `esf` / `json` / `unknown`。"""
    if text.startswith("%esf/1"):
        return "esf"
    if _match_eft_header(_first_line(text)):
        return "eft"
    if text.startswith("{") or text.startswith("["):
        # 不是 EFT 头的 `[...]`，只能是 JSON（数组）或格式错误
        return "json"
    if _looks_like_dna(text):
        return "dna"
    return "unknown"


# ---- EFT 结构预扫描（只判形态，不查名字）----


def _strip_offline_marker(line: str) -> str:
    return _OFFLINE_SUFFIX_RE.sub("", line).strip()


def _parse_module_line(line: str) -> str | None:
    """把一条模块行解析成模块名；无法归类返回 None。

    认得三种写法：`Damage Control II`、`Weapon, Ammo Name`、`Weapon, Ammo Name x100`
    以及 `Warrior II x5`（无人机），并允许尾部 `Offline`。
    """
    if line.startswith("[") or "[" in line or "]" in line:
        return None

    body = _strip_offline_marker(line)
    if not body:
        return None

    name_part = body.split(",", 1)[0].strip()
    quantity = _QUANTITY_RE.match(name_part)
    if quantity is not None:
        name_part = quantity.group("base").strip()

    if not name_part:
        return None
    if not any(ch.isalnum() for ch in name_part):
        return None
    return name_part


def _prescan_eft(text: str) -> list[contracts.NormalizeError]:
    """按行检查 EFT 结构，返回可读错误列表（空列表 = 形态没问题）。"""
    errors: list[contracts.NormalizeError] = []
    seen_header = False

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        if _match_eft_header(line) is not None:
            if seen_header:
                errors.append(
                    _error(
                        line=lineno,
                        raw=line,
                        failed_at="ship_name",
                        reason="malformed",
                    )
                )
            seen_header = True
            continue

        if _EMPTY_SLOT_RE.match(line):
            if not seen_header:
                errors.append(
                    _error(
                        line=lineno,
                        raw=line,
                        failed_at="ship_name",
                        reason="malformed",
                    )
                )
            continue

        module_name = _parse_module_line(line)
        if module_name is None or not seen_header:
            errors.append(
                _error(
                    line=lineno,
                    raw=line,
                    failed_at="ship_name" if not seen_header else "module_name",
                    reason="malformed",
                )
            )

    if not seen_header and not errors:
        errors.append(
            _error(
                line=None,
                raw=_first_line(text),
                failed_at="ship_name",
                reason="malformed",
            )
        )
    return errors


# ---- 引擎异常 → NormalizeError 的映射 ----


def _classify_engine_message(message: str) -> tuple[str, str]:
    """把引擎的 `ValueError` 消息映射成 (failed_at, reason)。

    引擎消息实测形态：`invalid EFT header` / `empty EFT` / `unknown type X` /
    `type id N is not a ship` / `the DNA has no ship` / `missing field `name`` /
    `line 1: malformed header`。

    注意：引擎对**船名**未知也报 `unknown type X`（实测 `unknown type NoSuchShipXyz`），
    这里只能先归到 `module_name`；随后 `_enrich_errors()` 用 EFT 头的船名把这类
    误标纠正回 `ship_name`（并因此不给模块候选）。
    """
    lowered = message.lower()
    if "unknown type" in lowered:
        return "module_name", "unknown"
    if "not a ship" in lowered or "no ship" in lowered:
        return "ship_name", "malformed"
    if "malformed" in lowered or "invalid" in lowered or "empty" in lowered:
        return "format", "malformed"
    if "missing field" in lowered:
        return "format", "malformed"
    return "format", "unknown"


def _engine_error(exc: Exception) -> contracts.NormalizeError:
    failed_at, reason = _classify_engine_message(str(exc))
    return _error(failed_at=failed_at, reason=reason, raw=str(exc))


# ---- 各形态的导入分支 ----


def _eft_ship_name(text: str) -> str | None:
    """EFT 头里的船名（用于区分"船名行失败"与"模块名行失败"）；取不到返回 `None`。"""
    match = _match_eft_header(_first_line(text))
    return match.group("ship").strip() if match else None


def _patch_header_for_engine(text: str) -> str:
    """把无配装名的首行 `[Ship]` 补成 `[Ship, ]` 再交给引擎。

    引擎（Rust）的 EFT 解析器要求首行必须有逗号，而真实 EFT 文件与我们自己的
    `eft` 渲染器对「无名配装」都写 `[Ship]`。这是**输入侧宽容**，不改引擎、不改输出格式。
    """
    first = _first_line(text)
    if _EFT_HEADER_RE.match(first) is not None or _EMPTY_SLOT_RE.match(first):
        return text
    matched = _EFT_HEADER_NO_NAME_RE.match(first)
    if matched is None:
        return text
    rest = text.split("\n", 1)
    patched = f"[{matched.group('ship').strip()}, ]"
    return patched if len(rest) == 1 else patched + "\n" + rest[1]


def _normalize_eft(text: str) -> contracts.NormalizeResult:
    ship_name = _eft_ship_name(text)
    errors = _prescan_eft(text)
    if errors:
        return _fail("eft", errors, ship_name=ship_name)
    try:
        fit = adapter.load_eft(_patch_header_for_engine(text))
    except Exception as exc:  # 边界层：引擎异常一律转成可修复错误
        return _fail("eft", [_engine_error(exc)], ship_name=ship_name)
    return _ok(fit, "eft")


def _strip_dna_prefix(text: str) -> str:
    """`fitting:` 是 EVE 聊天里的 DNA 前缀，引擎的 `load_dna` 只吃后面的裸 DNA。"""
    compact = text.strip()
    if compact[: len("fitting:")].lower() == "fitting:":
        return compact[len("fitting:") :].strip()
    return compact


def _normalize_dna(text: str) -> contracts.NormalizeResult:
    try:
        fit = adapter.load_dna(_strip_dna_prefix(text))
    except Exception as exc:
        return _fail("dna", [_engine_error(exc)])
    return _ok(fit, "dna")


def _normalize_esf(text: str) -> contracts.NormalizeResult:
    try:
        fit = adapter.load_esf(text)
    except Exception as exc:
        return _fail("esf", [_engine_error(exc)])
    return _ok(fit, "esf")


def _structured_states_inferred(fit: dict) -> bool:
    items = fit.get("items") or []
    return any(not isinstance(item, dict) or "state" not in item for item in items)


# ---- killmail 的「舱内物品」：不在已装槽位的模块（T-22 / 字段规格 §0.6）----
#
# 背景（实测，见 PROGRESS §3 第 137 条）：`load_killmail` 只还原**槽位 flag** 上的物品，
# flag 177（SubSystemBay 子系统舱）里的东西**没有静默丢弃的提示**——f4 的
# `Loki Defensive - Covert Reconfiguration`（type 45595）就躺在 177 里，它是唯一能免掉
# covert cloak 那 10000 CPU 罚值的子系统；丢掉它之后，`resources` 把「真实不可能在线」
# 的 cloak 当成实占用（`cpu.load 10572.1` vs 真实可飞 472.1）。
#
# 处置边界（**不得越过**）：
# - 只追加 `warnings`，**不改还原结果**（那件装备仍然不装，引擎行为一行不改）；
# - 判据是 **flag 是否在已装槽位**，不是「检测子系统类型」——模块类型会随版本增加，
#   而「东西在舱里、效果没算」这个事实不会。

SLOT_FLAGS: frozenset[int] = frozenset(
    {5, 87, 88}  # 货舱 / 无人机舱 / 无人机舱（备用）
    | set(range(11, 19))  # low 0–7
    | set(range(19, 27))  # medium 0–7
    | set(range(27, 35))  # high 0–7
    | set(range(89, 100))  # 植入体 / booster / rig 0–7
    | set(range(125, 133))  # 子系统槽 0–7（**已装**子系统走这里）
    | set(range(155, 172))  # 战斗机发射管 / 机库 / 服务槽 / 结构槽
)
"""`load_killmail` **会**还原的槽位 flag（其余 flag 上的物品一律进「舱内未计入」提示）。

177（SubSystemBay）刻意**不在**这个集合里：它是子系统**存放**舱，不是已装槽位——
这正是 f4 的坑所在。
"""

SUBSYSTEM_BAY_FLAG: int = 177
"""`SubSystemBay` —— 子系统存放舱的 flag（f4 的 45595 就在这个 flag 上）。"""

MAX_BAY_NAMES: int = 3
"""提示里最多列几个名字（更多时用「等」收尾，避免一条 warning 变成长清单）。"""


def _type_name(type_id: int) -> str:
    """`type_id` → 名字；没有注入索引（或查不到）时退回 `type_id N`（不猜名字）。"""
    index = _INDEX
    getter = getattr(index, "get_type", None) if index is not None else None
    if callable(getter):
        try:
            row = getter(type_id)
        except Exception:  # 边界：名字查询失败不能影响归一化
            row = None
        name = row.get("name") if isinstance(row, dict) else None
        if isinstance(name, str) and name:
            return name
    return f"type_id {type_id}"


def _bay_items(data: dict) -> list[tuple[int, int]]:
    """killmail 里**落在非槽位 flag** 的物品 → `[(flag, type_id), ...]`（保持原始顺序）。

    只读 `victim.items`，不碰引擎：`flag` / `item_type_id` 缺失或不是整数的条目直接跳过
    （那属于 killmail 本身的畸形，不是本提示要管的事）。
    """
    victim = data.get("victim")
    items = victim.get("items") if isinstance(victim, dict) else None
    if not isinstance(items, list):
        return []

    bays: list[tuple[int, int]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        flag = item.get("flag")
        type_id = item.get("item_type_id")
        if not isinstance(flag, int) or not isinstance(type_id, int):
            continue
        if flag in SLOT_FLAGS:
            continue
        bays.append((flag, type_id))
    return bays


def _bay_warning(data: dict) -> str | None:
    """舱内物品提示（没有则 `None`）。

    文案（字段规格 §0.6 / 任务书原文）：
    `子系统舱内另有 N 件可用模块（flag 177 等不在已装槽位），其效果未计入：<名字，最多 3 个>`
    """
    bays = _bay_items(data)
    if not bays:
        return None

    flags = sorted({flag for flag, _type_id in bays})
    names = [_type_name(type_id) for _flag, type_id in bays[:MAX_BAY_NAMES]]
    listing = "、".join(names)
    if len(bays) > MAX_BAY_NAMES:
        listing = f"{listing} 等"

    where = "子系统舱内" if SUBSYSTEM_BAY_FLAG in flags else "舱内"
    flag_text = "、".join(str(flag) for flag in flags)
    return (
        f"{where}另有 {len(bays)} 件可用模块（flag {flag_text} 等不在已装槽位），"
        f"其效果未计入：{listing}"
    )


def _normalize_json(text: str) -> contracts.NormalizeResult:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return _fail(
            "structured",
            [
                _error(
                    failed_at="format",
                    reason="malformed",
                    raw=f"JSON 解析失败: {exc.msg} (line {exc.lineno} col {exc.colno})",
                    line=exc.lineno,
                )
            ],
        )

    if isinstance(data, list):
        if len(data) != 1 or not isinstance(data[0], dict):
            return _fail(
                "structured",
                [
                    _error(
                        failed_at="format",
                        reason="malformed",
                        raw=f"JSON 数组含 {len(data)} 条记录，只接受单条配装/killmail",
                    )
                ],
            )
        data = data[0]

    if not isinstance(data, dict):
        return _fail(
            "structured",
            [
                _error(
                    failed_at="format",
                    reason="malformed",
                    raw=f"JSON 顶层是 {type(data).__name__}，不是对象",
                )
            ],
        )

    if "ship" in data and "items" in data:
        if not isinstance(data["ship"], dict) or not isinstance(data["items"], list):
            return _fail(
                "structured",
                [
                    _error(
                        failed_at="format",
                        reason="malformed",
                        raw="structured fit 的 `ship` 必须是对象、`items` 必须是数组",
                    )
                ],
            )
        return _ok(data, "structured", states_inferred=_structured_states_inferred(data))

    is_killmail = "victim" in data
    target = "killmail" if is_killmail else "esi_fitting"
    loader = adapter.load_killmail if is_killmail else adapter.load_esi_fitting
    try:
        fit = loader(data)
    except Exception as exc:
        return _fail(target, [_engine_error(exc)])

    result = _ok(fit, target)
    if is_killmail:
        # T-22 / §0.6：舱内模块「存在但没被计入」是**事实**，必须显式说出来。
        # 只加 warning（还原结果一个字不改）：那件装备仍然不装。
        warning = _bay_warning(data)
        if warning is not None:
            result["warnings"].append(warning)
    return result


# ---- 公开入口 ----


def normalize_fit(text: str) -> contracts.NormalizeResult:
    """把任意输入文本归一化成引擎 `fit` 结构。

    成功：`fit` 有值、`errors` 为空；失败：`fit` 为 None、`errors` 里至少一条
    可修复错误（含 `raw`，EFT 预扫描错误还含 `line`）。
    """
    if not isinstance(text, str):
        return _fail(
            "unknown",
            [
                _error(
                    failed_at="format",
                    reason="malformed",
                    raw=f"输入不是字符串，而是 {type(text).__name__}",
                )
            ],
        )

    # Windows 剪贴板常带 BOM
    source = text.lstrip("\ufeff").strip()
    if not source:
        return _fail(
            "unknown",
            [_error(failed_at="format", reason="unknown", raw="")],
        )

    source_format = _detect_format(source)
    if source_format == "eft":
        return _normalize_eft(source)
    if source_format == "dna":
        return _normalize_dna(source)
    if source_format == "esf":
        return _normalize_esf(source)
    if source_format == "json":
        return _normalize_json(source)

    return _fail(
        "unknown",
        [
            _error(
                failed_at="format",
                reason="unknown",
                raw=_first_line(source),
            )
        ],
    )
