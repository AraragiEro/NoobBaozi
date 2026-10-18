"""evefit 六视角验收 runner（QA 自持，不依赖投影层实现细节）。

设计原则
--------
1. **独立复核**：I1–I5、I7 由本 runner 直接从引擎算，**不采信实现自报**；
2. **实现无关**：六视角结构通过三种途径取得，优先顺序如下
     a. `--views-json`：外部（主人 / 开发代理 / PM）提供的六视角 JSON；
     b. `--entry pkg.mod:func`：指定投影层入口，runner 负责调用；
     c. 自动探测若干常见入口名（`evefit.project.render:render_overview` 等）。
   三者都没有 → 判定 `blocked`（投影层未就绪），不得误判成"通过"。
3. **缺陷分类计数**：字段缺失 / 口径错误 / 单位错误 / provenance 错误 / 不变式失败。
4. **不联网、不写产品代码、不使用 pytest `tmp_path`**（沙箱 `WinError 5`）。

用法
----
    $env:UV_CACHE_DIR = (Join-Path $PWD "evefit/spike/uvcache")
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
      python evefit/acceptance/run_acceptance.py --label 基线

    # 只验某个外部产出的六视角 JSON
    python evefit/acceptance/run_acceptance.py --views-json out/views.json --label P2

    # 只对某个样本（主人后续给的 killmail / EFT）
    python evefit/acceptance/run_acceptance.py --eft path/to/x.eft

    # 打印字段勾选表（用于重生成验收计划里的表格）
    python evefit/acceptance/run_acceptance.py --emit-field-table

退出码：0 = 无缺陷（pass）；1 = 有缺陷（fail）；2 = 阻塞（投影层未就绪 / 环境不可用）。
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import math
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # evefit/
WORKSPACE = ROOT.parent
sys.path.insert(0, str(WORKSPACE))

FIELD_SPEC_PATH = HERE / "field_spec.json"
FIXTURE_DIR = HERE / "fixtures"
DEFAULT_OUT = HERE / "out"

PROVENANCE_VALUES = ("measured", "assumed", "unknown")

#: 保守度排序（越大越保守）。T-30：实例级**只允许往更保守的方向升格**，
#: `measured → assumed` 合法（配装状态敏感），`assumed → measured` 是降级，禁止。
PROVENANCE_CONSERVATISM = {"measured": 0, "assumed": 1, "unknown": 2}
#: 可信度等级（与 provenance 正交，见 Lead 裁决 2026-10-18）
CONFIDENCE_VALUES = ("A", "B", "C", "D", "E")

#: T-12 之后的技能档契约：必须传 index 才会拿到这个档名（512 个技能）。
EXPECTED_SKILL_TIER_NAME = "all4+min_required"
#: 技能数下限（Lead 裁决：保留 100）。新档实测 512，旧档 19，能挡住退化。
EXPECTED_MIN_SKILLS = 100
FAILED_AT_VALUES = ("ship_name", "module_name", "quantity", "slot", "format")
REASON_VALUES = ("ambiguous", "unknown", "malformed", "duplicate_slot")

#: 渲染值的比较容差（0.1%）：实现通常四舍五入到 6 位，不能拿 1e-6 卡。
RENDER_REL_TOL = 1e-3
#: 不变式容差（开发规范 §6.1）
INVARIANT_ABS_TOL = 1e-6

#: 自动探测的投影层入口（按顺序尝试）
ENTRY_CANDIDATES = (
    ("evefit.project.render", "render_overview"),
    ("evefit.project.render", "render"),
    ("evefit.project", "understand"),
    ("evefit.project.pipeline", "understand_fit"),
    ("evefit.project.views", "extract_views"),
)

#: I4 往返无损的 8 项指标
I4_METRICS = (
    (-43, "ehp_mixed"),
    (-12, "dps"),
    (-11, "alpha"),
    (-1, "align_time"),
    (-72, "cap_stable_pct"),
    (-7, "cap_depletes_in"),
    (-9, "cpu_free"),
    (-10, "powergrid_free"),
)

#: I5 抗性叠加用：三层在**船体上的**真实共振 ID（EM/EXP/KIN/THM 顺序）
RESONANCE_LAYERS: dict[str, dict[str, int]] = {
    "shield": {"em": 271, "explosive": 272, "kinetic": 273, "thermal": 274},
    "armor": {"em": 267, "explosive": 268, "kinetic": 269, "thermal": 270},
    "hull": {"em": 113, "explosive": 111, "kinetic": 109, "thermal": 110},
}
#: 模块侧的共振修饰属性（护甲/护盾确定；结构层用 974–977，顺序 EM/EXP/KIN/THM）
MODULE_RESONANCE: dict[str, dict[str, int]] = {
    "shield": {"em": 271, "explosive": 272, "kinetic": 273, "thermal": 274},
    "armor": {"em": 267, "explosive": 268, "kinetic": 269, "thermal": 270},
    "hull": {"em": 974, "explosive": 975, "kinetic": 976, "thermal": 977},
}

#: 语义口径直查表：views 字段路径 → (属性 ID, 说明)。用于"口径错误"的客观判定。
#: 路径用 `.` 分隔；`<view>` 省略（同一路径在各视角内独立比对）。
SEMANTIC_MAP: dict[str, int] = {
    "dps.base": -12,
    "dps.with_reload": -13,
    "dph": -11,
    "layers.shield_hp": 263,
    "layers.armor_hp": 265,
    "layers.hull_hp": 9,
    "layer_ehp.shield": -30,
    "layer_ehp.armor": -28,
    "layer_ehp.hull": -29,
    "ehp_mixed": -43,
    "speed_mps": 37,
    "align_time_s": -1,
    "signature_radius_m": 552,
    "mass_kg": 4,
    "mass": 4,
    "agility": 70,
    "warp_speed_au_s": 600,
    "cpu.output": 48,
    "cpu.load": 49,
    "powergrid.output": 11,
    "powergrid.load": 15,
    "calibration.output": 1132,
    "calibration.load": 1152,
    "hardpoints_free.turret": 102,
    "hardpoints_free.launcher": 101,
    "drone.bandwidth.total": 1271,
    "drone.capacity.total": 283,
    "cargo.total": 38,
    "capacitor.capacity": 482,
    "capacitor.recharge_s": 55,     # 毫秒来源 → 需 ÷1000
    "capacitor.stable_pct": -72,
    "capacitor.depletes_in_s": -7,
    "capacitor.peak_load": -4,
    "capacitor.peak_recharge": -2,
    "capacitor.delta": -5,
}

#: 已知需要换算的属性：毫秒来源（÷1000）与 ×1000 来源。
#: 注意 `-2`（peakRecharge，GJ/s）**不是**毫秒；`-3`（cycleTime）才是。
MS_ATTRS = frozenset({55, 79, 479, 51, 1795, 1985, -3})
KILO_ATTRS = frozenset({160})   # trackingSpeed / optimalSigRadius 等


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Defect:
    """一条缺陷。类别沿用任务约定的五分类。"""

    category: str            # 字段缺失 / 口径错误 / 单位错误 / provenance 错误 / 不变式失败
    severity: str            # blocker / high / medium / low
    case: str
    where: str
    expected: str
    actual: str
    note: str = ""

    def key(self) -> tuple[str, str, str, str]:
        return (self.category, self.case, self.where, self.expected)


@dataclass
class CaseFacts:
    """一个样本的引擎级事实（独立于投影层）。"""

    name: str
    eft_path: Path | None
    eft_text: str
    source_format: str
    fit: dict
    skills: dict[int, int]
    skill_tier_name: str
    assumptions: dict
    meta: dict
    ship: dict[int, float | None]
    items: list[dict[int, float | None]]
    item_states: list[str | None]
    violations: list[dict]
    modes: list[dict] = field(default_factory=list)

    def raw_index(self) -> dict[float, int]:
        """{原始值: 属性 ID}，用于「是否输出了未换算原值」的单位检查。"""
        index: dict[float, int] = {}
        for source in (self.ship, *self.items):
            for attr_id, value in source.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    index.setdefault(float(value), attr_id)
        return index

    def raw_by_attr(self) -> dict[int, float]:
        """{属性 ID: 原始值}（ship 级优先，缺失则取 item 级）。"""
        out: dict[int, float] = {}
        for source in (*self.items, self.ship):
            for attr_id, value in source.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out[attr_id] = float(value)
        return out


@dataclass
class UnitProbe:
    """单位错误的判定证据。"""

    attr_id: int
    raw: float
    physical: float


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def rel_close(a: float, b: float, rel_tol: float) -> bool:
    if a == b:
        return True
    return math.isclose(a, b, rel_tol=rel_tol, abs_tol=0.0)


def attr_map(node: dict | None) -> dict[int, float | None]:
    """引擎 attributes 节点 → {attr_id: 裸值}（兼容 {"value": v} / 裸数）。"""
    out: dict[int, float | None] = {}
    for key, value in (node or {}).items():
        try:
            attr_id = int(key)
        except (TypeError, ValueError):
            continue
        out[attr_id] = value["value"] if isinstance(value, dict) else value
    return out


def load_field_spec() -> dict:
    return json.loads(FIELD_SPEC_PATH.read_text(encoding="utf-8"))


def all_fields(spec: dict) -> list[tuple[str, dict, dict]]:
    """展开成 [(view_name, field_dict, view_meta)]。"""
    out: list[tuple[str, dict, dict]] = []
    for view_name, view in spec["views"].items():
        for spec_field in view["fields"]:
            out.append((view_name, spec_field, view))
    return out


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# 样本载入 + 引擎事实
# ---------------------------------------------------------------------------


class EngineFacts:
    """封装引擎调用，逐样本产出 `CaseFacts`。

    **必须持有一个 `Index` 并传给 `default_assumptions`**：T-12 之后
    `default_assumptions(fit, engine)` 不传 index 会退化到旧档 `min_required_only`（19 个技能），
    于是所有数值静默跑在旧档上——这正是 Lead 点名的"最危险"失效模式。
    """

    def __init__(self) -> None:
        from evefit.config import paths as paths_mod
        from evefit.engine import adapter
        from evefit.sde.index import Index

        self.adapter = adapter
        self.engine = adapter.Engine()
        self.index = Index.open(paths_mod.db_path())

    def close(self) -> None:
        try:
            self.index.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响验收结论
            pass

    def build(self, name: str, eft_text: str, eft_path: Path | None) -> CaseFacts:
        from evefit.engine.assumptions import default_assumptions
        from evefit.engine.normalize import normalize_fit

        normalized = normalize_fit(eft_text)
        if normalized["fit"] is None:
            raise ValueError(
                f"{name}: 归一化失败 → {json.dumps(normalized['errors'], ensure_ascii=False)}"
            )
        fit = normalized["fit"]
        # index 必传：否则静默退回旧技能档（T-12 后的硬约定）
        assumptions = default_assumptions(fit, self.engine, self.index)
        skills = assumptions["skill_tier"]
        calc = self.engine.calculate({**fit, "character": {"skills": skills}})
        ship = attr_map(calc["ship"]["attributes"])
        items = [attr_map(item.get("attributes")) for item in calc["items"]]
        states = [item.get("state") for item in calc["items"]]
        violations = self.engine.validate({**fit, "character": {"skills": skills}})
        return CaseFacts(
            name=name,
            eft_path=eft_path,
            eft_text=eft_text,
            source_format=normalized["source_format"],
            fit=fit,
            skills=skills,
            skill_tier_name=assumptions["skill_tier_name"],
            assumptions=assumptions,
            meta=self.engine.meta(),
            ship=ship,
            items=items,
            item_states=states,
            violations=violations,
        )

    def calculate_with(self, facts: CaseFacts, *, items: list[dict] | None = None,
                        mode: int | None = None, states: dict[int, str] | None = None) -> dict[int, float | None]:
        fit = dict(facts.fit)
        if items is not None:
            fit["items"] = items
        ship = dict(facts.fit.get("ship") or {})
        if mode is not None:
            ship["mode"] = mode
        fit["ship"] = ship
        if states is not None:
            new_items = []
            for idx, item in enumerate(fit.get("items") or []):
                new_items.append({**item, "state": states[idx]} if idx in states else item)
            fit["items"] = new_items
        fit["character"] = {"skills": facts.skills}
        return attr_map(self.engine.calculate(fit)["ship"]["attributes"])

    def modes_of(self, facts: CaseFacts) -> list[dict]:
        """该船可用的模式（无模式船返回空列表）。复用持有的索引。"""
        ship_type_id = (facts.fit.get("ship") or {}).get("type_id")
        return [dict(row) for row in self.index.modes_for_ship(ship_type_id)]


# ---------------------------------------------------------------------------
# 不变式 I1–I5
# ---------------------------------------------------------------------------


NON_WEAPON_SLOTS = ("drone_bay", "fighter_bay", "fighter_tube", "cargo",
                    "implant", "booster")
"""不算「武器/抗性模块」的槽位：无人机/战斗机有自己的 volley 与抗性，
把它们算进 alpha 或抗性乘性链会立刻造出假缺陷（T-19 新样本实测踩到）。"""


def slot_types(facts: CaseFacts) -> list[str]:
    """输入 fit 的逐项 slot.type（与 calc["items"] 同序；D8 记录的隐式顺序契约）。"""
    return [((item.get("slot") or {}).get("type") or "") for item in (facts.fit.get("items") or [])]


def is_real_module(slots: list[str], index: int) -> bool:
    if index >= len(slots):
        return True          # 长度对不上时保守当作真实模块（宁可报也不漏）
    return slots[index] not in NON_WEAPON_SLOTS


def check_invariants(facts: CaseFacts, eng: EngineFacts) -> list[Defect]:
    defects: list[Defect] = []
    ship = facts.ship

    # ---- I1: EHP = Σ 三层 EHP ----
    total = ship.get(-43)
    parts = [ship.get(-30), ship.get(-28), ship.get(-29)]
    if total is None or any(part is None for part in parts):
        defects.append(Defect("不变式失败", "blocker", facts.name, "I1",
                              "EHP 与三层 EHP 均有值",
                              f"EHP={total!r}, 三层={parts!r}", "引擎缺少必要属性"))
    else:
        ssum = math.fsum(float(p) for p in parts)  # type: ignore[arg-type]
        if not math.isclose(float(total), ssum, abs_tol=INVARIANT_ABS_TOL, rel_tol=0.0):
            defects.append(Defect("不变式失败", "blocker", facts.name, "I1",
                                  f"{ssum:.9f}", f"{float(total):.9f}",
                                  "EHP ≠ shieldEhp+armorEhp+hullEhp"))

    # ---- I2: alpha = Σ 逐装备 volley ----
    alpha = ship.get(-11)
    slots = slot_types(facts)
    volley_sum = math.fsum(
        float(item.get(-21) or 0.0)
        for index, item in enumerate(facts.items)
        if is_real_module(slots, index)
    )
    if alpha is None:
        if not math.isclose(volley_sum, 0.0, abs_tol=INVARIANT_ABS_TOL):
            defects.append(Defect("不变式失败", "blocker", facts.name, "I2",
                                  f"有武器 volley（Σ={volley_sum:.6f}）就应有 alpha",
                                  "alpha=None", ""))
        # 无武器（volley 合计 0）时 alpha=None 合法，不报
    elif not math.isclose(float(alpha), volley_sum, abs_tol=INVARIANT_ABS_TOL, rel_tol=0.0):
        defects.append(Defect("不变式失败", "blocker", facts.name, "I2",
                              f"{volley_sum:.9f}", f"{float(alpha):.9f}",
                              "alpha ≠ Σ 逐装备 volley"))

    # ---- I3: 混合 EHP 按层复算，且 ≠ 算术平均 / ≠ 调和平均 ----
    weights = {"em": 0.25, "explosive": 0.25, "kinetic": 0.25, "thermal": 0.25}
    layer_hp = {"shield": ship.get(263), "armor": ship.get(265), "hull": ship.get(9)}
    mixed_calc = 0.0
    ok = True
    for layer, hp in layer_hp.items():
        if hp is None:
            ok = False
            break
        denom = 0.0
        for dmg, attr_id in RESONANCE_LAYERS[layer].items():
            resonance = ship.get(attr_id)
            if resonance is None:
                ok = False
                break
            # 规格 §2：ehp_mixed = Σ_layer HP_layer / Σ_i (w_i × r_i,layer)
            # r 是「共振率」（伤害乘数，0.85 = 15% 抗性），所以是乘不是除。
            denom += weights[dmg] * float(resonance)
        if not ok:
            break
        mixed_calc += float(hp) / denom
    if ok and total is not None:
        if not math.isclose(mixed_calc, float(total), abs_tol=INVARIANT_ABS_TOL, rel_tol=0.0):
            defects.append(Defect("不变式失败", "blocker", facts.name, "I3",
                                  f"{mixed_calc:.9f}", f"{float(total):.9f}",
                                  "混合 EHP ≠ Σ_layer HP/Σ(w×r) 复算值"))
        pure = {}
        for dmg in weights:
            value = 0.0
            for layer, hp in layer_hp.items():
                value += float(hp) / float(ship[RESONANCE_LAYERS[layer][dmg]])  # type: ignore[arg-type]
            pure[dmg] = value
        arithmetic = math.fsum(pure.values()) / 4.0
        harmonic = 4.0 / math.fsum(1.0 / value for value in pure.values())
        if abs(float(total) - arithmetic) / arithmetic <= 0.01:
            # 这是**样本性质**，不是实现缺陷：层抗性接近均匀时（f31 结构坦抗性全 0.402），
            # 混合 EHP 本来就会贴近算术平均（差 0.54%）。真正的不变式是上面那条
            # 「Σ HP/Σ(w×r) 复算」；这里只记覆盖度观察，避免把好实现当红线。
            defects.append(Defect("不变式失败", "low", facts.name, "I3",
                                  "与算术平均差 >1%（用于区分「混合」口径）",
                                  f"混合={float(total):.3f}，差={abs(float(total)-arithmetic)/arithmetic:.2%}",
                                  "该样本层抗性接近均匀：混合与算术平均天然接近，不足以区分口径（非缺陷）"))
        if math.isclose(float(total), harmonic, rel_tol=1e-6):
            defects.append(Defect("不变式失败", "blocker", facts.name, "I3",
                                  "≠ 四系调和平均（仅单层时两者才相等）",
                                  f"混合={float(total):.6f} == 调和={harmonic:.6f}",
                                  "红线 N6 的反向错误：把混合 EHP 记成调和平均"))

    # ---- I4: EFT 往返无损 ----
    try:
        eft_back = eng.engine.save_eft(facts.fit)
        from evefit.engine.normalize import normalize_fit

        again = normalize_fit(eft_back)
        if again["fit"] is None:
            defects.append(Defect("不变式失败", "high", facts.name, "I4",
                                  "save_eft 结果可被 load_eft 读回",
                                  f"读回失败: {again['errors']}"))
        else:
            before = eng.engine.calculate({**facts.fit, "character": {"skills": facts.skills}})
            after = eng.engine.calculate({**again["fit"], "character": {"skills": facts.skills}})
            ship_before = attr_map(before["ship"]["attributes"])
            ship_after = attr_map(after["ship"]["attributes"])
            for attr_id, label in I4_METRICS:
                v1, v2 = ship_before.get(attr_id), ship_after.get(attr_id)
                if v1 is None and v2 is None:
                    continue
                if v1 is None or v2 is None or not math.isclose(
                    float(v1), float(v2), abs_tol=INVARIANT_ABS_TOL, rel_tol=0.0
                ):
                    defects.append(Defect("不变式失败", "high", facts.name, f"I4:{label}",
                                          f"{v1!r}", f"{v2!r}", "往返后指标漂移"))
    except Exception as exc:  # noqa: BLE001 - 验收要抓异常，不能中断整轮
        defects.append(Defect("不变式失败", "high", facts.name, "I4",
                              "往返无损可执行", f"{type(exc).__name__}: {exc}",
                              traceback.format_exc(limit=2)))

    # ---- I5: 抗性叠加 = 共振率相乘 ----
    bare = eng.calculate_with(facts, items=[])
    slots = slot_types(facts)
    fit_items = list(facts.fit.get("items") or [])
    real_indices = [i for i in range(len(facts.items)) if is_real_module(slots, i)]
    effect_driven: list[str] = []
    stacked: list[str] = []

    def without(index: int) -> list[dict]:
        return [item for position, item in enumerate(fit_items) if position != index]

    for layer, attrs in RESONANCE_LAYERS.items():
        for dmg, attr_id in attrs.items():
            base_r = bare.get(attr_id)
            full_r = ship.get(attr_id)
            if base_r is None or full_r is None or float(base_r) == 0.0:
                continue
            ratio = float(full_r) / float(base_r)

            # 哪些**真实模块**通过基础属性声明了这一层/这一系的乘数？
            declaring: list[tuple[int, float]] = []
            for index in real_indices:
                value = facts.items[index].get(MODULE_RESONANCE[layer][dmg])
                if isinstance(value, (int, float)) and float(value) > 0.0:
                    declaring.append((index, float(value)))

            if not declaring:
                # 没有真实模块声明乘数，但比值变了 → 来自 dogma effect（hardener 等），
                # 基础属性上看不到乘数，I5 的乘性对照不适用（不是实现错误）。
                if not rel_close(ratio, 1.0, 1e-6):
                    effect_driven.append(f"{layer}.{dmg}")
                continue

            if len(declaring) > 1:
                # 多个模块同时声明同一层抗性 → 引擎会施加**堆叠惩罚**，
                # `ship/bare` 不再等于任一模块的声明乘数（实测 f7/f14：
                # 移除 0.85 的 DC II 得到 0.8696，正是惩罚的表现）。
                # 简单乘积只在**单模块**时成立（PROGRESS §3 第 4 条的实测就是单 DC II）。
                stacked.append(f"{layer}.{dmg}")
                continue

            # **逐模块移除实验**：移除模块 i 后 ship/bare 必须等于 i 声明的乘数。
            # 这样「部分模块声明、部分走效应」不会污染乘积。
            for index, declared in declaring:
                reduced = eng.calculate_with(facts, items=without(index))
                after = reduced.get(attr_id)
                if after is None or float(after) == 0.0:
                    continue
                single = float(full_r) / float(after)
                candidates = [declared]
                if layer == "hull":
                    # 974–977 的 EM/EXP/KIN/THM 顺序不确定：任一映射成立即通过
                    module = facts.items[index]
                    candidates = [float(module.get(MODULE_RESONANCE["hull"][d]) or declared)
                                  for d in MODULE_RESONANCE["hull"]]
                if not any(rel_close(single, candidate, 1e-6) for candidate in candidates):
                    defects.append(Defect(
                        "不变式失败", "high", facts.name, f"I5:{layer}.{dmg}",
                        f"移除该模块后 ship/bare = 模块乘数 {declared:.6f}",
                        f"实测 {single:.6f}",
                        "抗性叠加不是按共振率相乘（逐模块移除实验不成立）"))

    if effect_driven:
        defects.append(Defect(
            "不变式失败", "low", facts.name, "I5",
            "可验证乘性叠加（模块基础属性可见乘数）",
            f"{len(effect_driven)} 项来自效应型模块（如 hardener），基础属性无乘数：{effect_driven[:4]}",
            "I5 对这些项不适用；要验需走 effect 级 sources（P3 explain 才有）"))
    if stacked:
        defects.append(Defect(
            "不变式失败", "low", facts.name, "I5",
            "单模块抗性 → 乘性对照",
            f"{len(set(stacked))} 组（{sorted(set(stacked))[:4]}）有多个模块同时声明抗性",
            "多模块抗性叠加受**堆叠惩罚**，简单乘积不成立；I5 只在单模块时可用"))
    return defects


# ---------------------------------------------------------------------------
# 不变式 I7（错误结构）
# ---------------------------------------------------------------------------

I7_SAMPLES: tuple[tuple[str, str], ...] = (
    ("完全无法识别", "这不是一套配装，只是一句话。"),
    ("未知模块名", "[Hecate, bad module]\n不存在的模块名字\n"),
    ("未知船名", "[不存在的船, bad ship]\nDamage Control II\n"),
    ("坏 JSON", '{"ship": {"type_id": 35683}, "items": ['),
)


def check_error_structure() -> list[Defect]:
    """I7：解析失败必须返回可修复结构。"""
    from evefit.engine.normalize import normalize_fit

    defects: list[Defect] = []
    for label, text in I7_SAMPLES:
        result = normalize_fit(text)
        if result.get("fit") is not None:
            defects.append(Defect("不变式失败", "high", label, "I7",
                                  "坏输入必须解析失败", "却解析成功了"))
            continue
        errors = result.get("errors") or []
        if not errors:
            defects.append(Defect("不变式失败", "high", label, "I7",
                                  "errors 非空", "errors 为空"))
            continue
        for position, error in enumerate(errors):
            where = f"I7:{label}#{position}"
            if not isinstance(error, dict):
                defects.append(Defect("不变式失败", "high", label, where, "错误是 dict", repr(error)))
                continue
            missing = [key for key in
                       ("line", "raw", "failed_at", "candidates", "reason") if key not in error]
            if missing:
                defects.append(Defect("不变式失败", "high", label, where,
                                      "含 line/raw/failed_at/candidates/reason",
                                      f"缺 {missing}", "契约 NormalizeError 结构不完整"))
            if error.get("failed_at") not in FAILED_AT_VALUES:
                defects.append(Defect("不变式失败", "medium", label, where,
                                      f"failed_at ∈ {FAILED_AT_VALUES}",
                                      repr(error.get("failed_at"))))
            if error.get("reason") not in REASON_VALUES:
                defects.append(Defect("不变式失败", "medium", label, where,
                                      f"reason ∈ {REASON_VALUES}", repr(error.get("reason"))))
            if not str(error.get("raw") or "").strip():
                defects.append(Defect("不变式失败", "medium", label, where,
                                      "raw 保留原文", repr(error.get("raw"))))
            if not isinstance(error.get("candidates"), list):
                defects.append(Defect("不变式失败", "medium", label, where,
                                      "candidates 是 list", repr(error.get("candidates"))))
    return defects


def check_empty_slot_markers() -> list[Defect]:
    """观察项：EFT 空槽标记的接受度与错误行号。

    实测（SDE 3607705 / 引擎 13.6.0）：
      `[Empty High slot]` / `[Empty Med slot]` / `[Empty Low slot]` / `[Empty Rig slot]`
      / `[Empty Subsystem slot]` 通过；`[Empty Medium Slot]` / `[Empty Cargo Slot]` 被引擎
      以 `invalid empty slot` 拒绝。错误结构里 `line=None`，用户无法定位到行。
    """
    from evefit.engine.normalize import normalize_fit

    defects: list[Defect] = []
    cases = (
        ("[Empty High slot]", True),
        ("[Empty Med slot]", True),
        ("[Empty Low slot]", True),
        ("[Empty Rig slot]", True),
        ("[Empty Subsystem slot]", True),
        ("[Empty Medium Slot]", False),
        ("[Empty Cargo Slot]", False),
    )
    for marker, should_pass in cases:
        result = normalize_fit(f"[Hecate, empty slot probe]\n{marker}\nDamage Control II\n")
        passed = result.get("fit") is not None
        if passed != should_pass:
            defects.append(Defect(
                "口径错误", "medium", "空槽标记", marker,
                "引擎接受这套 EFT" if should_pass else "引擎明确拒绝（且应有行号）",
                "解析成功" if passed else f"解析失败: {(result.get('errors') or [{}])[0].get('raw')!r}",
                "EFT 空槽标记的接受范围需与引擎真实行为对齐（预扫描放行 ≠ 引擎接受）"))
            continue
        if not should_pass:
            error = (result.get("errors") or [{}])[0]
            if error.get("line") is None:
                defects.append(Defect(
                    "不变式失败", "low", "空槽标记", marker,
                    "错误含行号（规范 §4.3 要求可定位）",
                    f"line=None, raw={error.get('raw')!r}",
                    "引擎侧错误未回填行号；预扫描本可定位，但放行后丢失"))
    return defects


# ---------------------------------------------------------------------------
# 六视角结构检查：字段齐全 / I6 provenance / 口径 / 单位
# ---------------------------------------------------------------------------


SUBSYSTEM_SLOTS_ATTR = 1367
"""`maxSubSystems`：T3C 的子系统槽位数。为 0 时 `slots.subsystem` 是**条件字段**，
缺失不算字段缺失（T-23 裁决）。"""


def condition_holds(name: str, facts: CaseFacts) -> bool:
    if name == "subsystem_slots":
        return float(facts.ship.get(SUBSYSTEM_SLOTS_ATTR) or 0.0) > 0.0
    return True


def lookup_field(overview: dict, spec_field: dict) -> tuple[bool, object]:
    for name in (spec_field["name"], *spec_field.get("aliases", [])):
        if name in overview:
            return True, overview[name]
    return False, None


def resolve_view_entry(views: dict, view_name: str) -> tuple[dict | None, dict | None]:
    """取某视角的 `(overview, provenance 表)`。

    支持两种对外形态（Lead 裁决 2026-10-18）：

    - **形态 A（新，优先）**：`{view: {"overview": {...裸值...}, "provenance": {"dps.base": "assumed"}}}`
      ——renders 层的实际形态，provenance 是**平行的字段级元信息表**；
    - **形态 B（旧，兼容）**：`{view: {"overview": {... TypedValue ...}}}` 或直接给 overview，
      provenance 裹在值里。
    """
    views = views.get("views", views)
    node = views.get(view_name)
    if not isinstance(node, dict):
        return None, None
    if "overview" in node:
        table = node.get("provenance")
        return node["overview"], table if isinstance(table, dict) else None
    return node, None


def resolve_view(views: dict, view_name: str) -> dict | None:
    return resolve_view_entry(views, view_name)[0]


ARRAY_MARKER = "[]"
"""数组元素路径标记。与渲染层 `_leaf_paths` 的约定一致：`weapons[].name`。"""


@dataclass
class Leaf:
    """一个 overview 叶子（= 一条 provenance 的管辖单元）。"""

    path: str
    numbers: list[float]
    provenance: str | None
    source_attr: int | None
    form: str                    # "bare"（形态 A：查平行表）/ "typed"（形态 B：值自带）
    confidence: str | None = None
    value_none: bool = False     # T-27：值为 None 时 provenance 强制 unknown


def collect_leaves(overview: object, table: dict | None = None) -> list[Leaf]:
    """按渲染层的键空间收集叶子路径，并解析每个叶子的 provenance。

    键空间规则（必须与 `project/render._leaf_paths` 逐字一致，否则会造出假缺陷）：
    - 对象 → 逐键递归 `prefix.key`（**整数键也展开成 str**，故 `count_by_level.4`）；
    - 非空且元素**全是对象**的列表 → 逐元素递归 `prefix[].key`；
    - 其余（标量 / 字符串 / `None` / 布尔 / 空列表 / 空对象 / **标量列表**）→ `prefix` 本身。

    provenance 解析顺序：
    1. 形态 A：平行表按整条路径命中 → 用它；
    2. 形态 B：TypedValue 自带的 `provenance`（或容器级的 `provenance`）继承；
    3. 都没有 → `None`（I6 违反：这个叶子没有 provenance）。
    """
    out: list[Leaf] = []
    _collect(overview, "", out, table)
    return out


def _collect(node: object, prefix: str, out: list[Leaf],
             table: dict | None, inherited: str | None = None,
             inherited_attr: int | None = None,
             inherited_conf: str | None = None) -> None:
    path = prefix or "$"

    # ---- 形态 B：TypedValue ----
    is_typed = (
        isinstance(node, dict)
        and "value" in node
        and ("provenance" in node or "unit" in node or "source_attr" in node)
    )
    if is_typed:
        raw_value = node.get("value")
        numbers: list[float] = []
        if is_number(raw_value):
            numbers = [float(raw_value)]
        local = node.get("provenance") if isinstance(node.get("provenance"), str) else inherited
        attr = node.get("source_attr") if isinstance(node.get("source_attr"), int) else inherited_attr
        conf = node.get("confidence") if isinstance(node.get("confidence"), str) else inherited_conf
        out.append(Leaf(path=path, numbers=numbers, provenance=local,
                        source_attr=attr, form="typed", confidence=conf,
                        value_none=raw_value is None))
        return

    # ---- 对象：逐键递归 ----
    if isinstance(node, dict) and node:
        local = node.get("provenance") if isinstance(node.get("provenance"), str) else inherited
        attr = node.get("source_attr") if isinstance(node.get("source_attr"), int) else inherited_attr
        local_conf = node.get("confidence") if isinstance(node.get("confidence"), str) else inherited_conf
        for key, child in node.items():
            if key in ("provenance", "unit", "source_attr", "confidence"):
                continue
            child_path = f"{prefix}.{key}" if prefix else str(key)
            _collect(child, child_path, out, table, local, attr, local_conf)
        return

    # ---- 非空对象列表：`prefix[].key` ----
    if isinstance(node, list) and node and all(isinstance(item, dict) for item in node):
        for item in node:
            _collect(item, f"{prefix}{ARRAY_MARKER}", out, table, inherited,
                     inherited_attr, inherited_conf)
        return

    # ---- 叶子（含标量列表：整张表算一个叶子）----
    numbers = []
    if is_number(node):
        numbers = [float(node)]
    elif isinstance(node, list):
        numbers = [float(item) for item in node if is_number(item)]
    provenance = inherited
    if table is not None and path in table and isinstance(table[path], str):
        provenance = table[path]
    out.append(Leaf(path=path, numbers=numbers, provenance=provenance,
                    source_attr=inherited_attr, form="bare",
                    confidence=inherited_conf, value_none=node is None))


def leaf_paths(overview: object, table: dict | None = None) -> set[str]:
    return {leaf.path for leaf in collect_leaves(overview, table)}


def walk_numeric(overview: object, table: dict | None = None
                 ) -> list[tuple[str, float, str | None, int | None]]:
    """兼容旧调用：把叶子压平成 `(path, value, provenance, source_attr)`（只留数值叶子）。"""
    flat: list[tuple[str, float, str | None, int | None]] = []
    for leaf in collect_leaves(overview, table):
        for value in leaf.numbers:
            flat.append((leaf.path, value, leaf.provenance, leaf.source_attr))
    return flat


class ProvenanceOracle:
    """provenance 权威表的读取器（Lead 裁决：权威在投影层，不在字段规格）。

    优先 `evefit/project/provenance.py` 的 `tag(attr_id)`；`tag` 不存在时退化为
    读模块里的映射表（`TAG_BY_ATTR` / `TAGS` / `PROVENANCE_BY_ATTR`）。
    都拿不到 → `available=False`，runner 退化为**只做结构校验**（三态合法性 + 全字段覆盖），
    绝不硬编码期望值——这样 T-08 用实测重推该表后 runner 自动跟随。
    """

    MODULE = "evefit.project.provenance"

    def __init__(self) -> None:
        self.available = False
        self.source = ""
        self._tag = None
        self._mapping: dict[int, str] = {}
        try:
            module = importlib.import_module(self.MODULE)
        except Exception as exc:  # noqa: BLE001 - 未就绪是常态
            self.source = f"{self.MODULE} 不可用（{type(exc).__name__}）"
            return
        tag = getattr(module, "tag", None)
        if callable(tag):
            self._tag = tag
            self.available = True
            self.source = f"{self.MODULE}.tag(attr_id)"
            return
        for name in ("TAG_BY_ATTR", "TAGS", "PROVENANCE_BY_ATTR", "TAG"):
            mapping = getattr(module, name, None)
            if isinstance(mapping, dict):
                self._mapping = {int(k): v for k, v in mapping.items() if str(k).lstrip("-").isdigit()}
                self.available = True
                self.source = f"{self.MODULE}.{name}"
                return
        self.source = f"{self.MODULE} 存在但既无 tag() 也无映射表"

    def tag(self, attr_id: int) -> str | None:
        """返回该属性的权威 provenance；取不到返回 None。"""
        if self._tag is not None:
            try:
                value = self._tag(attr_id)
            except Exception:  # noqa: BLE001
                return None
            return value if isinstance(value, str) else None
        return self._mapping.get(attr_id)



def check_render_meta(case: str, payload: dict, facts: CaseFacts) -> list[Defect]:
    """校验渲染层 meta / budget 与本次验收口径一致。

    1. `meta.assumptions.skill_tier_name` 必须和 runner 推导的档一致——
       否则"实现算在旧档、runner 拿新档比对"，会产生一片假缺陷；
    2. `budget.degraded=True` 会让列表被截断（`cap_by_module` / `weapons` 等），
       覆盖度下降。这是**设计内行为**（预算护栏），记 low 观察不阻塞，
       但必须在报告里可见，否则"验收通过"的范围被悄悄缩小。
    """
    defects: list[Defect] = []
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    assumptions = meta.get("assumptions") if isinstance(meta.get("assumptions"), dict) else {}
    declared_tier = assumptions.get("skill_tier_name")
    if isinstance(declared_tier, str) and declared_tier != facts.skill_tier_name:
        defects.append(Defect(
            "口径错误", "blocker", case, "meta.assumptions.skill_tier_name",
            f"{facts.skill_tier_name}（本次验收口径）", declared_tier,
            "渲染层与验收器的技能档不一致：所有数值不可直接比对"))
    budget = payload.get("budget") if isinstance(payload.get("budget"), dict) else {}
    if budget.get("degraded") is True:
        defects.append(Defect(
            "字段缺失", "low", case, "budget.degraded",
            "overview 装进预算（degraded=False），覆盖完整列表",
            f"degraded=True，chars={budget.get('chars')}",
            "预算护栏已触发：明细列表被截到前 N 项，本轮验收对列表的覆盖度下降"))
    return defects


class ConfidenceOracle:
    """可信度权威表的读取器（`evefit/project/confidence.py`）。

    **稀疏约定（Lead 裁决 2026-10-18）**：
    - `<view>.confidence` 只输出**非 `"A"`** 的叶子路径；缺省 = `"A"`；空表输出 `{}`；
    - 登记表 `CONFIDENCE[view]` 同样只登记非 A 项，支持容器路径继承（`weapons` 覆盖元素）。

    因此「反查防漏」的语义是：对每个 overview 叶子算 `confidence_of(view, path)`，
    得到非 A 的，**输出表里必须出现**；得到 A 的，输出表里不该出现（稀疏约定）。
    """

    MODULE = "evefit.project.confidence"

    def __init__(self) -> None:
        self.available = False
        self.source = ""
        self.default = "A"
        self.levels: frozenset[str] = frozenset({"A", "B", "C", "D", "E"})
        self._func = None
        self._table: dict[str, dict[str, str]] = {}
        try:
            module = importlib.import_module(self.MODULE)
        except Exception as exc:  # noqa: BLE001 - 未就绪是常态
            self.source = f"{self.MODULE} 不可用（{type(exc).__name__}）"
            return
        default = getattr(module, "DEFAULT_CONFIDENCE", "A")
        levels = getattr(module, "CONFIDENCE_LEVELS", None)
        func = getattr(module, "confidence_of", None)
        table = getattr(module, "CONFIDENCE", None)
        if isinstance(default, str):
            self.default = default
        if isinstance(levels, frozenset) and levels:
            self.levels = levels
        if callable(func):
            self._func = func
            self.available = True
            self.source = f"{self.MODULE}.confidence_of(view, path)"
        elif isinstance(table, dict):
            self._table = {str(k): dict(v) for k, v in table.items() if isinstance(v, dict)}
            self.available = True
            self.source = f"{self.MODULE}.CONFIDENCE"

    def level(self, view: str, path: str) -> str | None:
        """该叶子的权威可信度；取不到返回 None（调用方跳过反查）。"""
        if self._func is not None:
            try:
                value = self._func(view, path)
            except Exception:  # noqa: BLE001 - 登记表写错时不要打断整轮
                return None
            return value if isinstance(value, str) else None
        return self._table.get(view, {}).get(path, self.default)

    def expected_non_default(self, view: str, paths: list[str]) -> dict[str, str]:
        """反查：叶子路径里**应当出现在稀疏表**的项 → 期望档位。"""
        result: dict[str, str] = {}
        for path in paths:
            value = self.level(view, path)
            if isinstance(value, str) and value.upper() != self.default:
                result[path] = value.upper()
        return result


def check_confidence(case: str, view_name: str, leaves: list[Leaf],
                     table: dict | None, declared_inline: dict[str, str],
                     leaf_paths: set[str], oracle: "ConfidenceOracle",
                     default: str, levels: frozenset[str]) -> list[Defect]:
    """可信度校验（稀疏表语义）。

    1. 先按 overview 叶子路径补齐默认 `A`，再查表；
    2. `set(confidence) ⊆ set(provenance)`（= overview 叶子路径集合）；
    3. 每个**非 A 登记项**都必须出现（用 `confidence_of` 反查，防漏输出）；
    4. 显式写 `A` 的条目违反稀疏约定 → low 观察（T-18 落地后应消失）。
    """
    defects: list[Defect] = []
    declared: dict[str, str] = {}
    if isinstance(table, dict):
        for path, level in table.items():
            if isinstance(path, str) and isinstance(level, str):
                declared[path] = level.upper()
    for path, level in declared_inline.items():
        declared.setdefault(path, level.upper())

    # (1) 合法值
    for path, level in declared.items():
        if level not in levels:
            defects.append(Defect("字段缺失", "medium", case,
                                  f"{view_name}.confidence.{path}",
                                  f"∈ {sorted(levels)}", level))

    # (2) 键集合必须是 overview 叶子路径的子集
    unknown_paths = sorted(set(declared) - leaf_paths)
    if unknown_paths:
        defects.append(Defect("字段缺失", "medium", case, f"{view_name}.confidence",
                              "confidence 的键 ⊆ provenance 的键（= overview 叶子）",
                              f"多出 {len(unknown_paths)} 个不在 overview 里的键：{unknown_paths[:5]}",
                              "键空间必须与 provenance 一致，否则下游按同一套路径取不到"))

    # (3) 反查：非 A 登记项必须出现且值正确
    if oracle.available:
        expected = oracle.expected_non_default(view_name, sorted(leaf_paths))
        for path, want in expected.items():
            got = declared.get(path)
            if got is None:
                defects.append(Defect("字段缺失", "high", case,
                                      f"{view_name}.confidence.{path}",
                                      f'{want}（{oracle.source} 登记为非 {default}，必须出现在稀疏表里）',
                                      f"未输出（缺省会被当成 {default}）",
                                      "稀疏表漏输出非 A 项：agent 会把未建模/不可用当成可用"))
            elif got != want:
                defects.append(Defect("字段缺失", "high", case,
                                      f"{view_name}.confidence.{path}",
                                      f"{want}（{oracle.source}）", got,
                                      "输出档位与权威登记表不一致"))

    # (4) 稀疏约定观察：显式写了 A 的条目
    explicit_default = sorted(path for path, level in declared.items() if level == default)
    if explicit_default:
        defects.append(Defect(
            "字段缺失", "low", case, f"{view_name}.confidence",
            "稀疏表：只输出非 A 的叶子（缺省 = A，空表 = {}）",
            f"显式写了 {len(explicit_default)} 条 A（如 {explicit_default[:4]}）",
            "非 A 才需要登记；全量表会白白撑爆 token 预算（T-18 稀疏化后应消失）"))
    return defects


MODE_ENTRY_KEYS = ("mode_id", "mode_name", "ehp_mixed", "dps_base", "align_time_s")
"""`modes[]` 每条必须具备的键（T-17 的顶层 `modes` 块）。"""

MODE_DIRECTION: dict[str, tuple[str, str]] = {
    "Defense": ("ehp_mixed", "gt"),
    "Propulsion": ("align_time_s", "lt"),
    "Sharpshooter": ("dps_base", "gt"),
}
"""§10.2 承诺的**方向性**：Defense 更硬、Propulsion 更快对齐、Sharpshooter 更高 DPS。

