"""L5 传输形态之一：**CLI**（开发期与人工验证的基线，文档 §10.5）。

```
python -m noobbaozi.cli parse    --fit-file x.eft [--json]
python -m noobbaozi.cli killmail --fit-file killmail.json [--json]
python -m noobbaozi.cli index    --fit-file x.eft [--killmail k.json] [--eft "…"] [--skill-tier all5] [--json]
python -m noobbaozi.cli view     --fit-file x.eft --view mobility
python -m noobbaozi.cli get      --fit-file x.eft --path mobility.speed_mps
python -m noobbaozi.cli detail   --fit-file x.eft [--view stability]
python -m noobbaozi.cli search   --character NAME --ship SHIP --past-seconds 604800 [--json]
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

1. **本文件不实现任何逻辑**：每个子命令都是一次 `noobbaozi.api.<入口>()` 调用，`--json`
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

from noobbaozi import api

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


_HINT_CHARS: int = 600
"""失败信封里 `hint` 的展示上限（比普通字段宽得多）。

为什么单独放宽：`hint` 常常是**可用值全集**（`metric` 的 14 个指标、六个视角名…），
按 160 字符截断会把「照抄就能用」变成「只看到前四个 + 一个省略号」—— 那比不给提示更糟。
"""


def _human_error(error: object) -> str:
    """失败信封的 `error` → 人话（`kind` / `message` + 附带字段逐条给）。"""
    if not isinstance(error, dict):
        return f"失败：{_compact(error)}"
    lines = [f"失败 {error.get('kind')}: {error.get('message')}"]
    for key, value in error.items():
        if key in ("kind", "message"):
            continue
        limit = _HINT_CHARS if key == "hint" else TRUNCATE_CHARS
        lines.append(f"{_INDENT}{key}: {_compact(value, limit)}")
    return "\n".join(lines)


def _human_parse(result: dict) -> str:
    """`parse` 的人话摘要：**我读懂了什么**（船 / 件数 / 技能档 / 警告）+ **规范 EFT 文本**。

    EFT 段（T-54）是给人**直接复制**的：它不是给人读的摘要，所以原样整段贴出、
    不加缩进、不折行（折行会破坏 EFT 的「一行一件」语义）。`eft` 为 `null` 时
    只说明渲染失败的原因（原因在 `warnings` 里），**不手写一份补上**。
    """
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
    eft = result.get("eft")
    if isinstance(eft, str) and eft:
        # 原样贴出（可复制）：首尾各一条分隔线，段落内部一个字符都不动
        lines.append(f"{_INDENT}eft（可直接复制）:")
        lines.append(eft)
    else:
        lines.append(f"{_INDENT}eft: null（渲染失败，原因见 warnings；不要手拼）")
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

_RELATED_SHIP_ROWS: int = 6
"""`related` 人话摘要里每边舰船构成铺几行（完整清单在 `--json` 的 `sides.*.ships` 里）。"""


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


_STATS_TOP_KEYS: tuple[str, ...] = (
    "characters",
    "corporations",
    "alliances",
    "ships",
    "systems",
    "locations",
)
"""`zkb_stats` 的 6 类榜单（顺序 = 人类的阅读顺序：人 → 组织 → 船 → 空间）。

