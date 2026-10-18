"""T2 契约测试：P1 计算封装层（adapter / normalize / assumptions / scenarios）。

覆盖（对应任务书五条 + 两条实测基线）：

1. `Engine()` 构造两次不会二次加载 SDE（模块级标志 + 引擎自身的 `RuntimeError` 双重断言）；
2. `calculate` 的实例级 LRU（命中计数、`validate`/`sources` 不参与缓存、`clear_cache`）；
3. `normalize_fit` 的五种形态（eft / dna / esf / structured / killmail）；
4. `normalize_fit("这不是配装")` 返回错误而非抛异常；
5. `derive_skill_tier` 对上 T-12 后的实测基线：**512 个技能 = 510×4 + 2×5**（全部已发布
   技能 4 级 + 补最低）；不传索引时必须退化成旧档且名字可区分（不静默冒充）；
6. `default_assumptions` 与技能档一致、且各字段符合字段规格 §0.5；
7. `run_variants` 单 spec 返回 dict 且含 `"ship"`，第二次命中缓存（命中计数断言）：
8. 红线相关的反例：不支持的 `states` 键必须 `ValueError`，不再发明近似语义。

约束：**无网络、无随机性**（红线 N1）；样本来自 `noobbaozi/spike/kill.json`（只读，不修改）。
本文件只读契约与样本，不改任何被冻结的结构。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import esf_dogma_engine as dogma
import eveshipfit_sde
import pytest

from noobbaozi import contracts
from noobbaozi.config.paths import db_path
from noobbaozi.engine import adapter
from noobbaozi.engine.adapter import CACHE_MAX_ENTRIES, Engine
from noobbaozi.engine.assumptions import (
    DEFAULT_DAMAGE_PROFILE,
    DEFAULT_SKILL_LEVEL,
    MODULE_STATE_SOURCE,
    SKILL_TIER_NAME,
    SKILL_TIER_NAME_MIN_ONLY,
    default_assumptions,
    derive_skill_tier,
)
from noobbaozi.engine.normalize import STATE_WARNING, normalize_fit
from noobbaozi.engine.scenarios import build_variant, run_variants
from noobbaozi.sde.index import Index

# ---- 样本与常量 ----

EVE_ROOT = Path(__file__).resolve().parents[1]
KILL_PATH = EVE_ROOT / "spike" / "kill.json"

HECATE = 35683
DAMAGE_CONTROL_II = 2048
MICROWARPDRIVE = 35658
HECATE_DEFENSE_MODE = 35686

# 引擎派生属性（负 ID）。名字见 spike/smoke.py 的 NEG 表；红线 N12 要求不裸写。
ATTR_DPS_WITHOUT_RELOAD = -12
ATTR_EHP = -43

EFT_TEXT = (
    "[Hecate, 测试配装]\n"
    "Damage Control II\n"
    "\n"
    "[Empty High slot]\n"
    "\n"
    "5MN Quad LiF Restrained Microwarpdrive"
)

BASE_SPEC = [{"name": "base", "states": {"all": "keep"}, "mode_id": None}]


# ---- fixtures ----


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine()


@pytest.fixture(scope="module")
def sample_killmail() -> dict:
    """zkillboard 附加的 `zkb` 不是 ESI killmail 字段（spike/smoke.py 的丢弃做法）。"""
    raw = json.loads(KILL_PATH.read_text(encoding="utf-8"))[0]
    raw.pop("zkb", None)
    return raw


@pytest.fixture(scope="module")
def sample_fit(sample_killmail: dict) -> dict:
    return adapter.load_killmail(sample_killmail)


@pytest.fixture(scope="module")
def index() -> Iterator[Index]:
    """真实 SDE 索引（T-12 起技能档需要它取「全部技能」）；索引库没建就跳过。"""
    try:
        opened = Index.open(db_path())
    except FileNotFoundError as exc:
        pytest.skip(f"索引库未构建：{exc}")
    try:
        yield opened
    finally:
        opened.close()


def _attr_value(calculation: dict, attribute_id: int) -> float:
    raw = calculation["ship"]["attributes"][attribute_id]
    return float(raw["value"] if isinstance(raw, dict) else raw)


# ---- 1. SDE 只加载一次 ----


def test_engine_constructs_twice_without_reloading_sde() -> None:
    first = Engine()
    assert adapter.sde_loaded() is True
    loads_after_first = adapter.sde_load_count()
    assert loads_after_first <= 1, "首次构造最多真实加载一次"

    second = Engine()

    assert adapter.sde_load_count() == loads_after_first, "第二次构造不得再次加载 SDE"
    assert id(first) != id(second), "两次构造应得到不同实例"
    assert first.sde_build() == second.sde_build()
    assert first.sde_build() == int(eveshipfit_sde.build_number())


def test_engine_cannot_double_load_sde() -> None:
    # 引擎自身对二次加载是硬拒绝的：我们的守卫必须先挡住，否则这里会炸出 RuntimeError
    Engine()
    with pytest.raises(RuntimeError):
        dogma.load_sde_from_file(eveshipfit_sde.sde_path())


def test_meta_reports_version_triple(engine: Engine) -> None:
    meta = engine.meta()
    assert tuple(meta) == ("sde_build", "engine_version", "spec_version")
    assert meta["engine_version"] == contracts.ENGINE_VERSION == "13.6.0"
    assert meta["spec_version"] == contracts.SPEC_VERSION == "v1"
    assert meta["sde_build"] == engine.sde_build()


# ---- 2. 计算、校验、格式互转、缓存 ----


def test_validate_returns_violation_list(engine: Engine, sample_fit: dict) -> None:
    violations = engine.validate(sample_fit)
    assert isinstance(violations, list)
    assert violations, "空技能集的样本配装必然有技能类违规"
    assert all(isinstance(violation, dict) and "rule" in violation for violation in violations)


def test_calculate_cache_hits_and_clear(engine: Engine, sample_fit: dict) -> None:
    engine.clear_cache()
    assert engine.cache_stats() == {
        "entries": 0,
        "hits": 0,
        "misses": 0,
        "capacity": CACHE_MAX_ENTRIES,
    }

    first = engine.calculate(sample_fit)
    assert engine.cache_stats()["misses"] == 1
    assert engine.cache_stats()["entries"] == 1

    second = engine.calculate(sample_fit)
    assert engine.cache_stats()["hits"] == 1
    assert second is first, "命中缓存应返回同一对象"

    # validate / sources 的结果形状不同，不参与缓存
    engine.calculate(sample_fit, validate=True)
    engine.calculate(sample_fit, sources=True)
    assert engine.cache_stats()["entries"] == 1

    engine.clear_cache()
    assert engine.cache_stats()["entries"] == 0


def test_save_eft_load_eft_roundtrip(engine: Engine, sample_fit: dict) -> None:
    eft = engine.save_eft(sample_fit)
    assert eft.startswith("[")

    again = engine.load_eft(eft)
    assert again["ship"]["type_id"] == sample_fit["ship"]["type_id"]
    # 顺序可能按槽位重排，所以比较 type_id 多重集（P1 的往返无损口径）
    assert sorted(item["type_id"] for item in again["items"]) == sorted(
        item["type_id"] for item in sample_fit["items"]
    )


# ---- 3. 归一化：成功路径 ----


def test_normalize_eft_success(engine: Engine, sample_fit: dict) -> None:
    eft = engine.save_eft(sample_fit)
    result = normalize_fit(eft)

    assert tuple(result) == ("fit", "source_format", "errors", "warnings")
    assert result["source_format"] == "eft"
    assert result["errors"] == []
    assert result["fit"] is not None
    assert result["fit"]["ship"]["type_id"] == HECATE
    assert STATE_WARNING in result["warnings"]
    assert len(result["fit"]["items"]) == len(sample_fit["items"])


def test_normalize_eft_minimal_text() -> None:
    result = normalize_fit(EFT_TEXT)
    assert result["source_format"] == "eft"
    assert result["errors"] == []
    assert result["fit"] is not None
    assert result["fit"]["ship"]["type_id"] == HECATE
    assert [item["type_id"] for item in result["fit"]["items"]] == [
        DAMAGE_CONTROL_II,
        MICROWARPDRIVE,
    ]


def test_normalize_dna() -> None:
    for text in (f"{HECATE}:{DAMAGE_CONTROL_II};1::", f"fitting:{HECATE}:{DAMAGE_CONTROL_II};1::"):
        result = normalize_fit(text)
        assert result["source_format"] == "dna", text
        assert result["errors"] == [], text
        assert result["fit"]["ship"]["type_id"] == HECATE
        assert [item["type_id"] for item in result["fit"]["items"]] == [DAMAGE_CONTROL_II]


def test_normalize_esf(sample_fit: dict) -> None:
    result = normalize_fit(dogma.save_esf(sample_fit))
    assert result["source_format"] == "esf"
    assert result["errors"] == []
    assert result["fit"]["ship"]["type_id"] == HECATE


def test_normalize_structured_json() -> None:
    fit = {
        "ship": {"type_id": HECATE},
        "items": [
            {"type_id": DAMAGE_CONTROL_II, "slot": {"type": "low", "index": 0}, "state": "active"}
        ],
    }
    result = normalize_fit(json.dumps(fit))
    assert result["source_format"] == "structured"
    assert result["errors"] == []
    assert result["fit"] == fit
    # 输入自带 state，就不需要「状态是推定的」这条提示
    assert result["warnings"] == []


def test_normalize_structured_json_without_states_warns() -> None:
    fit = {"ship": {"type_id": HECATE}, "items": [{"type_id": DAMAGE_CONTROL_II}]}
    result = normalize_fit(json.dumps(fit))
    assert result["source_format"] == "structured"
    assert STATE_WARNING in result["warnings"]


def test_normalize_killmail_json_tolerates_zkb() -> None:
    # 直接吃原始 zkillboard 载荷（带 zkb 字段），引擎实测会忽略它
    raw = json.loads(KILL_PATH.read_text(encoding="utf-8"))[0]
    assert "zkb" in raw

    result = normalize_fit(json.dumps(raw))
    assert result["source_format"] == "killmail"
    assert result["errors"] == []
    assert result["fit"]["ship"]["type_id"] == HECATE


def test_normalize_esi_fitting_json() -> None:
    fitting = {
        "name": "esi",
        "ship_type_id": HECATE,
        "items": [{"flag": "LoSlot0", "quantity": 1, "type_id": DAMAGE_CONTROL_II}],
    }
    result = normalize_fit(json.dumps(fitting))
    assert result["source_format"] == "esi_fitting"
    assert result["errors"] == []
    assert result["fit"]["ship"]["type_id"] == HECATE


# ---- 4. 归一化：失败路径（不抛异常，产出可修复错误）----


def test_normalize_garbage_returns_error_not_exception() -> None:
    result = normalize_fit("这不是配装")

    assert result["fit"] is None
    assert result["source_format"] == "unknown"
    assert len(result["errors"]) == 1

    error = result["errors"][0]
    assert error["raw"] == "这不是配装", "错误必须带原文，便于用户修"
    assert error["line"] is None
    assert error["failed_at"] == "format"
    assert error["candidates"] == []
    assert error["reason"] in {"unknown", "malformed"}


def test_normalize_empty_and_non_string_input() -> None:
    assert normalize_fit("   ")["errors"][0]["failed_at"] == "format"
    non_string = normalize_fit(None)  # type: ignore[arg-type]
    assert non_string["fit"] is None
    assert non_string["errors"]


def test_normalize_eft_prescan_reports_line_and_raw() -> None:
    text = "[Hecate, t]\nDamage Control II\n[]"
    result = normalize_fit(text)

    assert result["fit"] is None
    assert result["source_format"] == "eft"
    error = result["errors"][0]
    assert error["line"] == 3
    assert error["raw"] == "[]"
    assert error["failed_at"] == "module_name"
    assert error["reason"] == "malformed"


def test_normalize_module_line_without_header_is_unknown_format() -> None:
    # 形态判定要求 EFT 以 `[船名, 配装名]` 开头；裸模块行判定依据不足 → unknown（不猜）
    result = normalize_fit("Damage Control II\n[Hecate, t]")
    assert result["fit"] is None
    assert result["source_format"] == "unknown"
    assert result["errors"][0]["failed_at"] == "format"
    assert result["errors"][0]["raw"] == "Damage Control II"


def test_normalize_engine_value_error_becomes_structured_error() -> None:
    # 预扫描认得这条模块行，是引擎不认这个名字 → 错误必须来自引擎且带原始消息
    result = normalize_fit("[Hecate, t]\n这不是模块 II")
    assert result["fit"] is None
    assert result["source_format"] == "eft"
    assert result["errors"][0]["failed_at"] == "module_name"
    assert result["errors"][0]["raw"]


def test_normalize_offline_suffix_surfaces_as_unknown_module() -> None:
    # 已知边界：引擎 EFT 解析器不认 `Offline` 后缀（实测 `unknown type ... Offline`）。
    # 预扫描认得这条形态，于是错误以「模块名未知」给出，而不是把 offline 静默当成在线。
    result = normalize_fit("[Hecate, t]\nDamage Control II Offline")
    assert result["fit"] is None
    assert result["errors"][0]["failed_at"] == "module_name"
    assert result["errors"][0]["reason"] == "unknown"


# ---- 5. 技能档基线（T-12 修正后重标）----
#
# 主人的约定是「**全部技能 4 级** + 补最低要求」，不是「只补被模块要求的」。
# 实测（SDE build 3607705 / 引擎 13.6.0）：全部已发布技能 512 个 → 510×4 + 2×5。
# 判别特征「技能总数 > 100」是「全部技能 4 级」的直接指纹（旧档只有 19 个）。

#: 全部已发布技能的实测条数（SDE build 3607705）；SDE 更新后用实测值重标。
ALL_SKILL_COUNT = 512
#: 最低要求为 5 级的技能（type_id 锁死，不放宽成「任意两个 5 级」）。
SKILLS_REQUIRED_5 = frozenset({3301, 33093})


def test_skills_index_extension_returns_sorted_published_skills(index: Index) -> None:
    """`Index.skills()` 是契约外扩展：升序、条数 > 100、每条都是已发布类型。"""
    skills = index.skills()

    assert skills == sorted(skills), "必须按 type_id 升序（稳定可复现）"
    assert len(skills) > 100, f"全部技能应远多于 100 个，实际 {len(skills)}"
    assert len(set(skills)) == len(skills), "不得有重复 type_id"
    # 只返回 published=1 的技能（未发布不是玩家技能）
    assert all(index.get_type(type_id)["published"] for type_id in skills)


def test_derive_skill_tier_matches_measured_baseline(
    engine: Engine, sample_fit: dict, index: Index
) -> None:
    tier = derive_skill_tier(sample_fit, engine, index=index)

    # 「全部技能 4 级」的判别特征：技能总数必须 > 100（旧档只有 19 个）
    assert len(tier) > 100, f"全部技能 4 级应覆盖上百个技能，实际 {len(tier)}：{tier}"
    assert len(tier) == ALL_SKILL_COUNT, f"实测基线是 {ALL_SKILL_COUNT} 个技能，实际 {len(tier)}"
    assert Counter(tier.values()) == {DEFAULT_SKILL_LEVEL: ALL_SKILL_COUNT - 2, 5: 2}, (
        f"档位分布必须是 510×4 + 2×5，实际 {Counter(tier.values())}"
    )
    assert {type_id for type_id, level in tier.items() if level == 5} == SKILLS_REQUIRED_5
    # 基线集合就是索引里的全部技能（不是「被要求的那些」）
    assert set(tier) == set(index.skills())


def test_derive_skill_tier_without_index_degrades_but_is_labelled(
    engine: Engine, sample_fit: dict, index: Index
) -> None:
    """没传索引 → 旧行为（只补最低要求），档位名字必须能区分（红线 N4）。"""
    degraded = derive_skill_tier(sample_fit, engine)
    assert len(degraded) == 19, f"旧行为是 19 个技能，实际 {len(degraded)}"
    assert set(degraded) < set(index.skills()), "旧行为只是全部技能的一个小子集"

    old_asm = default_assumptions(sample_fit, engine)
    new_asm = default_assumptions(sample_fit, engine, index=index)
    assert old_asm["skill_tier_name"] == SKILL_TIER_NAME_MIN_ONLY == "min_required_only"
    assert new_asm["skill_tier_name"] == SKILL_TIER_NAME == "all4+min_required"
    assert old_asm["skill_tier"] == degraded
    assert new_asm["skill_tier"] != degraded, "两种档位不得静默相同"


def test_derive_skill_tier_does_not_mutate_fit(
    engine: Engine, sample_fit: dict, index: Index
) -> None:
    before = json.dumps(sample_fit, sort_keys=True)
    derive_skill_tier(sample_fit, engine, index=index)
    assert json.dumps(sample_fit, sort_keys=True) == before


class _RunawayEngine:
    """每轮都要求一个新技能 → 永不收敛。用来验证不会静默返回半成品。"""

    def __init__(self) -> None:
        self.calls = 0

    def validate(self, fit: dict) -> list[dict]:
        self.calls += 1
        return [{"rule": {"type": "skill", "type_id": 10000 + self.calls, "required": 4}}]


class _NonSkillEngine:
    """只有非技能类违规 → 应当立即收敛成空技能表。"""

    def validate(self, fit: dict) -> list[dict]:
        return [{"rule": {"type": "slot", "message": "no such slot"}}]


class _MalformedSkillRuleEngine:
    """技能类违规缺少 required → 结构不符预期，必须炸出来而不是猜。"""

    def validate(self, fit: dict) -> list[dict]:
        return [{"rule": {"type": "skill", "type_id": 3300}}]


def test_derive_skill_tier_raises_when_not_converged() -> None:
    fake = _RunawayEngine()
    with pytest.raises(RuntimeError, match="未收敛"):
        derive_skill_tier({"ship": {"type_id": HECATE}, "items": []}, fake)
    assert fake.calls <= 8


def test_derive_skill_tier_converges_on_non_skill_violations() -> None:
    assert derive_skill_tier({"ship": {"type_id": HECATE}, "items": []}, _NonSkillEngine()) == {}


def test_derive_skill_tier_rejects_malformed_skill_rule() -> None:
    with pytest.raises(RuntimeError, match="结构不符合预期"):
        derive_skill_tier({"ship": {"type_id": HECATE}, "items": []}, _MalformedSkillRuleEngine())


# ---- 6. 默认假设 ----


def test_default_assumptions_matches_skill_tier(
    engine: Engine, sample_fit: dict, index: Index
) -> None:
    assumptions = default_assumptions(sample_fit, engine, index=index)
    tier = derive_skill_tier(sample_fit, engine, index=index)

    assert tuple(assumptions) == (
        "skill_tier",
        "skill_tier_name",
        "module_states",
        "mode_id",
        "damage_profile",
        "ammo_loaded",
        "implants",
    )
    assert assumptions["skill_tier"] == tier
    assert assumptions["skill_tier_name"] == SKILL_TIER_NAME == "all4+min_required"
    assert assumptions["mode_id"] is None, "样本配装没有模式"
    assert assumptions["ammo_loaded"] is True
    assert assumptions["implants"] == "none"

    assert set(assumptions["module_states"]) == {"high", "medium", "low", "rig"}
    assert set(assumptions["module_states"].values()) == {MODULE_STATE_SOURCE}

    assert set(assumptions["damage_profile"]) == set(DEFAULT_DAMAGE_PROFILE)
    for element, weight in DEFAULT_DAMAGE_PROFILE.items():
        assert math.isclose(
            assumptions["damage_profile"][element], weight, rel_tol=1e-9, abs_tol=1e-12
        )


def test_default_assumptions_returns_detached_damage_profile(engine: Engine, sample_fit: dict) -> None:
    first = default_assumptions(sample_fit, engine)
    first["damage_profile"]["em"] = 1.0

    second = default_assumptions(sample_fit, engine)
    assert math.isclose(second["damage_profile"]["em"], DEFAULT_DAMAGE_PROFILE["em"], rel_tol=1e-9)


# ---- 7. 变体运行 + 缓存复用 ----


def test_run_variants_base_returns_dict_with_ship(engine: Engine, sample_fit: dict) -> None:
    results = run_variants(sample_fit, BASE_SPEC, engine)

    assert isinstance(results, dict)
    assert list(results) == ["base"]
    calculation = results["base"]
    assert isinstance(calculation, dict)
    assert "ship" in calculation
    assert "items" in calculation
    assert calculation["ship"] is not None


def test_build_variant_keep_and_all_states(sample_fit: dict) -> None:
    kept = build_variant(sample_fit, BASE_SPEC[0])
    assert [item.get("state") for item in kept["items"]] == [
        item.get("state") for item in sample_fit["items"]
    ]

    online = build_variant(sample_fit, {"name": "online", "states": {"all": "online"}, "mode_id": None})
    assert {item["state"] for item in online["items"]} == {"online"}
    # 变体不得到改动入参
    assert {item.get("state") for item in sample_fit["items"]} != {"online"}


def test_run_variants_second_call_hits_engine_cache(engine: Engine, sample_fit: dict) -> None:
    engine.clear_cache()

    first = run_variants(sample_fit, BASE_SPEC, engine)
    after_first = engine.cache_stats()
    second = run_variants(sample_fit, BASE_SPEC, engine)
    after_second = engine.cache_stats()

    assert first == second
    assert first["base"] is second["base"], "第二次必须直接命中 Engine 的 LRU"
    assert after_first["misses"] == 1
    assert after_first["hits"] == 0
    assert after_second["hits"] == 1
    assert after_second["entries"] == after_first["entries"]


def test_run_variants_applies_state_to_all_items(engine: Engine, sample_fit: dict) -> None:
    before = json.dumps(sample_fit, sort_keys=True)

    results = run_variants(
        sample_fit, [{"name": "all_offline", "states": {"all": "offline"}, "mode_id": None}], engine
    )

    assert json.dumps(sample_fit, sort_keys=True) == before, "不得改动入参 fit"
    # 全部 offline 时武器不工作 → DPS 归零（PROGRESS §3 第 9 条：state 是数量级差异）
    assert math.isclose(
        _attr_value(results["all_offline"], ATTR_DPS_WITHOUT_RELOAD), 0.0, abs_tol=1e-9
    )


def test_run_variants_applies_mode(engine: Engine, sample_fit: dict) -> None:
    results = run_variants(
        sample_fit,
        [
            {"name": "base", "states": {"all": "keep"}, "mode_id": None},
            {"name": "defense", "states": {"all": "keep"}, "mode_id": HECATE_DEFENSE_MODE},
        ],
        engine,
    )

    base_ehp = _attr_value(results["base"], ATTR_EHP)
    defense_ehp = _attr_value(results["defense"], ATTR_EHP)
    # Defense 模式 EHP +38%（PROGRESS §3 第 14 条），这里只锁方向，不锁快照数值
    assert defense_ehp > base_ehp


def test_run_variants_rejects_unsupported_state_key(engine: Engine, sample_fit: dict) -> None:
    with pytest.raises(ValueError, match="不支持的 states 键"):
        run_variants(
            sample_fit,
            [{"name": "weapons_only", "states": {"high": "only_weapons"}, "mode_id": None}],
            engine,
        )


def test_run_variants_rejects_illegal_state_value(engine: Engine, sample_fit: dict) -> None:
    with pytest.raises(ValueError, match="非法的 state"):
        run_variants(
            sample_fit, [{"name": "med", "states": {"all": "med"}, "mode_id": None}], engine
        )


def test_run_variants_rejects_duplicate_names(engine: Engine, sample_fit: dict) -> None:
    with pytest.raises(ValueError, match="重复的 ScenarioSpec.name"):
        run_variants(sample_fit, [BASE_SPEC[0], BASE_SPEC[0]], engine)


# ---- 8. candidates 接线（T-04 契约漂移修复）----------------------------------
#
# 契约：`NormalizeError.candidates` 是**可修复建议**，不是自动替换结果（红线 N2）。
# 未注入索引 → 恒空列表；注入后只对"模块名"失败行给候选；船名行 / 格式行不给。

from noobbaozi.engine import normalize  # noqa: E402

#: 截断式写错（少一个 e）：真实类型名见 TYPO_MODULE_REAL。
TYPO_MODULE_TEXT = "5MN Quad LiF Restrained Microwarpdriv"
TYPO_MODULE_REAL = "5MN Quad LiF Restrained Microwarpdrive"


class _StubIndex:
    """鸭子类型的索引替身：只实现 `search_types`，记录调用参数。"""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.calls: list[tuple[str, int]] = []

    def search_types(self, query: str, limit: int = 10) -> list[dict]:
        self.calls.append((query, limit))
        return [{"name": name} for name in self.names[:limit]]


class _BrokenIndex:
    """`search_types` 会炸的替身：候选增强不得让归一化失败。"""

    def search_types(self, query: str, limit: int = 10) -> list[dict]:
        raise RuntimeError("索引库已关闭")


@pytest.fixture(autouse=False)
def no_index() -> Iterator[None]:
    """保证每个用例从"未注入索引"的干净状态开始，并在结束后复位。"""
    normalize.clear_index()
    try:
        yield
    finally:
        normalize.clear_index()


def test_candidates_are_empty_without_index(no_index: None) -> None:
    result = normalize_fit(f"[Hecate, t]\n{TYPO_MODULE_TEXT}")
    assert result["fit"] is None
    assert result["source_format"] == "eft"
    error = result["errors"][0]
    assert error["failed_at"] == "module_name"
    assert error["reason"] == "unknown"
    assert error["candidates"] == [], "未注入索引时不得编造候选"


def test_candidates_are_filled_from_injected_index(no_index: None) -> None:
    stub = _StubIndex([TYPO_MODULE_REAL, "5MN Cold-Gas Enduring Microwarpdrive"])
    normalize.set_index(stub)
    try:
        result = normalize_fit(f"[Hecate, t]\n{TYPO_MODULE_TEXT}")
    finally:
        normalize.clear_index()

    assert result["fit"] is None, "候选只是建议，不得代填成成功结果"
    error = result["errors"][0]
    assert error["candidates"] == [
        TYPO_MODULE_REAL,
        "5MN Cold-Gas Enduring Microwarpdrive",
    ]
    # 查询用的是失败行的原文（用户写的那个名字），limit=3（模块常量 CANDIDATE_LIMIT）
    assert stub.calls == [(TYPO_MODULE_TEXT, normalize.CANDIDATE_LIMIT)]


def test_single_candidate_is_not_auto_replaced(no_index: None) -> None:
    # 红线 N2：哪怕索引只给回一个候选，也只填 candidates，不替用户决定
    stub = _StubIndex([TYPO_MODULE_REAL])
    normalize.set_index(stub)
    try:
        result = normalize_fit(f"[Hecate, t]\n{TYPO_MODULE_TEXT}")
    finally:
        normalize.clear_index()

    assert result["fit"] is None
    assert len(result["errors"][0]["candidates"]) == 1
    assert result["errors"][0]["candidates"][0] != TYPO_MODULE_TEXT


def test_bad_ship_line_gets_no_candidates(no_index: None) -> None:
    # 引擎把"船名未知"也报成 `unknown type X`；本层必须认回船名行并保持候选为空
    stub = _StubIndex(["Hecate"])
    normalize.set_index(stub)
    try:
        result = normalize_fit("[NoSuchShipXyz, t]\nDamage Control II")
    finally:
        normalize.clear_index()

    # 船名行：候选为空，且不应因"缺失索引"而失败
    assert result["errors"][0]["failed_at"] == "ship_name"
    assert result["errors"][0]["candidates"] == []


def test_malformed_format_line_gets_no_candidates(no_index: None) -> None:
    stub = _StubIndex(["Damage Control II"])
    normalize.set_index(stub)
    try:
        result = normalize_fit("[Hecate, t]\n[]")
    finally:
        normalize.clear_index()

    assert [error["candidates"] for error in result["errors"]] == [[]]


def test_broken_index_does_not_break_normalize(no_index: None) -> None:
    normalize.set_index(_BrokenIndex())
    try:
        result = normalize_fit(f"[Hecate, t]\n{TYPO_MODULE_TEXT}")
    finally:
        normalize.clear_index()

    assert result["source_format"] == "eft"
    assert result["errors"][0]["failed_at"] == "module_name"
    assert result["errors"][0]["candidates"] == []


def test_set_index_rejects_object_without_search_types(no_index: None) -> None:
    with pytest.raises(TypeError, match="search_types"):
        normalize.set_index(object())  # type: ignore[arg-type]


def test_clear_index_is_idempotent(no_index: None) -> None:
    normalize.clear_index()
    normalize.clear_index()
    assert normalize_fit(f"[Hecate, t]\n{TYPO_MODULE_TEXT}")["errors"][0]["candidates"] == []


def test_candidates_end_to_end_with_real_index(no_index: None) -> None:
    """真实索引接线（契约 `Index.search_types`）：截断的模块名应给出唯一候选。"""
    from noobbaozi.config.paths import db_path
    from noobbaozi.sde.index import Index

    try:
        index = Index.open(db_path())
    except FileNotFoundError as exc:  # 索引库没建：跳过而不是失败
        pytest.skip(f"索引库未构建：{exc}")

    normalize.set_index(index)
    try:
        result = normalize_fit(f"[Hecate, t]\n{TYPO_MODULE_TEXT}")
    finally:
        normalize.clear_index()
        index.close()

    error = result["errors"][0]
    assert error["failed_at"] == "module_name"
    assert TYPO_MODULE_REAL in error["candidates"], error
    assert result["fit"] is None


# =============================================================================
# 十一、killmail 的「舱内物品」提示（T-22 / 字段规格 §0.6）
# =============================================================================
#
# 背景（PROGRESS §3 第 137 条，实测）：`load_killmail` 只还原**槽位 flag** 上的物品，
# f4（Loki）的 `Loki Defensive - Covert Reconfiguration`（type 45595）躺在
# **flag 177（SubSystemBay 子系统舱）** 里 → 被丢掉了，而它是唯一能免掉 covert cloak
# 那 10000 CPU 罚值的子系统。丢掉它之后 `resources` 把「真实不可能在线」的 cloak
# 当实占用（`cpu.load 10572.1` vs 真实可飞 472.1）。
#
# 本任务只做**提示**：还原结果一个字不改（那件装备仍然不装）。

ACCEPTANCE_FIXTURES = EVE_ROOT / "acceptance" / "fixtures"
T3C_KILLMAIL_PATH = ACCEPTANCE_FIXTURES / "f4-non-hecate.json"
MISSILE_KILLMAIL_PATH = ACCEPTANCE_FIXTURES / "f5-no-mwd.json"

SUBSYSTEM_BAY_FLAG = 177
"""`SubSystemBay` —— 子系统**存放**舱（不是已装槽位 125–128）。"""

BAY_ITEM_TYPE_ID = 45595
"""f4 舱内那件 `Loki Defensive - Covert Reconfiguration`。"""

BAY_WARNING_PREFIX = "子系统舱内另有 1 件可用模块（flag 177 等不在已装槽位），其效果未计入："


def _killmail_payload(path: Path) -> str:
    """读验收 killmail 样本成**原始文本**（走 `normalize_fit` 的 JSON 分支）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = data[0]
    data.pop("zkb", None)
    return json.dumps(data, ensure_ascii=False)


