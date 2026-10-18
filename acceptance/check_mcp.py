"""MCP 服务器独立验收（**自己写的客户端**，不复用开发方的 client_smoke）。

五组：
  ① 协议层：initialize 版本协商 / notifications 不回包 / tools/list 结构 / ping / 错误码
  ② stdout 纯净性：逐行断言 `jsonrpc == "2.0"`（出现任何日志行 → high）
  ③ 五个工具实际返回 + `fit_index` 对照我的 L0 契约 + `fit_get` 逐位一致 + skill_tier 回显
  ④ 负例语义：业务失败（isError + kind）vs 协议错误（-32602）
  ⑤ 健壮性：垃圾行 / 超大 payload / 缺字段 → 服务不死；同输入 20 次 → LRU 命中

用法：
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
      python evefit/acceptance/check_mcp.py

坑（都已固化在本文件里）：
- **不要用 asyncio.create_subprocess_exec**：本机报 `WinError 5`；只用 `subprocess`。
- stdin 必须是**无 BOM 的 UTF-8 字节**（PowerShell 管道会加 BOM → 第一条 -32700）。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(HERE))

from check_layers import L0_CHECKLIST  # 我自己的清单（不是产品的 INDEX_SPEC）

EXPECTED_VERSIONS = ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
PREFERRED = EXPECTED_VERSIONS[0]
TOOL_ORDER = ["fit_parse", "fit_index", "fit_view", "fit_get", "fit_detail"]
VIEWS = ["firepower", "survivability", "mobility", "resources", "constraints", "stability"]


class Client:
    """极简 MCP stdio 客户端（只依赖标准库）。"""

    def __init__(self, name: str = "sess") -> None:
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # 仅供调试：默认不打开（打开会往 stderr 写，不影响 stdout 纯净性断言）
        self.name = name
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "evefit.mcp_server"],
            cwd=str(ROOT.parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env,
        )
        self.raw_lines: list[bytes] = []
        self.responses: list[dict] = []
        self.queue: queue.Queue = queue.Queue()
        self.stderr_lines: list[str] = []
        self._next_id = 0
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # ---- I/O ----
    def _read_stdout(self) -> None:
        # **必须逐行 readline**：`read(n)` 在管道上会一直阻塞到读满 n 字节，
        # 一条响应才几十字节，于是永远不返回（第一次跑就是这么卡死的）。
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.rstrip(b"\r\n")
            if not line.strip():
                continue
            self.raw_lines.append(line)
            text = line.decode("utf-8", "replace").strip()
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {"__unparsable__": text}
            self.responses.append(parsed)
            self.queue.put(text)

    def _read_stderr(self) -> None:
        for line in self.proc.stderr:
            self.stderr_lines.append(line.decode("utf-8", "replace").rstrip())

    def send_bytes(self, payload: bytes) -> None:
        self.proc.stdin.write(payload + b"\n")
        self.proc.stdin.flush()

    def send(self, obj: dict) -> None:
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def wait_for(self, request_id: object, timeout: float = 60.0) -> dict | None:
        """等到指定 id 的响应（其它响应排队丢弃）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                text = self.queue.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                return None
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("id") == request_id:
                return msg
        return None

    def drain(self, seconds: float = 0.6) -> list[str]:
        """收集一段时间内的所有输出（用于断言"通知不回包"）。"""
        out: list[str] = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                out.append(self.queue.get(timeout=max(0.02, deadline - time.time())))
            except queue.Empty:
                break
        return out

    def request(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        self._next_id += 1
        request_id = self._next_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)
        response = self.wait_for(request_id, timeout=timeout)
        return response if response is not None else {"__timeout__": True}

    def initialize(self, version: str) -> dict:
        return self.request("initialize", {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "evefit-acceptance-client", "version": "1.0"},
        })

    def call_tool(self, name: str, arguments: dict, timeout: float = 120.0) -> dict:
        response = self.request("tools/call", {"name": name, "arguments": arguments},
                                timeout=timeout)
        return response

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def envelope_from(response: dict) -> tuple[dict | None, dict | None]:
    """从 tools/call 响应里取 (envelope, raw_result)。优先 structuredContent。"""
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, dict):
        return None, None
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured, result
    content = result.get("content")
    if isinstance(content, list) and content:
        text = content[0].get("text") if isinstance(content[0], dict) else None
        if isinstance(text, str):
            try:
                return json.loads(text), result
            except json.JSONDecodeError:
                return None, result
    return None, result


