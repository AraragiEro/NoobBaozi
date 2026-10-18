"""L5 传输形态之一：**CLI**（开发期与人工验证的基线，文档 §10.5）。

```
python -m evefit.cli parse  --fit-file x.eft [--json]
python -m evefit.cli index  --fit-file x.eft [--killmail k.json] [--eft "…"] [--skill-tier all5] [--json]
python -m evefit.cli view   --fit-file x.eft --view mobility
python -m evefit.cli get    --fit-file x.eft --path mobility.speed_mps
python -m evefit.cli detail --fit-file x.eft [--view stability]
```

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


_HUMAN: dict[str, Callable[[dict, dict], str]] = {
    "parse": lambda result, _meta: _human_parse(result),
    "index": lambda result, _meta: _human_index(result),
    "view": lambda result, _meta: _human_view(result),
    "get": lambda result, _meta: _human_get(result),
    "detail": _human_detail,
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
    """五个子命令共用的选项（来源 / 三个共同参数 / `--json`，§10.2）。"""
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
    """命令行定义（五个子命令 + 共同选项）。"""
    parser = argparse.ArgumentParser(
        prog="python -m evefit.cli",
        description="evefit L5 接口层的 CLI 基线（与 MCP 共用 evefit/api.py 的逻辑）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<命令>")

    parse_cmd = subparsers.add_parser("parse", help="把输入归一化成结构（确认读懂了没有）")
    _add_common(parse_cmd)

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
    if args.command == "index":
        return api.fit_index(fit, **params)
    if args.command == "view":
        return api.fit_view(fit, args.view, **params)
    if args.command == "get":
        return api.fit_get(fit, args.path, **params)
    return api.fit_detail(fit, getattr(args, "view", None), getattr(args, "path", None), **params)


# =============================================================================
# 四、入口
# =============================================================================


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

    fit, problem = _pick_fit(args)
    if problem is not None:
        print(f"参数错误：{problem}", file=sys.stderr)
        return EXIT_USAGE

    envelope = _dispatch(args, fit)
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


if __name__ == "__main__":  # pragma: no cover - 进程入口
    raise SystemExit(main())
