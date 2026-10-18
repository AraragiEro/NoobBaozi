"""T-28 探针：f26 航母「载机零伤害」——载机到底在机库还是发射管？

**只读脚本**：不写产品代码、不改 fixture、不联网。结论逐条重算，不要求读者相信注释。

待核实的问题（验收员 R2 的 medium）：f26 里 `dps.fighter = 0.0`、item 级 `-55/-56/-58 = 0`，
只有 `-57 = 14000`。到底是引擎限制（bug），还是「载机在机库未发射」的正确行为？

复跑（仓库根目录）：

    $env:UV_CACHE_DIR = "$PWD\\noobbaozi\\spike\\uvcache"
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
        python noobbaozi/spike/carrier_fighter_probe.py

输出 7 段（S0–S6）：

    S0  样本与前置
    S1  原始 killmail 里每件「载机相关 flag」的物品（原样 flag + 语义 + 数量字段）
    S2  fixture 里出现过的全部 flag 分布（证明 159–163 一根都没出现）
    S3  load_killmail 之后 fit 的 slot.type（引擎把 158 读成了什么）
    S4  baseline 计算：船级 -55..-62、item 级 -55/-56/-57/-58
    S5  改造实验：载机从 fighter_bay 手工移进 fighter_tube -> -58 / -59..-62 变化
    S6  flag -> slot.type 映射表（合成 killmail 逐 flag 探，155..164）

S1/S2 的 flag 语义来自**现代 SDE 的 invFlags 表**（本地 SDE zip 不含此表）：

    https://sde.zzeve.com/invFlags.json   (Last Updated: Sun Nov 09 2025)

    155 FleetHangar / 156 HiddenModifiers / 157 StructureOffline / 158 FighterBay
    159 FighterTube0 / 160 FighterTube1 / 161 FighterTube2 / 162 FighterTube3 / 163 FighterTube4

注意 158 是 **FighterBay（机库）**，159–163 才是发射管——这一点是本探针的判据基础。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import esf_dogma_engine as dogma
from eveshipfit_sde import build_number, sde_path

# 直接 `python noobbaozi/spike/carrier_fighter_probe.py` 时 sys.path[0] 是 script 目录，
# 先把仓库根塞进来，才能 `import noobbaozi`（uv 的 --no-project 不吃 pyproject）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from noobbaozi.config import paths  # noqa: E402
from noobbaozi.sde.index import Index  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parents[1] / "acceptance" / "fixtures" / "f26-carrier-fighter.json"
)
"""真实样本。任务书写的是 `f26-carrier-thanatos.json`，**这个文件不存在**；
真实样本是 `f26-carrier-fighter.json`（killmail 139248511，victim ship 23911 = Thanatos）。"""

# ---- 现代 SDE invFlags（155..163 全段，来源见模块 docstring） ----
FLAG_NAMES: dict[int, str] = {
    87: "DroneBay",
    90: "ShipHangar",
    92: "RigSlot0",
    93: "RigSlot1",
    94: "RigSlot2",
    133: "SpecializedFuelBay",
    155: "FleetHangar",
    156: "HiddenModifiers",
    157: "StructureOffline",
    158: "FighterBay",
    159: "FighterTube0",
    160: "FighterTube1",
    161: "FighterTube2",
    162: "FighterTube3",
    163: "FighterTube4",
    177: "SubsystemBay",
}

FIGHTER_FLAGS: tuple[int, ...] = (158, 159, 160, 161, 162, 163)
"""载机可能出现的 flag：158 = 机库，159–163 = 发射管 0–4。"""

# 属性 ID（见 noobbaozi/config/attributes.py）
ATTR_FIGHTER_ABILITY_ATTACK_DPS = -55   # fighterAbilityAttackMissileDamagePerSecond
ATTR_FIGHTER_ABILITY_MISSILES_DPS = -56  # fighterAbilityMissilesDamagePerSecond
ATTR_FIGHTER_BAY_USED = -57              # fighterCapacityLoad（机库占用 m3）
ATTR_FIGHTER_DPS = -58                   # fighterDps（舰船级）
ATTR_HEAVY_FIGHTER_SLOTS_USED = -59
ATTR_LIGHT_FIGHTER_SLOTS_USED = -60
ATTR_SUPPORT_FIGHTER_SLOTS_USED = -61
ATTR_FIGHTER_TUBES_USED = -62            # fighterTubesUsed（按中队计）

ATTR_DAMAGE_DPS = -12
ATTR_DRONE_DPS = -14
ATTR_FIGHTER_TUBES = 2216
ATTR_LIGHT_FIGHTER_SLOTS = 2217
ATTR_SUPPORT_FIGHTER_SLOTS = 2218
ATTR_HEAVY_FIGHTER_SLOTS = 2219
ATTR_FIGHTER_SQUADRON_MAX_SIZE = 2215

SHIP_CHECK = (
    (ATTR_FIGHTER_ABILITY_ATTACK_DPS, "-55 攻击能力DPS(item)"),
    (ATTR_FIGHTER_ABILITY_MISSILES_DPS, "-56 导弹能力DPS(item)"),
    (ATTR_FIGHTER_BAY_USED, "-57 机库占用"),
    (ATTR_FIGHTER_DPS, "-58 fighterDps(船)"),
    (ATTR_HEAVY_FIGHTER_SLOTS_USED, "-59 重战槽占用"),
    (ATTR_LIGHT_FIGHTER_SLOTS_USED, "-60 轻战槽占用"),
    (ATTR_SUPPORT_FIGHTER_SLOTS_USED, "-61 支援槽占用"),
    (ATTR_FIGHTER_TUBES_USED, "-62 发射管占用"),
    (ATTR_DAMAGE_DPS, "-12 船级总DPS"),
    (ATTR_DRONE_DPS, "-14 无人机DPS"),
)


# ---------------------------------------------------------------- 小工具


def v(entry: object) -> float | None:
    """`{base, value}` → `value`；裸数 → 原值；缺 → None。"""
    if isinstance(entry, dict):
        entry = entry.get("value")
    return float(entry) if isinstance(entry, (int, float)) else None


def ship_attr(calc: dict, attr_id: int) -> float | None:
    return v(((calc.get("ship") or {}).get("attributes") or {}).get(attr_id))


def item_attr(calc: dict, index: int, attr_id: int) -> float | None:
    attrs = ((calc.get("items") or [])[index] or {}).get("attributes") or {}
    return v(attrs.get(attr_id))


def flag_label(flag: int) -> str:
    return f"{flag} {FLAG_NAMES.get(flag, '?')}"


def slot_text(fit_item: dict) -> str:
    slot = fit_item.get("slot") or {}
    if not isinstance(slot, dict):
        return f"<raw {slot!r}>"
    where = str(slot.get("type"))
    if "index" in slot:
        where += f"[{slot['index']}]"
    return where


# ---------------------------------------------------------------- 准备


print("=" * 78)
print("S0  样本与前置")
print("=" * 78)
print("引擎 SDE build :", build_number())
dogma.load_sde_from_file(sde_path())
index = Index.open(paths.db_path())


def type_name(type_id: object) -> str:
    if not isinstance(type_id, int):
        return f"<{type_id!r}>"
    row = index.get_type(type_id)
    return (row or {}).get("name") or f"<unknown {type_id}>"


raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
killmail = copy.deepcopy(raw)
killmail.pop("zkb", None)

victim = killmail["victim"]
print("fixture               :", FIXTURE.name)
print("killmail_id           :", killmail.get("killmail_id"))
print("victim ship_type_id   :", victim.get("ship_type_id"), "=", type_name(victim.get("ship_type_id")))
print("victim item 条数      :", len(victim.get("items") or []))

# ---------------------------------------------------------------- S1


print()
print("=" * 78)
print("S1  原始 killmail 里「载机相关 flag」的每一件（原样打印）")
print("=" * 78)

carrier_items = [
    it for it in (victim.get("items") or [])
    if isinstance(it, dict) and it.get("flag") in FIGHTER_FLAGS
]
print(f"flag ∈ {FIGHTER_FLAGS} 的物品共 {len(carrier_items)} 条：")
print()
print(f"  {'flag':>4} {'语义':<14} {'item_type_id':>12} {'名称':<22} "
      f"{'destroyed':>9} {'dropped':>7} {'singleton':>9}")
print("  " + "-" * 92)
for it in sorted(carrier_items, key=lambda x: (x.get("flag") or 0, x.get("item_type_id") or 0)):
    flag = it.get("flag")
    tid = it.get("item_type_id")
    print(f"  {flag:>4} {FLAG_NAMES.get(flag, '?'):<14} {tid:>12} {type_name(tid):<22} "
          f"{it.get('quantity_destroyed', 0):>9} {it.get('quantity_dropped', 0):>7} "
          f"{it.get('singleton', 0):>9}")

print()
print("  -> 装载机（Siren/Templar）的条目全部落在 flag 158 = FighterBay（机库）。")
print("     没有任何一条落在 159–163 = FighterTube0..4（已发射的发射管）。")

# ---------------------------------------------------------------- S2


print()
print("=" * 78)
print("S2  fixture 里出现过的全部 flag 分布（含嵌套容器内）")
print("=" * 78)


def walk(items: list, depth: int, acc: dict[int, dict[int, int]]) -> None:
    for it in items:
        if not isinstance(it, dict):
            continue
        flag = it.get("flag")
        tid = it.get("item_type_id")
        qty = (it.get("quantity_destroyed") or 0) + (it.get("quantity_dropped") or 0)
        if isinstance(flag, int):
            acc.setdefault(flag, {})
            if isinstance(tid, int):
                acc[flag][tid] = acc[flag].get(tid, 0) + max(qty, 1)
        kids = it.get("items")
        if isinstance(kids, list):
            walk(kids, depth + 1, acc)


dist: dict[int, dict[int, int]] = {}
walk(victim.get("items") or [], 0, dist)
print("  flag  语义             条目种类数   （是否载机 flag）")
print("  " + "-" * 70)
for flag in sorted(dist):
    kinds = len(dist[flag])
    mark = "  <-- 载机 flag" if flag in FIGHTER_FLAGS else ""
    print(f"  {flag:>4}  {FLAG_NAMES.get(flag, '?'):<16} {kinds:>8}{mark}")

missing = [f for f in (159, 160, 161, 162, 163) if f not in dist]
print()
print("  159–163（FighterTube0..4）中**未出现**的 flag：", missing or "无（全出现）")
print("  => 本次击杀里一发载机都没在发射管里，全部在 158 机库。")

# ---------------------------------------------------------------- S3


print()
print("=" * 78)
print("S3  load_killmail 之后：载机落在哪个 slot.type")
print("=" * 78)
fit = dogma.load_killmail(killmail)
print("ship:", (fit.get("ship") or {}).get("type_id"), "=", type_name((fit.get("ship") or {}).get("type_id")))
print("fit items 条数:", len(fit.get("items") or []))
print()
print("  fit items 明细：")
print(f"    {'idx':>3} {'slot.type':<16} {'item_type_id':>12} {'名称':<24} {'qty':>5} {'state':<8}")
print("    " + "-" * 82)
for i, it in enumerate(fit.get("items") or []):
    print(f"    {i:>3} {slot_text(it):<16} {it.get('type_id'):>12} "
          f"{type_name(it.get('type_id')):<24} {it.get('quantity', 1):>5} {str(it.get('state')):<8}")

fighter_fit_idx = [
    i for i, it in enumerate(fit.get("items") or [])
    if it.get("slot", {}).get("type") in ("fighter_bay", "fighter_tube")
]
print()
print("  载机（fighter_bay / fighter_tube）在 fit 中的下标:", fighter_fit_idx)
for i in fighter_fit_idx:
    it = fit["items"][i]
    print(f"    [{i}] {type_name(it.get('type_id'))} x{it.get('quantity', 1)} -> {slot_text(it)}")

print()
print("  load_killmail -> EFT：")
for line in dogma.save_eft(fit).splitlines():
    print("    " + line)

# ---------------------------------------------------------------- S4


print()
print("=" * 78)
print("S4  baseline 计算（killmail 原样：全部载机在 fighter_bay）")
print("=" * 78)


def report(calc: dict, title: str, subject: dict | None = None) -> dict[int, float | None]:
    """打印一次计算的船级/item 级关键属性；`subject` 是这次计算用的 fit（打印槽位用）。"""
    ship = calc.get("ship") or {}
    subject_items = (subject or fit).get("items") or []
    vals: dict[int, float | None] = {}
    print(f"  [{title}]")
    print("    船级：")
    for attr_id, label in SHIP_CHECK:
        value = ship_attr(calc, attr_id)
        vals[attr_id] = value
        print(f"      {label:<24} = {value!r}")
    print("    船体槽位容量（正 ID 常量）：")
    for attr_id, label in (
        (ATTR_FIGHTER_TUBES, "2216 fighterTubes"),
        (ATTR_LIGHT_FIGHTER_SLOTS, "2217 fighterLightSlots"),
        (ATTR_SUPPORT_FIGHTER_SLOTS, "2218 fighterSupportSlots"),
        (ATTR_HEAVY_FIGHTER_SLOTS, "2219 fighterHeavySlots"),
    ):
        print(f"      {label:<24} = {ship_attr(calc, attr_id)!r}")
    print("    item 级（逐件 -55/-56/-57/-58/-12；槽位按本次计算用的 fit 打印）：")
    for i in fighter_fit_idx:
        row = (calc.get("items") or [])[i]
        src = subject_items[i]
        print(f"      [{i}] {type_name(src.get('type_id'))} x{src.get('quantity', 1)}"
              f" slot={slot_text(src)} state={src.get('state')}")
        for attr_id, label in (
            (ATTR_FIGHTER_ABILITY_ATTACK_DPS, "-55 攻击DPS"),
            (ATTR_FIGHTER_ABILITY_MISSILES_DPS, "-56 导弹DPS"),
            (ATTR_FIGHTER_BAY_USED, "-57 机库占用"),
            (ATTR_FIGHTER_DPS, "-58 fighterDps"),
            (ATTR_DAMAGE_DPS, "-12 dps"),
        ):
            print(f"          {label:<18} = {item_attr(calc, i, attr_id)!r}")
    return vals


baseline = dogma.calculate(fit)
base_vals = report(baseline, "baseline = killmail 原样", fit)

print()
print("  体积核对（-57 应当 = Σ 架数 × types.volume）：")
vol_total = 0.0
for i in fighter_fit_idx:
    it = fit["items"][i]
    row = index.get_type(it.get("type_id")) or {}
    vol = float(row.get("volume") or 0.0)
    qty = int(it.get("quantity", 1))
    vol_total += vol * qty
    print(f"    {type_name(it.get('type_id')):<22} volume={vol:>9.1f} x {qty} = {vol * qty:>10.1f}")
print(f"    Σ = {vol_total:.1f}   （引擎 -57 = {base_vals[ATTR_FIGHTER_BAY_USED]!r}）")

print()
print("  与验收员原话的对齐（R2 报告第 1 条）：")
print("    - 船级 `-58 = 0.0`、`-57 = 14000` —— 复现，一致。")
print("    - 验收员写的「item 级 `-57 = 14000`」在引擎输出里**不成立**：item 级根本没有 -57，")
print("      -57（fighterCapacityLoad）只出现在船级；14000 是船级值。")
print("    - item 级 `-55/-56 = 0.0` 只对 **Templar I** 成立；Siren I 连 -55/-56 都没有（支援机）。")
print("    - 两者都不改变结论：载机在机库 → 不产出 -58。")

# ---------------------------------------------------------------- S5


print()
print("=" * 78)
print("S5  改造实验：载机从 fighter_bay 手工移进 fighter_tube")
print("=" * 78)

# 按 type_id 分组，构造「移进发射管」的变体
groups: dict[int, dict] = {}
for i in fighter_fit_idx:
    it = fit["items"][i]
    groups.setdefault(it.get("type_id"), {"qty": 0, "idx": []})
    groups[it.get("type_id")]["qty"] += int(it.get("quantity", 1))
    groups[it.get("type_id")]["idx"].append(i)

print("  fit 中的载机分组：")
for tid, info in groups.items():
    print(f"    {type_name(tid):<22} x{info['qty']}  (fit 下标 {info['idx']})")

fighter_indices = set(fighter_fit_idx)


def build_variant(moved: list[int]) -> dict:
    """`moved` = 要搬进发射管的 type_id 列表（按顺序占 tube 0,1,...）。"""
    variant = copy.deepcopy(fit)
    tube_of: dict[int, int] = {tid: n for n, tid in enumerate(moved)}
    for i in fighter_indices:
        it = variant["items"][i]
        tid = it.get("type_id")
        if tid in tube_of:
            it["slot"] = {"type": "fighter_tube", "index": tube_of[tid]}
            it["state"] = "active"
        else:
            it["slot"] = {"type": "fighter_bay", "index": 0}
            it["state"] = "active"
    return variant


variants: list[tuple[str, list[int]]] = [
    ("A. 全部留在 fighter_bay（原样重写）", []),
    ("B. 只把 Templar 移进 fighter_tube[0]", [23055]),
    ("C. 只把 Siren 移进 fighter_tube[0]", [40346]),
    ("D. 两批都移进发射管（Siren->tube0, Templar->tube1）", [40346, 23055]),
]

results: list[tuple[str, dict[int, float | None]]] = []
for title, moved in variants:
    variant = build_variant(moved)
    calc = dogma.calculate(variant)
    vals = report(calc, title, variant)
    results.append((title, vals))

print()
print("  改造前后对照表（-57/-58/-59/-60/-61/-62）：")
print(f"    {'变体':<44} {'-57':>10} {'-58':>10} {'-59':>6} {'-60':>6} {'-61':>6} {'-62':>6}")
print("    " + "-" * 100)
for title, vals in results:
    cells = " ".join(
        f"{(vals[a] if vals[a] is not None else float('nan')):>10.4f}" if a == ATTR_FIGHTER_BAY_USED
        else f"{(vals[a] if vals[a] is not None else float('nan')):>10.4f}" if a == ATTR_FIGHTER_DPS
        else f"{(vals[a] if vals[a] is not None else float('nan')):>6.1f}"
        for a in (
            ATTR_FIGHTER_BAY_USED, ATTR_FIGHTER_DPS, ATTR_HEAVY_FIGHTER_SLOTS_USED,
            ATTR_LIGHT_FIGHTER_SLOTS_USED, ATTR_SUPPORT_FIGHTER_SLOTS_USED,
            ATTR_FIGHTER_TUBES_USED,
        )
    )
    print(f"    {title:<44} {cells}")

print()
print("  读法（关键对照）：")
print("    - A 与 B 唯一的差别就是 slot.type：A 里载机虽然 state=active，但仍在 fighter_bay，")
print("      -58 依旧是 0 → 成因是「在机库」，**不是**「state=offline」。")
print("    - B/D 把 Templar 挪进发射管后 -58 立刻 = 240.85 → 引擎有完整的战斗机伤害链路，")
print("      不存在「航母战斗机算不出 DPS」的引擎限制。")
print("    - C 只挪 Siren I：-58 仍 0，但 -61（支援槽占用）与 -62（发射管占用）变成 1")
print("      → 引擎把 Siren I 记成**支援中队**（占槽不出 DPS），这是与「在机库」无关的")
print("      第二条独立原因：即使把 Siren 发射出去，它也不会产出 -58。")

# ---------------------------------------------------------------- S6


print()
print("=" * 78)
print("S6  flag -> slot.type 映射表（合成 killmail 逐 flag 探 155..164）")
print("=" * 78)
FIGHTER_TYPE = 23055  # Templar I（本 sample 里 x5 的那批）
print(f"  探针物品：{type_name(FIGHTER_TYPE)}（{FIGHTER_TYPE}）x5，逐 flag 各来一发")
print()
print(f"    {'flag':>4} {'语义':<16} {'load_killmail 结果':<34} {'-57':>10} {'-58':>10} {'-62':>6}")
print("    " + "-" * 88)
for flag in range(155, 165):
    synth = {
        "killmail_id": 1,
        "killmail_time": "2026-01-01T00:00:00Z",
        "solar_system_id": 30002556,
        "victim": {
            "character_id": 1,
            "corporation_id": 1,
            "ship_type_id": 23911,
            "items": [
                {
                    "flag": flag,
                    "item_type_id": FIGHTER_TYPE,
                    "quantity_dropped": 5,
                    "singleton": 0,
                }
            ],
        },
        "attackers": [],
    }
    sfit = dogma.load_killmail(synth)
    kept = list(sfit.get("items") or [])
    if kept:
        where = ", ".join(f"{slot_text(it)} x{it.get('quantity', 1)}" for it in kept)
    else:
        where = "<被丢弃：fit 无此槽位>"
    scalc = dogma.calculate(sfit)
    print(f"    {flag:>4} {FLAG_NAMES.get(flag, '?'):<16} {where:<34} "
          f"{str(ship_attr(scalc, ATTR_FIGHTER_BAY_USED)):>10} "
          f"{str(ship_attr(scalc, ATTR_FIGHTER_DPS)):>10} "
          f"{str(ship_attr(scalc, ATTR_FIGHTER_TUBES_USED)):>6}")

print()
print("  注 1：155（FleetHangar）/156/157 的载机在 fit 里**没有对应槽位 → 整条丢弃**，")
print("        所以 fixture 里 flag 155 的那批东西（是舰队机库里的杂物，不是载机）不进 fit。")
print("  注 2：164 = StructureServiceSlot0，引擎把它映射到 slot.type=service，条目没被丢；")
print("        一条载机躺在 service 槽里仍被算出 -57=1000、-58=33.86（数量被压成 1）。")
print("        f26 里没有任何 164，**与本次判定无关**，只作登记、未定性。")

# ---------------------------------------------------------------- S7 结论


print()
print("=" * 78)
print("S7  结论")
print("=" * 78)
print("  判据（两条独立证据）：")
print("    (1) flag 语义：现代 SDE invFlags 158 = FighterBay（机库）、159–163 = FighterTube0..4。")
print("        f26 的 8 架载机全在 158，159–163 一条都没有（S2 全列了）。")
print("    (2) 引擎行为：S6 逐 flag 探测，158 -> slot.type=fighter_bay、159–163 -> fighter_tube[0..4]，")
print("        与 SDE 语义一一对应，引擎映射没错。")
print("  结论：`-58 = 0` 是**正确行为**（载机停在机库、未进发射管），不是缺陷；")
print("        不该改成 `null` + flag —— 输出 0 才是对的，`null` 反而会丢掉「确定没有载机在飞」这个事实。")
print("  补充：`-62 fighterTubesUsed = 0`，与「没有载机在管里」自洽。")
print("  反面证据（排除引擎限制）：把 Templar 挪进 fighter_tube 后 -58 = 240.85、-62 = 1，")
print("        说明战斗机伤害链路本身是通的；若真要说限制，只有「支援机 Siren 占槽不出 DPS」这一条，")
print("        而那是 EVE 规则，不是引擎缺陷。")