方向比绝对值更稳：它对 SDE 漂移不敏感，且能抓住「模式接错了」这类错误。
"""


def check_modes(case: str, payload: dict, facts: CaseFacts, supported: list[dict],
                eng: "EngineFacts", expected: dict | None = None) -> list[Defect]:
    """校验顶层 `modes` 块（`字段规格 §10.2` 的承诺）。

    三层强度，逐层加严：
    1. **结构**：有模式的船必须有 `modes` 列表，且 Hecate 三模式齐全、每条键齐；
    2. **方向**：Defense EHP ↑ / Propulsion 对齐 ↓ / Sharpshooter DPS ↑；
    3. **数值**：与我自己从引擎重算的模式值逐项对拍（独立于渲染层）。

    `expected` 只给**最小探针配装**（Hecate + 1 炮）用——那才是文档快照的权威样本；
    对 killmail 只验 1–3，不拿它当快照。
    """
    defects: list[Defect] = []
    modes = payload.get("modes")
    if not supported:
        if modes:
            defects.append(Defect("口径错误", "medium", case, "modes",
                                  "无模式的船应输出空列表（或省略）",
                                  f"输出 {len(modes)} 条模式", "船体不支持模式切换"))
        return defects
    if modes is None:
        defects.append(Defect("字段缺失", "high", case, "modes",
                              "有模式的船必须有顶层 modes 块（§10.2 承诺）", "缺失"))
        return defects
    if not isinstance(modes, list):
        defects.append(Defect("口径错误", "high", case, "modes",
                              "list", type(modes).__name__))
        return defects

    by_name: dict[str, dict] = {}
    for index, entry in enumerate(modes):
        if not isinstance(entry, dict):
            defects.append(Defect("口径错误", "medium", case, f"modes[{index}]",
                                  "每条形如 {mode_id, mode_name, ehp_mixed, dps_base, align_time_s}",
                                  type(entry).__name__))
            continue
        for key in MODE_ENTRY_KEYS:
            if key not in entry:
                defects.append(Defect("字段缺失", "medium", case, f"modes[{index}].{key}",
                                      f"键 {key}", "缺失"))
        name = entry.get("mode_name")
        if isinstance(name, str):
            by_name[name] = entry

    # 1) 结构：本船支持的模式必须全部出现
    #    引擎给全名（`Confessor Defense Mode`），渲染层给短名（`Defense`）。
    # 归一规则：去掉 ` Mode` 后缀后取**最后一个词** —— 不需要知道船名，
    #    也不能硬编码船名（第一版写死 "Hecate "，结果只有 Hecate 能对上）。
    for row in supported:
        name = row["name"]
        if name.endswith(" Mode"):
            name = name[: -len(" Mode")]
        name = name.split(" ")[-1]
        if name not in by_name:
            defects.append(Defect("字段缺失", "high", case, f"modes[{name}]",
                                  f"{row['name']}（引擎 modes_for_ship 列出）", "未输出"))

    # 2) 方向
    base = {
        "ehp_mixed": facts.ship.get(-43),
        "dps_base": facts.ship.get(-12),
        "align_time_s": facts.ship.get(-1),
    }
    for name, (key, direction) in MODE_DIRECTION.items():
        entry = by_name.get(name)
        if entry is None:
            continue
        value = entry.get(key)
        baseline = base.get(key)
        if not is_number(value) or baseline is None:
            continue
        better = float(value) > float(baseline) if direction == "gt" else float(value) < float(baseline)
        if not better:
            defects.append(Defect(
                "口径错误", "high", case, f"modes[{name}].{key}",
                f"{'>' if direction == 'gt' else '<'} 无模式基准 {float(baseline):.6f}",
                f"{float(value):.6f}", "§10.2 的方向性不成立（模式接错或算错）"))

    # 3) 数值：与我的引擎重算逐项对拍（不采信渲染层）
    for name, entry in by_name.items():
        mode_id = entry.get("mode_id")
        if not isinstance(mode_id, int):
            continue
        try:
            recomputed = eng.calculate_with(facts, mode=mode_id)
        except Exception as exc:  # noqa: BLE001
            defects.append(Defect("口径错误", "medium", case, f"modes[{name}]",
                                  "模式可被引擎重算", f"{type(exc).__name__}: {exc}"))
            continue
        pairs = (("ehp_mixed", -43), ("dps_base", -12), ("align_time_s", -1))
        for key, attr_id in pairs:
            value = entry.get(key)
            want = recomputed.get(attr_id)
            if not is_number(value) or want is None:
                continue
            if not rel_close(float(value), float(want), RENDER_REL_TOL):
                defects.append(Defect(
                    "口径错误", "high", case, f"modes[{name}].{key}",
                    f"{float(want):.6f}（引擎重算 属性 {attr_id}）", f"{float(value):.6f}",
                    "模式值与引擎独立重算不一致"))

    # 4) 最小探针配装：与 fixture 期望值对拍（文档快照的权威样本）
    if expected:
        for entry in by_name.values():
            mode_id = entry.get("mode_id")
            reference = expected.get(str(mode_id)) if mode_id is not None else None
            if not reference:
                continue
            for key, ref_key in (("ehp_mixed", "ehp"), ("dps_base", "dps"),
                                 ("align_time_s", "align_time_s")):
                value = entry.get(key)
                want = reference.get(ref_key)
                if not is_number(value) or not is_number(want):
                    continue
                if not rel_close(float(value), float(want), RENDER_REL_TOL):
                    defects.append(Defect(
                        "口径错误", "high", case, f"modes[{entry.get('mode_name')}].{key}",
                        f"{float(want)!r}（fixtures 期望值）", f"{float(value)!r}",
                        "最小探针配装的模式值与标定值不一致"))
    return defects


REPAIR_LAYER_ATTRS = {
    # 层名 → (raw 属性, effective 属性)。T-19 裁决：这四层各有独立 has_source。
    "shield": (-47, -50),          # 主动盾修（模块）
    "shield_passive": (-51, -52),  # 被动回充（舰体自带，恒 True）
    "armor": (-45, -48),
    "hull": (-46, -49),
}


def check_repair_zero(case: str, payload: dict, facts: CaseFacts,
                      oracle: "ProvenanceOracle") -> list[Defect]:
    """`repair` 零值语义（**T-19 新裁决**，比原 Q5 更细）。

    1. **值**：每层 `raw_hp_s` / `effective_ehp_s` 必须是 float（无模块给 `0.0`，不是 `null`）；
    2. **provenance**：**不再要求 `measured`**，按投影层属性级 tag 走
       （`-46`/`-47` 实测表里就是 `unknown`）；只校验「键存在」；
       **硬约束：不许手工塞 `measured`** —— 若声明 `measured` 而属性级 tag 不是，
       记 `provenance 错误/high`（"为了让下游好看而撒谎"比缺键更严重）；
    3. **新增字段**：每层必须有 `has_source`（bool）；
    4. **交叉一致性**：`has_source == False` 时 `raw_hp_s` 必须是 `0.0`
       （否则就是"没有来源却有修复量"，真缺陷）。

    分层说明：`shield`（主动 `-47`）与 `shield_passive`（被动 `-51`）**各自独立**——
    `-51` 是舰体自带（裸船恒 ≈3.0469 hp/s），合并会让 `shield.has_source` 永远为 True。
    判「护盾层有没有任何恢复」应取 `shield.has_source or shield_passive.has_source`。
    """
    defects: list[Defect] = []
    overview, table = resolve_view_entry(payload.get("views", payload), "survivability")
    if not isinstance(overview, dict):
        return defects
    repair = overview.get("repair")
    if not isinstance(repair, dict):
        return defects

    for layer, (raw_attr, eff_attr) in REPAIR_LAYER_ATTRS.items():
        node = repair.get(layer)
        if not isinstance(node, dict):
            defects.append(Defect("字段缺失", "medium", case, f"survivability.repair.{layer}",
                                  "{has_source, raw_hp_s, effective_ehp_s}", "该层缺失"))
            continue
        # 1) 值必须是 float（0.0 而不是 null）
        for key in ("raw_hp_s", "effective_ehp_s"):
            value = node.get(key)
            if value is None:
                defects.append(Defect(
                    "口径错误", "medium", case, f"survivability.repair.{layer}.{key}",
                    "0.0（float；无模块也给 0.0，不给 null）", "None",
                    "T-19 裁决：零值是 0.0，null 会让下游数值运算出错"))
            elif not is_number(value):
                defects.append(Defect(
                    "口径错误", "medium", case, f"survivability.repair.{layer}.{key}",
                    "float", repr(value)))
        # 3) has_source 必须是 bool
        has_source = node.get("has_source")
        if not isinstance(has_source, bool):
            defects.append(Defect(
                "字段缺失", "medium", case, f"survivability.repair.{layer}.has_source",
                "bool（该层有没有修复来源）", repr(has_source),
                "T-19 新增字段：『没装模块』的正确载体是 has_source，不是 provenance"))
        # 4) has_source=False 时 raw 必须为 0.0
        raw_value = node.get("raw_hp_s")
        if has_source is False and is_number(raw_value) and not math.isclose(
            float(raw_value), 0.0, abs_tol=1e-9
        ):
            defects.append(Defect(
                "口径错误", "high", case, f"survivability.repair.{layer}.raw_hp_s",
                "has_source=False 时 raw_hp_s 必须是 0.0",
                f"has_source=False 但 raw_hp_s={float(raw_value)!r}",
                "没有修复来源却报出修复量：自相矛盾"))
        # 2') provenance：只校验键存在 + 不许手工塞 measured
        for key, attr_id in (("raw_hp_s", raw_attr), ("effective_ehp_s", eff_attr),
                             ("has_source", None)):
            path = f"repair.{layer}.{key}"
            if isinstance(table, dict) and path not in table:
                defects.append(Defect(
                    "provenance 错误", "high", case, f"survivability.{path}",
                    "平行表里有该键", "缺键",
                    "I6：repair 的每个叶子都要有一条 provenance"))
                continue
            if attr_id is None or not oracle.available or not isinstance(table, dict):
                continue
            declared = table.get(path)
            authoritative = oracle.tag(attr_id)
            if declared is None or authoritative is None or declared == authoritative:
                continue
            severity = "high" if declared == "measured" else "medium"
            defects.append(Defect(
                "provenance 错误", severity, case, f"survivability.{path}",
                f"{authoritative}（投影层属性 {attr_id} 的 tag）", declared,
                "不许手工塞 measured：provenance 必须跟随属性级 tag"
                if declared == "measured" else "provenance 与属性级 tag 不一致"))
    return defects


CAP_SENTINEL_SECONDS = -1.0
"""引擎 `-7`（capDepletesIn）对「电容稳定、永不耗尽」给的哨兵值。