**没有 `regions`** —— zkb 没有区域榜；`locations` 是行星 / 月球 / 小行星带 / 星门级，
不是星系（要星系看 `systems`）。
"""


def _human_stats(result: dict, meta: dict) -> str:
    """`stats` 的人话摘要：身份 → 战斗总量 → 风格指标 → 7 天活动 → 6 类榜单 → 大块 → 警告。

    两处**必须带量纲**，否则一定被误读：
    - `solo_ratio` 后面写 `%`（它是 0–100 的百分数，不是 0–1 比例）；
    - `avg_gang_size` 后面写 `人/次`（它是实数均值，不是比例）。
    另外 `efficiency` 明确标成「按 isk 派生」，免得被当成 zkb 的 `danger_ratio`。
    """
    entity = result.get("entity") if isinstance(result.get("entity"), dict) else {}
    combat = result.get("combat") if isinstance(result.get("combat"), dict) else {}
    style = result.get("style") if isinstance(result.get("style"), dict) else {}
    activity = result.get("activity_7d") if isinstance(result.get("activity_7d"), dict) else {}
    top = result.get("top") if isinstance(result.get("top"), dict) else {}
    efficiency = combat.get("efficiency") if isinstance(combat.get("efficiency"), dict) else {}

    lines = [
        f"stats · {entity.get('type')} · {entity.get('name') or '?'} (id={entity.get('id')})",
        f"{_INDENT}ticker={entity.get('ticker')} · sec={entity.get('security_status')} · "
        f"founded={entity.get('founded')}",
        f"{_INDENT}combat: ships {combat.get('ships_destroyed')} 杀 / {combat.get('ships_lost')} 损 · "
        f"points {combat.get('points_destroyed')} / {combat.get('points_lost')}",
        f"{_INDENT}isk: 毁 {combat.get('isk_destroyed')} / 损 {combat.get('isk_lost')} · "
        f"efficiency={efficiency.get('value')}（按 isk 派生，≠ zkb 的 danger_ratio）",
        f"{_INDENT}style: danger={style.get('danger_ratio')} · gang={style.get('gang_ratio')} · "
        f"solo={style.get('solo_ratio')}%（百分数） · "
        f"avg_gang_size={style.get('avg_gang_size')} 人/次 · "
        f"solo {style.get('solo_kills')} 杀 / {style.get('solo_losses')} 损",
        f"{_INDENT}style 公式未公开（formula={style.get('formula')}）：只转述，不据此判威胁等级",
        f"{_INDENT}activity_7d: {_block_line(activity, 220)}",
    ]
    for key in _STATS_TOP_KEYS:
        rows = top.get(key) if isinstance(top.get(key), list) else []
        lines.append(f"{_INDENT}top.{key}({len(rows)}): {_block_line(rows, 220)}")
    blocks = result.get("blocks") if isinstance(result.get("blocks"), dict) else {}
    for name, block in blocks.items():
        if not isinstance(block, dict):
            continue
        extra = ""
        if name == "months":
            extra = (
                f" · range={block.get('range')} · observed={block.get('observed_months')}"
                f" · 补内部空洞={block.get('filled_internal_gaps')}（不向两端外扩）"
            )
        lines.append(
            f"{_INDENT}block {name}: bytes={block.get('bytes')} "
            f"truncated={block.get('truncated')}{extra}"
        )
    reasons = result.get("field_reasons")
    if isinstance(reasons, dict) and reasons:
        lines.append(f"{_INDENT}field_reasons: {_block_line(reasons, 220)}")
    warnings = result.get("warnings") or []
    if warnings:
        lines.append(f"{_INDENT}warnings({len(warnings)}):")
        lines.extend(f"{_INDENT * 2}- {warn}" for warn in warnings)
    if isinstance(meta, dict) and meta.get("bytes") is not None:
        lines.append(f"{_INDENT}bytes={meta.get('bytes')}")
    return "\n".join(lines)


_EXPLAIN_ROWS: int = 12
"""`explain` 人话摘要里最多铺几行源（完整清单永远在 `--json` 里）。"""


def _num(value: object, digits: int = 4) -> str:
    """数值 → 人话（`digits` 位小数、千分位，末尾零去掉）；非数值原样给。

    **只用于展示**：`explain.value` 是全精度，`--json` 里那份不做任何舍入
    （A 类的「各项之和 == value」只在全精度上成立，展示层不许把结论建立在舍入值上）。
    """
    if value is None:
        return "-"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    text = f"{value:,.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _explain_step(entry: dict) -> str:
    """B / C 类的一条因子 → `× 6.8645（post_percent +586.45%）` 形式的一步。

    **为什么不直接印 `factor` 当乘数**：`factor` 是引擎的**算子原始值**——
    `post_percent` 要按 `1 + factor/100` 解释、`post_div` 是除数、`pre/post_mul` 要按
    堆叠惩罚 `1 + (factor-1)·penalty` 折算。把原始值写进 `base × 因子链` 会得到一个
    算不回去的等式（实测 `speed_mps` 的 `post_percent 586.45` 实际是 ×6.8645）。
    这里给**折算后的乘数**，原始值与算子名放在括号里可溯源。
    """
    operator = entry.get("operator")
    factor = entry.get("factor")
    if not isinstance(factor, (int, float)):
        return f"{operator} {factor}"
    penalty = entry.get("penalty")
    weight = 1.0 if penalty is None else float(penalty)
    if operator == "post_percent":
        multiplier = 1.0 + (float(factor) / 100.0) * weight
        return f"× {_num(multiplier)}（post_percent +{_num(factor)}%）"
    if operator in ("pre_mul", "post_mul"):
        multiplier = 1.0 + (float(factor) - 1.0) * weight
        suffix = f"（{operator} {_num(factor)}）" if multiplier != float(factor) else ""
        return f"× {_num(multiplier)}{suffix}"
    if operator == "post_div":
        divisor = 1.0 + (float(factor) - 1.0) * weight
        suffix = f"（post_div {_num(factor)}）" if divisor != float(factor) else ""
        return f"÷ {_num(divisor)}{suffix}"
    return f"{operator} {_num(factor)}"


def _human_explain(result: dict, _meta: dict) -> str:
    """`explain` 的人话摘要（三类口径分开铺，**不混算**）。

    A 可加：逐项 `+ 数值 · 名字 · 占比`，末尾给 `Σ == value`；
    B 乘性：`base` → 因子链（折算后的乘数，带算子名）→ 结果（附 `replay_ok`）；
    C 投影：先给 `formula`，再给「层 HP」与「共振因子」两项。

    完整精度与全部字段在 `--json`（人话这里只舍入展示）。
    """
    metric = result.get("metric")
    unit = result.get("unit")
    unit_text = f" {unit}" if unit else ""
    combine = result.get("combine")
    head = (
        f"{metric} = {_num(result.get('value'))}{unit_text}"
        f" · combine={combine} · provenance={result.get('provenance')}"
    )
    if result.get("tied"):
        head += " · 四系并列"
    lines = [head]

    sources = result.get("sources") if isinstance(result.get("sources"), list) else []
    value = result.get("value")

    if combine == "additive":
        lines.append(
            f"{_INDENT}可加类：各项 contribution 之和 == value"
            f"（unexplained={_num(result.get('unexplained'))}）"
        )
        for entry in sources[:_EXPLAIN_ROWS]:
            contribution = entry.get("contribution")
            share = ""
            if isinstance(contribution, (int, float)) and isinstance(value, (int, float)) and value:
                share = f" · {contribution / value * 100:.1f}%"
            sign = "-" if isinstance(contribution, (int, float)) and contribution < 0 else "+"
            amount = abs(contribution) if isinstance(contribution, (int, float)) else None
            lines.append(
                f"{_INDENT}{sign} {_num(amount)}{unit_text} · {entry.get('label')}"
                f"（{entry.get('operator')}）{share}"
            )
        if len(sources) > _EXPLAIN_ROWS:
            lines.append(f"{_INDENT}…还有 {len(sources) - _EXPLAIN_ROWS} 项（--json 取全量）")
        total = result.get("sum_of_contributions")
        if total is not None:
            lines.append(f"{_INDENT}Σ = {_num(total)}{unit_text} = value")
        return "\n".join(lines)

    # B / C 类：base → 因子链 → 结果。`factor` 一律按算子语义折算后再展示。
    formula = result.get("formula")
    if formula:
        # C 投影类：`base` 就是「层 HP」，它下面那条 contribution 就是同一个数 ——
        # 只铺一次，别让读者以为有两项。
        lines.append(f"{_INDENT}公式：{formula}")
    base = result.get("base")
    if base is not None and not formula:
        lines.append(f"{_INDENT}base = {_num(base)}{unit_text}")
    for entry in sources[:_EXPLAIN_ROWS]:
        label = entry.get("label") or "?"
        contribution = entry.get("contribution")
        if entry.get("factor") is not None:
            lines.append(f"{_INDENT}{_explain_step(entry)} · {label}")
        elif contribution is not None:
            # 投影类的「层 HP」项：它是被除的那个数（pre_assign），不是因子
            prefix = "层 HP = " if formula else "= "
            lines.append(f"{_INDENT}{prefix}{_num(contribution)}{unit_text} · {label}")
    if len(sources) > _EXPLAIN_ROWS:
        lines.append(f"{_INDENT}…还有 {len(sources) - _EXPLAIN_ROWS} 项（--json 取全量）")
    lines.append(
        f"{_INDENT}→ {_num(result.get('replayed'))}{unit_text}"
        f"（replay_ok={result.get('replay_ok')}，残差 {_num(result.get('unexplained'))}）"
    )
    lines.append(f"{_INDENT}注意：乘性因子不能相加（contribution 一律 None）")
    return "\n".join(lines)


_SENSITIVITY_ROWS: int = 12
"""`sensitivity` 人话摘要里最多铺几行 change（完整清单在 `--json`）。"""


def _human_sensitivity(result: dict, _meta: dict) -> str:
    """`sensitivity` 的人话摘要：基线 + 计划/执行计数 + 逐条 `Δ`（+ warnings）。

    `delta_pct` 是**比例**（`delta / baseline`），所以展示时 ×100；基线为 0 时模块给
    `null`（不是 0、也不是无限大），这里如实写「比例不适用」。
    """
    metric = result.get("metric")
    combine = result.get("combine")
    lines = [
        f"{metric} = {_num(result.get('baseline'))} · combine={combine}"
        f" · provenance={result.get('provenance')}"
    ]
    lines.append(
        f"{_INDENT}计划 {result.get('ops_planned')} · 执行 {result.get('ops_executed')}"
        f" · 跳过 {result.get('skipped')} · 截断 {result.get('truncated')}"
        f"（max_ops={result.get('max_ops')}）"
    )
    changes = result.get("changes") if isinstance(result.get("changes"), list) else []
    for change in changes[:_SENSITIVITY_ROWS]:
        op = change.get("op") if isinstance(change.get("op"), dict) else {}
        kind = op.get("kind")
        detail = ""
        if kind == "set_mode":
            detail = f"（→ {op.get('label') or op.get('mode_name') or op.get('mode_id')}）"
        elif kind == "set_state":
            detail = f"（→ {op.get('state')}）"
        label = op.get("label") or f"item_index={op.get('item_index')}"
        delta = change.get("delta")
        pct = change.get("delta_pct")
        if pct is None:
            pct_text = "基线为 0，比例不适用"
        elif isinstance(pct, (int, float)):
            pct_text = f"{pct * 100:+.1f}%"
        else:
            pct_text = str(pct)
        sign = "+" if isinstance(delta, (int, float)) and delta > 0 else ""
        lines.append(
            f"{_INDENT}{sign}{_num(delta)} · {kind} · {label}{detail}"
            f" → {_num(change.get('value'))}（{pct_text}）"
        )
        note = change.get("note")
        if note:
            lines.append(f"{_INDENT * 2}note: {note}")
    if len(changes) > _SENSITIVITY_ROWS:
        lines.append(f"{_INDENT}…还有 {len(changes) - _SENSITIVITY_ROWS} 条（--json 取全量）")
    lines.append(
        f"{_INDENT}注意：delta_pct = delta ÷ baseline；基线为负时百分比符号会反过来，"
        "看 delta 更直观；基线为 0 时比例不适用（null）"
    )
    warnings = result.get("warnings") or []
    if warnings:
        lines.append(f"{_INDENT}warnings({len(warnings)}):")
        for warning in warnings:
            message = warning.get("message") if isinstance(warning, dict) else warning
            lines.append(f"{_INDENT * 2}- {message}")
    return "\n".join(lines)


_PROJECT_ROWS: int = 8
"""`project` 人话摘要里「其他指标」最多铺几行（完整 10 条与 attributes 在 `--json`）。"""


def _delta_line(entry: object, *, unit: str = "") -> str:
    """一条 `{before, after, delta, delta_pct}` → `before → after（+delta / +pct%）`。

    **不补数**：`before` / `after` 有一侧缺失时如实写 `null`，`delta` 为 `null` 时写
    「不可比」—— 这是投影里最常见的一种「看起来像 0 变化」的误读来源。
    """
    if not isinstance(entry, dict):
        return "-"
    before = entry.get("before")
    after = entry.get("after")
    delta = entry.get("delta")
    pct = entry.get("delta_pct")
    suffix = f" {unit}" if unit else ""
    head = f"{_num(before)}{suffix} → {_num(after)}{suffix}"
    if not isinstance(delta, (int, float)):
        return f"{head}（不可比：一侧缺失）"
    pct_text = f" / {pct * 100:+.1f}%" if isinstance(pct, (int, float)) else ""
    return f"{head}（{delta:+,.4f}{pct_text}）"


def _human_project(result: dict, _meta: dict) -> str:
    """`project` 的人话摘要：**先把「改了 EHP」与「改了回血速度」分开**，再铺其余指标。

    为什么必须分开：遥修只改「回血速度」，EHP 一点没动 —— 两者混在一张表里，读的人会
    把「+675 修复/秒」当成「EHP 多了 675」。所以 headline 只放 EHP，回血单独一节。
    """
    target = result.get("target") if isinstance(result.get("target"), dict) else {}
    ship = target.get("ship") if isinstance(target.get("ship"), dict) else {}
    ship_name = ship.get("name") or f"type_id={ship.get('type_id')}"
    target_label = target.get("name")
    head = f"{ship_name}"
    if target_label:
        head += f"（{target_label}）"
    sources = result.get("sources") if isinstance(result.get("sources"), list) else []
    lines = [
        f"{head} · 来源 {len(sources)} 个 · strict_state={result.get('strict_state')}"
    ]

    # ① headline：只放 EHP（含混合 EHP 的变化）
    metrics = result.get("delta", {}).get("metrics") if isinstance(result.get("delta"), dict) else []
    metrics = metrics if isinstance(metrics, list) else []
    ehp = next((m for m in metrics if isinstance(m, dict) and m.get("path") == "survivability.ehp_mixed"), None)
    lines.append(f"EHP {_delta_line(ehp, unit='hp')}")

    # ② 来源：谁给的（labels 来自 sources 行；buffs 的 from 也指向它们）
    for row in sources:
        if not isinstance(row, dict):
            continue
        row_ship = row.get("ship") if isinstance(row.get("ship"), dict) else {}
        name = row_ship.get("name") or f"type_id={row_ship.get('type_id')}"
        lines.append(
            f"{_INDENT}← {row.get('label')}（{name}）· effects {row.get('effects')}"
            f" · buffs {row.get('buffs')}"
        )
        filtered = row.get("filtered")
        if isinstance(filtered, dict) and filtered.get("count"):
            lines.append(
                f"{_INDENT * 2}已按 state 过滤 {filtered.get('count')} 个非 active 来源的 buff"
            )

    # ③ 投射内容（effect / buff 各一行），buffs 带「谁给的」
    applied = result.get("applied") if isinstance(result.get("applied"), dict) else {}
    effects = applied.get("effects") if isinstance(applied.get("effects"), list) else []
    buffs = applied.get("buffs") if isinstance(applied.get("buffs"), list) else []
    if effects:
        kinds = [effect.get("type_name") or f"type_id={effect.get('type_id')}" for effect in effects if isinstance(effect, dict)]
        lines.append(f"{_INDENT}投射 effect（{len(effects)}）：{', '.join(kinds[:6])}"
                     + (" …" if len(kinds) > 6 else ""))
    if buffs:
        rows = []
        for buff in buffs[:6]:
            if not isinstance(buff, dict):
                continue
            origins = buff.get("from") if isinstance(buff.get("from"), list) else []
            who = ", ".join(
                str(origin.get("source"))
                for origin in origins
                if isinstance(origin, dict) and origin.get("source")
            )
            rows.append(f"buff#{buff.get('id')}={_num(buff.get('value'))}（← {who or '显式'}）")
        lines.append(f"{_INDENT}投射 buff（{len(buffs)}）：" + " · ".join(rows)
                     + (" …" if len(buffs) > 6 else ""))

    # ④ 改了回血速度（**单独一节**：它不是 EHP）
    repaired = result.get("repaired") if isinstance(result.get("repaired"), dict) else {}
    repair_lines: list[str] = []
    for layer in ("shield", "armor", "hull"):
        block = repaired.get(layer) if isinstance(repaired.get(layer), dict) else {}
        effective = block.get("effective")
        raw = block.get("rate")
        chosen = effective if isinstance(effective, dict) and effective.get("after") is not None else raw
        if not isinstance(chosen, dict):
            continue
        delta = chosen.get("delta")
        if not (isinstance(delta, (int, float)) and abs(delta) > 1e-12):
            continue
        label = "有效" if chosen is effective else "raw"
        repair_lines.append(
            f"{_INDENT}· {layer} {label}修复 {_delta_line(chosen, unit='hp/s')}"
        )
    if repair_lines:
        lines.append("改了回血速度（不改 EHP —— 它加的是每秒修复量，不是血量上限）：")
        lines.extend(repair_lines)

    # ⑤ 其余指标（EHP 已经在 headline 里）
    others = [
        m for m in metrics
        if isinstance(m, dict) and m.get("path") != "survivability.ehp_mixed"
        and isinstance(m.get("delta"), (int, float)) and abs(m.get("delta")) > 1e-12
    ]
    if others:
        lines.append("其他指标（有变化）：")
        for entry in others[:_PROJECT_ROWS]:
            unit = str(entry.get("unit") or "")
            lines.append(f"{_INDENT}· {entry.get('path')} {_delta_line(entry, unit=unit)}")
        if len(others) > _PROJECT_ROWS:
            lines.append(f"{_INDENT}…还有 {len(others) - _PROJECT_ROWS} 项（--json 取全量）")
    if not others and not repair_lines:
        lines.append("其他指标：没有变化（投射只影响上面列出的项）")

    warnings = result.get("warnings") or []
    if warnings:
        lines.append(f"warnings({len(warnings)}):")
        for warning in warnings:
            message = warning.get("message") if isinstance(warning, dict) else warning
            lines.append(f"{_INDENT}- {message}")
    return "\n".join(lines)


_ENGAGEMENT_ROWS: int = 8
"""`engagement` 人话摘要里最多铺几行（时间线采样 / 锚点格子）。"""


def _ship_of(block: object) -> str:
    """`{label, ship:{type_id,name}}` → 人能读的船名（缺名字给 type_id，不编）。"""
    if not isinstance(block, dict):
        return "?"
    ship = block.get("ship") if isinstance(block.get("ship"), dict) else {}
    name = ship.get("name")
    if name:
        return str(name)
    type_id = ship.get("type_id")
    return f"type_id={type_id}" if type_id is not None else "?"


def _engagement_direction(side: object) -> str:
    """单方向结果 → `命中 … · 应用 … · 有效 … dps` 一行。"""
    if not isinstance(side, dict):
        return "?"
    totals = side.get("totals") if isinstance(side.get("totals"), dict) else {}
    chance = totals.get("weighted_chance_to_hit")
    dps = totals.get("effective_dps")
    nominal = totals.get("nominal_dps")
    coverage = totals.get("coverage")
    if chance is None:
        hit = "命中 -"
    else:
        hit = f"命中 {_num(chance, 4)}"
    applied = ""
    if (
        isinstance(nominal, (int, float))
        and isinstance(dps, (int, float))
        and not isinstance(nominal, bool)
        and nominal > 0.0
    ):
        applied = f" · 应用 {_num(dps / nominal, 4)}（= 有效 ÷ 名义）"
    return (
        f"{hit}{applied} · 有效 {_num(dps)} / 名义 {_num(nominal)} dps"
        f"（coverage {_num(coverage)}，"
        f"{totals.get('supported_weapon_count')}/{totals.get('weapon_count')} 件已建模）"
    )


def _human_engagement(result: dict, _meta: dict) -> str:
    """`engagement` 人话：按 `kind` 分派（exchange / timeline / anchor_check / fleet）。"""
    kind = result.get("kind")
    if kind == "fleet_engagement":
        return _human_engagement_fleet(result)
    if kind == "timeline":
        return _human_engagement_timeline(result)
    if kind == "anchor_check":
        return _human_engagement_anchor(result)
    return _human_engagement_exchange(result)


def _human_engagement_exchange(result: dict) -> str:
    """双向换血：**三块分开印**（应用 / 两个血量口径 / 击穿与谁先死）。

    第三块必须把「无对敌武器」单独说出来：那时 `time_to_kill_s` 是 `null`，直接把
    `null` 读成 0 或「秒杀」都是错的。
    """
    inputs = result.get("inputs") if isinstance(result.get("inputs"), dict) else {}
    a_on_b = result.get("a_on_b") if isinstance(result.get("a_on_b"), dict) else {}
    b_on_a = result.get("b_on_a") if isinstance(result.get("b_on_a"), dict) else {}
    a_name = _ship_of(a_on_b.get("attacker"))
    b_name = _ship_of(a_on_b.get("defender"))
    lines = [
        f"{a_name} ↔ {b_name} @ {_num(inputs.get('range_m'))} m"
        f" · angular={_num(inputs.get('angular_vel'), 6)} rad/s"
        f" · wrecking={inputs.get('include_wrecking')}"
    ]

    lines.append("【命中率 / 应用系数 / 有效 DPS】")
    lines.append(f"{_INDENT}{a_name}→{b_name}：{_engagement_direction(a_on_b)}")
    lines.append(f"{_INDENT}{b_name}→{a_name}：{_engagement_direction(b_on_a)}")

    lines.append("【HP 池 vs EHP（用途不同，别混）】")
    for name, side in ((b_name, a_on_b), (a_name, b_on_a)):
        survival = side.get("defender_survival") if isinstance(side.get("defender_survival"), dict) else {}
        hp_pool = survival.get("hp_pool") if isinstance(survival.get("hp_pool"), dict) else {}
        ehp = survival.get("ehp") if isinstance(survival.get("ehp"), dict) else {}
        lines.append(
            f"{_INDENT}{name}：hp_pool {_num(hp_pool.get('value'))} hp"
            f"（{hp_pool.get('compare_with') or '战报 damage_taken 口径'}）"
            f" · ehp {_num(ehp.get('value'))} hp"
            f"（{ehp.get('compare_with') or '击穿时间口径'}）"
        )

    lines.append("【击穿时间 / 谁先死】")
    race = result.get("race") if isinstance(result.get("race"), dict) else {}
    a_ttk = race.get("a_time_to_kill_s")
    b_ttk = race.get("b_time_to_kill_s")
    if isinstance(a_ttk, (int, float)) and isinstance(b_ttk, (int, float)):
        lines.append(
            f"{_INDENT}{a_name}→{b_name} 需 {_num(a_ttk)} s；"
            f"{b_name}→{a_name} 需 {_num(b_ttk)} s"
        )
        first = race.get("first_to_die")
        if first == "a":
            lines.append(f"{_INDENT}→ {a_name} 先死（比例 {_num(race.get('ratio_a_over_b'))}）")
        elif first == "b":
            lines.append(f"{_INDENT}→ {b_name} 先死（比例 {_num(race.get('ratio_a_over_b'))}）")
        elif first == "even":
            lines.append(f"{_INDENT}→ 同时死（比例 1.0）")
        else:
            lines.append(f"{_INDENT}→ 无法判定谁先死（比例 {_num(race.get('ratio_a_over_b'))}）")
    else:
        dead = a_name if a_ttk is None else b_name
        alive = b_name if a_ttk is None else a_name
        lines.append(
            f"{_INDENT}{dead} 无对敌武器，无法对算"
            f"（该方向 ttk=null）；{alive} 单方面打穿需 "
            f"{_num(b_ttk if a_ttk is None else a_ttk)} s"
        )
    flags = result.get("a_on_b", {}).get("flags") or []
    for flag in flags:
        lines.append(f"{_INDENT}flag: {flag}")
    return "\n".join(lines)


def _human_engagement_timeline(result: dict) -> str:
    """时间线：速率 + 端点 + 采样（线性扣血模型）。"""
    inputs = result.get("inputs") if isinstance(result.get("inputs"), dict) else {}
    rates = result.get("rates") if isinstance(result.get("rates"), dict) else {}
    endpoints = result.get("endpoints") if isinstance(result.get("endpoints"), dict) else {}
    lines = [
        f"时间线：horizon {_num(inputs.get('horizon_s'))} s"
        f" · A→B {_num(rates.get('a_dps_on_b'))} dps / B→A {_num(rates.get('b_dps_on_a'))} dps"
    ]
    lines.append(
        f"{_INDENT}A 死于 {_num(endpoints.get('a_dies_at_s'))} s"
        f" · B 死于 {_num(endpoints.get('b_dies_at_s'))} s"
        f" · first_to_die={endpoints.get('first_to_die')}"
    )
    samples = result.get("samples") if isinstance(result.get("samples"), list) else []
    for sample in samples[:_ENGAGEMENT_ROWS]:
        if not isinstance(sample, dict):
            continue
        lines.append(
            f"{_INDENT}t={_num(sample.get('t_s'))}s · A 剩 {_num(sample.get('a_hp_fraction'), 3)}"
            f" / B 剩 {_num(sample.get('b_hp_fraction'), 3)}（按 EHP 比例）"
        )
    if len(samples) > _ENGAGEMENT_ROWS:
        lines.append(f"{_INDENT}…还有 {len(samples) - _ENGAGEMENT_ROWS} 个采样点（--json 取全量）")
    assumptions = result.get("assumptions")
    if isinstance(assumptions, dict):
        lines.append(f"{_INDENT}模型：{assumptions.get('model')}")
    return "\n".join(lines)


def _human_engagement_anchor(result: dict) -> str:
    """战报锚点：能不能解释 + 最优格 + 原因 + 限制（不拟合，只回答存在性）。"""
    lines = [
        f"战报锚点：observed {_num(result.get('observed_hp'))} hp / 窗口 "
        f"{_num(result.get('window_s'))} s（需要 {_num(result.get('required_hp_dps'))} hp/s）"
    ]
    lines.append(
        f"{_INDENT}可解释：{result.get('explainable')} · 满足窗口的格子 "
        f"{result.get('region_count')} 个 · 窗口内最多打掉 "
        f"{_num(result.get('max_hp_removed_in_window'))} hp"
    )
    best = result.get("best")
    if isinstance(best, dict):
        lines.append(
            f"{_INDENT}最优格：range={_num(best.get('range_m'))} m · "
            f"angular={_num(best.get('angular_vel'), 6)} rad/s · 需要 "
            f"{_num(best.get('window_needed_s'))} s"
        )
    reasons = result.get("reasons") or []
    if reasons:
        lines.append(f"{_INDENT}原因：")
        for reason in reasons[:_ENGAGEMENT_ROWS]:
            lines.append(f"{_INDENT * 2}- {reason}")
    caveats = result.get("caveats") or []
    if caveats:
        lines.append(f"{_INDENT}限制（永远存在，别当 bug）：")
        for caveat in caveats[:_ENGAGEMENT_ROWS]:
            lines.append(f"{_INDENT * 2}- {caveat}")
    return "\n".join(lines)


def _human_engagement_fleet(result: dict) -> str:
    """编队量级：**先声明这是量级模型**，再给单船 → 编队 → 击穿/窗口。"""
    lines = [
        f"编队量级：同型 × {result.get('count')} · 量级估计，不是个体配装"
    ]
    note = result.get("fleet_note")
    if note:
        lines.append(f"{_INDENT}{note}")
    single = result.get("single") if isinstance(result.get("single"), dict) else {}
    totals = result.get("totals") if isinstance(result.get("totals"), dict) else {}
    lines.append(
        f"{_INDENT}单船有效 {_num(single.get('effective_dps'))} dps → 编队 "
        f"{_num(totals.get('fleet_effective_dps'))} dps"
        f"（名义 {_num(totals.get('fleet_nominal_dps'))}，coverage {_num(totals.get('coverage'))}）"
    )
    survival = result.get("defender_survival") if isinstance(result.get("defender_survival"), dict) else {}
    hp_pool = survival.get("hp_pool") if isinstance(survival.get("hp_pool"), dict) else {}
    ehp = survival.get("ehp") if isinstance(survival.get("ehp"), dict) else {}
    lines.append(
        f"{_INDENT}守方：hp_pool {_num(hp_pool.get('value'))} hp（战报口径）· "
        f"ehp {_num(ehp.get('value'))} hp（击穿口径）"
    )
    ttk = survival.get("time_to_kill_s") if isinstance(survival.get("time_to_kill_s"), dict) else {}
    lines.append(f"{_INDENT}击穿：{_num(ttk.get('value'))} s（= ehp ÷ 编队有效 DPS）")
    window = result.get("window") if isinstance(result.get("window"), dict) else None
    if window:
        magnitude = window.get("magnitude") if isinstance(window.get("magnitude"), dict) else {}
        lines.append(
            f"{_INDENT}窗口 {_num(window.get('window_s'))} s：打掉 {_num(window.get('hp_removed'))} hp"
            + (f" · 量级校验 {magnitude.get('verdict')}" if magnitude else "")
        )
    flags = result.get("flags") or []
    if flags:
        lines.append(f"{_INDENT}flags: {', '.join(str(flag) for flag in flags)}")
    return "\n".join(lines)


def _human_related(result: dict, meta: dict) -> str:
    """`related` 的人话摘要：星系 / 时间窗 → 双方人数·舰船构成·ISK·kill 数 → killmail id。

    两处**必须带说明**，否则一定被误读：
    - `exHours` 是**窗口半径**且窗口**不对称**（实测 `startTime=time−exHours`、
      `endTime=time+2×exHours`），**不等于整场战斗的时长**；
    - `killmail_ids` 是**全的**，而逐 kill 明细按 `limit` 截断 —— 两个数要分开写，
      否则人会以为「一共就这几条」。
    """
    system = result.get("system") if isinstance(result.get("system"), dict) else {}
    window = result.get("window") if isinstance(result.get("window"), dict) else {}
    sides = result.get("sides") if isinstance(result.get("sides"), dict) else {}
    ids = result.get("killmail_ids") if isinstance(result.get("killmail_ids"), dict) else {}
    truncated = result.get("truncated") if isinstance(result.get("truncated"), dict) else {}

    lines = [
        f"related · {system.get('name') or '?'} ({system.get('id')}) · {result.get('region')}",
        f"{_INDENT}窗口: {window.get('startTime')} .. {window.get('endTime')} · "
        f"exHours={window.get('exHours')} · complete={window.get('complete')}",
        f"{_INDENT}注意: exHours 是**窗口半径**且窗口不对称 —— 不等于整场战斗的时长",
    ]
    for side_name in ("teamA", "teamB"):
        side = sides.get(side_name) if isinstance(sides.get(side_name), dict) else {}
        side_ids = ids.get(side_name) if isinstance(ids.get(side_name), list) else []
        lines.append(
            f"{_INDENT}{side_name}: {side.get('pilots')} 人 · {side.get('corporations')} 公司 · "
            f"{side.get('alliances')} 联盟 · ISK {_num(side.get('isk'))} · "
            f"{side.get('kills_total')} kill"
        )
        ships = side.get("ships") if isinstance(side.get("ships"), list) else []
        if ships:
            listing = "、".join(
                f"{row.get('ship')}×{row.get('count')}"
                for row in ships[:_RELATED_SHIP_ROWS]
                if isinstance(row, dict)
            )
            if len(ships) > _RELATED_SHIP_ROWS:
                listing += " …"
            lines.append(f"{_INDENT * 2}舰船: {listing}")
        kills = side.get("kills") if isinstance(side.get("kills"), list) else []
        if kills and isinstance(kills[0], dict):
            first = kills[0]
            lines.append(
                f"{_INDENT * 2}最新一条: {first.get('killmail_id')} · "
                f"{first.get('time')} · 伤害 {_num(first.get('damage_taken'))} · "
                f"{first.get('attacker_count')} 攻击者"
            )
        lines.append(
            f"{_INDENT * 2}killmail_ids: {len(side_ids)} 条（**全的**）"
            + (
                f"，明细展示 {len(kills)} 条（--limit 截断）"
                if side.get("kills_truncated")
                else ""
            )
        )
    omitted = truncated.get("omitted") if isinstance(truncated.get("omitted"), dict) else {}
    if any(omitted.values()):
        lines.append(f"{_INDENT}截断: limit={truncated.get('limit')} · {omitted}")
    for note in result.get("notes") or []:
        lines.append(f"{_INDENT}note: {note}")
    if isinstance(meta, dict) and meta.get("urls"):
        lines.append(f"{_INDENT}urls: {_block_line(meta.get('urls'), 200)}")
    return "\n".join(lines)


_HUMAN: dict[str, Callable[[dict, dict], str]] = {
    "parse": lambda result, _meta: _human_parse(result),
    "killmail": lambda result, _meta: _human_killmail(result, _meta),
    "index": lambda result, _meta: _human_index(result),
    "view": lambda result, _meta: _human_view(result),
    "get": lambda result, _meta: _human_get(result),
    "detail": _human_detail,
    "explain": _human_explain,
    "sensitivity": _human_sensitivity,
    "project": _human_project,
    "engagement": _human_engagement,
    "search": _human_search,
    "stats": _human_stats,
    "related": _human_related,
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
        prog="python -m noobbaozi.cli",
        description="noobbaozi L5 接口层的 CLI 基线（与 MCP 共用 noobbaozi/api.py 的逻辑）",
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

    explain_cmd = subparsers.add_parser(
        "explain", help="某个指标为什么是这个数（归因链：A 可加 / B 乘性 / C 投影）"
    )
    _add_common(explain_cmd)
    explain_cmd.add_argument(
        "--metric",
        metavar="PATH",
        required=True,
        help=(
            "要归因的指标（如 survivability.ehp_mixed / firepower.dps.base / "
            "mobility.speed_mps / survivability.thinnest.shield.ehp）；"
            "不在登记表 → invalid_path + 可用列表"
        ),
    )
    explain_cmd.add_argument(
        "--depth",
        type=int,
        default=api.EXPLAIN_MAX_DEPTH,
        metavar="N",
        help=(
            f"归因深度 1 或 2（默认 {api.EXPLAIN_MAX_DEPTH}）：1 只看直接贡献者，"
            "2 再展开子源；其它值由 api 报 invalid_argument"
        ),
    )

    sensitivity_cmd = subparsers.add_parser(
        "sensitivity", help="改一个输入看指标怎么变（逐件关闭 / 状态 / 模式）"
    )
    _add_common(sensitivity_cmd)
    sensitivity_cmd.add_argument(
        "--metric",
        metavar="PATH",
        required=True,
        help=(
            "要算灵敏度的指标（如 resources.cpu.free / stability.capacitor.stable_pct）；"
            "不在登记表 → invalid_path + 可用列表"
        ),
    )
    sensitivity_cmd.add_argument(
        "--max-ops",
        type=int,
        default=api.SENSITIVITY_MAX_OPS,
        metavar="N",
        help=(
            f"最多执行几个 op（默认 {api.SENSITIVITY_MAX_OPS}）：每个 op 一次引擎重算，"
            "超了截断并给 warning；非正整数由 api 报 invalid_argument"
        ),
    )
    sensitivity_cmd.add_argument(
        "--ops-json",
        metavar="JSON",
        help=(
            '显式 op 列表（JSON 数组），如 [{"kind":"disable_item","item_index":0}]；'
            "不给 = 自动计划（每件已装模块关一次 + 每个非当前模式切一次）"
        ),
    )

    project_cmd = subparsers.add_parser(
        "project", help="一艘船给另一艘船什么（加成 / 遥修 / 负面，一给一）"
    )
    _add_common(project_cmd)
    project_cmd.add_argument(
        "--source",
        action="append",
        metavar="PATH",
        help=(
            "来源船配装文件（UTF-8，可重复；`-` 表示 stdin）。"
            "目标 = 上面的 --fit-file / --eft / --killmail …；顺序按给出的顺序"
        ),
    )
    project_cmd.add_argument(
        "--source-eft",
        action="append",
        metavar="TEXT",
        help="直接给来源船的 EFT 文本（可重复；排在所有 --source 之后）",
    )
    project_cmd.add_argument(
        "--effects-json",
        metavar="JSON",
        help=(
            '显式 effect 数组（JSON），如 [{"type_id":527,"effect_id":6426,"attributes":{}}]；'
            "未知 effect_id 进 warnings，不报错"
        ),
    )
    project_cmd.add_argument(
        "--no-strict-state",
        dest="strict_state",
        action="store_false",
        help=(
            "不按 state 过滤（引擎原样：offline 的指挥爆发器也照样给加成）。"
            "默认按 EVE 过滤非 active 源"
        ),
    )

    engagement_cmd = subparsers.add_parser(
        "engagement", help="交战数学：这套打得动他吗（命中 / 有效 DPS / 击穿时间）"
    )
    _add_common(engagement_cmd)
    engagement_cmd.add_argument(
        "--defender",
        metavar="PATH",
        help="守方配装文件（UTF-8；`-` 表示 stdin）。攻方 = 上面的 --fit-file / --eft / …",
    )
    engagement_cmd.add_argument(
        "--defender-eft", metavar="TEXT", help="直接给守方 EFT 文本（与 --defender 互斥）"
    )
    engagement_cmd.add_argument(
        "--distance-m",
        type=float,
        required=True,
        metavar="M",
        help="**必填**：面到面距离（m）。距离与角速度是输入，本层不做几何假设",
    )
    engagement_cmd.add_argument(
        "--angular-velocity",
        type=float,
        default=0.0,
        metavar="RAD_S",
        help="角速度（rad/s，默认 0 = 乐观上界）",
    )
    engagement_cmd.add_argument(
        "--target-velocity",
        type=float,
        default=0.0,
        metavar="M_S",
        help="守方绝对速度（只对 --mode anchor / --fleet 生效；exchange/timeline 自动用 maxVelocity）",
    )
    engagement_cmd.add_argument(
        "--no-wrecking",
        dest="include_wrecking",
        action="store_false",
        help="不含 wrecking（默认含，与 pyfa 一致；不含时归一到纸面口径）",
    )
    engagement_cmd.add_argument(
        "--mode",
        choices=api.ENGAGEMENT_MODES,
        default=api.ENGAGEMENT_MODE_EXCHANGE,
        help=f"exchange 双向换血（默认）/ timeline 时间线 / anchor 战报锚点",
    )
    engagement_cmd.add_argument(
        "--fleet",
        type=int,
        metavar="N",
        help="编队量级：N 条同型船（**量级模型**，不是个体配装；只在 exchange 下允许）",
    )
    engagement_cmd.add_argument(
        "--observed-hp", type=float, metavar="HP",
        help="战报里扣掉的血（damage_taken）：anchor 必填；fleet 下可选（量级校验）",
    )
    engagement_cmd.add_argument(
        "--window-s", type=float, metavar="S", help="时间窗（秒）：anchor 必填；fleet 下可选"
    )
    engagement_cmd.add_argument(
        "--duration-s", type=float, metavar="S", help="timeline 的时间窗（秒）"
    )
    engagement_cmd.add_argument(
        "--points", type=int, default=6, metavar="N", help="timeline 采样点数（默认 6，至少 2）"
    )

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

    stats_cmd = subparsers.add_parser(
        "stats", help="实体画像（战绩 / 风格指标 / 6 类榜单）"
    )
    stats_cmd.add_argument("--character", metavar="NAME", help="角色（名字或整数 id）")
    stats_cmd.add_argument("--corporation", metavar="NAME", help="公司（名字或整数 id）")
    stats_cmd.add_argument("--alliance", metavar="NAME", help="联盟（名字或整数 id）")
    stats_cmd.add_argument(
        "--include",
        action="append",
        choices=api.ZKB_STATS_BLOCKS,
        metavar="BLOCK",
        help=(
            "追加一个大块（可重复）：months / rankings / top_all_time / groups。"
            "默认一块都不给；联盟四块全要约 120KB"
        ),
    )
    stats_cmd.add_argument(
        "--json", action="store_true", help="输出机器可读 JSON（api 返回值原样）"
    )

    related_cmd = subparsers.add_parser(
        "related", help="星系交火总览（双方阵营 / 舰船构成 / ISK / killmail 清单）"
    )
    related_cmd.add_argument(
        "--url",
        metavar="URL",
        help=(
            "首选：https://zkillboard.com/related/<systemID>/<YYYYMMDDHHMM>/ "
            "（也认 /api/related/...）。给了它就别再给 --system / --time"
        ),
    )
    related_cmd.add_argument("--system", metavar="NAME", help="星系（名字或整数 id）")
    related_cmd.add_argument(
        "--time",
        metavar="STAMP",
        help="时间戳，固定 12 位 YYYYMMDDHHMM（如 202609091400）；不做宽松解析",
    )
    related_cmd.add_argument(
        "--limit",
        type=int,
        default=api.ZKB_RELATED_KILL_LIMIT,
        metavar="N",
        help=(
            f"每边最多几条逐 kill 明细（默认 {api.ZKB_RELATED_KILL_LIMIT}）；"
            "killmail_ids 不受它限制"
        ),
    )
    related_cmd.add_argument(
        "--json", action="store_true", help="输出机器可读 JSON（api 返回值原样）"
    )

    return parser


def _dispatch(args: argparse.Namespace, fit: object) -> dict:
    """子命令 → 一次 `noobbaozi.api` 调用（**本文件不做任何逻辑**）。"""
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
    if args.command == "explain":
        return api.fit_explain(fit, args.metric, depth=args.depth, **params)
    if args.command == "sensitivity":
        return api.fit_sensitivity(
            fit,
            args.metric,
            ops=getattr(args, "ops", None),
            max_ops=args.max_ops,
            **params,
        )
    if args.command == "project":
        return api.fit_project(
            fit,
            getattr(args, "sources", None),
            getattr(args, "effects", None),
            strict_state=getattr(args, "strict_state", True),
            **params,
        )
    if args.command == "engagement":
        return api.fit_engagement(
            fit,
            getattr(args, "defender_input", None),
            distance_m=args.distance_m,
            angular_velocity=args.angular_velocity,
            target_velocity=args.target_velocity,
            include_wrecking=args.include_wrecking,
            mode=args.mode,
            fleet=args.fleet,
            observed_hp=args.observed_hp,
            window_s=args.window_s,
            duration_s=args.duration_s,
            points=args.points,
            **params,
        )
    return api.fit_detail(fit, getattr(args, "view", None), getattr(args, "path", None), **params)


def _pick_defender(args: argparse.Namespace) -> tuple[object | None, str | None]:
    """`engagement` 的守方：`--defender PATH` / `--defender-eft TEXT` **恰好一个**。

    Returns:
        `(守方配装输入, 错误信息)`。读文件失败 / 两个都给 / 都没给 → 参数错误（退出码 2）。
    """
    given: list[tuple[str, str]] = []
    if getattr(args, "defender", None):
        given.append(("file", args.defender))
    if getattr(args, "defender_eft", None):
        given.append(("eft", args.defender_eft))
    if not given:
        return None, (
            "engagement 需要守方：--defender PATH（或 - 读 stdin）/ --defender-eft TEXT"
        )
    if len(given) > 1:
        return None, "守方来源互斥，一次只能给一个（--defender / --defender-eft）"
    kind, value = given[0]
    if kind == "eft":
        return value, None
    text, problem = _from_stdin_or_file(value)
    if problem is not None:
        return None, problem
    return text, None


def _pick_sources(args: argparse.Namespace) -> tuple[list[object] | None, str | None]:
    """`--source`（文件，可重复）与 `--source-eft`（内联，可重复）→ `sources` 数组。

    Returns:
        `(sources, 错误信息)`。**读文件失败 / 编码失败**是参数错误（退出码 2）——
        连来源内容都没拿到时不该去问引擎；而**内容本身解析失败**由 `api` 报
        `invalid_argument` + `index`（退出码 1，能指出是第几项）。
    """
    sources: list[object] = []
    for path in getattr(args, "source", None) or []:
        text, problem = _from_stdin_or_file(path)
        if problem is not None:
            return None, problem
        sources.append(text)
    for text in getattr(args, "source_eft", None) or []:
        sources.append(text)
    return sources, None


def _pick_json_array(text: str | None, flag: str) -> tuple[object | None, str | None]:
    """`--ops-json` / `--effects-json` → 数组参数（`None` = 没给）。

    Returns:
        `(数组, 错误信息)`。**JSON 解析失败 / 顶层不是数组**是参数错误（退出码 2）——
        连"要操作什么"都没说清时，不该拿去问引擎；而**数组里的单个坏条目**是数据问题，
        由 `sensitivity` / `projection` 写进 `warnings` 并跳过（退出码 0，结果照给）。
    """
    if not text or not str(text).strip():
        return None, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"{flag} 不是合法 JSON：{exc}"
    if not isinstance(parsed, list):
        return None, f"{flag} 必须是 JSON 数组，收到 {type(parsed).__name__}"
    return parsed, None


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


def _dispatch_related(args: argparse.Namespace) -> dict:
    """`related` → 一次 `api.zkb_related()`（**本文件不做任何逻辑**）。

    `url` 与 `system`/`time` 的**互斥**、时间戳 12 位校验、星系名字解析 —— 全在 `api` 层判
    （CLI 与 MCP 必须共用同一套判断，否则同一个查询两个出口不同结果）。
    """
    return api.zkb_related(
        url=args.url,
        system=args.system,
        time=args.time,
        limit=args.limit,
    )


def _pick_stats(args: argparse.Namespace) -> tuple[dict | None, str | None]:
    """从 `--character` / `--corporation` / `--alliance` 里挑**恰好一个**。

    Returns:
        `(api 参数, None)` 或 `(None, 人话问题描述)`。三个标志对应 zkb 的三个独立命名空间，
        所以「同时给两个」是真歧义，不能猜（同一串数字在三处都可能存在）。
    """
    given = [
        (kind, getattr(args, kind))
        for kind in api.ZKB_STATS_ENTITY_KINDS
        if getattr(args, kind) is not None
    ]
    if not given:
        return None, (
            "stats 需要恰好一个实体：--character / --corporation / --alliance"
        )
    if len(given) > 1:
        return None, (
            "stats 只能查一个实体：--character / --corporation / --alliance 请只给一个"
            f"（收到 {', '.join(kind for kind, _value in given)}）"
        )
    kind, value = given[0]
    params: dict = {"entity": value, "entity_type": kind}
    if getattr(args, "include", None):
        params["include"] = list(args.include)
    return params, None


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
        这样 `python -m noobbaozi.cli` 与进程内调用行为一致。
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

    if args.command == "stats":
        # 同理：`stats` 的输入是「实体」，不是配装
        params, problem = _pick_stats(args)
        if problem is not None:
            print(f"参数错误：{problem}", file=sys.stderr)
            return EXIT_USAGE
        return _report(args, api.zkb_stats(**params))

    if args.command == "related":
        # 同理：`related` 的输入是「星系 + 时间窗」或一条 related 链接
        return _report(args, _dispatch_related(args))

    fit, problem = _pick_fit(args)
    if problem is not None:
        print(f"参数错误：{problem}", file=sys.stderr)
        return EXIT_USAGE

    if args.command == "sensitivity":
        # `--ops-json` 的**顶层**形态是参数问题（退出码 2）；数组里的坏 op 走 warnings。
        ops, problem = _pick_json_array(getattr(args, "ops_json", None), "--ops-json")
        if problem is not None:
            print(f"参数错误：{problem}", file=sys.stderr)
            return EXIT_USAGE
        args.ops = ops

    if args.command == "project":
        # 来源文件读不到 / `--effects-json` 解析失败 → 参数问题（2）；
        # 来源**内容**解析失败由 api 报 invalid_argument + index（1）。
        sources, problem = _pick_sources(args)
        if problem is not None:
            print(f"参数错误：{problem}", file=sys.stderr)
            return EXIT_USAGE
        args.sources = sources
        effects, problem = _pick_json_array(getattr(args, "effects_json", None), "--effects-json")
        if problem is not None:
            print(f"参数错误：{problem}", file=sys.stderr)
            return EXIT_USAGE
        args.effects = effects

    if args.command == "engagement":
        # 守方文件读不到 / 两个来源都给 → 参数问题（2）；守方**内容**坏由 api 报
        # invalid_argument + field="defender"（1）。
        defender, problem = _pick_defender(args)
        if problem is not None:
            print(f"参数错误：{problem}", file=sys.stderr)
            return EXIT_USAGE
        args.defender_input = defender

    return _report(args, _dispatch(args, fit))


if __name__ == "__main__":  # pragma: no cover - 进程入口
    raise SystemExit(main())
