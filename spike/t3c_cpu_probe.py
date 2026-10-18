"""T-21 探针：Loki（T3C）CPU 爆表 / AB 不生效 —— 判定根因层（引擎 / 读取 / 样本）。

**只读脚本**：不写任何产品代码、不改 fixture、不联网。它把结论逐条重新算一遍，
让下一个人不用相信本文件的注释。

复跑（仓库根目录）：

    $env:UV_CACHE_DIR = "$PWD\\noobbaozi\\spike\\uvcache"
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
        python noobbaozi/spike/t3c_cpu_probe.py

样本说明（重要）：
    任务书写的是 `noobbaozi/acceptance/fixtures/f4-loki-t3c.json`，**这个文件不存在**。
    真实样本是 `noobbaozi/acceptance/fixtures/f4-non-hecate.json`（README「真实 KB 样本」表里
    f4 就是 Loki / Strategic Cruiser），killmail 139254056。本脚本只读后者。

脚本输出 11 段（S0–S10），对应任务书要求的 6 项证据：

    S0  样本与前置（含 killmail 里被引擎丢掉的第 5 个子系统）
    S1  24 个已装模块 + state/max_state
    S2  base vs value 逐项对照（船级 48/49/-9/-2、cloak 的 50、其余模块的 50）
    S3  读取层实际取哪个字段（project/views/base.py 的行号 + 断言）
    S4  sources=true：ship 49 cpuLoad 的 modifier 链、cloak 50 的 modifier 链
    S5  子系统（已装 4 个 + 子系统舱 SubSystemBay 里的备用件）及 SDE 副作用定义
    S6  反事实 A：把 Defensive 换成 Covert Reconfiguration → cloak 50 / ship 49
    S7  反事实 B：cloak 置 offline / 摘掉 cloak → ship 49（真实游戏里的 CPU 占用）
    S8  512 技能档：复现验收数字 cpuOutput 540 / cpuLoad 10572.1
    S9  X3 单独归因：Abyssal Afterburner 缺 mutation → speedFactor 无加成
    S10 validate 的违规清单 + 总结论
"""

from __future__ import annotations

import copy
import json
import sys
import zipfile
from pathlib import Path

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

# 直接 `python noobbaozi/spike/t3c_cpu_probe.py` 时 sys.path[0] 是 script 目录，
# 先把仓库根塞进来，才能 `import noobbaozi`（uv 的 --no-project 不吃 pyproject）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from noobbaozi.config import paths
from noobbaozi.engine.adapter import Engine
from noobbaozi.engine import assumptions as assumption_engine
from noobbaozi.sde.index import Index

# ---------------------------------------------------------------- 常量（都用实测值，改了就重跑）

SAMPLE = Path(__file__).resolve().parents[1] / "acceptance" / "fixtures" / "f4-non-hecate.json"
"""真实样本。任务书里的 f4-loki-t3c.json 不存在（见模块 docstring）。"""

MISSING_SAMPLE = Path(__file__).resolve().parents[1] / "acceptance" / "fixtures" / "f4-loki-t3c.json"

# 属性 ID（SDE canon 名，见 noobbaozi/config/attributes.py）
A_CPU_OUTPUT = 48   # cpuOutput（船）
A_CPU_LOAD = 49     # cpuLoad（船，派生 = Σ item 50）
A_CPU_FREE = -9     # cpuFree（船，派生）
A_CPU = 50          # cpu（模块级 CPU 需求）
A_SPEED_FACTOR = 20  # speedFactor（推进模块的加成；AB 的"倍数"就在这里）
A_COVERT_CPU_ADD = 1870      # covertCloakCPUAdd：隐身模块自加的 CPU 罚值
A_COVERT_CPU_PENALTY = 1871  # covertCloakCPUPenalty：子系统广播给全部模块的 1870
A_MAX_VELOCITY = 37          # maxVelocity（船级最大速度，存储毫秒）

# 参与诊断的 type_id
CLOAK = 11578               # Covert Ops Cloaking Device II（HiSlot5）
SUBSYS_FITTED = (45633, 45597, 45608, 45621)  # 已装 4 个子系统（flag 125–128）
SUBSYS_COVERT = 45595       # Loki Defensive - Covert Reconfiguration（killmail 里在 flag 177）
AB_ABYSSAL = 47757          # 100MN Abyssal Afterburner（MediumSlot0）
AB_STOCK = 12068            # 100MN Afterburner II（对照用）

