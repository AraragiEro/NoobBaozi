r"""T-24 探针：复核 f14（Damnation 指挥舰）整装装甲 HP 34,142 的量级。

只读诊断：读 fixture、只读索引库（sqlite，`mode=ro`）、只读 SDE zip、调引擎。
不写任何文件、不改产品代码。运行（仓库根目录）：

    $env:UV_CACHE_DIR = "$PWD\noobbaozi\spike\uvcache"
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" \
        python noobbaozi/spike/armor_hp_probe.py

回答的唯一问题：34,142 是对的还是错的，并给出可复算的分解。

证据链设计：**先自己读 SDE 常量**（不信任引擎输入的中间值）→ 再取引擎结果 →
最后用 SDE 常量 + sources 链把引擎值**闭式重算**一遍，逐位比对。
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

ARMOR_HP = 265
ARMOR_EM = 267
HULL_HP = 9
EHP_MIXED = -43
SHIELD_HP = 263

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "noobbaozi/acceptance/fixtures/f14-command-damnation.json"
EXPECTED = ROOT / "noobbaozi/acceptance/fixtures/f14-command-damnation.expected.json"
DB = ROOT / "noobbaozi/data/noobbaozi-3607705.sqlite3"
SDE_ZIP = ROOT / "noobbaozi/data/eve-online-static-data-3607705-jsonl.zip"

SHIP = 22474            # Damnation
PLATE_1600 = 31902      # Federation Navy 1600mm Steel Plates
PLATE_800 = 31918       # Federation Navy 800mm Steel Plates
TRIMARK2 = 31059        # Medium Trimark Armor Pump II
DC2 = 2048              # Damage Control II
BURST = 43552           # Armor Command Burst II
# 对照实验用（验证引擎在「该罚的属性」上确实会罚）
CONTROL_RIG = 31005     # Medium EM Armor Reinforcer II（抗性，EVE 规矩：要罚）

SKILL_CMD_SHIPS = 23950  # Command Ships（eliteBonusCommandShips1 = 10%/级 → 装甲 HP）
SKILL_HULL_UP = 3394     # Hull Upgrades（armorHpBonus 5%/级）

STACK_2ND = 0.8691199808003974  # EVE 第 2 个受罚模块的惩罚系数（e^-((1/2.67)^2)）


def hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------------------
# 0) 环境
# ---------------------------------------------------------------------------
dogma.load_sde_from_file(sde_path())
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

expected = json.loads(EXPECTED.read_text(encoding="utf-8"))


def type_name(type_id: int) -> str:
    row = con.execute("select name from types where type_id=?", (type_id,)).fetchone()
    return row[0] if row else f"<unknown {type_id}>"


def attr_name(attr_id: int) -> str:
    row = con.execute("select name from attributes where attribute_id=?", (attr_id,)).fetchone()
    return row[0] if row else f"<{attr_id}>"


def sde_dogma(type_ids: set[int]) -> dict[int, dict[int, float]]:
    """直接从 SDE zip 读 typeDogma（不经过引擎），作为「真值常量」来源。"""
    out: dict[int, dict[int, float]] = {}
    with zipfile.ZipFile(SDE_ZIP) as zf, zf.open("typeDogma.jsonl") as fh:
        for line in fh:
            obj = json.loads(line)
            key = obj.get("_key")
            if key in type_ids:
                out[key] = {a["attributeID"]: a["value"] for a in obj["dogmaAttributes"]}
    return out


def value_of(node: dict, attr_id: int):
    if attr_id not in node:
        return None
    v = node[attr_id]
    return v["value"] if isinstance(v, dict) else v


# ---------------------------------------------------------------------------
# 1) 技能档：all4 + 补最低（与 acceptance 的 derive_skill_tier 同算法）
# ---------------------------------------------------------------------------
kill = json.loads(FIXTURE.read_text(encoding="utf-8"))
kill.pop("zkb", None)
fit = dogma.load_killmail(kill)
items = list(fit["items"])


def derive_skill_tier() -> dict[int, int]:
    """全部已发布技能 4 级 + 用 validate 的 skill 违规补到 max(4, required)。"""
    skills = {
        int(t): 4
        for (t,) in con.execute(
            "select t.type_id from types t join groups g on t.group_id = g.group_id "
            "where g.category_id = 16 and t.published = 1"
        )
    }
    for _ in range(8):
        calc = dogma.calculate(
            {"ship": dict(fit["ship"]), "items": items, "character": {"skills": skills}},
            {"validate": True},
        )
        rules = [v["rule"] for v in (calc.get("violations") or []) if v["rule"]["type"] == "skill"]
        if not rules:
            return skills
        changed = False
        for rule in rules:
            want = max(4, int(rule["required"]))
            if skills.get(int(rule["type_id"]), 0) < want:
                skills[int(rule["type_id"])] = want
                changed = True
        if not changed:
            return skills
    raise RuntimeError("技能档未收敛")


skills = derive_skill_tier()
hist: dict[int, int] = {}
for lvl in skills.values():
    hist[lvl] = hist.get(lvl, 0) + 1

hr("[0] 环境与技能档")
print(f"引擎 SDE build : {sde_path()}")
print(f"fixture        : {FIXTURE.relative_to(ROOT)}")
print(f"技能档         : {len(skills)} 个，分布 {dict(sorted(hist.items()))}（期望 {expected['skill_tier_histogram']}）")
print(f"技能档一致     : {hist == {int(k): v for k, v in expected['skill_tier_histogram'].items()}}")
print(f"expected.json  : armor_hp={expected['snapshot']['armor_hp']}  armor_ehp={expected['snapshot']['armor_ehp']}"
      f"  ehp_mixed={expected['snapshot']['ehp_mixed']}")

# ---------------------------------------------------------------------------
# 2) 载入配装（fit / items 已在技能档推导前载入）
# ---------------------------------------------------------------------------
hr("[1] 配装还原（load_killmail）")
print(f"船型：{SHIP} {type_name(SHIP)}")
for idx, item in enumerate(items):
    slot = item.get("slot") or {}
    where = slot.get("type", "?")
    if "index" in slot:
        where += f"[{slot['index']}]"
    charge = item.get("charge") or {}
    charge_txt = f" + {type_name(charge['type_id'])}" if charge.get("type_id") else ""
    print(f"  [{idx:2d}] {item['type_id']:6d} {type_name(item['type_id']):46s} {where:12s} {item['state']:8s}{charge_txt}")

plates = [i for i, it in enumerate(items) if it["type_id"] in (PLATE_1600, PLATE_800)]
rigs = [i for i, it in enumerate(items) if it["type_id"] == TRIMARK2]
dcs = [i for i, it in enumerate(items) if it["type_id"] == DC2]
bursts = [i for i, it in enumerate(items) if it["type_id"] == BURST]
print(f"\n识别：装甲板 idx={plates}  装甲泵 rig idx={rigs}  DC idx={dcs}  指挥脉冲波 idx={bursts}")


def snapshot(selected: list[int] | None = None, skill_set: dict[int, int] | None = None,
             sources: bool = False, validate: bool = False) -> dict:
    chosen = items if selected is None else [items[i] for i in selected]
    calc = dogma.calculate(
        {"ship": dict(fit["ship"]), "items": chosen, "character": {"skills": skill_set or skills}},
        {"sources": True} if sources else ({"validate": True} if validate else None),
    )
    return calc


def armor_hp(calc: dict) -> float:
    return float(value_of(calc["ship"]["attributes"], ARMOR_HP))


bare_calc = snapshot([])
bare = armor_hp(bare_calc)
full_calc = snapshot(sources=True)
full = armor_hp(full_calc)

hr("[2] 裸船 vs 整装（armorHP = 265）")
print(f"裸船（同技能档、无模块）armorHP(265) = {bare:.6f}")
print(f"整装（killmail 全部 32 项）     armorHP(265) = {full:.6f}")
print(f"总增量 = {full - bare:.6f}（检验报告写的是 +26,246）")
print(f"与 expected.json 逐位相同：{abs(full - float(expected['snapshot']['armor_hp'])) < 1e-6}")

# ---------------------------------------------------------------------------
# 3) 逐件累加
# ---------------------------------------------------------------------------
hr("[3] 逐件累加：每件的实际增量")
plate_only_1600 = [plates[0]]
plate_only_800 = [plates[1]]
plate_both = list(plates)
one_rig = [rigs[0]]
two_rigs = list(rigs)
dc_only = list(dcs)

cases = [
    ("裸船", []),
    ("+ 1×1600 板", plate_only_1600),
    ("+ 1×800 板", plate_only_800),
    ("+ 2 板", plate_both),
    ("+ 1× Trimark II", one_rig),
    ("+ 2× Trimark II", two_rigs),
    ("+ DC II", dc_only),
    ("+ 2 板 + 2 rig", plate_both + two_rigs),
    ("+ 2 板 + 2 rig + DC", plate_both + two_rigs + dc_only),
    ("整装（全部）", None),
]
measured: dict[str, float] = {}
for label, sel in cases:
    measured[label] = armor_hp(snapshot(sel))
for label in measured:
    print(f"  {label:22s} armorHP = {measured[label]:12.6f}")

print("\n逐件净增量（相对裸船）：")
inc_1600 = measured["+ 1×1600 板"] - bare
inc_800 = measured["+ 1×800 板"] - bare
inc_1rig = measured["+ 1× Trimark II"] - bare
inc_2rig = measured["+ 2× Trimark II"] - bare
inc_dc = measured["+ DC II"] - bare
print(f"  1×1600 板     Δ = +{inc_1600:10.6f}   （比值 {measured['+ 1×1600 板'] / bare:.6f}）")
print(f"  1×800 板      Δ = +{inc_800:10.6f}   （比值 {measured['+ 1×800 板'] / bare:.6f}）")
print(f"  2 板合计      Δ = +{measured['+ 2 板'] - bare:10.6f}  （= 单件之和？"
      f"{abs((measured['+ 2 板'] - bare) - (inc_1600 + inc_800)) < 1e-9}）")
print(f"  1× Trimark II Δ = +{inc_1rig:10.6f}   （比值 {measured['+ 1× Trimark II'] / bare:.6f}）")
print(f"  2× Trimark II Δ = +{inc_2rig:10.6f}   （比值 {measured['+ 2× Trimark II'] / bare:.6f}）")
print(f"  DC II         Δ = +{inc_dc:10.6f}   （DC 只动抗性，不动 HP）")

# ---------------------------------------------------------------------------
# 4) 正交组合：可加性 / 可乘性
# ---------------------------------------------------------------------------
hr("[4] 正交组合：增量是可加还是可乘")
combo_2p_2r = measured["+ 2 板 + 2 rig"]
combo_all3 = measured["+ 2 板 + 2 rig + DC"]
print(f"2 板 + 2 rig               = {combo_2p_2r:.6f}")
print(f"2 板 + 2 rig + DC          = {combo_all3:.6f}  （DC 不参与 → 与上式相同："
      f"{abs(combo_all3 - combo_2p_2r) < 1e-9}）")
print(f"可加性检验 (裸船 + 板增量 + rig 增量) = {bare + (inc_1600 + inc_800) + inc_2rig:.6f}"
      f"  ← 与实测差 {combo_2p_2r - (bare + (inc_1600 + inc_800) + inc_2rig):.6f}（可加不成立，说明是乘性）")
print(f"可乘性检验：(裸船 + 板增量) × (1 + 44%) = {(bare + inc_1600 + inc_800) * 1.44:.6f}"
      f"  ← 与实测差 {combo_2p_2r - (bare + inc_1600 + inc_800) * 1.44:.9f}")
print(f"rig 两级乘子：{1.2 * 1.2:.6f} = 1.2 × 1.2（第 2 块按满值生效，见 §8 堆叠惩罚）")

# ---------------------------------------------------------------------------
# 5) SDE 常量（自己读，不信任引擎中间值）
# ---------------------------------------------------------------------------
hr("[5] SDE 真值常量（直接读 SDE zip 的 typeDogma）")
consts = sde_dogma({SHIP, PLATE_1600, PLATE_800, TRIMARK2, DC2})
INTERESTING = {9: "hp", 263: "shieldCapacity", 265: "armorHP", 999: "eliteBonusCommandShips2",
               1000: "eliteBonusCommandShips1", 1159: "armorHPBonusAdd", 335: "armorHpBonus",
               148: "armorHPMultiplier"}
for tid in (SHIP, PLATE_1600, PLATE_800, TRIMARK2):
    picked = {INTERESTING[a]: consts[tid][a] for a in consts[tid] if a in INTERESTING}
    print(f"  {tid} {type_name(tid)}: {json.dumps(picked, ensure_ascii=False, sort_keys=True)}")
sde_base = consts[SHIP][265]
plate_1600_add = consts[PLATE_1600][1159]     # armorHPBonusAdd
plate_800_add = consts[PLATE_800][1159]
rig_percent = consts[TRIMARK2][335]            # armorHpBonus（%）
ship_bonus_per_level = consts[SHIP][1000]      # eliteBonusCommandShips1（%/级）
print(f"\n  armorHP(265) 基础值（SDE 船体）        = {sde_base}")
print(f"  armorHPBonusAdd(1159)：1600 板 {plate_1600_add} / 800 板 {plate_800_add} 合计 {plate_1600_add + plate_800_add}")
print(f"  armorHpBonus(335)：Trimark II = {rig_percent}%（每块）")
print(f"  eliteBonusCommandShips1(1000) = {ship_bonus_per_level}%/级 → × Command Ships({SKILL_CMD_SHIPS}) 等级 {skills[SKILL_CMD_SHIPS]}"
      f" = +{ship_bonus_per_level * skills[SKILL_CMD_SHIPS]}%")

# ---------------------------------------------------------------------------
# 6) sources 链
# ---------------------------------------------------------------------------
hr(f"[6] ship armorHP({ARMOR_HP}) 的完整 modifier 链（sources: true，整装）")
node = full_calc["ship"]["attributes"][ARMOR_HP]
print(f"base = {node['base']}   value = {node['value']}")
print(f"{'来源':28s} {'effect':>7s} {'srcAttr':>20s} {'算子':12s} {'值':>12s} {'qty':>4s} {'惩罚':>20s} applied")
for src in node.get("sources") or []:
    ref = src["from"]
    kind = ref.get("type")
    if kind == "item":
        label = f"item[{ref['index']}] {type_name(items[ref['index']]['type_id'])}"
    elif kind == "skill":
        label = f"skill({ref['type_id']}) {type_name(ref['type_id'])}"
    elif kind == "buff":
        label = f"buff({ref['id']})"
    elif kind == "ship":
        label = "ship（船体自身加成）"
    elif kind == "mode":
        label = "mode"
    else:
        label = f"character"
    sid = src.get("source_attribute_id")
    src_attr = f"{sid} {attr_name(sid)}" if sid else "None"
    print(f"{label:28s} {str(src.get('effect_id')):>7s} {src_attr:>20s} {src['operator']:12s}"
          f" {src['value']:12.6f} {src['quantity']:4d} {str(src['penalty']):>20s} {src['applied']}")

print("\n各乘子的语义：")
print("  mod_add      : armorHPBonusAdd(1159)，装甲板加的是**平板值**（5000 / 2500）")
print("  post_percent : 船体 eliteBonusCommandShips1(1000) 10%/级；Hull Upgrades(3394) 5%/级；")
print("                 Trimark II 的 armorHpBonus(335) 20%；指挥脉冲波自增益 15.68%")
print("  post_mul     : 多谱式电压薄膜的 armorHPMultiplier(148) = 1.0（空操作，但确实在链上）")
print("\n指挥脉冲波自增益 15.68% 的来源（部分确认）：SDE 里装甲强化弹药(42834) 的加成值是 8%，")
print("  引擎最终给 15.68%；按 Command Ships 等级做敏感性：0/4/5 级 → 14.00/15.68/16.10，")
print("  即船体每级 +3%、4 级共 +12%；8% → 14% 之间的 ×1.75 来自模块 43552 的脉冲波强度属性，")
print("  **未逐项分解（未确认）**，但不影响 armorHP 的复算闭包（链上用的就是 15.68 这个终值）。")

print("\n同样的链，去掉其它模块后（只留 2 板 + 2 rig）：")
subset_items = [items[i] for i in plate_both + two_rigs]
subset = snapshot(plate_both + two_rigs, sources=True)
sub_node = subset["ship"]["attributes"][ARMOR_HP]
print(f"  base={sub_node['base']}  value={sub_node['value']}")
for src in sub_node.get("sources") or []:
    ref = src["from"]
    if ref.get("type") == "item":
        label = f"item[{ref['index']}] {type_name(subset_items[ref['index']]['type_id'])}"
    elif ref.get("type") == "skill":
        label = f"skill({ref['type_id']}) {type_name(ref['type_id'])}"
    else:
        label = f"{ref.get('type')}（船体自身加成）"
    print(f"    {label:52s} {src['operator']:12s} {src['value']:12.6f} penalty={src['penalty']}")

# ---------------------------------------------------------------------------
# 7) 基数判定
# ---------------------------------------------------------------------------
hr("[7] 基数判定：Trimark 的 +% 作用在「裸船」还是「含装甲板」上")
# 从链里取的乘子（全部 post_percent / post_mul，不带惩罚）
percent_ship = 1 + (ship_bonus_per_level * skills[SKILL_CMD_SHIPS]) / 100.0   # 船体 elite bonus
percent_skill = 1 + 20 / 100.0                                                # Hull Upgrades 4 级
percent_rigs = (1 + rig_percent / 100.0) ** len(rigs)                          # 2 块，无惩罚
buff_value = None
for src in node.get("sources") or []:
    if src["from"].get("type") == "buff":
        buff_value = src["value"]
percent_buff = 1 + buff_value / 100.0

m_all = percent_ship * percent_skill * percent_rigs * percent_buff
m_extra = percent_rigs * percent_buff
plates_flat = plate_1600_add + plate_800_add

print(f"船体 elite +{ship_bonus_per_level * skills[SKILL_CMD_SHIPS]}%  ×{percent_ship}")
print(f"Hull Upgrades 4 级 +20%        ×{percent_skill}")
print(f"2× Trimark II (无惩罚)        ×{percent_rigs}")
print(f"指挥脉冲波自增益 +{buff_value}%     ×{percent_buff}")
print(f"→ 全部百分链 m_all = {m_all:.12f}；仅 rig+burst 的 m_extra = {m_extra:.12f}")
print(f"→ 装甲板平板值合计 = {plates_flat}")

algo_a = bare * m_extra + plates_flat            # 百分只作用于裸船（板最后加）
algo_b = (bare + plates_flat) * m_extra          # 题面写法：板按标称值加在 7896 上再乘
algo_c = (sde_base + plates_flat) * m_all        # 板先一起走完整百分链（EVE 的 mod_add→post_percent 顺序）
print(f"\n  算法 A「百分只作用裸船，板最后加」  {bare} × {m_extra:.9f} + {plates_flat} = {algo_a:.6f}")
print(f"  算法 B「(7896 + 标称板值) × rig+burst」 ({bare} + {plates_flat}) × {m_extra:.9f} = {algo_b:.6f}")
print(f"  算法 C「(SDE 基础 + 平板值) × 全部百分链」 ({sde_base} + {plates_flat}) × {m_all:.9f} = {algo_c:.6f}")
print(f"\n引擎实测 = {full:.6f}")
for label, val in (("A", algo_a), ("B", algo_b), ("C", algo_c)):
    print(f"  与算法 {label} 相对偏差 = {abs(full - val) / val:.3e}   吻合={abs(full - val) < 1e-6}")
print("\n判定：引擎走的是算法 C —— mod_add(装甲板) 先合入基数，post_percent 再乘")
print("      （这正是 EVE dogma 的算子次序），所以装甲板**也**被船体 +40% 与 Hull Upgrades +20% 放大：")
print(f"      5000 × {percent_ship * percent_skill:.2f} = {plate_1600_add * percent_ship * percent_skill:.0f}"
      f"（这就是单板「看起来」加了 {inc_1600:.0f} 而不是 5000 的原因）")
print(f"      2500 × {percent_ship * percent_skill:.2f} = {plate_800_add * percent_ship * percent_skill:.0f}")

# ---------------------------------------------------------------------------
# 8) 堆叠惩罚
# ---------------------------------------------------------------------------
hr("[8] 堆叠惩罚：2× Trimark II 是否被罚")
rig_sources = [s for s in (node.get("sources") or []) if s["from"].get("type") == "item"]
print("整装链里 Trimark 的惩罚字段：")
for src in node.get("sources") or []:
    ref = src["from"]
    if ref.get("type") == "item" and items[ref["index"]]["type_id"] == TRIMARK2:
        print(f"  item[{ref['index']}] Trimark II  value={src['value']}  penalty={src['penalty']}  applied={src['applied']}")
print(f"\n→ 两块都是满值 20%，合计 ×{(1 + rig_percent / 100) ** 2:.6f} = ×1.44（无惩罚）")
print(f"→ 若按第 2 块 {STACK_2ND:.6f} 惩罚，应为 ×1.2 × (1 + 0.2×{STACK_2ND:.6f}) = "
      f"×{1.2 * (1 + 0.2 * STACK_2ND):.6f}，整装会变成 {(sde_base + plates_flat) * percent_ship * percent_skill * (1.2 * (1 + 0.2 * STACK_2ND)) * percent_buff:.3f}")

print("\n对照实验（证明引擎在「该罚」的属性上确实会罚）：")
ctrl = dogma.calculate(
    {"ship": {"type_id": SHIP},
     "items": [{"type_id": CONTROL_RIG, "slot": {"type": "rig", "index": 0}, "state": "active"},
               {"type_id": CONTROL_RIG, "slot": {"type": "rig", "index": 1}, "state": "active"}],
     "character": {"skills": skills}},
    {"sources": True},
)
ctrl_src = [s for s in ctrl["ship"]["attributes"][ARMOR_EM]["sources"] if s["from"].get("type") == "item"]
print(f"  2× {type_name(CONTROL_RIG)}（装甲抗性，EVE 规定**要罚**）：")
for src in ctrl_src:
    print(f"    value={src['value']:8.3f}  penalty={src['penalty']}  applied={src['applied']}")
print(f"  267 值 = {value_of(ctrl['ship']['attributes'], ARMOR_EM):.6f}")
print("\nEVE 规则（EVE University Wiki「Stacking penalties」的表）：")
print("  「Shield / armor / hull hit points → **no**（扁平与百分比加成都免罚）」")
print("  「Shield / armor / hull resistances → yes」")
print("  来源：https://wiki.eveuniversity.org/Stacking_penalties")
print("→ 装甲 HP 不受堆叠惩罚是**对的**，引擎在这里没有漏罚；反倒是抗性那侧照罚（上面实测 0.8691）。")

# ---------------------------------------------------------------------------
# 9) 闭式复算
# ---------------------------------------------------------------------------
hr("[9] 闭式复算：只用 SDE 常量 + 技能等级 + 链上乘子重算 34,142")
recalc = (sde_base + plates_flat) * percent_ship * percent_skill * percent_rigs * percent_buff
print(f"  ({sde_base} + {plate_1600_add} + {plate_800_add})")
print(f"  × {percent_ship}   (船体 eliteBonusCommandShips1 {ship_bonus_per_level}%/级 × {skills[SKILL_CMD_SHIPS]} 级)")
print(f"  × {percent_skill}   (Hull Upgrades {skills[SKILL_HULL_UP]} 级，5%/级)")
print(f"  × {percent_rigs:.9f}   (2× Trimark II {rig_percent}%，无惩罚)")
print(f"  × {percent_buff}   (指挥脉冲波自增益 {buff_value}%)")
print(f"  = {recalc:.9f}")
print(f"引擎值 = {full:.9f}   差 = {abs(recalc - full):.3e}   闭包={'PASS' if abs(recalc - full) < 1e-9 else 'FAIL'}")

# 增量归因（每一项对最终值的绝对贡献）
contrib = {
    "裸船（SDE 4700 × 船体+技能）": sde_base * percent_ship * percent_skill,
    "2 块装甲板（5000+2500 经百分链放大）": plates_flat * percent_ship * percent_skill,
    "2× Trimark II（+44%）": (sde_base + plates_flat) * percent_ship * percent_skill * (percent_rigs - 1),
    "指挥脉冲波自增益（+15.68%）": (sde_base + plates_flat) * percent_ship * percent_skill * percent_rigs * (percent_buff - 1),
}
print("\n对最终值的绝对贡献：")
for label, val in contrib.items():
    print(f"  {label:46s} {val:12.3f}")
print(f"  {'合计':46s} {sum(contrib.values()):12.3f}")
print(f"  裸船实测 {bare:.3f} vs 归因 {contrib['裸船（SDE 4700 × 船体+技能）']:.3f}"
      f"  （差 {abs(bare - contrib['裸船（SDE 4700 × 船体+技能）']):.3e}）")

# ---------------------------------------------------------------------------
# 10) 额外检查
# ---------------------------------------------------------------------------
hr("[10] 额外检查")
viol = dogma.calculate(
    {"ship": dict(fit["ship"]), "items": items, "character": {"skills": skills}}, {"validate": True}
).get("violations") or []
print(f"violations（all4+补最低 档下）：{len(viol)} 条  {json.dumps(viol, ensure_ascii=False)[:300]}")
print(f"expected.json violations：{len(expected['violations'])} 条 → hard_violations / tier_violations 均为空："
      f"{len(expected['violations']) == 0}")

abyssal = []
for idx, item in enumerate(items):
    row = con.execute(
        "select g.name from types t left join groups g on t.group_id = g.group_id where t.type_id=?",
        (item["type_id"],),
    ).fetchone()
    group_name = row[0] if row else ""
    name = type_name(item["type_id"])
    if "abyssal" in name.lower() or "abyssal" in (group_name or "").lower() or "mutant" in (group_name or "").lower():
        abyssal.append((idx, item["type_id"], name, group_name))
print(f"突变件（Abyssal/Mutant）：{len(abyssal)} 个 {abyssal}")
print("→ killmail 本身不携带 mutation，但本配装没有 Abyssal 件，因此 X5 与 X3/D7 的「突变丢失」问题无关联。")

# EHP 关系核对
resonances = {a: float(value_of(full_calc["ship"]["attributes"], a)) for a in (267, 268, 269, 270)}
armor_ehp = full / (0.25 * sum(resonances.values()))
print(f"\n装甲四系共振：{ {a: round(v, 9) for a, v in resonances.items()} }")
print(f"装甲 EHP 复算 = {full} / (0.25×{sum(resonances.values()):.9f}) = {armor_ehp:.6f}"
      f"   expected={expected['snapshot']['armor_ehp']}  吻合={abs(armor_ehp - expected['snapshot']['armor_ehp']) < 1e-6}")
print(f"引擎 ehp_mixed(-43) = {value_of(full_calc['ship']['attributes'], EHP_MIXED)}"
      f"   expected={expected['snapshot']['ehp_mixed']}")
shield = float(value_of(full_calc["ship"]["attributes"], SHIELD_HP))
hull = float(value_of(full_calc["ship"]["attributes"], HULL_HP))
print(f"分层：shield {shield:.3f} / armor {full:.3f} / hull {hull:.3f}"
      f"  → EHP 234,743 是 armor 34142 的直接放大（armor_ehp/armor_hp = {armor_ehp / full:.4f}）")

# 技能档敏感性（说明这是「假设」而不是「硬事实」）
print("\n技能档敏感性（34,142 依赖的技能假设）：")
for skill_id, label in ((SKILL_CMD_SHIPS, "Command Ships"), (SKILL_HULL_UP, "Hull Upgrades")):
    for lvl in (4, 5):
        alt = dict(skills)
        alt[skill_id] = lvl
        val = armor_hp(snapshot(None, alt))
        print(f"  {label} = {lvl} → armorHP = {val:.3f}  (相对 4 级 {val - full:+.3f})")

print("\n模块状态敏感性：")
burst_off = [{**it, "state": "offline"} if it["type_id"] == BURST else it for it in items]
burst_off_calc = dogma.calculate(
    {"ship": dict(fit["ship"]), "items": burst_off, "character": {"skills": skills}}, {"sources": True}
)
print(f"  两块指挥脉冲波改 offline → armorHP = {armor_hp(burst_off_calc):.3f}"
      "   ← 与 online 相同，说明引擎的 buff 传播不受模块 state 影响（观察项，未定性）")

# ---------------------------------------------------------------------------
# 11) 结论
# ---------------------------------------------------------------------------
hr("[11] 结论")
ok = abs(full - recalc) < 1e-9 and abs(full - float(expected["snapshot"]["armor_hp"])) < 1e-6
print(f"裸船 armorHP(265)                = {bare:.3f}")
print(f"整装 armorHP(265)                = {full:.3f}")
print(f"闭式复算                          = {recalc:.3f}   → {'逐位吻合' if ok else '不吻合'}")
print(f"判定：34,142 {'正确' if ok else '错误'}")
print("分解：(4700 基础 + 5000 + 2500 板) × 1.4（Command Ships） × 1.2（Hull Upgrades）"
      " × 1.44（2× Trimark II，免罚） × 1.1568（指挥脉冲波自增益） = 34142.072832")
print("不存在「装甲板 + Trimark 重复计入」：两条路径分别是 mod_add 与 post_percent，链上各出现一次。")
print("34,142 与「2 万量级」的直觉差距来自三项被忽略的乘子：1.4×1.2 的百分链放大（板 7500→12600）"
      " + Trimark 的 +44% + 脉冲波 +15.68%；只算前两项到「板 + 无 rig」时恰好是 20,496（正是那个直觉数字）。")