def main() -> int:
    defects: list[dict] = []
    notes: list[str] = []
    results: dict[str, dict] = {}

    def defect(category: str, severity: str, where: str, expected: str, actual: str) -> None:
        defects.append({"category": category, "severity": severity, "where": where,
                        "expected": expected, "actual": actual})

    eft = (HERE / "fixtures" / "f1-hecate-killmail.eft").read_text(encoding="utf-8")

    # ---------------- ① 协议层（每个版本一个轻量会话） ----------------
    print("=" * 78)
    print("① 协议层")
    version_rows = []
    for version in [*EXPECTED_VERSIONS, "1999-01-01"]:
        client = Client(f"ver-{version}")
        try:
            response = client.initialize(version)
            got = ((response.get("result") or {}).get("protocolVersion")
                   if isinstance(response, dict) else None)
            want = version if version in EXPECTED_VERSIONS else PREFERRED
            ok = got == want
            version_rows.append((version, got, want, ok))
            if not ok:
                defect("口径错误", "high", f"initialize({version})",
                       f"protocolVersion == {want}", repr(got))
        finally:
            client.close()
    for version, got, want, ok in version_rows:
        print(f"  initialize({version:12s}) → {got!r}  期望 {want!r}  {'✅' if ok else '❌'}")

    client = Client("main")
    try:
        response = client.initialize(PREFERRED)
        init_result = response.get("result") or {}
        capabilities = init_result.get("capabilities") or {}
        tools_cap = capabilities.get("tools")
        print(f"  initialize（主会话）：protocolVersion={init_result.get('protocolVersion')!r} "
              f"capabilities.tools={'yes' if tools_cap is not None else 'MISSING'} "
              f"instructions={'yes' if init_result.get('instructions') else 'no'}")
        if tools_cap is None:
            defect("字段缺失", "high", "initialize", "capabilities.tools", "缺失")

        # 通知不得回包
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        leaked = client.drain(0.6)
        print(f"  notifications/initialized → 回包条数：{len(leaked)}  期望 0  "
              f"{'✅' if not leaked else '❌'}")
        if leaked:
            defect("口径错误", "high", "notifications/initialized", "不回包", leaked[:2])

        # tools/list
        response = client.request("tools/list")
        tools = (response.get("result") or {}).get("tools") or []
        names = [tool.get("name") for tool in tools]
        print(f"  tools/list → {len(tools)} 个：{names}")
        if names != TOOL_ORDER:
            defect("口径错误", "high", "tools/list", f"{TOOL_ORDER}", names)

        by_name = {tool.get("name"): tool for tool in tools}
        view_tool = by_name.get("fit_view") or {}
        view_schema = (view_tool.get("inputSchema") or {}).get("properties") or {}
        view_enum = (view_schema.get("view") or {}).get("enum")
        print(f"  fit_view.inputSchema.view.enum = {view_enum}")
        if view_enum is None or sorted(view_enum) != sorted(VIEWS):
            defect("口径错误", "high", "tools/list.fit_view", f"enum == {VIEWS}", repr(view_enum))
        get_tool = by_name.get("fit_get") or {}
        required = (get_tool.get("inputSchema") or {}).get("required")
        print(f"  fit_get.inputSchema.required = {required}")
        if not (isinstance(required, list) and "path" in required):
            defect("口径错误", "high", "tools/list.fit_get", "required 含 path", repr(required))

        # ping
        response = client.request("ping")
        ping_ok = isinstance(response.get("result"), dict)
        print(f"  ping → result={response.get('result')!r}  {'✅' if ping_ok else '❌'}")
        if not ping_ok:
            defect("口径错误", "high", "ping", "result 为对象", repr(response)[:120])

        # 错误码：坏 JSON / 未知方法 / 参数不合法
        # 解析错误时服务端拿不到 id，响应里 `id: null` —— 所以**不能按 id 等**，
        # 要按 error.code 在若干条响应里找（这是我第一版的错，不是服务端缺陷）。
        client.send_bytes(b'{"jsonrpc": "2.0", "id": 77, "method": ')   # 坏 JSON
        code = None
        for text in client.drain(2.0):
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                continue
            candidate = (message.get("error") or {}).get("code")
            if candidate == -32700:
                code = candidate
                break
            if code is None and candidate is not None:
                code = candidate
        print(f"  坏 JSON → error.code={code}  期望 -32700  {'✅' if code == -32700 else '❌'}")
        if code != -32700:
            defect("口径错误", "high", "错误码(坏 JSON)", "-32700", repr(code))

        response = client.request("no/such/method")
        code = ((response.get("error") or {}).get("code"))
        print(f"  未知方法 → error.code={code}  期望 -32601  {'✅' if code == -32601 else '❌'}")
        if code != -32601:
            defect("口径错误", "high", "错误码(未知方法)", "-32601", repr(code))

        response = client.request("tools/call", {"name": "fit_index", "arguments": {}})
        code = ((response.get("error") or {}).get("code"))
        print(f"  缺 fit → error.code={code}  期望 -32602  {'✅' if code == -32602 else '❌'}")
        if code != -32602:
            defect("口径错误", "high", "错误码(缺 fit)", "-32602", repr(response)[:160])

        # ---------------- ③ 五个工具 ----------------
        print()
        print("=" * 78)
        print("③ 五个工具的实际返回")
        t0 = time.time()
        parsed = client.call_tool("fit_parse", {"fit": eft})
        dt_parse = time.time() - t0
        env_parse, _ = envelope_from(parsed)
        ship = ((env_parse or {}).get("result") or {}).get("ship") if env_parse else None
        print(f"  fit_parse（{dt_parse:.2f}s）ok={(env_parse or {}).get('ok')} ship={ship}")

        t0 = time.time()
        index_resp = client.call_tool("fit_index", {"fit": eft})
        dt_index = time.time() - t0
        env_index, _ = envelope_from(index_resp)
        index_data = (env_index or {}).get("result") or {}
        print(f"  fit_index（{dt_index:.2f}s）ok={(env_index or {}).get('ok')} "
              f"顶层键={sorted(index_data)}")

        # 对照我的 L0 契约（字段名/形状）
        if not env_index or env_index.get("ok") is not True:
            defect("口径错误", "high", "fit_index", "ok == true", repr(env_index)[:200])
        else:
            for view, fields in L0_CHECKLIST.items():
                block = index_data.get(view)
                if not isinstance(block, dict):
                    defect("字段缺失", "high", f"fit_index.{view}", "视角块存在", repr(block)[:80])
                    continue
                missing = [name for name in fields if name not in block]
                extra = sorted(set(block) - set(fields))
                if missing:
                    defect("字段缺失", "high", f"fit_index.{view}", f"字段 {list(fields)}", f"缺 {missing}")
                if extra:
                    notes.append(f"fit_index.{view} 多出未登记字段：{extra}")
                for name, rule in fields.items():
                    if name in block and not isinstance(block[name], rule["type"]):
                        defect("口径错误", "high", f"fit_index.{view}.{name}",
                               f"类型 {rule['type']}", type(block[name]).__name__)
            meta = index_data.get("meta") or {}
            tier = meta.get("skill_tier") or {}
            print(f"  meta.skill_tier = {tier}")
            if tier.get("name") != "all4+min_required" or tier.get("count") != 512:
                defect("口径错误", "high", "fit_index.meta.skill_tier",
                       "all4+min_required / 512", repr(tier))

        view_resp = client.call_tool("fit_view", {"fit": eft, "view": "mobility"})
        env_view, _ = envelope_from(view_resp)
        view_data = (env_view or {}).get("result") or {}
        print(f"  fit_view(mobility) ok={(env_view or {}).get('ok')} keys={sorted(view_data)}")
        for key in ("overview", "provenance", "unknown_reasons", "confidence", "flags"):
            if key not in view_data:
                defect("字段缺失", "high", f"fit_view(mobility).{key}", "存在", "缺失")
        mobility_overview = view_data.get("overview") or {}
        view_speed = mobility_overview.get("speed_mps")

        get_resp = client.call_tool("fit_get", {"fit": eft, "path": "mobility.speed_mps"})
        env_get, _ = envelope_from(get_resp)
        get_data = (env_get or {}).get("result") or {}
        print(f"  fit_get(mobility.speed_mps) → {json.dumps(get_data, ensure_ascii=False)[:160]}")
        for key in ("value", "provenance", "source_view"):
            if key not in get_data:
                defect("字段缺失", "high", f"fit_get.{key}", "存在", "缺失")
        index_speed = ((index_data.get("mobility") or {}).get("speed_mps"))
        got_speed = get_data.get("value")
        same = (isinstance(got_speed, (int, float)) and isinstance(index_speed, (int, float))
                and float(got_speed) == float(index_speed)
                and isinstance(view_speed, (int, float)) and float(got_speed) == float(view_speed))
        print(f"  speed 三处一致：fit_index={index_speed} / fit_view={view_speed} / "
              f"fit_get={got_speed}  {'✅' if same else '❌'}")
        if not same:
            defect("口径错误", "high", "fit_get vs fit_index/fit_view",
                   "逐位相同", f"{index_speed} / {view_speed} / {got_speed}")

        detail_resp = client.call_tool("fit_detail", {"fit": eft, "view": "stability"})
        env_detail, _ = envelope_from(detail_resp)
        detail_data = (env_detail or {}).get("result") or {}
        print(f"  fit_detail(stability) ok={(env_detail or {}).get('ok')} keys={sorted(detail_data)}")

        # ---------------- ④ 负例语义 ----------------
        print()
        print("=" * 78)
        print("④ 负例语义")
        bad_path = client.call_tool("fit_get", {"fit": eft, "path": "mobility.nope"})
        env_bp, raw_bp = envelope_from(bad_path)
        error = (env_bp or {}).get("error") or {}
        is_error = (raw_bp or {}).get("isError")
        normal_rpc = isinstance(bad_path.get("result"), dict) and "error" not in bad_path
        print(f"  fit_get(mobility.nope) → 正常响应={normal_rpc} isError={is_error} "
              f"ok={(env_bp or {}).get('ok')} kind={error.get('kind')} "
              f"hint={str(error.get('hint'))[:60]!r}")
        for label, ok, detail in (
            ("是正常 JSON-RPC 响应", normal_rpc, repr(bad_path)[:120]),
            ("isError == true", is_error is True, repr(is_error)),
            ("ok == false", (env_bp or {}).get("ok") is False, repr((env_bp or {}).get("ok"))),
            ("kind == invalid_path", error.get("kind") == "invalid_path", repr(error.get("kind"))),
            ("带 hint", bool(error.get("hint")), repr(error.get("hint"))[:80]),
        ):
            if not ok:
                defect("口径错误", "high", "fit_get(负例)", label, detail)

        typo_fit = eft.replace("Damage Control II", "Damage Controll II")
        typo_resp = client.call_tool("fit_index", {"fit": typo_fit})
        env_typo, raw_typo = envelope_from(typo_resp)
        error_typo = (env_typo or {}).get("error") or {}
        candidates = error_typo.get("candidates")
        print(f"  fit_index(拼错模块) → isError={(raw_typo or {}).get('isError')} "
              f"kind={error_typo.get('kind')} candidates={str(candidates)[:80]}")
        if (raw_typo or {}).get("isError") is not True:
            defect("口径错误", "high", "fit_index(负例)", "isError == true",
                   repr((raw_typo or {}).get("isError")))
        if error_typo.get("kind") != "unknown_module":
            defect("口径错误", "high", "fit_index(负例)", "kind == unknown_module",
                   repr(error_typo.get("kind")))
        if not candidates:
            defect("字段缺失", "high", "fit_index(负例).candidates", "非空候选列表", repr(candidates))

        oversized = "A" * (256 * 1024 + 10)
        for label, arguments in (
            ("fit 为空串", {"fit": ""}),
            ("fit 超大(>256KiB)", {"fit": oversized}),
            ("fit 类型错(int)", {"fit": 123}),
            ("arguments 类型错", "not-a-dict"),
        ):
            response = client.request("tools/call", {"name": "fit_index", "arguments": arguments},
                                      timeout=120)
            code = (response.get("error") or {}).get("code")
            print(f"  {label} → error.code={code}  期望 -32602  {'✅' if code == -32602 else '❌'}")
            if code != -32602:
                defect("口径错误", "high", f"错误码({label})", "-32602", repr(response)[:140])
    finally:
        client.close()

    # ---------------- ② stdout 纯净性（用主会话的原始字节流） ----------------
    print()
    print("=" * 78)
    print("② stdout 纯净性")
    raw = client.raw_lines
    bad = []
    for line in raw:
        if not line.strip():
            continue
        try:
            parsed = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            bad.append(line[:120])
            continue
        if not isinstance(parsed, dict) or parsed.get("jsonrpc") != "2.0":
            bad.append(line[:120])
    bom = raw[0].startswith(b"\xef\xbb\xbf") if raw else False
    print(f"  stdout 行数={len(raw)} 非 JSON-RPC 行={len(bad)} 首行 BOM={bom}")
    for item in bad[:3]:
        print(f"    非协议行：{item!r}")
    if bad:
        defect("口径错误", "high", "stdout 纯净性", "每行都是 JSON-RPC 2.0 消息",
               f"{len(bad)} 行违规：{bad[:3]}")
    if bom:
        defect("口径错误", "high", "stdout 纯净性", "无 BOM", "首行带 UTF-8 BOM")

    # ---------------- ⑤ 健壮性 ----------------
    print()
    print("=" * 78)
    print("⑤ 健壮性")
    client = Client("robust")
    try:
        client.initialize(PREFERRED)
        client.send_bytes(b"this is not json at all")
        code = None
        for text in client.drain(2.0):
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                continue
            candidate = (message.get("error") or {}).get("code")
            if candidate in (-32700, -32600):
                code = candidate
                break
            if code is None and candidate is not None:
                code = candidate
        print(f"  垃圾行 → error.code={code}（-32700 或 -32600 均可）")
        if code not in (-32700, -32600):
            defect("口径错误", "medium", "健壮性(垃圾行)", "-32700/-32600", repr(code))

        response = client.request("ping")
        alive = isinstance(response.get("result"), dict)
        print(f"  垃圾行之后 ping → {'✅ 存活' if alive else '❌ 无响应'}")
        if not alive:
            defect("口径错误", "blocker", "健壮性", "服务必须存活", repr(response)[:120])

        big = json.dumps({"jsonrpc": "2.0", "id": 501, "method": "tools/call",
                          "params": {"name": "fit_index",
                                     "arguments": {"fit": "A" * (1024 * 1024)}}})
        client.send_bytes(big.encode("utf-8"))
        big_resp = client.wait_for(501, timeout=180) or {}
        code = (big_resp.get("error") or {}).get("code")
        print(f"  1MB payload → error.code={code}  期望 -32602  {'✅' if code == -32602 else '❌'}")
        response = client.request("ping")
        if not isinstance(response.get("result"), dict):
            defect("口径错误", "blocker", "健壮性(大 payload)", "服务必须存活", "ping 无响应")

        client.send({"jsonrpc": "2.0", "id": 502, "method": "tools/call",
                     "params": {"name": "fit_index"}})
        missing = client.wait_for(502, timeout=30) or {}
        code = (missing.get("error") or {}).get("code")
        print(f"  tools/call 缺 arguments → error.code={code}  期望 -32602  "
              f"{'✅' if code == -32602 else '❌'}")

        # LRU：同输入 20 次
        timings = []
        for _ in range(20):
            started = time.time()
            client.call_tool("fit_index", {"fit": eft}, timeout=180)
            timings.append(time.time() - started)
        first = timings[0]
        rest = sorted(timings[1:])
        median_rest = rest[len(rest) // 2]
        speedup = first / median_rest if median_rest > 0 else float("inf")
        print(f"  同输入 20 次：第 1 次={first*1000:.0f}ms 后续中位={median_rest*1000:.0f}ms "
              f"加速={speedup:.1f}×  {'✅' if speedup >= 1.5 else '❌'}")
        if speedup < 1.5:
            defect("口径错误", "medium", "LRU 缓存", "第 2 次起明显更快（≥1.5×）",
                   f"加速仅 {speedup:.2f}×")
    finally:
        client.close()

    # ---------------- 汇总 ----------------
    print()
    print("=" * 78)
    counts: dict[str, int] = {}
    for item in defects:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    print(f"缺陷合计 {len(defects)}：{counts or '无'}")
    for item in defects[:12]:
        print(f"  [{item['severity']}] {item['category']} | {item['where']}"
              f" | 期望 {item['expected']} | 实际 {item['actual']}")
    if notes:
        print(f"观察 {len(notes)} 条：{notes[:5]}")

    out = HERE / "out" / "mcp-check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "versions": [{"sent": v, "got": g, "want": w, "ok": ok}
                     for v, g, w, ok in version_rows],
        "stdout_lines": len(raw), "stdout_bad_lines": len(bad),
        "defects": defects, "notes": notes,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {out}")
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
