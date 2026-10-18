"""L5 传输形态之一：**CLI**（开发期与人工验证的基线，文档 §10.5）。

```
python -m evefit.cli parse    --fit-file x.eft [--json]
python -m evefit.cli killmail --fit-file killmail.json [--json]
python -m evefit.cli index    --fit-file x.eft [--killmail k.json] [--eft "…"] [--skill-tier all5] [--json]
python -m evefit.cli view     --fit-file x.eft --view mobility
python -m evefit.cli get      --fit-file x.eft --path mobility.speed_mps
python -m evefit.cli detail   --fit-file x.eft [--view stability]
python -m evefit.cli search   --character NAME --ship SHIP --past-seconds 604800 [--json]
```

`killmail` 是唯一**只接受战报**的子命令（`api.fit_killmail`）：给 EFT / DNA 时它按契约
返回 `ok:false` + `kind: "not_a_killmail"`，退出码 `1`（配装内容没问题，是这个入口不适用
这类输入 —— 看配装请用 `parse` / `index`）。

`search` 是唯一**不要配装输入**的子命令（`api.zkb_search`）：它查的是**战报列表**
（谁 / 哪个公司 / 哪种船 / 哪个星系的战报），所以不给 `--fit-file` / `--eft` 那一套，
只给过滤条件。时间窗口只有 `--past-seconds`（zkb 已停用起止时间，传起止时间会得到
`invalid_argument`），更早的数据用 `--page` 往回翻；每次出网之间有 1.2 s 限流，
所以 `search` 天然比其它子命令慢一点。

三条纪律（每一条都对应 §10.7 的红线）：

1. **本文件不实现任何逻辑**：每个子命令都是一次 `evefit.api.<入口>()` 调用，`--json`
   就是 `json.dumps(api 返回值)` 原样输出。CLI 与 MCP **共用同一个 Python 函数层**，
   不许各自实现一遍（§10.5）。
2. **人话输出不做换算、不做判词**：数值一律 `json.dumps` 原样打印（最短可逆表示），
   不四舍五入、不除以 1000、不挑「好 / 坏」。人话模式只负责**排版**（键值、计数、
   截断超长列表），不产生任何新事实。
3. **退出码三档**：`ok:true` → `0`；`ok:false` → `1`；**参数 / IO 错误 → `2`**
   （配装内容本身有问题**不是**参数错误，走 `1` + 结构化错误）。

输入来源（互斥，必须给一个）：

| 选项 | 含义 |
|---|---|
| `--fit-file PATH` | 从文件读（`-` = **stdin**；编码固定 **UTF-8**） |
| `--eft "…"` | 直接给 EFT 文本（命令行内联） |
| `--dna "…"` | 直接给 DNA 字符串（`fitting:` 前缀可有可无） |
| `--killmail PATH` | 从文件读 killmail JSON（同样按 UTF-8） |
| `--killmail-id N [--hash H]` | **联网**取 killmail（EVE ESI / zkillboard） |

`--fit-file` / `--killmail` 的编码失败给**人话提示**（哪一步、怎么修），退出码 `2`：
那不是「配装有问题」，是「这份文件不是 UTF-8 文本」。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from evefit import api

STDIN_PATH: str = "-"
"""`--fit-file -` 表示从标准输入读（文档 §10.5 的人工验证基线会用到）。"""

EXIT_OK: int = 0
EXIT_API_FAILURE: int = 1
EXIT_USAGE: int = 2

TRUNCATE_CHARS: int = 160
"""人话输出里单个值的最大长度（超了截断并加 `…`；完整内容永远在 `--json` 里）。"""

_INDENT: str = "  "


# =============================================================================
# 一、输出（人话 / JSON）
# =============================================================================


def _configure_streams() -> None:
    """把 stdout / stderr 固定成 UTF-8（**机器可读**的前提：管道里的字节可预测）。

    Windows 控制台默认编码随区域设置变（cp936 之类），同一份 `--json` 输出在不同机器
    上字节不同，下游解析就会碎。这里统一成 UTF-8；不支持 `reconfigure` 的流
    （测试替身 / 已包装的流）静默跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 极少见的受限流
            continue