def _bay_flags(payload: str) -> list[int]:
    """原始 killmail 里所有**非槽位 flag**（用于证明「提示的前提真的存在」）。"""
    data = json.loads(payload)
    flags = sorted(
        {
            item["flag"]
            for item in data["victim"]["items"]
            if isinstance(item.get("flag"), int) and item["flag"] not in normalize.SLOT_FLAGS
        }
    )
    return flags


def test_killmail_with_subsystem_bay_item_warns(no_index: None) -> None:
    """f4：舱内有 1 件子系统模块 → warnings 必须显式说出来（不得静默丢弃）。"""
    payload = _killmail_payload(T3C_KILLMAIL_PATH)

    # 前提自检：这条样本真的有舱内物品，且落在这个用例声称的 flag 上
    assert _bay_flags(payload) == [SUBSYSTEM_BAY_FLAG]

    result = normalize_fit(payload)

    assert result["errors"] == []
    assert result["source_format"] == "killmail"
    assert STATE_WARNING in result["warnings"]
    bay_warnings = [
        warning for warning in result["warnings"] if warning.startswith(BAY_WARNING_PREFIX)
    ]
    assert len(bay_warnings) == 1, result["warnings"]
    # 没有索引时不编造名字：退回 `type_id N`
    assert bay_warnings[0] == f"{BAY_WARNING_PREFIX}type_id {BAY_ITEM_TYPE_ID}"

    # 反面：**还原结果一个字不改**（那件装备仍然不装，引擎行为没被这层碰过）
    fitted_types = [item.get("type_id") for item in result["fit"]["items"]]
    assert BAY_ITEM_TYPE_ID not in fitted_types
    assert len(fitted_types) == len(adapter.load_killmail(json.loads(payload))["items"])