它不是物理时长；输出层应转成 `null`（或省略），并用 `stable_pct > 0` 表达稳定。
"""


def check_cap_sentinel(case: str, payload: dict) -> list[Defect]:
    """D6：`depletes_in_s` 不得把引擎哨兵 `-1` 当秒数输出。"""
    defects: list[Defect] = []
    overview, _table = resolve_view_entry(payload.get("views", payload), "stability")
    if not isinstance(overview, dict):
        return defects
    cap = overview.get("capacitor")
    if not isinstance(cap, dict):
        return defects
    depletes = cap.get("depletes_in_s")
    stable = cap.get("stable_pct")
    if is_number(depletes) and float(depletes) < 0.0:
        defects.append(Defect(
            "口径错误", "medium", case, "stability.capacitor.depletes_in_s",
            "稳定时给 null（永不耗尽）；不得输出引擎哨兵 -1",
            f"{float(depletes)!r}（stable_pct={stable!r}）",
            "引擎 -7 的 -1 是「不耗尽」哨兵，不是物理时长；agent 会读成 −1 秒"))
    return defects


def check_ammo_endurance(case: str, payload: dict, facts: CaseFacts) -> list[Defect]:
    """D5：装了带弹药的武器却算不出弹药续航（导弹武器实测踩到）。

    根因：item 级 `cycleTime(-3)` 对发射器不提供值，但 `51`(speed，毫秒) 有值；
    投影层只用了 `-3`，于是 `ammo_endurance_s` / `ammo_rounds` 恒 None。
    """
    defects: list[Defect] = []
    overview, _table = resolve_view_entry(payload.get("views", payload), "firepower")
    if not isinstance(overview, dict):
        return defects
    weapons = overview.get("weapons")
    if not isinstance(weapons, list):
        return defects
    armed = [w for w in weapons
             if isinstance(w, dict) and w.get("ammo_name") and (w.get("count") or 0) > 0]
    if not armed:
        return defects
    firepower_value = overview.get("ammo_endurance_s")
    stability_overview, _t = resolve_view_entry(payload.get("views", payload), "stability")
    consumables = (stability_overview or {}).get("consumables")
    consumables_value = (consumables or {}).get("ammo_endurance_s") \
        if isinstance(consumables, dict) else None

    # 同一事实在两个视角必须一致（与 hardpoints_free 同类）
    if is_number(firepower_value) and consumables_value is None:
        defects.append(Defect(
            "口径错误", "medium", case, "stability.consumables.ammo_endurance_s",
            f"{float(firepower_value)!r}（与 firepower.ammo_endurance_s 同源，应一致）",
            "None",
            "同一份弹药续航在火力视角有值、不稳定视角为空——下游取哪个都会不一致"))
    elif is_number(consumables_value) and firepower_value is None:
        defects.append(Defect(
            "口径错误", "medium", case, "firepower.ammo_endurance_s",
            f"{float(consumables_value)!r}（与 stability.consumables 同源，应一致）",
            "None", "同一事实两处不一致"))
    elif firepower_value is None and consumables_value is None:
        defects.append(Defect(
            "字段缺失", "medium", case, "firepower.ammo_endurance_s",
            "弹匣量 × cycle（引擎 `51` 有值，可回退）",
            f"两处都是 None（已装弹药 {len(armed)} 组，如 {armed[0].get('name')}）",
            "导弹武器的 item 级 cycleTime(-3) 为空，投影层未回退到 51(speed)"))
    return defects


def check_weapons_vs_alpha(case: str, payload: dict) -> list[Defect]:
    """D7：`weapons[]` 与 `dph` 不得自相矛盾。

    f4（Loki）实测：`weapons[]` 列出 5 门「Heavy Missile Launcher II + Caldari Navy
    Nova Heavy Missile」，但 item 级 `-21`/`-12` 全为 0、`ship -11`(alpha) = 0、
    `dps.base` 完全等于 `dps.drone`。武器清单说"有带弹药的武器"，火力数值说"没有"。
    """
    defects: list[Defect] = []
    overview, _table = resolve_view_entry(payload.get("views", payload), "firepower")
    if not isinstance(overview, dict):
        return defects
    weapons = overview.get("weapons")
    if not isinstance(weapons, list):
        return defects
    dph = overview.get("dph")
    armed = [w for w in weapons
             if isinstance(w, dict) and w.get("ammo_name") and (w.get("count") or 0) > 0
             and w.get("family") != "drone"]
    if not armed:
        return defects

    firepower_flags = (payload.get("views", payload).get("firepower") or {}).get("flags") or []
    flag_ids = {f.get("id") for f in firepower_flags if isinstance(f, dict)}

    # T-22 裁决：Σ 武器槽 -21 == 0 且 -11 == 0 → `dph = null` + `weapon_damage_zero` flag。
    # 之前这里 `if not is_number(dph): return` 会**直接短路**，dph 变 None 后反而漏检。
    if dph is None:
        if "weapon_damage_zero" not in flag_ids:
            defects.append(Defect(
                "字段缺失", "medium", case, "firepower.dph",
                'dph=null 时必须带 `weapon_damage_zero` flag（T-22 裁决）',
                f"dph=None，但 firepower.flags 里没有该 id（现有 {sorted(flag_ids)}）",
                "null 的语义只由 flag 表达；没有 flag 的 null 是裸 null，下游无法区分"))
        return defects

    if not is_number(dph):
        defects.append(Defect(
            "口径错误", "medium", case, "firepower.dph",
            "float 或 null", repr(dph)))
        return defects
    if math.isclose(float(dph), 0.0, abs_tol=1e-9):
        defects.append(Defect(
            "口径错误", "medium", case, "firepower.dph",
            f'null + `weapon_damage_zero` flag（有 {len(armed)} 组带弹药的武器但齐射为 0）',
            "0.0",
            "T-22 裁决：这种情况必须给 null + flag，不能输出数值 0"))
    return defects


OVER_BUDGET_FLAG = "resource_over_budget_unflyable"


def check_feasible(case: str, payload: dict, facts: CaseFacts) -> list[Defect]:
    """T-23：`feasible` / `over_budget` / `resource_over_budget_unflyable` 三者必须自洽。

    - `feasible == (cpu.free >= 0 and powergrid.free >= 0 and calibration.free >= 0)`
      （`calibration.load` 为 None 时按 0 处理）
    - `over_budget.{cpu,powergrid}` = `max(0, -free)`，可行时两项均为 0.0
    - flag 必须与 `feasible` 同步：不可行必须有，可行必须没有
    """
    defects: list[Defect] = []
    views = payload.get("views", payload)
    overview, _table = resolve_view_entry(views, "resources")
    if not isinstance(overview, dict):
        return defects

    feasible = overview.get("feasible")
    if not isinstance(feasible, bool):
        defects.append(Defect(
            "字段缺失", "high", case, "resources.feasible",
            "bool", repr(feasible), "T-23 新增字段：资源可行性"))
        return defects

    def free_of(field_name: str) -> float | None:
        node = overview.get(field_name)
        if not isinstance(node, dict):
            return None
        value = node.get("free")
        return float(value) if is_number(value) else None

    cpu_free = free_of("cpu")
    pg_free = free_of("powergrid")
    cal_free = free_of("calibration")
    if cpu_free is None or pg_free is None:
        return defects                       # 缺字段的问题由字段齐全检查报
    expected = cpu_free >= 0.0 and pg_free >= 0.0 and (cal_free is None or cal_free >= 0.0)
    if feasible != expected:
        defects.append(Defect(
            "口径错误", "high", case, "resources.feasible",
            f"{expected}（cpu.free={cpu_free} / powergrid.free={pg_free} / "
            f"calibration.free={cal_free}）",
            repr(feasible),
            "feasible 必须由三个余量直接决定"))

    over_budget = overview.get("over_budget")
    if not isinstance(over_budget, dict):
        defects.append(Defect(
            "字段缺失", "high", case, "resources.over_budget",
            "{cpu, powergrid}", repr(over_budget), "T-23 新增字段：缺口绝对值"))
    else:
        for key, free in (("cpu", cpu_free), ("powergrid", pg_free)):
            want = max(0.0, -free)
            got = over_budget.get(key)
            if not is_number(got):
                defects.append(Defect(
                    "字段缺失", "high", case, f"resources.over_budget.{key}",
                    "float（缺口绝对值，可行时为 0.0）", repr(got)))
            elif not math.isclose(float(got), want, abs_tol=1e-6):
                defects.append(Defect(
                    "口径错误", "high", case, f"resources.over_budget.{key}",
                    f"{want:.6f}（= max(0, -{free})）", f"{float(got):.6f}",
                    "缺口必须等于余量的绝对值"))

    resources_node = views.get("resources") if isinstance(views, dict) else None
    flags = (resources_node or {}).get("flags") or []
    has_flag = any(isinstance(f, dict) and f.get("id") == OVER_BUDGET_FLAG for f in flags)
    if not feasible and not has_flag:
        defects.append(Defect(
            "字段缺失", "medium", case, "resources.flags",
            f'不可行时必须带 `{OVER_BUDGET_FLAG}` flag', f"现有 flags={[f.get('id') for f in flags if isinstance(f, dict)]}",
            "T-23 裁决：资源不可行必须有对应 flag，否则下游只能自己拼"))
    if feasible and has_flag:
        defects.append(Defect(
            "口径错误", "medium", case, "resources.flags",
            f"可行时不应出现 `{OVER_BUDGET_FLAG}`", "出现了",
            "flag 与 feasible 自相矛盾"))
    return defects


def check_fighter_chain(case: str, payload: dict, facts: "CaseFacts") -> list[Defect]:
    """载机伤害链路（**T-28 实证结论**，不是"有载机就该有伤害"）。

    实证：`f26 Thanatos` 的 8 架载机 flag 全是 **158 = FighterBay（机库）**，
    不是 159–163 的 FighterTube。机库里的载机**完全不参与伤害**，
    只计入船级 `-57`（机库占用 m³）。改造实验把 Templar 移进发射管后 `-58` 才从 0 变 240.846。

    所以本检查只在这三个条件**同时成立**时才要求 `dps.fighter > 0`：
    1. 船级 `-62 fighterTubesUsed > 0`（真有中队进了发射管）；
    2. 进管的中队里**至少有一个不是支援型**（支援型 item 级 `-61 = 1`，不出伤害）；
    3. 输出没有 `fighters_in_bay_only` flag（T-29 的诊断 flag，表示载机只在机库）。
    """
    defects: list[Defect] = []
    overview, _table = resolve_view_entry(payload.get("views", payload), "firepower")
    if not isinstance(overview, dict):
        return defects
    weapons = overview.get("weapons")
    dps = overview.get("dps")
    if not isinstance(weapons, list) or not isinstance(dps, dict):
        return defects
    fighters = [w for w in weapons
                if isinstance(w, dict) and w.get("family") == "fighter"
                and (w.get("count") or 0) > 0]
    if not fighters:
        return defects

    # 3) 输出已声明"只在机库" → 合法状态
    firepower_node = payload.get("views", payload).get("firepower") or {}
    flag_ids = {f.get("id") for f in (firepower_node.get("flags") or []) if isinstance(f, dict)}
    if "fighters_in_bay_only" in flag_ids:
        return defects

    # 1) 发射管是否真的被用了
    tubes_used = float(facts.ship.get(-62) or 0.0)
    if tubes_used <= 0.0:
        return defects                     # 载机都在机库：`-58 = 0` 正确

    # 2) 进管的中队是否全是支援型（item 级 `-61`）
    slots = slot_types(facts)
    launched: list[int] = [i for i, slot in enumerate(slots) if slot == "fighter_tube"]
    if not launched:
        return defects
    support_only = True
    for index in launched:
        attrs = facts.items[index] if index < len(facts.items) else {}
        if float(attrs.get(-61) or 0.0) < 1.0:
            support_only = False
            break
    if support_only:
        return defects                     # 支援型中队不出伤害

    fighter_dps = dps.get("fighter")
    if fighter_dps is None or (is_number(fighter_dps) and math.isclose(
            float(fighter_dps), 0.0, abs_tol=1e-9)):
        defects.append(Defect(
            "口径错误", "medium", case, "firepower.dps.fighter",
            "> 0（有已发射的作战中队）",
            f"{fighter_dps!r}（-62 fighterTubesUsed={tubes_used}，"
            f"进管中队 {len(launched)} 个）",
            "-55/-56/-58 对已发射的作战中队产出 0.0"))
    return defects


def check_provenance_instances(case: str, payload: dict, spec: dict,
                               oracle: "ProvenanceOracle") -> list[Defect]:
    """T-27 实例规则 + 数组元素口径。

    **实例规则**：某叶子在本次计算里**值为 `None`** → provenance 强制 `unknown`
    （覆盖属性级静态表）；值存在时仍按静态表 `tag()`。

    **数组元素口径**（Lead 裁决）：一条路径只有**一个**标签。
    `weapons[].ammo_name` 在混装配装上「一门有弹、一门没装」→ 按缺值判 `unknown`；
    全部有弹才算 `assumed`。所以这里按路径聚合，任一元素为 None 即按缺值处理。
    """
    defects: list[Defect] = []
    views = payload.get("views", payload)
    for view_name in spec["views"]:
        overview, table = resolve_view_entry(views, view_name)
        if overview is None:
            continue
        groups: dict[str, list[Leaf]] = {}
        for leaf in collect_leaves(overview, table):
            groups.setdefault(leaf.path, []).append(leaf)
        for path, leaves in groups.items():
            declared = next((leaf.provenance for leaf in leaves if leaf.provenance), None)
            if declared is None:
                continue                       # 缺键由 I6 报
            any_none = any(leaf.value_none for leaf in leaves)
            if any_none and declared != "unknown":
                none_count = sum(1 for leaf in leaves if leaf.value_none)
                defects.append(Defect(
                    "provenance 错误", "high", case, f"{view_name}.{path}",
                    "unknown（T-27：值缺失即 unknown）", declared,
                    f"{none_count}/{len(leaves)} 个元素值为 None，静态表必须让位于实例规则"))
            elif not any_none and oracle.available:
                expected = None
                for leaf in leaves:
                    if leaf.source_attr is not None:
                        expected = oracle.tag(leaf.source_attr)
                        if expected is not None:
                            break
                if expected is None or declared == expected:
                    continue
                declared_rank = PROVENANCE_CONSERVATISM.get(declared)
                expected_rank = PROVENANCE_CONSERVATISM.get(expected)
                if declared_rank is None or expected_rank is None:
                    continue
                if declared_rank > expected_rank:
                    # **T-30 合法升格**：实例级比静态表更保守，例如某属性随模块
                    # state（active/online）变化 → 静态表判 measured，本次判 assumed。
                    continue
                # 到这里只剩「降级」（如 assumed → measured）：禁止
                defects.append(Defect(
                    "provenance 错误", "medium", case, f"{view_name}.{path}",
                    f"{expected}（静态表 tag），或更保守的 assumed/unknown",
                    declared,
                    "禁止降级：静态表判 assumed/unknown 时不得声称 measured"
                    "（实例规则只允许往更保守方向升格）"))
    return defects


def check_view_structure(case: str, payload: dict, spec: dict,
                         facts: CaseFacts, oracle: "ProvenanceOracle",
                         confidence_oracle: "ConfidenceOracle" | None = None) -> list[Defect]:
    """字段齐全 + I6 + 口径 + 单位 四类检查。"""
    defects: list[Defect] = []
    views = payload.get("views", payload)

    for view_name, spec_field in ((v, f) for v, f, _m in all_fields(spec)):
        overview, _provenance_table = resolve_view_entry(views, view_name)
        node_entry = views.get(view_name) if isinstance(views, dict) else None
        confidence_table = None
        if isinstance(node_entry, dict) and isinstance(node_entry.get("confidence"), dict):
            confidence_table = node_entry["confidence"]
        name = spec_field["name"]
        if overview is None:
            defects.append(Defect("字段缺失", "blocker", case, f"{view_name}",
                                  "视角存在（含 overview）", "视角缺失"))
            continue
        present, value = lookup_field(overview, spec_field)
        if not present:
            defects.append(Defect("字段缺失", spec_field["severe"], case,
                                  f"{view_name}.{name}",
                                  spec_field["meaning"], "字段不存在"))
            continue
        # 子键齐全（条件字段：只有该船真的有这种槽位时才要求）
        conditional = spec_field.get("conditional_subpaths") or {}
        for sub in spec_field.get("subpaths", []):
            if sub in conditional and not condition_holds(conditional[sub], facts):
                continue
            if not isinstance(value, dict) or sub not in value:
                defects.append(Defect("字段缺失", "medium", case,
                                      f"{view_name}.{name}.{sub}",
                                      f"子字段 {sub}", "子字段不存在"))
        one_of = spec_field.get("one_of_subpaths") or []
        if one_of and isinstance(value, dict) and not any(key in value for key in one_of):
            defects.append(Defect("字段缺失", "medium", case,
                                  f"{view_name}.{name}",
                                  f"至少一个: {one_of}", f"实际子键 {sorted(value)}"))
        # 数组元素的必备键
        for item_key in spec_field.get("item_keys", []):
            if isinstance(value, list) and value:
                if not isinstance(value[0], dict) or item_key not in value[0]:
                    defects.append(Defect("字段缺失", "medium", case,
                                          f"{view_name}.{name}[].{item_key}",
                                          f"元素含 {item_key}",
                                          f"元素={json.dumps(value[0], ensure_ascii=False)[:120]}"))
        # provenance：只校验三态合法性；**不**用字段规格的列去断言期望值
        # （Lead 裁决：权威是 evefit/project/provenance.py）
        declared = None
        if isinstance(value, dict) and isinstance(value.get("provenance"), str):
            declared = value["provenance"]
        if declared is not None and declared not in PROVENANCE_VALUES:
            defects.append(Defect("provenance 错误", "high", case,
                                  f"{view_name}.{name}.provenance",
                                  f"∈ {PROVENANCE_VALUES}", declared))
        # confidence 不在这里逐字段判：稀疏表的权威是
        # `evefit/project/confidence.py` 的 confidence_of()，在 I6 段按叶子统一校验
        # （见 check_confidence：反查「非 A 必须出现」+ 键空间 ⊆ provenance）。

    # ---- I6：**每个叶子**（数值／字符串／布尔／None）都必须有 provenance ----
    # 双形态：形态 A 查平行表（键空间 = render 的 _leaf_paths），形态 B 取值自带/继承。
    for view_name in spec["views"]:
        entry = views.get(view_name) if isinstance(views, dict) else None
        overview, table = resolve_view_entry(views, view_name)
        if overview is None:
            continue
        leaves = collect_leaves(overview, table)
        form_a = isinstance(table, dict) and bool(table)
        for leaf in leaves:
            prov = leaf.provenance
            has_numbers = bool(leaf.numbers)
            if prov is None:
                # 形态 A：平行表必须给每个叶子一条；缺键时
                #   - 有数值 → 真缺陷（high，I6 违反）
                #   - 无数值（空列表/字符串/None）→ 观察（low）：没有数据可误标，
                #     但覆盖度不全（渲染层本应对空容器也落一条 `unknown`）
                # 形态 B：TypedValue 只能裹数值，故字符串/空容器**不强求** provenance
                if form_a:
                    defects.append(Defect(
                        "provenance 错误", "high" if has_numbers else "low", case,
                        f"{view_name}.{leaf.path}",
                        "measured / assumed / unknown",
                        "无 provenance（平行表里没有这个键）",
                        "红线 N3：输出无 provenance 的叶子" if has_numbers
                        else "空容器/非数值叶子未登记（无数据可误标，仅覆盖度观察）"))
                elif has_numbers:
                    defects.append(Defect(
                        "provenance 错误", "high", case,
                        f"{view_name}.{leaf.path}",
                        "measured / assumed / unknown", "无 provenance",
                        "红线 N3：输出无 provenance 的数值"))
            elif prov not in PROVENANCE_VALUES:
                defects.append(Defect("provenance 错误", "high", case,
                                      f"{view_name}.{leaf.path}",
                                      f"∈ {PROVENANCE_VALUES}", prov))
            # 有权威表时逐值对拍（T-08 落地后本项自动生效）
            if leaf.source_attr is not None and oracle.available and not leaf.value_none:
                authoritative = oracle.tag(leaf.source_attr)
                if authoritative is not None and prov is not None and prov != authoritative:
                    defects.append(Defect(
                        "provenance 错误", "medium", case, f"{view_name}.{leaf.path}",
                        f"{authoritative}（{oracle.source} 对属性 {leaf.source_attr} 的结论）",
                        prov,
                        "provenance 与投影层权威表不一致"))
        # 键空间自检（信息项）：我的叶子路径集合 vs 平行表的键集合。
        # 形态 A 下两者应当**完全一致**；差异说明有一方的遍历规则不同，
        # 记 low 观察（不阻塞），因为它可能掩盖真正的缺失。
        if isinstance(table, dict) and table:
            mine = {leaf.path for leaf in leaves}
            theirs = set(table)
            only_theirs = sorted(theirs - mine)
            only_mine = sorted(mine - theirs)
            if only_theirs or only_mine:
                defects.append(Defect(
                    "provenance 错误", "low", case, f"{view_name}.provenance",
                    "平行表键集合 == overview 叶子路径集合",
                    f"表多出 {len(only_theirs)} 个（{only_theirs[:4]}）、"
                    f"表缺少 {len(only_mine)} 个（{only_mine[:4]}）",
                    "键空间不一致：可能遍历规则不同，需与渲染层对齐"))

        # ---- confidence（稀疏表）：默认 A、只输出非 A、非 A 必须出现 ----
        if confidence_oracle is not None:
            conf_table = None
            if isinstance(entry, dict) and isinstance(entry.get("confidence"), dict):
                conf_table = entry["confidence"]
            inline = {leaf.path: leaf.confidence for leaf in leaves if leaf.confidence}
            defects.extend(check_confidence(
                case, view_name, leaves, conf_table, inline,
                {leaf.path for leaf in leaves}, confidence_oracle,
                confidence_oracle.default, confidence_oracle.levels))

    # ---- 口径：与引擎事实直查比对 ----
    defects.extend(check_semantics(case, views, facts))
    # ---- 单位 ----
    defects.extend(check_units(case, views, facts))
    return defects


def check_semantics(case: str, views: dict, facts: CaseFacts) -> list[Defect]:
    defects: list[Defect] = []
    for view_name in views:
        node = resolve_view(views, view_name)
        if not isinstance(node, dict):
            continue
        for path, attr_id in SEMANTIC_MAP.items():
            target = node
            ok = True
            for part in path.split("."):
                if isinstance(target, dict) and part in target:
                    target = target[part]
                elif isinstance(target, dict):
                    ok = False
                    break
                else:
                    ok = False
                    break
            if not ok:
                continue
            if isinstance(target, dict) and "value" in target:
                target = target["value"]
            if not is_number(target):
                continue
            expected = facts.ship.get(attr_id)
            if expected is None:
                continue
            expected_value = float(expected)
            if attr_id in MS_ATTRS:
                expected_value = expected_value / 1000.0
            if attr_id in KILO_ATTRS:
                expected_value = expected_value / 1000.0
            # 渲染层用 fmt(3) 压小数位；0.39744→0.397 的相对误差已达 1.1e-3，
            # 所以必须叠加「半个末位」的绝对容差，否则会误报口径错误。
            if not math.isclose(float(target), expected_value,
                                rel_tol=RENDER_REL_TOL, abs_tol=5e-4):
                category = "口径错误"
                note = f"来源属性 {attr_id}"
                if attr_id in MS_ATTRS and rel_close(float(target), float(expected), 1e-9):
                    category = "单位错误"
                    note = f"输出了毫秒原值（属性 {attr_id} 存储值 {float(expected)} → 应为 {expected_value} 秒）"
                if attr_id in KILO_ATTRS and rel_close(float(target), float(expected), 1e-9):
                    category = "单位错误"
                    note = f"输出了 ×1000 原值（属性 {attr_id} 存储值 {float(expected)} → 应为 {expected_value}）"
                defects.append(Defect(category, "high", case, f"{view_name}.{path}",
                                      f"{expected_value!r}", f"{float(target)!r}", note))
    # 资源视角与火力视角的同名字段必须一致
    both = []
    for view_name in ("firepower", "resources"):
        node = resolve_view(views, view_name)
        if isinstance(node, dict) and isinstance(node.get("hardpoints_free"), dict):
            both.append((view_name, node["hardpoints_free"]))
    if len(both) == 2:
        (_, left), (_, right) = both
        for key in ("turret", "launcher"):
            lv = left.get(key)
            rv = right.get(key)
            lv = lv.get("value") if isinstance(lv, dict) else lv
            rv = rv.get("value") if isinstance(rv, dict) else rv
            if is_number(lv) and is_number(rv) and not rel_close(float(lv), float(rv), 1e-9):
                defects.append(Defect("口径错误", "medium", case,
                                      f"hardpoints_free.{key}",
                                      "火力与资源两视角取值一致",
                                      f"firepower={lv} resources={rv}",
                                      "同一事实在两个视角给出不同值"))

    # `damage_split` 是 **0..1 比率**（四值之和 = 1）；渲染层不做 ×100（Lead 裁决 2026-10-18）。
    for view_name in views:
        node = resolve_view(views, view_name)
        if not isinstance(node, dict) or not isinstance(node.get("damage_split"), dict):
            continue
        split = node["damage_split"]
        values: list[float] = []
        for key in ("em", "explosive", "kinetic", "thermal"):
            item = split.get(key)
            if isinstance(item, dict):
                item = item.get("value")
            if is_number(item):
                values.append(float(item))
        if len(values) != 4:
            continue
        total = math.fsum(values)
        if math.isclose(total, 0.0, abs_tol=1e-12):
            continue                      # 无武器/无弹药：不构成伤害分布，跳过
        if math.isclose(total, 100.0, rel_tol=1e-6):
            defects.append(Defect(
                "口径错误", "high", case, f"{view_name}.damage_split",
                "四值之和 = 1（0..1 比率）",
                f"四值之和 = {total}（百分比口径，渲染层不做 ×100）",
                "Lead 裁决：damage_split 用 0..1 比率"))
        elif not math.isclose(total, 1.0, rel_tol=1e-3, abs_tol=5e-3):
            defects.append(Defect(
                "口径错误", "medium", case, f"{view_name}.damage_split",
                "四值之和 ≈ 1", f"四值之和 = {total:.6f}",
                "伤害分布未归一"))
    return defects


def _rounding_match(value: float, target: float) -> bool:
    """`value` 是否等于 `target` 的某个**渲染精度**四舍五入结果（3–6 位小数）。

    渲染层用 `fmt(digits=3)` 压小数位，所以「毫秒原值」到了输出里是 `2304.636`
    而不是 `2304.63606981011`——必须按舍入后比较，否则整类错误会漏掉。

    **下界取 3 位**：取到 0–2 位会让 `3.24 → 3.2` 这种巧合也命中，
    把合法的 GJ/s（`cap_use_gj_s: 3.2`）误报成 ×1000 原值（T-19 实测踩到）。
    """
    for digits in range(3, 7):
        if abs(value - round(target, digits)) <= max(1e-12, abs(target) * 1e-12):
            return True
    return False


def check_units(case: str, views: dict, facts: CaseFacts) -> list[Defect]:
    """单位检查：不得出现毫秒原值、不得出现 ×1000 原值、不得违反量级常识。"""
    defects: list[Defect] = []
    raw_by_attr = facts.raw_by_attr()
    probes: dict[int, UnitProbe] = {}
    for attr_id in sorted(MS_ATTRS | KILO_ATTRS):
        for source in (facts.ship, *facts.items):
            raw = source.get(attr_id)
            if raw is None:
                continue
            physical = float(raw) / 1000.0
            probes[attr_id] = UnitProbe(attr_id=attr_id, raw=float(raw), physical=physical)
            break

    name_rules = (
        ("_s", "秒", 1e-9, None),
        ("_rad_s", "rad/s", 1e-9, 10.0),
        ("_au_s", "AU/秒", 1e-9, 50.0),
        ("_mps", "米/秒", 1e-9, 1e6),
    )

    for view_name in views:
        node, table = resolve_view_entry(views, view_name)
        if not isinstance(node, dict):
            continue
        for path, value, _prov, source_attr in walk_numeric(node, table):
            where = f"{view_name}.{path}"
            leaf_name = path.rsplit(".", 1)[-1].split("[")[0]
            flagged = False
            # (0) 用声明里的 source_attr 定向判定（最可靠：知道该属性该不该缩放）
            if isinstance(source_attr, int) and source_attr in raw_by_attr:
                raw = raw_by_attr[source_attr]
                physical = raw / 1000.0
                needs_scale = source_attr in MS_ATTRS or source_attr in KILO_ATTRS
                if needs_scale and _rounding_match(value, raw) and not _rounding_match(value, physical):
                    kind = "毫秒" if source_attr in MS_ATTRS else "×1000"
                    defects.append(Defect(
                        "单位错误", "high", case, where,
                        f"{physical!r}（属性 {source_attr} 已换算）",
                        f"{value!r}（等于{kind}存储原值 {raw!r}）",
                        "红线 N5：不得输出毫秒 / ×1000 原值"))
                    flagged = True
            # (1) 泛化扫描：仅对**带单位后缀**的字段名生效。
            # 否则 `count: 1`、`damage_split.explosive: 1.0` 这类计数/比率
            # 会撞上某个 ×1000 属性的舍入巧合，制造假「单位错误」。
            unit_like = leaf_name.endswith(("_s", "_rad_s", "_au_s", "_mps", "_m",
                                            "_pct", "_kg", "_m3"))
            if not flagged and unit_like:
                for attr_id, probe in probes.items():
                    if _rounding_match(value, probe.raw) and not _rounding_match(value, probe.physical):
                        defects.append(Defect(
                            "单位错误", "high", case, where,
                            f"{probe.physical!r}（属性 {attr_id} 已换算）",
                            f"{value!r}（等于存储原值→舍入）",
                            "红线 N5：不得输出毫秒 / ×1000 原值"))
                        break
            # (2) 后缀量级常识
            for suffix, unit_label, _tol, ceiling in name_rules:
                if not leaf_name.endswith(suffix) or ceiling is None:
                    continue
                if abs(value) > ceiling:
                    defects.append(Defect(
                        "单位错误", "high", case, where,
                        f"字段名要求 {unit_label}，量级应 ≤ {ceiling}",
                        f"{value!r}",
                        "红线 N5：字段名带单位后缀，值必须是该物理单位"))
    return defects


# ---------------------------------------------------------------------------
# 六视角 JSON 取得（三种途径）
# ---------------------------------------------------------------------------


def load_views_from_json(path: Path, case: str) -> dict | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("cases"), dict):
        entry = payload["cases"].get(case)
        return entry if isinstance(entry, dict) else None
    if isinstance(payload, dict) and "views" in payload:
        if case in payload["views"] and isinstance(payload["views"][case], dict):
            return {"views": payload["views"][case], "meta": payload.get("meta")}
        return payload
    if isinstance(payload, dict) and case in payload:
        return payload[case]
    return None


def load_expected_modes(case_name: str) -> dict | None:
    """读 `fixtures/<case>.expected.json` 的 `modes`（仅**最小探针配装**用作权威样本）。"""
    path = FIXTURE_DIR / f"{case_name}.expected.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    modes = payload.get("modes")
    return modes if isinstance(modes, dict) and modes else None


def build_view_context(facts: CaseFacts, eng: "EngineFacts") -> object:
    """构造渲染层的 `ViewContext`（六视角共用同一份变体缓存）。

    **关键约定**：`ViewContext` 只从 `ctx.fit` 建变体（`base.ViewContext._pair` →
    `scenarios.build_variant(self.fit, ...)`），**不读 `ctx.assumptions`**。
    所以技能档必须由调用方注入 `fit["character"]["skills"]`——与
    `tests/test_render.py` 的 `sample_fit` 完全一致。漏了这步，渲染层会按
    **零技能**算，于是每个数都对不上（这是验收器自己的错，不是实现的错）。
    """
    from evefit.project.views import base as views_base

    fit = dict(facts.fit)
    fit["character"] = {"skills": dict(facts.skills)}
    return views_base.ViewContext(
        engine=eng.engine,
        index=eng.index,
        fit=fit,
        assumptions=facts.assumptions,
        meta=facts.meta,
    )


def call_entry(spec: str, facts: CaseFacts, eng: "EngineFacts") -> dict:
    module_name, _, func_name = spec.partition(":")
    module = importlib.import_module(module_name)
    func = getattr(module, func_name)
    parameters = list(inspect.signature(func).parameters)
    first = parameters[0] if parameters else ""
    if first in ("ctx", "context"):
        # 渲染层入口：`render_overview(ctx, *, views=None)`
        return func(build_view_context(facts, eng))
    if first in ("eft", "text", "eft_text"):
        return func(facts.eft_text)
    if first in ("fit", "fit_dict", "data"):
        return func(facts.fit)
    try:
        return func(facts.fit)
    except TypeError:
        return func(facts.eft_text)


def discover_entry() -> str | None:
    for module_name, func_name in ENTRY_CANDIDATES:
        try:
            module = importlib.import_module(module_name)
            func = getattr(module, func_name, None)
            if callable(func):
                return f"{module_name}:{func_name}"
        except Exception:  # noqa: BLE001 - 探测失败即视为不可用
            continue
    return None


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def field_table_markdown(spec: dict) -> str:
    lines = [
        "| 视角 | 字段 | 类型 | 来源属性 ID | provenance（是否依赖假设） | 可信度 | 单位 | 期望含义 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for view_name, view in spec["views"].items():
        for spec_field in view["fields"]:
            attrs = spec_field.get("source_attrs") or []
            attr_text = "、".join(str(a) for a in attrs) if attrs else "—"
            sub = spec_field.get("subpaths") or spec_field.get("item_keys") or []
            sub_text = f"（{', '.join(str(s) for s in sub)}）" if sub else ""
            unit = (spec_field.get("unit") or "—").replace("|", "\\|")
            lines.append(
                f"| {view['label']} | `{spec_field['name']}`{sub_text} "
                f"| {spec_field['kind']} | {attr_text} "
                f"| {spec_field['provenance']} | {spec_field.get('confidence', '—')} "
                f"| {unit} | {spec_field['meaning'].replace('|', chr(92) + '|')} |"
            )
    return "\n".join(lines)


def render_report(payload: dict, spec: dict) -> str:
    defects = payload["defects"]
    counts = payload["counts"]
    status = payload["status"]
    lines: list[str] = []
    title = payload.get("label") or "验收"
    lines.append(f"# evefit 六视角验收报告 · {title}")
    lines.append("")
    lines.append(f"- 生成时间：{payload['generated_at']}")
    lines.append(f"- runner：`evefit/acceptance/run_acceptance.py`（v1）")
    lines.append(f"- 字段清单：`evefit/acceptance/field_spec.json`（{payload['field_total']} 个 overview 字段）")
    lines.append("")

    verdict = {"blocked": "阻塞（投影层未就绪）", "pass": "通过", "fail": "不通过"}[status]
    lines.append(f"## 0. 结论：**{verdict}**")
    lines.append("")
    if payload.get("blocked_reason"):
        lines.append(f"> {payload['blocked_reason']}")
        lines.append("")
    lines.append("| 缺陷类别 | 条数 |")
    lines.append("|---|---|")
    for category in ("字段缺失", "口径错误", "单位错误", "provenance 错误", "不变式失败"):
        lines.append(f"| {category} | {counts.get(category, 0)} |")
    lines.append(f"| **合计** | **{sum(counts.values())}** |")
    lines.append("")

    lines.append("## 1. 环境（版本三元组）")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    meta = payload.get("meta") or {}
    lines.append(f"| SDE build | {meta.get('sde_build')} |")
    lines.append(f"| 引擎版本 | {meta.get('engine_version')} |")
    lines.append(f"| 规格版本 | {meta.get('spec_version')} |")
    lines.append(f"| 样本数 | {len(payload['cases'])} |")
    oracle = payload.get("provenance_oracle") or {}
    lines.append(
        f"| provenance 权威表 | {'✅ ' + str(oracle.get('source')) if oracle.get('available') else '⚠️ 未就绪 → 仅做结构校验（不硬编码期望值）'} |"
    )
    lines.append("")

    lines.append("## 2. 样本")
    lines.append("")
    lines.append("| 样本 | 形态 | 船 | 技能档 | 技能数 | 模式 |")
    lines.append("|---|---|---|---|---|---|")
    for case in payload["cases"]:
        lines.append(
            f"| {case['name']} | {case['source_format']} | {case['ship_name']} "
            f"| {case['skill_tier_name']} | {case['skill_count']} "
            f"| {len(case['modes'])} |"
        )
    lines.append("")

    lines.append("## 3. 缺陷清单（按严重度）")
    lines.append("")
    if not defects:
        lines.append("无。")
    else:
        lines.append("| # | 严重度 | 类别 | 样本 | 位置 | 期望 | 实际 | 说明 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        order = {"blocker": 0, "high": 1, "medium": 2, "low": 3}
        for position, defect in enumerate(
            sorted(defects, key=lambda d: (order.get(d["severity"], 9), d["category"], d["case"])),
            start=1,
        ):
            lines.append(
                f"| {position} | {defect['severity']} | {defect['category']} "
                f"| {defect['case']} | `{defect['where']}` | {defect['expected']} "
                f"| {defect['actual']} | {defect['note']} |"
            )
    lines.append("")

    lines.append("## 4. 不变式明细（runner 独立复核）")
    lines.append("")
    lines.append("| 不变式 | 内容 | 结果 |")
    lines.append("|---|---|---|")
    invariant_failures = [d for d in defects if d["category"] == "不变式失败"]
    for key, text in spec["invariants"].items():
        related = [d for d in invariant_failures if d["where"].startswith(key)]
        if key == "I6" and not payload.get("views_available"):
            # 没有六视角输出时 I6 无对象可检，不能假装"成立"
            lines.append(f"| {key} | {text} | ⏸ 未执行（投影层未就绪） |")
            continue
        result = "✅ 成立" if not related else f"❌ {len(related)} 条失败"
        lines.append(f"| {key} | {text} | {result} |")
    lines.append("")

    lines.append("## 5. 字段勾选表（逐样本）")
    lines.append("")
    if payload.get("views_available"):
        for case in payload["cases"]:
            lines.append(f"### {case['name']}")
            lines.append("")
            lines.append("| 视角 | 字段 | 结果 |")
            lines.append("|---|---|---|")
            for entry in case["field_results"]:
                mark = "✅" if entry["ok"] else "❌"
                lines.append(f"| {entry['view']} | `{entry['field']}` | {mark} |")
            lines.append("")
    else:
        lines.append("> 投影层未就绪，字段勾选表全部记为缺失（不计入实现缺陷，见 `status=blocked`）。")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def collect_eft_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    for raw in args.eft or []:
        path = Path(raw)
        if path.is_dir():
            paths.extend(sorted(
                p for pattern in ("*.eft", "*.json")
                for p in path.glob(pattern)
                if not p.name.endswith(".expected.json")
            ))
        elif path.is_file():
            paths.append(path)
        else:
            raise SystemExit(f"样本不存在: {path}")
    return paths


def default_fixtures() -> list[Path]:
    """默认验收样本：EFT **与** 原始 killmail JSON 都算（`.expected.json` 是期望值，不是样本）。"""
    return sorted(
        p for pattern in ("*.eft", "*.json")
        for p in FIXTURE_DIR.glob(pattern)
        if not p.name.endswith(".expected.json")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="evefit 六视角验收 runner")
    parser.add_argument("--eft", action="append", help="EFT 文本文件或目录（可多次）")
    parser.add_argument("--views-json", action="append",
                        help="外部提供的六视角 JSON（可多次，按样本名匹配）")
    parser.add_argument("--entry", help="投影层入口 pkg.mod:func")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    parser.add_argument("--label", default="auto", help="报告标签")
    parser.add_argument("--emit-field-table", action="store_true", help="只打印字段勾选表")
    parser.add_argument("--no-invariants", action="store_true", help="跳过 I1–I7（调试用）")
    args = parser.parse_args(argv)

    spec = load_field_spec()
    if args.emit_field_table:
        print(field_table_markdown(spec))
        return 0

    field_total = len(all_fields(spec))
    defects: list[Defect] = []
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    eft_paths = collect_eft_paths(args) if args.eft else default_fixtures()
    if not eft_paths:
        print("没有可验收的样本（--eft 未给且 fixtures/*.eft 为空）", file=sys.stderr)
        return 2

    try:
        eng = EngineFacts()
    except Exception as exc:  # noqa: BLE001
        print(f"引擎 / SDE 不可用：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("检查 UV_CACHE_DIR 与 eveshipfit-dogma-engine[sde]==13.6.0", file=sys.stderr)
        return 2

    # ---- 不变式 I7 与空槽观察：与样本无关，跑一次 ----
    if not args.no_invariants:
        defects.extend(check_error_structure())
        defects.extend(check_empty_slot_markers())

    cases_payload: list[dict] = []
    views_available = False
    blocked_reason = ""

    entry = args.entry or discover_entry()
    oracle = ProvenanceOracle()
    confidence_oracle = ConfidenceOracle()
    views_json_paths = [Path(raw) for raw in (args.views_json or [])]
    for path in views_json_paths:
        if not path.is_file():
            print(f"views JSON 不存在: {path}", file=sys.stderr)
            return 2

    for eft_path in eft_paths:
        case_name = eft_path.stem
        print(f"[case] {case_name}")
        try:
            facts = eng.build(case_name, eft_path.read_text(encoding="utf-8"), eft_path)
        except Exception as exc:  # noqa: BLE001
            defects.append(Defect("口径错误", "blocker", case_name, "normalize",
                                  "样本可归一化", f"{type(exc).__name__}: {exc}"))
            continue

        # 技能档护栏：T-12 之后不传 index 会静默退回旧档（19 个技能），
        # 所有数值都会跑在旧档上——这是"看起来全绿其实全错"的失效模式，必须显式炸出来。
        if facts.skill_tier_name != EXPECTED_SKILL_TIER_NAME:
            defects.append(Defect(
                "口径错误", "blocker", case_name, "assumptions.skill_tier_name",
                f"{EXPECTED_SKILL_TIER_NAME}（全部技能 4 级 + 补最低）",
                f"{facts.skill_tier_name}（{len(facts.skills)} 个技能）",
                "技能档退化：default_assumptions 未传 index，全部数值跑在旧档上"))
        elif len(facts.skills) < EXPECTED_MIN_SKILLS:
            defects.append(Defect(
                "口径错误", "blocker", case_name, "assumptions.skill_tier",
                f"≥ {EXPECTED_MIN_SKILLS} 个技能", f"{len(facts.skills)} 个",
                "技能档名字对但技能数明显偏少，疑似只补了被模块要求的技能"))

        if not args.no_invariants:
            defects.extend(check_invariants(facts, eng))

        modes = []
        try:
            for mode_row in eng.modes_of(facts):
                ship = eng.calculate_with(facts, mode=mode_row["type_id"])
                modes.append({
                    "mode_id": mode_row["type_id"],
                    "name": mode_row["name"],
                    "ehp_mixed": ship.get(-43),
                    "dps": ship.get(-12),
                    "align_time_s": ship.get(-1),
                    "speed_mps": ship.get(37),
                })
        except Exception as exc:  # noqa: BLE001
            defects.append(Defect("口径错误", "low", case_name, "mode",
                                  "模式可枚举", f"{type(exc).__name__}: {exc}"))

        # ---- 取得六视角 ----
        payload_views: dict | None = None
        for views_path in views_json_paths:
            try:
                payload_views = load_views_from_json(views_path, case_name)
            except Exception as exc:  # noqa: BLE001
                print(f"  读取 {views_path.name} 失败: {exc}", file=sys.stderr)
                payload_views = None
            if payload_views is not None:
                break
        if payload_views is None and not views_json_paths and entry:
            try:
                payload_views = call_entry(entry, facts, eng)
            except Exception as exc:  # noqa: BLE001
                blocked_reason = (f"调用入口 `{entry}` 失败：{type(exc).__name__}: {exc}")
        if payload_views is None and views_json_paths:
            # **部分覆盖不能冒充通过**：外部 views JSON 里没有这个样本 → 本样本完全未验。
            defects.append(Defect(
                "字段缺失", "high", case_name, "views",
                "该样本的六视角结构",
                f"提供的 views JSON 里没有 `{case_name}`（本轮该样本未覆盖）",
                "样本未被验证时不得计入通过——请补齐 JSON 或用 --entry 直连实现"))

        field_results = []
        if payload_views is not None:
            views_available = True
            defects.extend(check_render_meta(case_name, payload_views, facts))
            defects.extend(check_repair_zero(case_name, payload_views, facts, oracle))
            defects.extend(check_cap_sentinel(case_name, payload_views))
            defects.extend(check_ammo_endurance(case_name, payload_views, facts))
            defects.extend(check_weapons_vs_alpha(case_name, payload_views))
            defects.extend(check_feasible(case_name, payload_views, facts))
            defects.extend(check_fighter_chain(case_name, payload_views, facts))
            defects.extend(check_provenance_instances(case_name, payload_views, spec, oracle))
            defects.extend(check_view_structure(case_name, payload_views, spec, facts,
                                                oracle, confidence_oracle))
            # modes 块（§10.2）：结构 + 方向 + 数值（对最小探针配装再对 fixture 期望值）
            fitted = [item for item in (facts.fit.get("items") or [])
                      if (item.get("slot") or {}).get("type") != "cargo"]
            expected_modes = load_expected_modes(case_name) if len(fitted) == 1 else None
            defects.extend(check_modes(case_name, payload_views, facts,
                                       eng.modes_of(facts), eng, expected_modes))
            views_node = payload_views.get("views", payload_views)
            for view_name, spec_field in ((v, f) for v, f, _m in all_fields(spec)):
                overview = resolve_view(views_node, view_name)
                present = False
                if isinstance(overview, dict):
                    present, _value = lookup_field(overview, spec_field)
                field_results.append({
                    "view": spec["views"][view_name]["label"],
                    "field": spec_field["name"],
                    "ok": present,
                })

        ship_name = "-"
        try:
            ship_type_id = (facts.fit.get("ship") or {}).get("type_id")
            row = eng.index.get_type(ship_type_id)
            ship_name = row["name"] if row else str(ship_type_id)
        except Exception:  # noqa: BLE001 - 装饰性信息，失败不阻塞
            pass

        cases_payload.append({
            "name": case_name,
            "eft_path": str(eft_path),
            "source_format": facts.source_format,
            "ship_name": ship_name,
            "ship_type_id": (facts.fit.get("ship") or {}).get("type_id"),
            "skill_tier_name": facts.skill_tier_name,
            "skill_count": len(facts.skills),
            "skill_tier_histogram": {
                str(level): list(facts.skills.values()).count(level)
                for level in sorted(set(facts.skills.values()))
            },
            "modes": modes,
            "violations": facts.violations,
            "facts": {
                "ehp_mixed": facts.ship.get(-43),
                "dps": facts.ship.get(-12),
                "alpha": facts.ship.get(-11),
                "align_time_s": facts.ship.get(-1),
                "speed_mps": facts.ship.get(37),
                "cpu": {"output": facts.ship.get(48), "load": facts.ship.get(49),
                        "free": facts.ship.get(-9)},
                "powergrid": {"output": facts.ship.get(11), "load": facts.ship.get(15),
                              "free": facts.ship.get(-10)},
                "cap_stable_pct": facts.ship.get(-72),
                "cap_depletes_in_s": facts.ship.get(-7),
                "warp_speed_au_s": facts.ship.get(600),
            },
            "field_results": field_results,
        })

    # ---- 去重 + 计数（同一位置同一类别只记一条，避免双计） ----
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[Defect] = []
    for defect in defects:
        if defect.key() in seen:
            continue
        seen.add(defect.key())
        unique.append(defect)
    defects = unique

    counts: dict[str, int] = {}
    for defect in defects:
        counts[defect.category] = counts.get(defect.category, 0) + 1

    if not views_available:
        status = "blocked"
        if not blocked_reason:
            blocked_reason = (
                "未取得任何一套六视角结构（可用 `--views-json` 指定实现产出，"
                "或 `--entry pkg.mod:func` 指定入口；也可等投影层 render 入口就绪）"
            )
    elif any(defect.severity != "low" for defect in defects):
        status = "fail"
    else:
        # 只有 low 级观察项时不判定失败（例如 I7 错误结构里的行号缺失）
        status = "pass"

    payload = {
        "label": args.label,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "status": status,
        "blocked_reason": blocked_reason if not views_available else "",
        "views_available": views_available,
        "entry": entry,
        "meta": eng.engine.meta(),
        "provenance_oracle": {"available": oracle.available, "source": oracle.source},
        "field_total": field_total,
        "counts": counts,
        "defects": [
            {"category": d.category, "severity": d.severity, "case": d.case,
             "where": d.where, "expected": d.expected, "actual": d.actual, "note": d.note}
            for d in defects
        ],
        "cases": cases_payload,
        "fixtures": [str(p) for p in eft_paths],
    }

    json_path = out_dir / f"acceptance-{args.label}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = out_dir / f"验收报告-{args.label}.md"
    report_path.write_text(render_report(payload, spec), encoding="utf-8")

    print(f"status   : {status}")
    print(f"counts   : {counts}")
    print(f"json     : {json_path}")
    print(f"report   : {report_path}")
    if not views_available:
        print(f"blocked  : {blocked_reason}")
        return 2
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