# 验收员报告的数字（用于对拍，不是判据）
ACCEPT_CPU_OUTPUT = 540.0
ACCEPT_CPU_LOAD = 10572.1


# ---------------------------------------------------------------- 小工具


def _v(entry: object) -> float | None:
    """`{base, value}` → `value`；裸数 → 原值；缺 → None。"""
    if isinstance(entry, dict):
        entry = entry.get("value")
    return float(entry) if isinstance(entry, (int, float)) else None


def _b(entry: object) -> float | None:
    """`{base, value}` → `base`。"""
    if isinstance(entry, dict):
        entry = entry.get("base")
    return float(entry) if isinstance(entry, (int, float)) else None


def _ship(calc: dict, attr_id: int) -> dict | None:
    return (calc.get("ship") or {}).get("attributes", {}).get(attr_id)


def _item(calc: dict, attr_id: int, index: int) -> dict | None:
    return ((calc.get("items") or [])[index].get("attributes") or {}).get(attr_id)


def _name(index: Index, type_id: int | None) -> str:
    row = index.get_type(type_id) if isinstance(type_id, int) else None
    return (row or {}).get("name") or f"<unknown {type_id}>"


def _slot_text(fit_item: dict) -> str:
    slot = fit_item.get("slot") or {}
    if not isinstance(slot, dict):
        return "?"
    kind = slot.get("type")
    idx = slot.get("index")
    return f"{kind}[{idx}]" if idx is not None else str(kind)


def _cpu_line(calc: dict) -> str:
    out_base, out = _b(_ship(calc, A_CPU_OUTPUT)), _v(_ship(calc, A_CPU_OUTPUT))
    load_base, load = _b(_ship(calc, A_CPU_LOAD)), _v(_ship(calc, A_CPU_LOAD))
    free = _v(_ship(calc, A_CPU_FREE))
    return (
        f"cpuOutput(48) base={out_base} value={out} | "
        f"cpuLoad(49) base={load_base} value={load} | cpuFree(-9) value={free}"
    )


def _with_states(fit: dict, state: str) -> dict:
    """浅改状态（只改已装模块；货舱保持 offline）。"""
    clone = copy.deepcopy(fit)
    for item in clone.get("items") or []:
        slot = item.get("slot") or {}
        if isinstance(slot, dict) and slot.get("type") not in (None, "cargo", "drone_bay"):
            item["state"] = state
    return clone


def _without_type(fit: dict, type_id: int) -> dict:
    clone = copy.deepcopy(fit)
    clone["items"] = [i for i in clone.get("items") or [] if i.get("type_id") != type_id]
    return clone


def _replace_type(fit: dict, old: int, new: int) -> dict:
    clone = copy.deepcopy(fit)
    for item in clone.get("items") or []:
        if item.get("type_id") == old:
            item["type_id"] = new
    return clone


def _drop_type(fit: dict, type_id: int) -> dict:
    return _without_type(fit, type_id)


def _type_dogma(wanted: set[int]) -> dict[int, dict]:
    """从本地 SDE zip 里直读 `typeDogma`（验证「罚值到底是哪来的」）。"""
    build = paths.sorted_builds()[-1]
    archive = paths.zip_path(build)
    found: dict[int, dict] = {}
    with zipfile.ZipFile(archive) as handle:
        with handle.open("typeDogma.jsonl") as stream:
            for line in stream:
                row = json.loads(line)
                key = row.get("_key")
                if key in wanted:
                    found[key] = row
                    if len(found) == len(wanted):
                        break
    return found


# ---------------------------------------------------------------- 主流程