def test_bay_warning_names_follow_injected_index() -> None:
    """注入索引后提示里给**名字**（不猜）；索引不可用时退回 `type_id N`。"""

    class _NamingIndex:
        """鸭子类型索引：`get_type` 给名字、`search_types` 满足 `set_index()` 的接口检查。"""

        def search_types(self, query: str, limit: int = 10) -> list[dict]:  # noqa: ARG002 - 桩
            return []

        def get_type(self, type_id: int) -> dict:
            assert type_id == BAY_ITEM_TYPE_ID
            return {"type_id": type_id, "name": "Loki Defensive - Covert Reconfiguration"}

    class _BrokenIndex:
        def search_types(self, query: str, limit: int = 10) -> list[dict]:  # noqa: ARG002 - 桩
            raise RuntimeError("索引库已关闭")

        def get_type(self, type_id: int) -> dict:  # noqa: ARG002 - 桩
            raise RuntimeError("索引库已关闭")

    payload = _killmail_payload(T3C_KILLMAIL_PATH)

    normalize.clear_index()
    normalize.set_index(_NamingIndex())
    try:
        named = normalize_fit(payload)
    finally:
        normalize.clear_index()

    bay = [warning for warning in named["warnings"] if warning.startswith(BAY_WARNING_PREFIX)]
    assert bay and bay[0].endswith("Loki Defensive - Covert Reconfiguration"), named["warnings"]

    normalize.set_index(_BrokenIndex())
    try:
        broken = normalize_fit(payload)
    finally:
        normalize.clear_index()

    # 名字查询失败不得让归一化失败，也不得编造名字
    assert broken["errors"] == []
    assert [w for w in broken["warnings"] if w.startswith(BAY_WARNING_PREFIX)] == [
        f"{BAY_WARNING_PREFIX}type_id {BAY_ITEM_TYPE_ID}"
    ]