def _scalar(value: object) -> str:
    """单个值 → 文本：**原样**（`json.dumps` 给最短可逆表示，不四舍五入）。"""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - api 的结果都是 JSON 可序列化的
        return repr(value)


def _compact(value: object, limit: int = TRUNCATE_CHARS) -> str:
    """紧凑 JSON 文本；超长截断（人话模式的护眼措施，不改数据）。"""
    text = _scalar(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _block_line(block: object, limit: int = TRUNCATE_CHARS) -> str:
    """一个 dict → `k=v · k=v`（只铺一层；嵌套值走 `_compact`）。不是 dict 就整体压。"""
    if not isinstance(block, dict):
        return _compact(block, limit)
    parts = [f"{key}={_compact(value, limit)}" for key, value in block.items()]
    return " · ".join(parts)


def _header(ship: object) -> str:
    """`Hecate (35683) · Tactical Destroyer`（名字缺失时老实给 `type_id`）。"""
    if not isinstance(ship, dict):
        return "（未知船）"
    name = ship.get("name")
    type_id = ship.get("type_id")
    group = ship.get("group")
    head = f"{name} ({type_id})" if name else f"type_id={type_id}"
    return f"{head} · {group}" if group else head


def _flag_ids(flags: object) -> list[str]:
    """`flags` 清单 → id 列表（**一条不丢**：`id` 缺失的条目用 `?` 占位，不静默跳过）。"""
    if not isinstance(flags, list):
        return []
    ids: list[str] = []
    for flag in flags:
        if isinstance(flag, dict):
            ids.append(str(flag.get("id")))
        else:
            ids.append("?")
    return ids


def _count_states(table: object) -> str:
    """`provenance` 表 → `measured=N assumed=N unknown=N`（只是数标签，不改任何值）。"""
    if not isinstance(table, dict):
        return "-"
    counts = {"measured": 0, "assumed": 0, "unknown": 0}
    for state in table.values():
        if state in counts:
            counts[state] += 1
    return " ".join(f"{state}={count}" for state, count in counts.items())


def _human_error(error: object) -> str:
    """失败信封的 `error` → 人话（`kind` / `message` + 附带字段逐条给）。"""
    if not isinstance(error, dict):
        return f"失败：{_compact(error)}"
    lines = [f"失败 {error.get('kind')}: {error.get('message')}"]
    for key, value in error.items():
        if key in ("kind", "message"):
            continue
        lines.append(f"{_INDENT}{key}: {_compact(value)}")
    return "\n".join(lines)


def _human_parse(result: dict) -> str:
    """`parse` 的人话摘要：**我读懂了什么**（船 / 件数 / 技能档 / 警告）。"""
    lines = [
        f"parse ok · {result.get('source_format')} · {_header(result.get('ship'))}",
        f"{_INDENT}modules: {_block_line(result.get('modules'))}",
        f"{_INDENT}skills: {_block_line(result.get('skills'))}",
    ]
    warnings = result.get("warnings") or []
    lines.append(f"{_INDENT}warnings({len(warnings)}):")
    lines.extend(f"{_INDENT * 2}- {warn}" for warn in warnings)
    fit = result.get("fit") if isinstance(result.get("fit"), dict) else {}
    lines.append(
        f"{_INDENT}fit: {len(fit.get('items') or [])} items"
        f"（完整结构走 --json 的 result.fit）"
    )
    return "\n".join(lines)


def _human_index(result: dict) -> str:
    """`index` 的人话摘要：船一行，之后每个顶层块一行（`k=v · k=v`）。"""
    lines = [_header(result.get("ship"))]
    for key, value in result.items():
        if key == "ship":
            continue
        if key == "flags":
            ids = _flag_ids(value)
            lines.append(f"{key}({len(ids)}): {', '.join(ids) if ids else '-'}")
            continue
        lines.append(f"{key}: {_block_line(value)}")
    return "\n".join(lines)


def _human_view(result: dict) -> str:
    """`view` 的人话摘要：overview 一行铺开 + provenance 计数 + 原因表 + flag id。"""
    lines = [f"overview: {_block_line(result.get('overview'))}"]
    lines.append(f"provenance: {_count_states(result.get('provenance'))}")
    reasons = result.get("unknown_reasons")
    if isinstance(reasons, dict) and reasons:
        lines.append(f"unknown_reasons: {_block_line(reasons)}")
    confidence = result.get("confidence")
    if isinstance(confidence, dict) and confidence:
        lines.append(f"confidence(非A): {_block_line(confidence)}")
    ids = _flag_ids(result.get("flags"))
    lines.append(f"flags({len(ids)}): {', '.join(ids) if ids else '-'}")
    return "\n".join(lines)


def _human_get(result: dict) -> str:
    """`get` 的人话摘要：`path = value` 一行 + 三态 / 来源（有 reason 就多一行）。"""
    lines = [f"{result.get('path')} = {_compact(result.get('value'), 400)}"]
    lines.append(
        f"{_INDENT}provenance={result.get('provenance')} · "
        f"source_view={result.get('source_view')}"
    )
    if "reason" in result:
        lines.append(f"{_INDENT}reason={result.get('reason')}")
    return "\n".join(lines)


_BUCKET_ROWS: int = 8
"""`killmail` 人话摘要里每个分桶最多铺几行（完整清单永远在 `--json` 里）。"""

_PILOT_ROWS: int = 3
"""`killmail` 人话摘要里「伤害 Top」铺几行（完整前 10 名在 `--json` 的 by_pilot 里）。"""


def _entity_name(entry: object) -> str:
    """身份块 → 名字；名字查不到时给 `id:N`（**绝不编一个名字**）。"""
    if not isinstance(entry, dict):
        return "-"
    name = entry.get("name")
    if isinstance(name, str) and name:
        return name
    identifier = entry.get("id")
    return f"id:{identifier}" if identifier is not None else "-"


def _who(block: object) -> str:
    """身份三件套 → `Alice (Corp A / Alliance X)`（缺哪个少哪个；都没有给 `-`）。

    `alliance` 缺失在 EVE 里很常见（没加联盟），所以它没有就不写，不占位。
    """
    if not isinstance(block, dict):
        return "-"
    parts: list[str] = []
    if isinstance(block.get("character"), dict):
        parts.append(_entity_name(block.get("character")))
    orgs = [
        _entity_name(block.get(key))
        for key in ("corporation", "alliance")
        if isinstance(block.get(key), dict)
    ]
    if orgs:
        parts.append(f"({ ' / '.join(orgs) })")
    return " ".join(parts) if parts else "-"


def _pilot_lines(title: str, rows: object, shown: int = _PILOT_ROWS) -> list[str]:
    """`by_pilot` → 一行「伤害 TopN: 谁 伤害 (占比) / …」（数字原样，不换算）。"""
    if not isinstance(rows, list) or not rows:
        return [f"{_INDENT}{title}: -"]
    parts = [
        f"{_who(row)} {row.get('damage')} ({row.get('damage_pct')})"
        for row in rows[:shown]
        if isinstance(row, dict)
    ]
    line = f"{_INDENT}{title} Top{len(parts)}: " + " / ".join(parts)
    if len(rows) > shown:
        line += f" / … 另 {len(rows) - shown} 名见 --json"
    return [line]


def _bucket_lines(title: str, rows: object, key: str) -> list[str]:
    """分桶列表 → 人话若干行（`key×count=damage(pct)`；超量只截断显示，不改数据）。

    `ship_attribution == "capsule"` 的行追加「（记录船，非实际输出船）」—— 蛋打不出武器
    伤害，那个数字属于该玩家**已被摧毁的船**，只是 ESI 把记录船写成了蛋。
    """
    if not isinstance(rows, list) or not rows:
        return [f"{_INDENT}{title}: -"]
    lines = [f"{_INDENT}{title}({len(rows)}):"]
    for row in rows[:_BUCKET_ROWS]:
        if not isinstance(row, dict):
            continue
        mark = "（记录船，非实际输出船）" if row.get("ship_attribution") == "capsule" else ""
        lines.append(
            f"{_INDENT * 2}- {row.get(key)} × {row.get('count')} = "
            f"{row.get('damage')} ({row.get('damage_pct')}){mark}"
        )
    if len(rows) > _BUCKET_ROWS:
        lines.append(f"{_INDENT * 2}… 另 {len(rows) - _BUCKET_ROWS} 项见 --json")
    return lines


def _human_killmail(result: dict, meta: dict) -> str:
    """`killmail` 的人话摘要：战报一行 + 受害者 + 攻击方分桶 + 伤害 vs EHP。

    只排版、不换算、不判词：所有数字都是 `result` 里的原值（`json.dumps` 的最短表示）。
    """
    km = result.get("killmail") if isinstance(result.get("killmail"), dict) else {}
    system = km.get("solar_system") if isinstance(km.get("solar_system"), dict) else {}
    security = km.get("security") if isinstance(km.get("security"), dict) else {}
    victim = result.get("victim") if isinstance(result.get("victim"), dict) else {}
    attackers = result.get("attackers") if isinstance(result.get("attackers"), dict) else {}
    context = result.get("context") if isinstance(result.get("context"), dict) else {}

    check = f"check={attackers.get('damage_check')}"
    if "damage_diff" in attackers:
        check += f"(diff={attackers.get('damage_diff')})"
    final = attackers.get("final_blow") if isinstance(attackers.get("final_blow"), dict) else {}
    final_weapon = final.get("weapon") if isinstance(final.get("weapon"), dict) else {}
    weapon_head = _header(final_weapon) if final_weapon else "-"
    lines = [
        f"killmail {km.get('killmail_id')} · {km.get('killmail_time')} · "
        f"{system.get('name')} ({system.get('id')}) · "
        f"security={security.get('status')} band={security.get('band')} · "
        f"source={result.get('killmail_source')}",
        f"{_INDENT}victim: {_who(victim)} — {_header(victim.get('ship'))} · "
        f"damage_taken={victim.get('damage_taken')}",
        f"{_INDENT}value: {_block_line(km.get('value'))}",
        f"{_INDENT}flags: {_block_line(km.get('flags'))} · labels: {_compact(km.get('labels'))}",
        f"{_INDENT}attackers: count={attackers.get('count')} · "
        f"有伤害 {attackers.get('damaging_count')} · "
        f"零伤害 {attackers.get('zero_damage_count')} · "
        f"记录船是蛋 {attackers.get('capsule_count')} · "
        f"damage_total={attackers.get('damage_total')} · {check}",
        f"{_INDENT}final_blow: {_who(final)} — {_header(final.get('ship'))} + {weapon_head}"
        f" · security={final.get('security_status')}",
        f"{_INDENT}attacker security: {_block_line(attackers.get('security'))}",
    ]
    lines.extend(_pilot_lines("伤害", attackers.get("by_pilot")))
    lines.extend(_bucket_lines("by_ship_group", attackers.get("by_ship_group"), "group"))
    lines.extend(_bucket_lines("by_weapon_family", attackers.get("by_weapon_family"), "family"))
    vs_ehp = (
        context.get("damage_taken_vs_ehp")
        if isinstance(context.get("damage_taken_vs_ehp"), dict)
        else {}
    )
    lines.append(
        f"{_INDENT}context.damage_taken_vs_ehp: damage_taken={vs_ehp.get('damage_taken')} · "
        f"ehp_mixed={vs_ehp.get('ehp_mixed')} · ratio={vs_ehp.get('ratio')} · "
        f"ehp_provenance={vs_ehp.get('ehp_provenance')}"
    )
    if context.get("note"):
        lines.append(f"{_INDENT}{context.get('note')}")
    warnings = result.get("warnings") or []
    lines.append(f"{_INDENT}warnings({len(warnings)}):")
    lines.extend(f"{_INDENT * 2}- {warn}" for warn in warnings)
    if attackers.get("capsule_count"):
        lines.append(
            f"{_INDENT}警告: {attackers.get('capsule_count')} 名攻击者的记录船是蛋（Capsule）"
            "—— 蛋装不了武器，其伤害来自该玩家已被摧毁的船（他已在这场交火里失去自己的船）；"
            "按船型统计时不要把蛋当实际输出船"
        )
    return "\n".join(lines)


def _human_detail(result: dict, meta: dict) -> str:
    """`detail` 的人话摘要：船一行，每个视角 2 行（overview 铺开 + 计数 / missing）。"""
    views = result.get("views") if isinstance(result.get("views"), dict) else {}
    lines = [f"detail · {_header(result.get('ship'))} · views({len(views)}): {', '.join(views)}"]
    for name, entry in views.items():
        overview = entry.get("overview") if isinstance(entry, dict) else {}
        lines.append(f"{_INDENT}[{name}] {_block_line(overview)}")
        flags = entry.get("flags") if isinstance(entry, dict) else []
        ids = _flag_ids(flags)
        missing = entry.get("missing") if isinstance(entry, dict) else []
        lines.append(
            f"{_INDENT * 2}provenance: {_count_states(entry.get('provenance'))}"
            f" · flags({len(ids)})"
            + (f" · missing={missing}" if missing else "")
        )
    assumptions = meta.get("assumptions") if isinstance(meta, dict) else None
    if isinstance(assumptions, dict):
        tier = assumptions.get("skill_tier") or {}
        lines.append(f"{_INDENT}完整技能表 {len(tier)} 条在 meta.assumptions.skill_tier（--json）")
    return "\n".join(lines)


def _human_search(result: dict, meta: dict) -> str:
    """`search` 的人话摘要：条件一行 → 计数 / 聚合 → 每条战报一行（带受害者人名）。

    `count` 与展示条数**分开写**（`limit` 只截断展示，聚合仍按整页算）：如果只印一行
    「200 条」而实际只给了 50 条，人会以为数据缺了。
    """
    query = result.get("query") if isinstance(result.get("query"), dict) else {}
    resolved = query.get("resolved") if isinstance(query.get("resolved"), dict) else {}
    conditions = " · ".join(
        f"{name}={entry.get('name') or entry.get('id')}"
        for name, entry in resolved.items()
        if isinstance(entry, dict)
    )
    aggregates = result.get("aggregates") if isinstance(result.get("aggregates"), dict) else {}
    span = aggregates.get("time_span") if isinstance(aggregates.get("time_span"), dict) else {}
    kills = result.get("kills") if isinstance(result.get("kills"), list) else []
    lines = [
        f"search · side={query.get('side')} · page={query.get('page')} · "
        f"past_seconds={query.get('past_seconds')}",
        f"{_INDENT}条件: {conditions or '-'}",
        f"{_INDENT}count={result.get('count')}（本页）· 展示 {len(kills)} 条 · "
        f"total_value={aggregates.get('total_value')} · median_value={aggregates.get('median_value')}",
        f"{_INDENT}time_span: {span.get('earliest')} → {span.get('latest')}",
    ]
    by_ship = aggregates.get("by_ship") if isinstance(aggregates.get("by_ship"), list) else []
    if by_ship:
        lines.append(f"{_INDENT}by_ship: {_block_line(by_ship, 240)}")
    for row in kills:
        if not isinstance(row, dict):
            continue
        ship = row.get("ship") if isinstance(row.get("ship"), dict) else {}
        victim = row.get("victim") if isinstance(row.get("victim"), dict) else {}
        lines.append(
            f"{_INDENT}{row.get('killmail_time')} · {row.get('killmail_id')} · "
            f"{ship.get('name') or ship.get('type_id')} · "
            f"受害者={_who(victim)} · value={row.get('value')} · "
            f"solo={row.get('solo')} npc={row.get('npc')}"
        )
    warnings = result.get("warnings") or []
    if warnings:
        lines.append(f"{_INDENT}warnings({len(warnings)}):")
        lines.extend(f"{_INDENT * 2}- {warn}" for warn in warnings)
    lines.append(f"{_INDENT}{result.get('note')}")
    if isinstance(meta, dict) and meta.get("urls"):
        lines.append(f"{_INDENT}urls: {_block_line(meta.get('urls'), 240)}")
    return "\n".join(lines)


_HUMAN: dict[str, Callable[[dict, dict], str]] = {
    "parse": lambda result, _meta: _human_parse(result),
    "killmail": lambda result, _meta: _human_killmail(result, _meta),
    "index": lambda result, _meta: _human_index(result),
    "view": lambda result, _meta: _human_view(result),
    "get": lambda result, _meta: _human_get(result),
    "detail": _human_detail,
    "search": _human_search,
}


# =============================================================================
# 二、输入来源（标准库读文件 / stdin；编码失败给人话）
# =============================================================================


def _read_utf8(path: str) -> str:
    """按 **UTF-8** 读一个文本文件。

    Raises:
        UnicodeDecodeError: 内容不是 UTF-8（调用方会转成人话 + 退出码 2）。
        OSError: 文件不存在 / 没权限。
    """
    return Path(path).read_text(encoding="utf-8")


def _read_stdin() -> str:
    """按 **UTF-8** 读标准输入（`--fit-file -`）。

    走 `sys.stdin.buffer` 而不是 `sys.stdin`：后者在 Windows 上按控制台代码页解码，
    管道里传进来的 UTF-8 配装会被解成乱码（乱码不是「配装语法错误」，比失败更糟糕）。
    """
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:  # 测试里 sys.stdin 可能已被替换成文本替身
        return sys.stdin.read()
    return buffer.read().decode("utf-8")


def _from_stdin_or_file(path: str) -> tuple[object | None, str | None]:
    """`--fit-file` → `(文本, 错误信息)`（二选一，永不两个都有值）。"""
    source = "标准输入" if path == STDIN_PATH else f"文件 {path}"
    try:
        if path == STDIN_PATH:
            return _read_stdin(), None
        return _read_utf8(path), None
    except UnicodeDecodeError as exc:
        position = f"第 {exc.start} 字节" if isinstance(exc.start, int) else "未知位置"
        return None, (
            f"{source} 不是 UTF-8 文本（在{position}处解码失败）；"
            "请用 UTF-8 保存后重试（EFT / DNA / killmail JSON 都按 UTF-8 读）"
        )
    except OSError as exc:
        return None, f"读不到 {source}：{exc}"


def _pick_fit(args: argparse.Namespace) -> tuple[object | None, str | None]:
    """从互斥的输入选项里取出 `fit` 参数（api 接受四种形态，这里只负责读字节）。

    Returns:
        `(fit, 错误信息)`：给了 0 个来源 / 给了多个来源 / 读失败时错误信息非空
        （都是**参数级**问题 → 退出码 2）。
    """
    sources: list[str] = []
    for name in ("fit_file", "eft", "dna", "killmail"):
        if getattr(args, name, None):
            sources.append(name)
    if getattr(args, "killmail_id", None) is not None:
        sources.append("killmail_id")
    if not sources:
        return None, (
            "必须给一个配装来源：--fit-file PATH（或 - 表示 stdin）/ --eft TEXT / "
            "--dna TEXT / --killmail PATH / --killmail-id N [--hash H]"
        )
    if len(sources) > 1:
        return None, f"配装来源互斥，一次只能给一个（收到 {sources}）"

    if args.fit_file:
        return _from_stdin_or_file(args.fit_file)
    if args.eft:
        return args.eft, None
    if args.dna:
        return args.dna, None
    if args.killmail:
        return _from_stdin_or_file(args.killmail)
    return {
        api.KILLMAIL_ID_FIELD: args.killmail_id,
        api.KILLMAIL_HASH_FIELD: getattr(args, "hash", None),
    }, None


# =============================================================================
# 三、参数解析
# =============================================================================


def _add_common(parser: argparse.ArgumentParser) -> None:
    """配装类子命令共用的选项（来源 / 三个共同参数 / `--json`，§10.2）。

    `search` **不用**这一套（它没有配装输入，见 `build_parser` 里的 `search_cmd`）。
    """
    parser.add_argument(
        "--fit-file",
        metavar="PATH",
        help="从文件读配装（UTF-8）；`-` 表示从标准输入读",
    )
    parser.add_argument("--eft", metavar="TEXT", help="直接给 EFT 文本")
    parser.add_argument("--dna", metavar="TEXT", help="直接给 DNA 字符串")
    parser.add_argument("--killmail", metavar="PATH", help="从文件读 killmail JSON（UTF-8）")
    parser.add_argument(
        "--killmail-id",
        type=int,
        metavar="N",
        help="联网取 killmail（需要 --hash；不给 hash 时先向 zkillboard 查）",
    )
    parser.add_argument("--hash", metavar="H", help="killmail 的 ESI hash（配合 --killmail-id）")
    parser.add_argument(
        "--skill-tier",
        choices=api.SKILL_TIERS,
        default=api.SKILL_TIER_ALL4,
        help=f"技能档（默认 {api.SKILL_TIER_ALL4}）",
    )
    parser.add_argument(
        "--damage-profile",
        choices=api.DAMAGE_PROFILES,
        default=api.DAMAGE_PROFILE_UNIFORM,
        help="伤害剖面（当前只支持 uniform = 25/25/25/25）",
    )
    parser.add_argument(
        "--state-policy",
        choices=api.STATE_POLICIES,
        default=api.STATE_POLICY_ENGINE_DEFAULT,
        help="模块状态推定策略（当前只支持 engine_default）",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON（api 返回值原样）")


def build_parser() -> argparse.ArgumentParser:
    """命令行定义（配装类子命令 + `search`）。"""
    parser = argparse.ArgumentParser(
        prog="python -m evefit.cli",
        description="evefit L5 接口层的 CLI 基线（与 MCP 共用 evefit/api.py 的逻辑）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<命令>")

    parse_cmd = subparsers.add_parser("parse", help="把输入归一化成结构（确认读懂了没有）")
    _add_common(parse_cmd)

    killmail_cmd = subparsers.add_parser(
        "killmail", help="战报上下文（谁打的、损失多少、挨了多少伤害）"
    )
    _add_common(killmail_cmd)

    index_cmd = subparsers.add_parser("index", help="六维度摘要（L0，默认入口）")
    _add_common(index_cmd)

    view_cmd = subparsers.add_parser("view", help="单视角全字段（L1）")
    _add_common(view_cmd)
    view_cmd.add_argument("--view", metavar="NAME", required=True, help="视角名（如 mobility）")

    get_cmd = subparsers.add_parser("get", help="按路径取一个值 + provenance")
    _add_common(get_cmd)
    get_cmd.add_argument(
        "--path", metavar="PATH", required=True, help="点路径（如 mobility.speed_mps）"
    )

    detail_cmd = subparsers.add_parser("detail", help="列表型明细 / 完整技能表")
    _add_common(detail_cmd)
    detail_cmd.add_argument("--view", metavar="NAME", help="只给这个视角（默认六个全给）")
    detail_cmd.add_argument("--path", metavar="PATH", help="只给这些字段（<视角>.<字段>）")

    search_cmd = subparsers.add_parser(
        "search", help="战报列表检索（谁在打谁 / 这个公司最近损失了什么）"
    )
    search_cmd.add_argument("--character", metavar="NAME", help="角色（名字或整数 id）")
    search_cmd.add_argument("--corporation", metavar="NAME", help="公司（名字或整数 id）")
    search_cmd.add_argument("--alliance", metavar="NAME", help="联盟（名字或整数 id）")
    search_cmd.add_argument(
        "--ship", metavar="NAME", help="船型（名字或整数 id，如 Sleipnir / 22444）"
    )
    search_cmd.add_argument("--solar-system", metavar="NAME", help="星系（名字或整数 id）")
    search_cmd.add_argument("--region", metavar="NAME", help="区域（名字或整数 id）")
    search_cmd.add_argument(
        "--past-seconds",
        type=int,
        metavar="N",
        help="时间窗口（秒）：604800 = 近 7 天；zkb 已停用起止时间，时间只能这样给",
    )
    search_cmd.add_argument(
        "--page", type=int, default=1, metavar="N", help="页码（默认 1；单页最多 200 条）"
    )
    search_cmd.add_argument(
        "--side",
        choices=api.ZKB_SIDES,
        default=api.ZKB_SIDE_BOTH,
        help=f"kills / losses / both（默认 {api.ZKB_SIDE_BOTH}：两条路径各查一次）",
    )
    search_cmd.add_argument(
        "--limit",
        type=int,
        default=api.ZKB_LIMIT_DEFAULT,
        metavar="N",
        help=(
            f"只截断展示条数（默认 {api.ZKB_LIMIT_DEFAULT}，上限 {api.ZKB_PAGE_SIZE}）；"
            "count 与 aggregates 仍按整页算"
        ),
    )
    search_cmd.add_argument(
        "--json", action="store_true", help="输出机器可读 JSON（api 返回值原样）"
    )

    return parser


def _dispatch(args: argparse.Namespace, fit: object) -> dict:
    """子命令 → 一次 `evefit.api` 调用（**本文件不做任何逻辑**）。"""
    params = {
        "skill_tier": args.skill_tier,
        "damage_profile": args.damage_profile,
        "state_policy": args.state_policy,
    }
    if args.command == "parse":
        return api.parse_fit(fit, **params)
    if args.command == "killmail":
        return api.fit_killmail(fit, **params)
    if args.command == "index":
        return api.fit_index(fit, **params)
    if args.command == "view":
        return api.fit_view(fit, args.view, **params)
    if args.command == "get":
        return api.fit_get(fit, args.path, **params)
    return api.fit_detail(fit, getattr(args, "view", None), getattr(args, "path", None), **params)


def _dispatch_search(args: argparse.Namespace) -> dict:
    """`search` → 一次 `api.zkb_search()`（**本文件不做任何逻辑**）。

    只把命令行选项原样递过去：条件校验 / 名字解析 / 限流全在 `api` 层（CLI 与 MCP
    必须共用同一套判断，否则「同一个查询两个出口不同结果」）。
    """
    return api.zkb_search(
        character=args.character,
        corporation=args.corporation,
        alliance=args.alliance,
        ship=args.ship,
        solar_system=args.solar_system,
        region=args.region,
        past_seconds=args.past_seconds,
        page=args.page,
        side=args.side,
        limit=args.limit,
    )


# =============================================================================
# 四、入口
# =============================================================================


def _report(args: argparse.Namespace, envelope: dict) -> int:
    """把信封写到 stdout / stderr 并给出退出码（人话与 `--json` 的唯一分岔点）。"""
    if args.json:
        print(json.dumps(envelope, ensure_ascii=False))
        return EXIT_OK if envelope.get("ok") else EXIT_API_FAILURE

    if envelope.get("ok"):
        printer = _HUMAN.get(args.command)
        text = (
            printer(envelope.get("result") or {}, envelope.get("meta") or {})
            if printer
            else _compact(envelope)
        )
        print(text)
        return EXIT_OK

    print(_human_error(envelope.get("error")), file=sys.stderr)
    return EXIT_API_FAILURE


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：返回退出码（`0` ok / `1` api 失败 / `2` 参数或 IO 错误）。

    Args:
        argv: 参数列表；`None` 表示用 `sys.argv[1:]`（测试可以直接传列表，不必起子进程）。

    Returns:
        退出码。**不抛异常**：`argparse` 的 `SystemExit` 也在这里收成退出码，
        这样 `python -m evefit.cli` 与进程内调用行为一致。
    """
    _configure_streams()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse 已经打过用法；这里只把退出码返回去
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE

    if args.command == "search":
        # `search` 没有配装输入：跳过 `_pick_fit`（它要求恰好一个来源，会直接报参数错）
        return _report(args, _dispatch_search(args))

    fit, problem = _pick_fit(args)
    if problem is not None:
        print(f"参数错误：{problem}", file=sys.stderr)
        return EXIT_USAGE

    return _report(args, _dispatch(args, fit))


if __name__ == "__main__":  # pragma: no cover - 进程入口
    raise SystemExit(main())