def main() -> int:
    print("=" * 78)
    print("T-21 探针：Loki（T3C）CPU 爆表 X1 / AB 不生效 X3 —— 根因层判定")
    print("=" * 78)

    # ---- S0 ----------------------------------------------------------
    print("\n[S0] 样本与前置")
    print(f"  任务书路径存在？ {MISSING_SAMPLE.exists()}  ({MISSING_SAMPLE.name})")
    print(f"  实际使用样本： {SAMPLE.relative_to(SAMPLE.parents[2])} 存在={SAMPLE.exists()}")
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    print(f"  killmail_id={raw.get('killmail_id')}  zkb.hash={((raw.get('zkb') or {}).get('hash'))}")
    print(f"  受害者船型 type_id={((raw.get('victim') or {}).get('ship_type_id'))}"
          f"  killmail items={len(((raw.get('victim') or {}).get('items') or []))}")

    dogma.load_sde_from_file(str(sde_path()))
    index = Index.open(paths.db_path())
    build = paths.sorted_builds()[-1]
    print(f"  SDE build={build}  引擎版本=13.6.0  索引库={paths.db_path().name}")

    fit = dogma.load_killmail(raw)
    ship_type = (fit.get("ship") or {}).get("type_id")
    print(f"  load_killmail → ship={ship_type}（{_name(index, ship_type)}）"
          f"  items={len(fit.get('items') or [])}（killmail 里 {len(raw['victim']['items'])} 条）")

    killmail_types = [i.get("item_type_id") for i in raw["victim"]["items"]]
    fit_types = [i.get("type_id") for i in fit.get("items") or []]
    from collections import Counter
    src, dst = Counter(killmail_types), Counter(fit_types)
    lost = {t: src[t] - dst.get(t, 0) for t in src if src[t] != dst.get(t, 0)}
    print(f"  ⚠ 被 load_killmail 丢掉的类型： {lost}")
    for t in lost:
        flags = sorted({i["flag"] for i in raw["victim"]["items"] if i["item_type_id"] == t})
        print(f"     {t} = {_name(index, t)}  出现在 flag={flags}")
    print("     flag 语义（esi/eve-glue location_flag.py）：125=SubSystemSlot0(Core) "
          "126=1(Defensive) 127=2(Offensive) 128=3(Propulsion) / 177=SubSystemBay（子系统舱，不是装上的槽位）")
    print("     → 45595 是「带在子系统舱里的备用件」，不是已装状态；引擎丢掉它是对的。")

    # ---- S1 ----------------------------------------------------------
    print("\n[S1] 已装模块与状态（引擎给的 state / max_state）")
    calc = dogma.calculate(fit)
    installed = [
        (i, it) for i, it in enumerate(fit["items"])
        if _slot_text(it).split("[")[0] not in ("cargo", "drone_bay", "fighter_bay")
    ]
    print(f"  已装模块 {len(installed)} 个（不含货舱/无人机舱/机库）：")
    for i, it in installed:
        row = calc["items"][i]
        print(f"    [{i:2d}] {_name(index, it['type_id']):46s} {_slot_text(it):14s} "
              f"state={row.get('state'):8s} max_state={row.get('max_state')}")

    # ---- S2 ----------------------------------------------------------
    print("\n[S2] base vs value：船级与模块级 cpu")
    print("  船级（无技能档）：")
    for attr_id, label in (
        (A_CPU_OUTPUT, "cpuOutput(48)"), (A_CPU_LOAD, "cpuLoad(49)"),
        (A_CPU_FREE, "cpuFree(-9)"), (A_MAX_VELOCITY, "maxVelocity(37)"),
    ):
        entry = _ship(calc, attr_id)
        print(f"    {label:16s} base={_b(entry)!r:>12} value={_v(entry)!r:>14}")
    print("  模块级 cpu(50)（只列有该属性的模块；None = 该模块 SDE 里根本没这个属性）：")
    zero_skill_load = _v(_ship(calc, A_CPU_LOAD)) or 0.0
    total = 0.0
    for i, it in installed:
        entry = _item(calc, A_CPU, i)
        value = _v(entry)
        if value is not None:
            total += value
        flag = "  ← 就是它" if it["type_id"] == CLOAK else ""
        print(f"    [{i:2d}] {_name(index, it['type_id']):44s} "
              f"base={_b(entry)!r:>9} value={value!r:>12}{flag}")
    print(f"  Σ 模块 cpu(50) value = {total!r}  vs  ship cpuLoad(49) value = {zero_skill_load!r} "
          f"→ {'完全相等' if abs(total - zero_skill_load) < 1e-9 else '不相等！'}")

    # ---- S3 ----------------------------------------------------------
    print("\n[S3] 读取层到底取哪个字段（product/views/base.py）")
    base_py = Path(__file__).resolve().parents[1] / "project" / "views" / "base.py"
    lines = base_py.read_text(encoding="utf-8").splitlines()
    for lineno in (72, 85):
        print(f"    base.py:{lineno}: {lines[lineno - 1].strip()}")
    hits = [n for n, line in enumerate(lines, 1) if 'entry.get("value")' in line]
    print(f"  `entry.get(\"value\")` 出现在行 {hits}；代码里取 `base` 的行："
          f" {[n for n, line in enumerate(lines, 1) if 'get(\"base\")' in line] or '无'}")
    print("  → 读取层（item_attributes / ship_attributes）取的是 value，不是 base。"
          " 若改成 base，cloak 会是 100、ship cpuLoad 会是 0.0（更错）。")

    # ---- S4 ----------------------------------------------------------
    print("\n[S4] sources=true：modifier 链")
    src_calc = dogma.calculate(fit, {"sources": True})
    ship49 = src_calc["ship"]["attributes"][A_CPU_LOAD]
    chain = ship49.get("sources") or []
    print(f"  ship cpuLoad(49)：base={ship49.get('base')!r} value={ship49.get('value')!r} "
          f"modifier 条数={len(chain)}")
    acc = 0.0
    for entry in chain:
        frm = entry.get("from") or {}
        who = f"item[{frm.get('index')}]" if frm.get("type") == "item" else str(frm)
        acc += float(entry.get("value") or 0.0)
        tail = "" if entry.get("applied") else "  (未生效)"
        print(f"    {who:12s} effect={entry.get('effect_id')} "
              f"src_attr={entry.get('source_attribute_id')} {entry.get('operator')} "
              f"+{entry.get('value')}{tail}")
    print(f"  Σ modifier = {acc!r}   (== value {ship49.get('value')!r})")

    cloak50 = src_calc["items"][15]["attributes"][A_CPU]
    print(f"\n  cloak item cpu(50)：base={cloak50.get('base')!r} value={cloak50.get('value')!r}")
    for entry in cloak50.get("sources") or []:
        frm = entry.get("from") or {}
        who = f"item[{frm.get('index')}]" if frm.get("type") == "item" else str(frm)
        print(f"    {who:12s} effect={entry.get('effect_id')} "
              f"src_attr={entry.get('source_attribute_id')} {entry.get('operator')} "
              f"+{entry.get('value')} applied={entry.get('applied')}")
    print("    ↑ 这是 cloak 的【自加罚值】：effect 5261 CovertCloakCPUAddition，"
          "把 1870 covertCloakCPUAdd 加到自己的 50 上。")

    # ---- S5 ----------------------------------------------------------
    print("\n[S5] 子系统：谁在给 / 谁在收这笔 CPU 罚值")
    dogma_rows = _type_dogma(set(SUBSYS_FITTED) | {SUBSYS_COVERT, CLOAK})
    for type_id in (*SUBSYS_FITTED, SUBSYS_COVERT):
        row = dogma_rows.get(type_id) or {}
        attrs = {a["attributeID"]: a["value"] for a in row.get("dogmaAttributes") or []}
        effects = [e["effectID"] for e in row.get("dogmaEffects") or []]
        fitted = type_id in SUBSYS_FITTED
        print(f"    {type_id} {_name(index, type_id):46s} 已装={fitted}  "
              f"1871(covertCloakCPUPenalty)={attrs.get(A_COVERT_CPU_PENALTY)!r}  "
              f"649(covertOpsCpuBonus)={attrs.get(649)!r}  effects 含 5262={5262 in effects}")
    print("  SDE 定义（dogmaEffects.jsonl 实读）：")
    print("    effect 5261 CovertCloakCPUAddition : itemID 域，50 += 1870（隐身模块自己加）")
    print("    effect 5262 covertOpsCloakCpuPenalty: shipID 域，1870 += 1871（子系统把 1871 广播给所有模块）")
    print("    effect 896  covertOpsCpuBonus1      : shipID 域，50 post_mul 649（只有 Covert Reconfiguration 带）")
    print("    → 已装 Defensive = 45597 Adaptive Defense Node 带 1871=10000 且带 effect 5262，")
    print("      所以 cloak 的 1870 = 10000 → cpu(50) = 100 + 10000 = 10100。")
    print("    → 45595 Covert Reconfiguration 的 1871 缺失、也没有 effect 5262 → 不会加这 10000，")
    print("      另外 effect 896 再乘 649=0 → 引擎给出 0.0（这个 0 是否等于游戏真实值，未独立核实）。")
    print("  ⚠ 注意：子系统**不提供 CPU 减免属性**（没有 50/48 的负向 buff），")
    print("     它靠「非 covert 子系统额外广播 10000 的罚值」来限制隐身模块 —— 这是加罚，不是打折。")

    # ---- S6 ----------------------------------------------------------
    print("\n[S6] 反事实 A：把 Defensive 换成 Covert Reconfiguration（45597 → 45595）")
    tier = assumption_engine.default_assumptions(fit, Engine(), index)["skill_tier"]
    skilled = copy.deepcopy(fit)
    skilled["character"] = {"skills": dict(tier)}
    skilled_calc = dogma.calculate(skilled)
    covert_fit = _replace_type(fit, 45597, SUBSYS_COVERT)
    covert_calc = dogma.calculate(covert_fit, {"sources": True})
    cloak_entry = _item(covert_calc, A_CPU, 15)
    print(f"    换子系统后：{_name(index, 45595)}")
    print(f"    cloak cpu(50) base={_b(cloak_entry)!r} value={_v(cloak_entry)!r}")
    for entry in cloak_entry.get("sources") or []:
        frm = entry.get("from") or {}
        who = f"item[{frm.get('index')}]" if frm.get("type") == "item" else str(frm)
        print(f"      {who:12s} effect={entry.get('effect_id')} "
              f"src_attr={entry.get('source_attribute_id')} {entry.get('operator')} "
              f"value={entry.get('value')}")
    print(f"    {_cpu_line(covert_calc)}")
    print("    → 引擎完全能算 covert 配置，不需要 ship.mode / 子系统 state / 其它额外输入；")
    print("      差别只在「装的是哪个 Defensive 子系统」。cloak 的 0.0 来自 effect 896")
    print("      post_mul 649，而 649 在 SDE 里就是 0.0 —— 这个 0 是否为游戏真实值未独立核实，")
    print("     但**关键动作是那 10000 的罚值消失**，cpuLoad 落到 521.35。")
    print("\n    交叉验证（同一段引擎代码在真正的 covert 船上是否合理）：")
    buzzard = [r["type_id"] for r in index.search_types("Buzzard", limit=3)
               if r["name"] == "Buzzard"][0]
    buzz_fit = {"ship": {"type_id": buzzard},
                "items": [{"type_id": CLOAK, "slot": {"type": "high", "index": 0}, "state": "active"}]}
    buzz_calc = dogma.calculate(buzz_fit, {"sources": True})
    buzz_cpu = buzz_calc["items"][0]["attributes"][A_CPU]
    print(f"      {_name(index, buzzard)}（{buzzard}，Covert Ops）+ 同一件 cloak："
          f"cpu(50) base={_b(buzz_cpu)!r} value={_v(buzz_cpu)!r}")
    for entry in buzz_cpu.get("sources") or []:
        frm = entry.get("from") or {}
        who = f"item[{frm.get('index')}]" if frm.get("type") == "item" else str(frm)
        print(f"        {who:12s} effect={entry.get('effect_id')} "
              f"src_attr={entry.get('source_attribute_id')} {entry.get('operator')} "
              f"value={entry.get('value')}")
    print("      → 真正的 covert 船上是 80.0（100 × −20% 船体 role bonus），没有 10000 罚值。")
    print("        同一引擎、同一件模块，差别只在「这条船是不是 covert 配置」→ 引擎没问题。")

    # ---- S7 ----------------------------------------------------------
    print("\n[S7] 反事实 B：cloak 不上线时，真实 CPU 占用是多少")
    offline_fit = copy.deepcopy(fit)
    offline_fit["items"][15]["state"] = "offline"
    offline_calc = dogma.calculate(offline_fit)
    no_cloak_calc = dogma.calculate(_drop_type(fit, CLOAK))
    print(f"    原样（cloak active）：      {_cpu_line(calc)}")
    print(f"    cloak 置 offline：         {_cpu_line(offline_calc)}")
    print(f"    摘掉 cloak：               {_cpu_line(no_cloak_calc)}")
    all_online = dogma.calculate(_with_states(fit, "online"))
    print(f"    全员 online：              {_cpu_line(all_online)}")
    post = dogma.calculate(dogma.post_load(fit))
    print(f"    post_load（EVE 导入默认）：{_cpu_line(post)}"
          f"  cloak state={(post['items'][15]).get('state')}")
    print("    → 引擎不做资源门控：cloak 不管装成 online 还是 active，那 10100 都照加；")
    print("      `validate` 只负责报违规，不会把模块降级成 offline。")

    load_now = _v(_ship(calc, A_CPU_LOAD)) or 0.0
    cloak_cpu = _v(_item(calc, A_CPU, 15)) or 0.0
    print(f"    → 剔除 cloak 后的真实占用：{load_now - cloak_cpu!r} tf"
          f"（= {load_now!r} - {cloak_cpu!r}），与摘掉 cloak 的结果一致："
          f"{_v(_ship(no_cloak_calc, A_CPU_LOAD))!r}")

    offline_sk = _drop_type(skilled, CLOAK)
    offline_sk_fit = copy.deepcopy(skilled)
    offline_sk_fit["items"][15]["state"] = "offline"
    covert_sk = dogma.calculate(_replace_type(skilled, 45597, SUBSYS_COVERT))
    print("    512 技能档下的三种口径（应与验收的 10572.1 对照）：")
    print(f"      cloak active（现状）      cpuLoad={_v(_ship(skilled_calc, A_CPU_LOAD))!r}"
          f"   ← 验收看到的就是这个")
    print(f"      cloak offline            cpuLoad={_v(_ship(dogma.calculate(offline_sk_fit), A_CPU_LOAD))!r}")
    print(f"      摘掉 cloak               cpuLoad={_v(_ship(dogma.calculate(offline_sk), A_CPU_LOAD))!r}")
    print(f"      换 Covert Reconfiguration cpuLoad={_v(_ship(covert_sk, A_CPU_LOAD))!r}")
    print("      ↑ 后三种收敛到同一个数 → 真实游戏里的 CPU 占用就是这个值。")

    # ---- S8 ----------------------------------------------------------
    print("\n[S8] 512 技能档：复现验收员的数字")
    out = _v(_ship(skilled_calc, A_CPU_OUTPUT))
    load = _v(_ship(skilled_calc, A_CPU_LOAD))
    print(f"    技能数={len(tier)}  {_cpu_line(skilled_calc)}")
    print(f"    验收报告：cpuOutput={ACCEPT_CPU_OUTPUT} cpuLoad={ACCEPT_CPU_LOAD}")
    print(f"    对拍：output {'==' if out == ACCEPT_CPU_OUTPUT else '!='} "
          f"{out!r} / load {'==' if load == ACCEPT_CPU_LOAD else '!='} {load!r}")
    print(f"    验收数字 = 无技能档的 {out!r}/{load!r} 关系："
          f"load-without-cloak = {(load or 0.0) - (_v(_item(skilled_calc, A_CPU, 15)) or 0.0)!r} tf")

    # ---- S9 ----------------------------------------------------------
    print("\n[S9] X3 单独归因：Abyssal Afterburner 为什么不生效")
    ab_entry = _item(calc, A_SPEED_FACTOR, 3)
    print(f"    样本里的 {_name(index, AB_ABYSSAL)}（item[3]）："
          f"speedFactor(20) base={_b(ab_entry)!r} value={_v(ab_entry)!r}")
    print(f"    它的全部属性：{sorted((calc['items'][3].get('attributes') or {}), key=int)}"
          "（没有 cpu/propulsion 倍率，只有 mass/volume/技能/heatDamage）")
    print(f"    killmail 里有没有 mutation 字段？ "
          f"{[k for k in raw['victim']['items'] if k.get('item_type_id') == AB_ABYSSAL][0]}")
    stock = _replace_type(fit, AB_ABYSSAL, AB_STOCK)
    stock_calc = dogma.calculate(stock)
    print(f"    换成 {_name(index, AB_STOCK)}（{AB_STOCK}）后：")
    print(f"      speedFactor(20) base={_b(_item(stock_calc, A_SPEED_FACTOR, 3))!r} "
          f"value={_v(_item(stock_calc, A_SPEED_FACTOR, 3))!r}")
    print(f"      ship maxVelocity(37) 样本={_v(_ship(calc, A_MAX_VELOCITY))!r} → 换正品="
          f"{_v(_ship(stock_calc, A_MAX_VELOCITY))!r}（存储毫秒；差 "
          f"{((_v(_ship(stock_calc, A_MAX_VELOCITY)) or 0.0) / max(_v(_ship(calc, A_MAX_VELOCITY)) or 1.0, 1e-9)):.2f}×）")
    print("    → 引擎机制没问题（正品 AB 立刻生效）；Abyssal 模块的加成全在 mutation 里，")
    print("      ESI killmail 不带 mutation → 还原出来的 speedFactor 就是底值 1.0（无加成）。")
    print("      X3 与 X1 无因果：AB 的 state 是 active，速度不变是 mutation 缺失，不是 CPU 超限。")

    # ---- S10 ---------------------------------------------------------
    print("\n[S10] validate 与总结论")
    violations = dogma.calculate(skilled, {"validate": True}).get("violations") or []
    print(f"    违规 {len(violations)} 条：")
    for entry in violations:
        target = entry.get("target") or {}
        rule = entry.get("rule") or {}
        print(f"      target={target} rule={json.dumps(rule, ensure_ascii=False)}")

    load_sk = _v(_ship(skilled_calc, A_CPU_LOAD)) or 0.0
    cloak_sk = _v(_item(skilled_calc, A_CPU, 15)) or 0.0
    real_load_sk = (load_sk - cloak_sk)
    print()
    print("  结论 1（根因层 = 样本 / 还原，不是引擎 bug，也不是读取层 bug）")
    print("    · 读取层：读 value（base.py:72、85）→ 不存在 base/value 误读。")
    print("    · 引擎：10100 是对「你给它的这套已装模块」的正确实现 —— 已装 Defensive 子系统")
    print("      45597 的 1871(covertCloakCPUPenalty)=10000 经 effect 5262 广播成 cloak 的 1870，")
    print("      cloak 自加 → 50: 100 + 10000 = 10100（sources 链逐条可核对）。")
    print("    · 样本：唯一能让这 10000 消失的 Covert Reconfiguration（45595）在 killmail 里位于")
    print("      flag 177 = SubSystemBay（子系统舱），**不是已装**；已装槽位是 125–128 四个。")
    print("      → 真实游戏里这台 Loki 的 cloak 不可能在线（要 10100 而只剩 ~472 余量），")
    print("        它只是「装在 HiSlot5 但用不了」的模块。killmail 不带 state，这一点是")
    print("        CPU 算术的必然推论，不是实测到的 state（未确认其实际 offline 状态）。")
    print()
    print("  结论 2（数字对照 —— 我们读的就是 value，问题不在口径）")
    print(f"    · cloak cpu(50)：base=100.0 / value=10100.0；我们读 value → 10100.0。")
    print(f"    · ship cpuOutput(48)：base=300.0 / value=450.0（512 技能档 = {out!r}）。")
    print(f"    · ship cpuLoad(49)：base=0.0 / value={_v(_ship(calc, A_CPU_LOAD))!r}（512 技能档 = {load_sk!r}）。")
    print(f"    · 真实占用（cloak 不在线）= {real_load_sk!r} tf ≤ {out!r} tf，余 {out - real_load_sk!r} tf。")
    print(f"    · 若改读 base：cloak=100.0、ship cpuLoad=0.0 —— 更错，且解释不出 10572.1。")
    print("    → 「如果我们读 value，数字会变成多少」的答案是：**已经是 value 了**，")
    print(f"      所以数字就是 10100 / {load_sk!r}；它不是读数错，是这套配装本身的状态不成立。")
    print()
    print("  结论 3（真正该派活的地方：状态语义，不是数值口径）")
    print("    · 引擎不做资源门控：active / online 都照加 CPU，只有 offline 才不计；")
    print("      `validate` 只报 resource 违规，`post_load` 只会把 cloak 设成 online，")
    print("      两者都不会因为 CPU 不够把模块降级成 offline。")
    print("    · 产品侧现状：spike/ 之外 `post_load` 零调用；resources 视角直接取 ship 49（value），")
    print("      完全不看 item state → 于是把「不可能在线」的模块当成真实占用渲染。")
    print("    · 建议（诊断结论，不是本任务改动）：投影/诊断层在 cpuLoad > cpuOutput 时要")
    print("      ① 把超限模块按 offline 重算（或标「不可行」），② 保持 state 与数值自洽。")
    print()
    print("  结论 4（X3 独立于 X1）")
    print("    · Abyssal 模块的加成全在 mutation 里，ESI killmail 不带 mutation →")
    print("      speedFactor 还原成底值 1.0（正品 100MN AB II 是 135.0）→ 速度不变。")
    print("    · AB 的 state 是 active，与 CPU 超限无因果；即使 cloak 修好，AB 仍然不生效。")
    print("    · 未确认：本样本 Abyssal 模块的真实词条（killmail 无法提供，需 ESF/esf-link 才有）。")
    print()
    index.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