def test_killmail_without_bay_items_has_no_bay_warning(no_index: None) -> None:
    """f5：所有物品都在已装槽位 → 不得凭空多一条「舱内」提示。"""
    payload = _killmail_payload(MISSILE_KILLMAIL_PATH)
    assert _bay_flags(payload) == []

    result = normalize_fit(payload)

    assert result["source_format"] == "killmail"
    assert result["warnings"] == [STATE_WARNING]


def test_bay_warning_is_not_a_parse_error(no_index: None) -> None:
    """提示只是提示：`errors` 必须为空、`fit` 必须有值（不是失败路径）。"""
    result = normalize_fit(_killmail_payload(T3C_KILLMAIL_PATH))

    assert result["errors"] == []
    assert result["fit"] is not None
    assert result["fit"]["ship"]["type_id"] is not None


def test_structured_fit_gets_no_bay_warning(no_index: None) -> None:
    """structured fit 没有 killmail 的 flag 结构 → 不加舱内提示（口径只对 killmail 成立）。"""
    fit = {"ship": {"type_id": HECATE}, "items": [{"type_id": DAMAGE_CONTROL_II}]}
    result = normalize_fit(json.dumps(fit))

    assert result["source_format"] == "structured"
    assert all("舱内" not in warning for warning in result["warnings"])
