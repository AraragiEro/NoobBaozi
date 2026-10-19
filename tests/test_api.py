"""L5 接口层（`noobbaozi/api.py`）的契约测试 —— 只测**契约**，不重算任何数值。

钉住的六件事（对应任务书与文档 §10）：

1. **三种输入形态结果一致**：同一套配装的 EFT / DNA / killmail 三种写法，`fit_index`
   的 `ship` / `ehp_mixed` / `speed_mps` / `align_time_s` **逐位相同**；
2. **技能档真的生效**：`all4+min_required` 与 `all5` 的 `ehp_mixed` 不同，
   `meta.skill_tier` 回显**实际档名与技能数**（技能数不是硬编码，而是与
   `Index.skills()` 对数）；
3. **错误契约**：失败一律结构化（`ok:false` + `error.kind`），坏 EFT 带 `line`、
   写错的模块名带 `candidates`、不存在的路径带 `hint`；
4. **缓存**：同一输入第二次调用**一次引擎都不碰**（计数桩），且只缓存成功结果；
5. **联网失败不抛异常**：`network_failed`（用替身把 `urlopen` 打爆，不真的联网）；
6. **断言不是永真**：把 `fit_index` 的返回键删掉一个，一致性断言必须变红。

第 6 条是 PROGRESS §3 第 196 条的做法：每条关键断言都要能被一次故意篡改打红。
"""

from __future__ import annotations

import copy
import json
import statistics
import time
import urllib.error
from pathlib import Path

import pytest

from noobbaozi import api

NOOBBAOZI_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = NOOBBAOZI_ROOT / "acceptance" / "fixtures"
EFT_PATH = FIXTURES_DIR / "f1-hecate-killmail.eft"
KILL_PATH = NOOBBAOZI_ROOT / "spike" / "kill.json"
EMPTY_FIT_PATH = FIXTURES_DIR / "f58-empty-fit.eft"
"""0 模块的样本：`stability.cap_depletes_in_s` 在它上面**合法地**是 `None`。"""


# =============================================================================
# fixtures / 小工具
# =============================================================================


@pytest.fixture(scope="module", autouse=True)
def _singletons() -> None:
    """整个模块共用一个 `Engine` / `Index`（进程级单例）；建不起索引就跳过。"""
    try:
        api.get_engine()
        api.get_index()
    except Exception as exc:  # noqa: BLE001 - 索引是构建产物，缺了就跳过
        pytest.skip(f"引擎 / 索引不可用：{exc}")


@pytest.fixture(scope="module")
def eft_text() -> str:
    return EFT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def killmail_text() -> str:
    """kill.json 原文：一个含单条 killmail 的 **JSON 数组文本**（走「JSON 字符串」入口）。"""
    return KILL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dna_text(eft_text: str) -> str:
    """把 EFT 转成**同一套配装**的 DNA 字符串（`<船>:<模块>;<数量>:…::`）。

    引擎没有 `save_dna`，所以按 DNA 语法从归一化结果拼：只拼**已装模块**（货舱里的
    弹药 / 探针不在 DNA 里），槽位顺序 = 低 / 中 / 高 / 改装 —— DNA 本身不带槽位信息，
    槽位由引擎按模块类型重新分配（实测回填结果与 EFT 逐件相同，
    `test_parse_three_forms_agree()` 会核对这一点）。
    """
    parsed = api.parse_fit(eft_text)
    assert parsed["ok"] is True, parsed
    fit = parsed["result"]["fit"]
    order = {"low": 0, "medium": 1, "high": 2, "rig": 3, "subsystem": 4}
    rows = [item for item in fit["items"] if item["slot"]["type"] != "cargo"]
    rows.sort(
        key=lambda item: (
            order.get(item["slot"]["type"], 99),
            item["slot"].get("index") or 0,
        )
    )
    body = ":".join(f"{item['type_id']};{item.get('quantity') or 1}" for item in rows)
    return f"{fit['ship']['type_id']}:{body}::"


def _core(envelope: dict) -> dict:
    """从 `fit_index` 的返回里取**被比较的核**（缺键会 `KeyError`，不静默跳过）。"""
    result = envelope["result"]
    return {
        "ship": result["ship"],
        "ehp_mixed": result["survivability"]["ehp_mixed"],
        "speed_mps": result["mobility"]["speed_mps"],
        "align_time_s": result["mobility"]["align_time_s"],
        "propulsion_state": result["mobility"]["propulsion_state"],
    }


def _assert_core_equal(*envelopes: dict) -> None:
    """多份 `fit_index` 结果的核必须逐位相同（`==` 对 float 就是逐位比较）。"""
    cores = [_core(envelope) for envelope in envelopes]
    first = cores[0]
    for other in cores[1:]:
        assert other == first, f"同一套配装的不同输入形态结果不一致：{other} != {first}"


def _fitted_set(envelope: dict) -> set[tuple]:
    """`parse_fit` 结果里的已装模块集合（`(type_id, 槽位, 槽位序号)`）。"""
    result = envelope["result"]
    return {
        (item["type_id"], item["slot"]["type"], item["slot"].get("index"))
        for item in result["fit"]["items"]
        if item["slot"]["type"] != "cargo"
    }


# =============================================================================
# 一、输入三形态：都能解析，而且是同一套配装
# =============================================================================


def test_parse_three_forms_agree(eft_text: str, dna_text: str, killmail_text: str) -> None:
    """EFT / DNA / killmail 三种写法归一出**同一套已装模块**（含槽位）。"""
    parsed = {
        "eft": api.parse_fit(eft_text),
        "dna": api.parse_fit(dna_text),
        "killmail": api.parse_fit(killmail_text),
    }
    for name, envelope in parsed.items():
        assert envelope["ok"] is True, f"{name} 解析失败：{envelope}"
        assert envelope["result"]["ship"]["type_id"] == 35683  # Hecate（三种写法同一艘船）

    assert parsed["eft"]["result"]["source_format"] == "eft"
    assert parsed["dna"]["result"]["source_format"] == "dna"
    assert parsed["killmail"]["result"]["source_format"] == "killmail"

    fitted = {name: _fitted_set(envelope) for name, envelope in parsed.items()}
    assert fitted["eft"] == fitted["dna"] == fitted["killmail"], (
        f"三种写法不是同一套配装：eft-dna={fitted['eft'] ^ fitted['dna']}；"
        f"eft-killmail={fitted['eft'] ^ fitted['killmail']}"
    )
    assert len(fitted["eft"]) == 17  # 4 low + 4 medium + 6 high + 3 rig
    # 件数口径（`fitted` 不含货舱）必须与 `parse_fit` 自报的一致
    assert parsed["eft"]["result"]["modules"]["fitted"] == 17


def test_index_three_forms_identical(eft_text: str, dna_text: str, killmail_text: str) -> None:
    """**核心一致性**：三种写法的 `fit_index` 在 ship / ehp / speed / align 上逐位相同。"""
    eft = api.fit_index(eft_text)
    dna = api.fit_index(dna_text)
    killmail = api.fit_index(killmail_text)
    for name, envelope in (("eft", eft), ("dna", dna), ("killmail", killmail)):
        assert envelope["ok"] is True, f"{name} 失败：{envelope}"
    _assert_core_equal(eft, dna, killmail)

    # 顺带把「数值不是空的」钉住：全 0 / 全 None 的核也能让上面那条通过
    core = _core(eft)
    assert core["ehp_mixed"] > 0
    assert core["speed_mps"] > 0
    assert core["align_time_s"] > 0
    assert core["ship"]["name"] == "Hecate"


def test_consistency_assertion_is_not_vacuous(eft_text: str) -> None:
    """把 `fit_index` 的返回键**删掉一个**，一致性断言必须变红（证明它不是永真）。

    PROGRESS §3 第 196 条：每个关键断言都要能被一次故意篡改打红。
    """
    baseline = api.fit_index(eft_text)
    assert baseline["ok"] is True

    # ① 删键 → 取值就炸（KeyError：比较器**读到了**那个键，不是跳过了它）
    missing = copy.deepcopy(baseline)
    del missing["result"]["mobility"]
    with pytest.raises(KeyError):
        _assert_core_equal(baseline, missing)

    # ② 改一个数（哪怕只差 0.001）→ 断言变红
    changed = copy.deepcopy(baseline)
    changed["result"]["mobility"]["speed_mps"] = baseline["result"]["mobility"]["speed_mps"] + 0.001
    with pytest.raises(AssertionError):
        _assert_core_equal(baseline, changed)


# =============================================================================
# 二、技能档
# =============================================================================


def test_skill_tier_changes_ehp_and_is_echoed(eft_text: str) -> None:
    """`all4+min_required` 与 `all5` 的 `ehp_mixed` 不同，且 `meta` 回显实际档名与技能数。"""
    all4 = api.fit_index(eft_text, skill_tier="all4+min_required")
    all5 = api.fit_index(eft_text, skill_tier="all5")
    assert all4["ok"] is True and all5["ok"] is True

    ehp4 = all4["result"]["survivability"]["ehp_mixed"]
    ehp5 = all5["result"]["survivability"]["ehp_mixed"]
    assert ehp4 != ehp5, f"技能档没有生效：两档 EHP 都是 {ehp4}"
    assert ehp5 > ehp4, f"全 5 档的 EHP 反而更低（{ehp5} <= {ehp4}），技能档很可能是错的"

    tier4 = all4["meta"]["skill_tier"]
    tier5 = all5["meta"]["skill_tier"]
    assert tier4["name"] == "all4+min_required"
    assert tier5["name"] == "all5"
    # 技能数**不硬编码**：与索引里「已发布技能」的总数对数（实测 512）
    published = len(api.get_index().skills())
    assert tier4["count"] == tier5["count"] == published
    assert published > 0
    # L0 自己的 meta 也必须是同一档（不能一个说 all4、另一个说 all5）
    assert all4["result"]["meta"]["skill_tier"]["name"] == "all4+min_required"
    assert all5["result"]["meta"]["skill_tier"]["name"] == "all5"
    assert all5["result"]["meta"]["skill_tier"]["count"] == published


def test_skill_tier_is_applied_to_the_context(eft_text: str) -> None:
    """技能档必须真的进了**计算用的上下文**（漏注入就按零技能算：EHP 7585.48）。

    可观测证据有两条：① `detail.meta.assumptions.skill_tier` 的等级分布按档位变；
    ② `fit_index` 的 `ehp_mixed` 按档位变（`test_skill_tier_changes_ehp_and_is_echoed`）。
    单看 ① 只能证明假设被记下来了，必须配合 ② 才是「注入进 `fit["character"]["skills"]`」。
    """
    all4 = api.fit_detail(eft_text, "mobility", skill_tier="all4+min_required")
    all5 = api.fit_detail(eft_text, "mobility", skill_tier="all5")
    assert all4["ok"] is True and all5["ok"] is True

    tier4 = all4["meta"]["assumptions"]["skill_tier"]
    tier5 = all5["meta"]["assumptions"]["skill_tier"]
    assert set(tier5.values()) == {5}, "all5 档里不该有低于 5 级的技能"
    # all4 档 = 全部 4 级 + 被模块要求补到 5 的少数几个（实测 512 个里 2 个）
    assert min(tier4.values()) == 4
    assert max(tier4.values()) >= 5
    assert sorted(tier4) == sorted(tier5), "两档覆盖的技能集合必须相同（只改等级）"
    assert all5["meta"]["skill_tier"]["count"] == len(tier5)


# =============================================================================
# 三、错误契约（§10.4）
# =============================================================================


def test_bad_eft_structure_gives_parse_failed_with_line() -> None:
    """结构坏的 EFT → `parse_failed` + **行号**（坏在第 2 行）。"""
    broken = "[Hecate, x]\n]broken[\nDamage Control II"
    out = api.parse_fit(broken)
    assert out["ok"] is False
    assert out["error"]["kind"] == "parse_failed"
    assert out["error"]["line"] == 2
    assert "]broken[" in out["error"]["raw"]
    assert "result" not in out


def test_typo_module_gives_unknown_module_with_candidates() -> None:
    """写错的模块名 → `unknown_module` + 名称候选（**只报不填**）。"""
    typo = "[Hecate, x]\nDamage Controll II\n[Empty Low slot]"
    out = api.parse_fit(typo)
    assert out["ok"] is False
    assert out["error"]["kind"] == "unknown_module"
    candidates = out["error"]["candidates"]
    assert candidates, "拼错模块名时必须给出候选（否则 agent 只能猜）"
    assert candidates[0]["name"] == "Damage Control II"
    assert isinstance(candidates[0]["type_id"], int)
    assert len(candidates) <= 3


def test_unknown_ship_gives_ambiguous_ship() -> None:
    """船名读不出唯一一艘 → `ambiguous_ship`。"""
    out = api.parse_fit("[NoSuchShipXyz, x]\nDamage Control II")
    assert out["ok"] is False
    assert out["error"]["kind"] == "ambiguous_ship"


def test_invalid_path_gives_hint(eft_text: str) -> None:
    """不存在的路径 → `invalid_path` + `hint`（可用前缀列表）。"""
    out = api.fit_get(eft_text, "mobility.no_such_field")
    assert out["ok"] is False
    assert out["error"]["kind"] == "invalid_path"
    hint = out["error"]["hint"]
    assert isinstance(hint, str) and hint, "invalid_path 必须带 hint"
    assert "speed_mps" in hint, f"hint 里应能看到该视角的可用字段：{hint}"


def test_none_value_is_a_legal_answer_not_an_error() -> None:
    """`value: null` 是**合法答案**（不是 `invalid_path`）：0 模块配装的电容耗尽时间是未知。"""
    text = EMPTY_FIT_PATH.read_text(encoding="utf-8")
    out = api.fit_get(text, "stability.cap_depletes_in_s")
    assert out["ok"] is True, out
    assert out["result"]["value"] is None
    assert out["result"]["provenance"] == "unknown"
    assert out["result"]["reason"] == "not_applicable"
    assert "hint" not in out["result"]


def test_invalid_arguments_are_structured(eft_text: str) -> None:
    """三个共同参数与视角名的不合法取值 → `invalid_argument`（**不静默退回默认档**）。"""
    for call in (
        lambda: api.fit_index(eft_text, skill_tier="all-5"),
        lambda: api.fit_index(eft_text, damage_profile="em"),
        lambda: api.fit_index(eft_text, state_policy="guess"),
    ):
        out = call()
        assert out["ok"] is False
        assert out["error"]["kind"] == "invalid_argument"
        assert out["error"]["field"]
        assert out["error"]["hint"]

    bad_view = api.fit_view(eft_text, "no_such_view")
    assert bad_view["ok"] is False
    assert bad_view["error"]["kind"] == "invalid_argument"
    assert "mobility" in bad_view["error"]["hint"]


def test_bad_input_type_is_parse_failed() -> None:
    """根本不是配装输入 → `parse_failed`（而不是裸抛 TypeError）。"""
    out = api.fit_index(12345)
    assert out["ok"] is False
    assert out["error"]["kind"] == "parse_failed"


def test_unexpected_exception_becomes_internal_error(
    eft_text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**永不抛裸异常**（§10.4）：渲染层炸了也只给 `internal_error` 信封。"""

    def boom(_ctx: object) -> dict:
        raise RuntimeError("引擎炸了")

    monkeypatch.setattr(api.render, "render_index", boom)
    api.clear_caches()
    out = api.fit_index(eft_text)
    assert out["ok"] is False
    assert out["error"]["kind"] == "internal_error"
    assert "RuntimeError" in out["error"]["detail"]
    assert api.cache_stats()["entries"] == 0


def test_full_killmail_dict_is_local_not_network(killmail_text: str) -> None:
    """完整 killmail（带 `victim`）即使有 `killmail_id` 也走**本地**，不联网。"""
    killmail = json.loads(killmail_text)
    killmail = killmail[0] if isinstance(killmail, list) else killmail
    killmail = {**killmail, "killmail_id": 139246907, "hash": "deadbeef"}
    out = api.parse_fit(killmail)
    assert out["ok"] is True, out
    assert out["result"]["source_format"] == "killmail"
    assert out["meta"]["source_format"] == "killmail"


# =============================================================================
# 四、联网（只在替身里跑，不真的联网）
# =============================================================================


def test_killmail_url_parsing_is_offline() -> None:
    """killmail URL → `(id, hash)`：只认 ESI 与 zkillboard 两种形态。"""
    assert api.killmail_ref_from_url("https://zkillboard.com/kill/139246907/") == (139246907, None)
    assert api.killmail_ref_from_url(
        "https://esi.evetech.net/latest/killmails/139246907/abc123def/"
    ) == (139246907, "abc123def")
    assert api.killmail_ref_from_url("https://zkillboard.com/character/12345/") is None
    assert api.killmail_ref_from_url("not a url") is None


def test_network_failure_is_structured_and_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """联网失败 → `network_failed`（**不抛异常**），且失败不进缓存。"""

    def boom(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.URLError("no network in tests")

    monkeypatch.setattr(api.urllib.request, "urlopen", boom)
    api.clear_caches()
    out = api.fit_index({"killmail_id": 139246907, "hash": "deadbeef"})
    assert out["ok"] is False
    assert out["error"]["kind"] == "network_failed"
    assert "url" in out["error"]
    # 失败不缓存：网络恢复后同一个调用不能还拿到旧错误
    assert api.cache_stats()["entries"] == 0

    bad_ref = api.fit_index({"killmail_id": "abc", "hash": "x"})
    assert bad_ref["ok"] is False
    assert bad_ref["error"]["kind"] == "parse_failed"


# =============================================================================
# 五、缓存（第二次调用不碰引擎）
# =============================================================================


def test_second_call_hits_cache_without_engine(
    eft_text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一输入连续两次 → 第二次**一次引擎计算都没有**（计数桩 + 引擎自报统计）。"""
    engine = api.get_engine()
    real_calculate = engine.calculate
    calls = {"n": 0}

    def counting(fit, **kwargs):  # noqa: ANN001, ANN003 - 与 Engine.calculate 同形
        calls["n"] += 1
        return real_calculate(fit, **kwargs)

    monkeypatch.setattr(engine, "calculate", counting)
    api.clear_caches()

    first = api.fit_index(eft_text)
    assert first["ok"] is True
    after_first = calls["n"]
    assert after_first > 0, "第一次调用必须真的算过（否则这条测不到缓存）"

    second = api.fit_index(eft_text)
    assert second == first
    assert calls["n"] == after_first, f"第二次又算了 {calls['n'] - after_first} 次引擎"
    assert api.cache_stats()["hits"] >= 1


def test_cache_is_bounded_and_capacity_is_64(eft_text: str) -> None:
    """缓存上限 = 64（§10.6 的 LRU），且失败的调用不占位。"""
    api.clear_caches()
    assert api.cache_stats()["capacity"] == api.CACHE_MAX_ENTRIES == 64
    assert api.fit_get(eft_text, "mobility.nope")["ok"] is False
    assert api.cache_stats()["entries"] == 0
    assert api.fit_index(eft_text)["ok"] is True
    assert api.cache_stats()["entries"] == 1


# =============================================================================
# 六、L1 / detail 的形状（不重算，只对形状与可达性）
# =============================================================================


def _leaf_paths(node: object, prefix: str = "") -> list[str]:
    """overview 的叶子点路径（与渲染层同一套寻址规则：对象逐键、对象数组用 `[]`）。"""
    if isinstance(node, dict) and node:
        found: list[str] = []
        for key, value in node.items():
            found.extend(_leaf_paths(value, f"{prefix}.{key}" if prefix else str(key)))
        return found
    if isinstance(node, list) and node and all(isinstance(item, dict) for item in node):
        found = []
        for item in node:
            found.extend(_leaf_paths(item, f"{prefix}[]"))
        return found
    return [prefix]


def test_view_agrees_with_index(eft_text: str) -> None:
    """L1 单视角与 L0 摘要对同一个数给**同一个值**（两条出口不漂移）。"""
    index = api.fit_index(eft_text)
    view = api.fit_view(eft_text, "mobility")
    assert view["ok"] is True
    assert set(view["result"]) == {
        "overview",
        "provenance",
        "unknown_reasons",
        "confidence",
        "flags",
    }
    assert view["result"]["overview"]["speed_mps"] == index["result"]["mobility"]["speed_mps"]
    # L1 的 provenance 是**全量**表：每个 overview 叶子都必须有一条（不是稀疏表）
    provenance = view["result"]["provenance"]
    assert set(provenance) == set(_leaf_paths(view["result"]["overview"]))
    assert set(provenance.values()) <= {"measured", "assumed", "unknown"}


def test_get_agrees_with_index(eft_text: str) -> None:
    """`fit_get` 与 `fit_index` 对同一个数给同一个值 + 三态。"""
    index = api.fit_index(eft_text)
    out = api.fit_get(eft_text, "mobility.speed_mps")
    assert out["ok"] is True
    assert out["result"]["path"] == "mobility.speed_mps"
    assert out["result"]["value"] == index["result"]["mobility"]["speed_mps"]
    assert out["result"]["provenance"] in ("measured", "assumed", "unknown")
    assert out["result"]["source_view"] == "mobility"


def test_detail_carries_full_skill_table(eft_text: str) -> None:
    """detail 层给**完整技能表**（512 条），且条目形状恒定。"""
    out = api.fit_detail(eft_text, "stability")
    assert out["ok"] is True
    result = out["result"]
    assert list(result["views"]) == ["stability"]
    entry = result["views"]["stability"]
    assert set(entry) == {
        "overview",
        "provenance",
        "unknown_reasons",
        "confidence",
        "flags",
        "missing",
    }
    assumptions = out["meta"]["assumptions"]
    tier = assumptions["skill_tier"]
    assert len(tier) == out["meta"]["skill_tier"]["count"] == len(api.get_index().skills())
    assert entry["missing"] == []

    # `resources.upgrade_space` 在 detail 里是**完整清单**（L0 只给摘要）
    resources = api.fit_detail(eft_text, "resources")
    assert resources["ok"] is True
    assert isinstance(resources["result"]["views"]["resources"]["overview"]["upgrade_space"], list)


def test_detail_defaults_to_all_views_and_filters_fields(eft_text: str) -> None:
    """不给 view 时给全部六个视角；给 path 时只给这些字段（找不到的进 `missing`）。"""
    everything = api.fit_detail(eft_text)
    assert everything["ok"] is True
    assert set(everything["result"]["views"]) == {
        "firepower",
        "survivability",
        "mobility",
        "resources",
        "constraints",
        "stability",
    }

    picked = api.fit_detail(eft_text, path="stability.capacitor")
    assert picked["ok"] is True
    view = picked["result"]["views"]["stability"]
    assert set(view["overview"]) == {"capacitor"}
    assert view["missing"] == []

    missing = api.fit_detail(eft_text, path="stability.no_such_field")
    assert missing["ok"] is True
    # 回显的是**调用方原始串**（带视角前缀），不是去掉前缀的那一段 ——
    # 这样 agent 能把 `missing` 与它自己传进来的 `fields` 直接对上（T-56 改判）。
    assert missing["result"]["views"]["stability"]["missing"] == [
        "stability.no_such_field"
    ]

    bad_path = api.fit_detail(eft_text, path="nope.field")
    assert bad_path["ok"] is False
    assert bad_path["error"]["kind"] == "invalid_path"
    assert bad_path["error"]["hint"]


def test_every_entry_echoes_versions_and_tier(eft_text: str) -> None:
    """五个入口的 `meta` 都回显版本三元组与**实际**技能档（§10.3）。"""
    envelopes = {
        "parse": api.parse_fit(eft_text),
        "index": api.fit_index(eft_text),
        "view": api.fit_view(eft_text, "resources"),
        "get": api.fit_get(eft_text, "resources.feasible"),
        "detail": api.fit_detail(eft_text, "resources"),
    }
    for name, envelope in envelopes.items():
        assert envelope["ok"] is True, f"{name}: {envelope}"
        meta = envelope["meta"]
        assert meta["spec_version"]
        assert meta["engine_version"]
        assert isinstance(meta["sde_build"], int)
        assert meta["skill_tier"]["name"] in api.SKILL_TIERS
        assert meta["skill_tier"]["count"] > 0
        assert meta["source_format"] in ("eft", "dna", "killmail", "structured")


def test_json_envelope_is_serialisable(eft_text: str) -> None:
    """返回一律 JSON 可序列化（MCP / CLI 共用同一个函数层，不许藏 tuple / set）。"""
    for envelope in (
        api.parse_fit(eft_text),
        api.fit_index(eft_text),
        api.fit_view(eft_text, "mobility"),
        api.fit_get(eft_text, "mobility.speed_mps"),
        api.fit_detail(eft_text, "mobility"),
        api.fit_index("[Hecate, x]\nDamage Controll II"),
    ):
        json.dumps(envelope, ensure_ascii=False)  # 不抛异常即通过


# =============================================================================
# 七、战报上下文（`fit_killmail`）：T-49
# =============================================================================
#
# 样本来源（**真实响应**，不是编的）：
#   `curl https://zkillboard.com/api/killID/139239127/`              -> 139239127-zkb.json
#   `curl https://esi.evetech.net/latest/killmails/139239127/<hash>/` -> 139239127-esi.json
# 两份都是 2026-10-17 22:31:32Z 那条 Sarathiel（Dreadnought, 87381）被 79 人打掉的原文：
# zkillboard 那份**有** `zkb` 价值块、`attackers` 里**没有** `items`；ESI 那份相反。
# `139239127-esi-mismatch.json` 是**故意改坏**的副本（`victim.damage_taken` -1），
# 专门用来证明 `damage_check` 不是恒返回 matches_victim。
#
# 星系名 / 安全等级在测试里**一律打桩**（`_offline_system`），值取自真实 ESI：
# 30002537 = Amamake，security_status = 0.43876123428344727，security_class = "E"。

KILLMAIL_FIXTURES = NOOBBAOZI_ROOT / "tests" / "fixtures" / "killmail"
KILLMAIL_ZKB_PATH = KILLMAIL_FIXTURES / "139239127-zkb.json"
KILLMAIL_ESI_PATH = KILLMAIL_FIXTURES / "139239127-esi.json"
KILLMAIL_MISMATCH_PATH = KILLMAIL_FIXTURES / "139239127-esi-mismatch.json"

KILLMAIL_ID = 139239127
KILLMAIL_HASH = "fdd6f14f8620ba2d1e9c81eb2e53d202b3a010a0"
SOLAR_SYSTEM_ID = 30002537

AMAMAKE = {
    "name": "Amamake",
    "security_status": 0.43876123428344727,
    "security_class": "E",
    "has_class": True,
}
"""真实 ESI 快照（`GET /universe/systems/30002537/`）：lowsec，类名 `E`。"""


def _offline_system(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """把星系查询换成离线桩（名字 + 安全等级都来自上面的真实快照），返回被查的 system id。

    顺带 `clear_caches()`：结果缓存与星系名缓存都是**进程级**的，不清会让「上一个用例
    造出来的缓存」悄悄变成这个用例的前提。
    """
    calls: list[int] = []

    def fake_system(system_id: int) -> dict:
        calls.append(system_id)
        return dict(AMAMAKE)

    monkeypatch.setattr(
        api, "universe_names", lambda ids: {SOLAR_SYSTEM_ID: AMAMAKE["name"]}
    )
    monkeypatch.setattr(api, "universe_system", fake_system)
    api.clear_caches()
    return calls


def test_fit_killmail_reports_the_whole_battle(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实战报（zkb 原文）→ 价值 / 星系 / 参战方 / 最终一击 / 伤害校验，一项不少。"""
    text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")
    calls = _offline_system(monkeypatch)

    out = api.fit_killmail(text)
    assert out["ok"] is True, out
    result = out["result"]
    killmail = result["killmail"]

    assert killmail["killmail_id"] == KILLMAIL_ID
    assert killmail["killmail_time"] == "2026-10-17T22:31:32Z"
    assert killmail["solar_system"] == {"id": SOLAR_SYSTEM_ID, "name": "Amamake"}
    assert calls == [SOLAR_SYSTEM_ID], "星系只该查一次"
    assert killmail["security"]["status"] == AMAMAKE["security_status"]
    assert killmail["security"]["band"] == "lowsec"
    assert killmail["value"] == {
        "fitted": 76815307221.7,
        "dropped": 29383036870.34,
        "destroyed": 47559005933.18,
        "total": 76942042803.52,
    }
    assert killmail["flags"] == {"npc": False, "solo": False, "awox": False, "points": 1}
    assert "capital" in killmail["labels"]

    victim = result["victim"]
    assert victim["damage_taken"] == 6558773
    assert victim["ship"] == {
        "type_id": 87381,
        "name": "Sarathiel",
        "group": "Dreadnought",
    }

    attackers = result["attackers"]
    assert attackers["count"] == 79
    assert attackers["damage_total"] == victim["damage_taken"] == 6558773
    assert attackers["damage_check"] == "matches_victim"
    assert "damage_diff" not in attackers, "相等时不该给 diff"

    final = attackers["final_blow"]
    assert final is not None, "这条战报有 final_blow=true 的攻击者"
    assert final["ship"] == {
        "type_id": 73787,
        "name": "Naglfar Fleet Issue",
        "group": "Dreadnought",
    }
    assert final["weapon"]["group"] == "Projectile Weapon"
    assert final["security_status"] == pytest.approx(-7.6)

    for key in ("by_ship_group", "by_weapon_family"):
        rows = attackers[key]
        assert rows, f"{key} 不该为空"
        assert sum(row["damage_pct"] for row in rows) == pytest.approx(1.0, abs=1e-4)
        assert [row["damage"] for row in rows] == sorted(
            (row["damage"] for row in rows), reverse=True
        ), f"{key} 应按伤害降序"
    assert {row["group"] for row in attackers["by_ship_group"]} >= {"Dreadnought", "Battleship"}
    assert {row["family"] for row in attackers["by_weapon_family"]} <= set(api.WEAPON_FAMILIES)

    security = attackers["security"]
    assert security["min"] == pytest.approx(-10.0)
    assert security["max"] == pytest.approx(5.0)
    assert security["criminal_count"] == 9, "security_status <= -5 的才算 criminal"

    assert result["killmail_source"] == "local"
    assert out["meta"]["killmail_source"] == "local"
    assert out["meta"]["source_format"] == "killmail"


def test_fit_killmail_ehp_comes_from_the_projection_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`context.ehp_mixed` 与 `fit_get("survivability.ehp_mixed")` **逐位相同**（接口层不重算）。"""
    text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")
    _offline_system(monkeypatch)

    out = api.fit_killmail(text)
    assert out["ok"] is True, out
    context = out["result"]["context"]
    vs_ehp = context["damage_taken_vs_ehp"]
    reference = api.fit_get(text, api.KILLMAIL_EHP_PATH)["result"]
    assert vs_ehp["ehp_mixed"] == reference["value"]
    assert vs_ehp["ehp_provenance"] == reference["provenance"]
    assert vs_ehp["damage_taken"] == 6558773
    assert vs_ehp["ratio"] == pytest.approx(6558773 / reference["value"], abs=1e-3)
    assert "不给生死结论" in context["note"]


def test_fit_killmail_is_not_vacuous_on_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """**断言非永真**：把 `damage_check` 恒返回 matches_victim，这条必须变红。

    mismatch 样本 = 真实 ESI 响应把 `victim.damage_taken` 减 1（Σattackers 还是 6558773）。
    """
    _offline_system(monkeypatch)

    matched = api.fit_killmail(KILLMAIL_ESI_PATH.read_text(encoding="utf-8"))
    assert matched["result"]["attackers"]["damage_check"] == "matches_victim"

    broken = api.fit_killmail(KILLMAIL_MISMATCH_PATH.read_text(encoding="utf-8"))
    assert broken["ok"] is True, broken
    attackers = broken["result"]["attackers"]
    victim = broken["result"]["victim"]
    assert attackers["damage_total"] == 6558773
    assert victim["damage_taken"] == 6558772
    assert attackers["damage_check"] == "mismatch", "Σattackers != victim 必须报 mismatch"
    assert attackers["damage_diff"] == 1, "差值 = Σattackers - victim"


# =============================================================================
# 十二、战报口径（T-53）：蛋不算输出船 / 挂名人数 ≠ 有伤害人数
# =============================================================================
#
# 事实背景（实测 killmail 139239127）：ESI 的 79 个 attackers 里
#   - 22 个 `damage_done == 0`（挂名没输出）→ damaging_count 57 / zero_damage_count 22；
#   - 3 个 `ship_type_id == 670`（Capsule，蛋），伤害 1086009 / 76069 / 843。
# 其中 character_id 2121910483（1086009）的损失记录显示他在**同一星系、同一场交火**里
# 早 10 分钟丢了自己的船（ship 29340 Osprey Navy Issue）—— 蛋只是 ESI 记的「当时的船」。
# 所以：**伤害不扣，船归属标注**。

CAPSULE_TYPE_ID = 670
"""蛋的 type_id（`Capsule`）；`ship_type_id == 670` 就是「记录船是蛋」。"""


def _killmail_result(monkeypatch: pytest.MonkeyPatch) -> dict:
    """ESI 原文（79 attackers / 3 个蛋 / 22 个零伤害）→ `result`（星系查询走离线桩）。"""
    _offline_system(monkeypatch)
    out = api.fit_killmail(KILLMAIL_ESI_PATH.read_text(encoding="utf-8"))
    assert out["ok"] is True, out
    return out["result"]


def _assert_capsule_facts(result: dict) -> None:
    """「蛋 + 零伤害」口径的全部断言（抽成函数，才能做下面那条非永真的配对断言）。"""
    attackers = result["attackers"]
    assert attackers["count"] == 79
    assert attackers["damaging_count"] == 57
    assert attackers["zero_damage_count"] == 22
    assert attackers["capsule_count"] == 3
    assert (
        attackers["damaging_count"] + attackers["zero_damage_count"] == attackers["count"]
    ), "有伤害 + 零伤害必须正好划分挂名人数"
    assert any("蛋" in warning for warning in result["warnings"]), result["warnings"]


def test_killmail_counts_capsule_and_zero_damage(monkeypatch: pytest.MonkeyPatch) -> None:
    """79 攻击者 = 57 有伤害 + 22 零伤害；3 个记录船是蛋，**伤害一分不扣**。"""
    result = _killmail_result(monkeypatch)
    _assert_capsule_facts(result)
    attackers = result["attackers"]

    assert attackers["damage_total"] == 6558773, "蛋的 1162921 必须还在总伤害里"
    assert sum(row["damage"] for row in attackers["by_ship_group"]) == 6558773, (
        "分桶的伤害之和必须等于总伤害：蛋的伤害不许为了「好看」被扣掉"
    )

    capsule_rows = [row for row in attackers["by_ship_group"] if row["group"] == "Capsule"]
    assert len(capsule_rows) == 1, "Capsule 只有一个桶"
    capsule_row = capsule_rows[0]
    assert capsule_row["ship_attribution"] == "capsule", "蛋桶必须标「船归属不可信」"
    assert capsule_row["damage"] == 1162921
    assert capsule_row["count"] == 4, (
        "Capsule 组里有两种 type：670（3 人）+ 33328 Genolution 变体（1 人 / 0 伤害）；"
        "capsule_count 严格按 670 数 = 3，组内条目数是 4"
    )
    assert all(
        "ship_attribution" in row for row in attackers["by_ship_group"]
    ), "形状稳定：by_ship_group **每一行**都带 ship_attribution"
    assert [
        row["group"] for row in attackers["by_ship_group"] if row["ship_attribution"] == "capsule"
    ] == ["Capsule"], "只有蛋桶是 capsule，其它桶一律 normal"
    assert all(
        row["ship_attribution"] == "normal"
        for row in attackers["by_ship_group"]
        if row["group"] != "Capsule"
    ), "非蛋行必须是 normal（全表一致，不留例外）"

    warning = next(warn for warn in result["warnings"] if "蛋" in warn)
    for fact in ("无法装备", "被摧毁的船", "已经失去了自己的船"):
        assert fact in warning, f"蛋警告必须写明「{fact}」：{warning}"


def test_killmail_by_pilot_marks_capsule_recorded_ship(monkeypatch: pytest.MonkeyPatch) -> None:
    """`by_pilot` 里记录船是蛋的人带 `recorded_ship_is_capsule: true`，伤害照旧保留。"""
    result = _killmail_result(monkeypatch)
    pilots = result["attackers"]["by_pilot"]
    assert all("recorded_ship_is_capsule" in row for row in pilots), "形状稳定：每行都有这个键"

    capsule_pilots = [row for row in pilots if row["recorded_ship_is_capsule"]]
    assert len(capsule_pilots) == 1, (
        "Top10 里只有 1 个蛋（1086009）：另两个蛋 76069 / 843 伤害太低，没进榜"
    )
    woof = capsule_pilots[0]
    assert woof["ship"]["type_id"] == CAPSULE_TYPE_ID
    assert woof["ship"]["group"] == "Capsule"
    assert woof["character"]["id"] == 2121910483, (
        "就是那个 2026-10-17T22:21:40Z 在 30002537 丢了 29340（Osprey Navy Issue）、"
        "10 分钟后以蛋的状态还在场的人"
    )
    assert woof["damage"] == 1086009, "标记归标记，伤害数字照旧（那是他被摧毁的船打的）"

    others = [row for row in pilots if not row["recorded_ship_is_capsule"]]
    assert others, "Top10 里剩下的人都不该被标成蛋"
    assert all(row["ship"]["type_id"] != CAPSULE_TYPE_ID for row in others), (
        "标记必须与这一行打出来的 ship 一致：ship 不是 670 就不许标 true"
    )


def test_capsule_counts_are_not_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """**配对断言**：`capsule_count` 恒 0、`damaging_count` 恒等于 `count` → 上面的断言必须红。

    走的是真实链路（改 `_attacker_counts` → 清结果缓存 → 重新组装整条战报），所以它同时
    证明「那些断言读的是线上算出来的数」，不是测试自己编的常量。
    """
    text = KILLMAIL_ESI_PATH.read_text(encoding="utf-8")
    _offline_system(monkeypatch)
    real = api._attacker_counts

    def without_capsule(attackers: list[dict]) -> dict:
        return {**real(attackers), "capsule_count": 0}

    def all_damaging(attackers: list[dict]) -> dict:
        counts = real(attackers)
        return {**counts, "damaging_count": counts["count"], "zero_damage_count": 0}

    monkeypatch.setattr(api, "_attacker_counts", without_capsule)
    api.clear_caches()
    with pytest.raises(AssertionError):
        _assert_capsule_facts(api.fit_killmail(text)["result"])

    monkeypatch.setattr(api, "_attacker_counts", all_damaging)
    api.clear_caches()
    with pytest.raises(AssertionError):
        _assert_capsule_facts(api.fit_killmail(text)["result"])


def _zkb_payload_with_items(items: object, *, drop_key: bool = False) -> object:
    """zkb `killID` 原文的副本，只动 `victim.items` 一个字段。

    `drop_key=True` 时把**键整个删掉**（模拟 zkb 版本 / 裁剪差异），否则把值换掉。
    其余（attackers / victim 其它字段 / zkb 块）一个字节都不变 ——
    这样两次运行的差异就只可能是「items 判定分支」。
    """
    payload = copy.deepcopy(json.loads(KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")))
    entry = payload[0] if isinstance(payload, list) else payload
    if drop_key:
        entry["victim"].pop("items", None)
    else:
        entry["victim"]["items"] = items
    return payload


def test_fit_killmail_uses_the_complete_zkb_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """zkb 的 `/api/killID/` 自带**整条** killmail（含 items）→ **一次都不请求 ESI**。

    实测依据：16/16 样本的 zkb 原文与 ESI 原文逐字段一致（attackers 条数与每个字段值、
    victim 全字段、items 逐项）。所以只要 items 非空，跳过 ESI 不会少任何数据 ——
    这省掉的是冷路径里最贵的 **1.7–2.6 s**。
    """
    zkb_payload = json.loads(KILLMAIL_ZKB_PATH.read_text(encoding="utf-8"))
    seen: list[str] = []

    def fake_http(url: str) -> object:
        seen.append(url)
        return zkb_payload

    _offline_system(monkeypatch)
    monkeypatch.setattr(api, "_http_json", fake_http)

    out = api.fit_killmail({"killmail_id": KILLMAIL_ID, "hash": KILLMAIL_HASH})
    assert out["ok"] is True, out
    result = out["result"]
    assert result["killmail_source"] == "network", "输入形态仍是联网"
    assert result["killmail_origin"] == "zkillboard", "原文来自 zkb"
    assert out["meta"]["killmail_origin"] == "zkillboard"

    # 数据完整：价值块 / 名字外的一切 / 伤害 / 参战方 一个都不少
    assert result["killmail"]["value"]["total"] == 76942042803.52
    assert "capinv" in result["killmail"]["labels"]
    assert result["victim"]["damage_taken"] == 6558773
    assert result["attackers"]["count"] == 79
    # 79 个挂名里只有 57 个真的打出了伤害（余下 22 个是零伤害 —— 两行 fixture 都这么算）
    assert result["attackers"]["damaging_count"] == 57
    assert (
        result["attackers"]["damaging_count"] + result["attackers"]["zero_damage_count"]
        == result["attackers"]["count"]
    )

    assert seen == [
        f"https://zkillboard.com/api/killID/{KILLMAIL_ID}/"
    ], f"zkb 带 items 时只该有一次请求，实际 {seen}"
    assert not any("回落" in warning for warning in result["warnings"]), result["warnings"]


def test_fit_killmail_short_circuits_when_items_is_an_empty_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`items` 是 **`[]`**（键在、值为空）→ 直接短路，**不请求 ESI**，但要**说出来**。

    `[]` 与「我们漏了模块数据」在返回里长得一模一样 —— agent 看不到我们的取证，
    一句 `ZKB_EMPTY_ITEMS_WARNING` 就是它唯一的判断依据。
    """
    zkb_payload = _zkb_payload_with_items([])
    seen: list[str] = []

    def fake_http(url: str) -> object:
        seen.append(url)
        return zkb_payload

    _offline_system(monkeypatch)
    monkeypatch.setattr(api, "_http_json", fake_http)
    api.clear_caches()

    out = api.fit_killmail({"killmail_id": KILLMAIL_ID, "hash": KILLMAIL_HASH})
    assert out["ok"] is True, out
    result = out["result"]
    assert result["killmail_origin"] == "zkillboard", "空数组是事实，不必回落"
    assert seen == [
        f"https://zkillboard.com/api/killID/{KILLMAIL_ID}/"
    ], f"空数组不该触发 ESI，实际 {seen}"
    assert api.ZKB_EMPTY_ITEMS_WARNING in result["warnings"], (
        f"空 items 必须显式说明，实际 {result['warnings']}"
    )
    # 其余数据照常给（价值 / 参战方都在 zkb 原文里）
    assert result["attackers"]["count"] == 79
    assert result["killmail"]["value"]["total"] == 76942042803.52


def test_fit_killmail_falls_back_to_esi_when_items_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`items` **键不存在**（不是设为空）→ 可疑，**仍然回落 ESI** 并说出来。

    这是「zkb 可能被裁剪过」那条风险路径：空数组是事实，键不见是疑点，
    两者必须分道 —— 拿不准就多花一次 1.7–2.6 s，不猜。
    """
    zkb_payload = _zkb_payload_with_items(None, drop_key=True)
    esi_payload = json.loads(KILLMAIL_ESI_PATH.read_text(encoding="utf-8"))
    seen: list[str] = []

    def fake_http(url: str) -> object:
        seen.append(url)
        return zkb_payload if "zkillboard" in url else esi_payload

    _offline_system(monkeypatch)
    monkeypatch.setattr(api, "_http_json", fake_http)
    api.clear_caches()

    out = api.fit_killmail({"killmail_id": KILLMAIL_ID, "hash": KILLMAIL_HASH})
    assert out["ok"] is True, out
    result = out["result"]
    assert result["killmail_origin"] == "esi"
    assert api.ZKB_ITEMS_FALLBACK_WARNING in result["warnings"], (
        f"回落 ESI 必须写进 warnings，实际 {result['warnings']}"
    )
    # 回落后数据依然完整（ESI 是原路径）
    assert result["victim"]["damage_taken"] == 6558773
    assert result["attackers"]["count"] == 79
    assert result["killmail"]["value"]["total"] == 76942042803.52
    assert seen == [
        f"https://zkillboard.com/api/killID/{KILLMAIL_ID}/",
        f"https://esi.evetech.net/latest/killmails/{KILLMAIL_ID}/{KILLMAIL_HASH}/",
    ], seen


def test_zkb_items_shortcut_is_not_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """**配对证明**：同一份 fixture 只改 `victim.items`，请求数必须是 `1 / 1 / 2`。

    | `victim.items` | 期望请求数 |
    |---|---:|
    | 非空 | 1（短路） |
    | `[]` | 1（短路 —— 空数组是事实） |
    | 键不存在 | 2（回落 ESI —— 可疑） |

    三种情况跑同一段代码，差异只来自那一个字段 —— 所以它证明的不是「恰好没请求 ESI」，
    而是「判定分支真的按预期在分叉」。把 `isinstance(items, list)` 退化成 `if items:`，
    中间那行立刻变 2；把分支整段删掉，第一行变 2。
    """
    esi_payload = json.loads(KILLMAIL_ESI_PATH.read_text(encoding="utf-8"))
    with_items = json.loads(KILLMAIL_ZKB_PATH.read_text(encoding="utf-8"))
    empty_items = _zkb_payload_with_items([])
    missing_key = _zkb_payload_with_items(None, drop_key=True)

    def run(payload: object) -> list[str]:
        seen: list[str] = []

        def fake_http(url: str) -> object:
            seen.append(url)
            return payload if "zkillboard" in url else esi_payload

        _offline_system(monkeypatch)
        monkeypatch.setattr(api, "_http_json", fake_http)
        api.clear_caches()  # 结果缓存是进程级的：不清就会在上一次的结果上打转
        envelope = api.fit_killmail({"killmail_id": KILLMAIL_ID, "hash": KILLMAIL_HASH})
        assert envelope["ok"] is True, envelope
        return seen

    assert len(run(with_items)) == 1, "非空 items 应只请求 zkb"
    assert len(run(empty_items)) == 1, "空数组也是事实，不该回落"
    assert len(run(missing_key)) == 2, "键不存在必须回落 ESI"


def test_fit_killmail_uses_the_zkb_hash_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只给 `killmail_id`（不给 hash）也能跑：hash 从 zkillboard 的 `zkb.hash` 拿。

    （要用「items 键不存在」的样本才能走到 ESI —— 键在时那条路根本不会走。）
    """
    zkb_payload = _zkb_payload_with_items(None, drop_key=True)
    esi_payload = json.loads(KILLMAIL_ESI_PATH.read_text(encoding="utf-8"))
    seen: list[str] = []

    def fake_http(url: str) -> object:
        seen.append(url)
        return zkb_payload if "zkillboard" in url else esi_payload

    _offline_system(monkeypatch)
    monkeypatch.setattr(api, "_http_json", fake_http)
    api.clear_caches()

    out = api.fit_killmail({"killmail_id": KILLMAIL_ID})
    assert out["ok"] is True, out
    esi_urls = [url for url in seen if "esi.evetech.net" in url]
    assert esi_urls == [
        f"https://esi.evetech.net/latest/killmails/{KILLMAIL_ID}/{KILLMAIL_HASH}/"
    ], esi_urls


def test_fit_killmail_runs_names_and_system_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`names` 与 `systems` 互不依赖 → **必须并行**（各睡 0.3 s，串行会是 0.6 s）。

    用**本地 killmail** 做样本：这样整条路径只剩这两个桩，网络零参与，差异只可能来自
    并行与否。阈值 0.5 s 卡在 0.3 与 0.6 之间，串行必然超。
    """
    api.get_index()  # 预热：索引首次打开要 100–300 ms，别把它算进计时

    def slow_names(ids: object) -> dict:
        time.sleep(0.3)
        return {SOLAR_SYSTEM_ID: AMAMAKE["name"]}

    def slow_system(system_id: int) -> dict:
        time.sleep(0.3)
        return dict(AMAMAKE)

    monkeypatch.setattr(api, "universe_names", slow_names)
    monkeypatch.setattr(api, "universe_system", slow_system)
    api.clear_caches()

    text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")
    started = time.monotonic()
    out = api.fit_killmail(text)
    elapsed = time.monotonic() - started

    assert out["ok"] is True, out
    assert out["result"]["killmail_origin"] == api.KILLMAIL_ORIGIN_LOCAL
    assert elapsed < 0.5, (
        f"names 与 systems 串行了：{elapsed:.2f}s（并行应 ≈0.3 s，串行 0.6 s）"
    )


def test_fit_killmail_system_failure_does_not_hide_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """并行时**一个失败不许掩盖另一个**：星系挂了，名字也得照常给。"""
    def broken_system(system_id: int) -> dict:
        raise api._ApiFailure("network_failed", "星系接口超时")

    monkeypatch.setattr(api, "universe_names", lambda ids: {SOLAR_SYSTEM_ID: AMAMAKE["name"]})
    monkeypatch.setattr(api, "universe_system", broken_system)
    api.clear_caches()

    out = api.fit_killmail(KILLMAIL_ZKB_PATH.read_text(encoding="utf-8"))
    assert out["ok"] is True, out
    result = out["result"]
    # 星系降级：security 给 null + 一条 warning
    assert result["killmail"]["security"]["status"] is None
    assert any("星系安全等级查不到" in warning for warning in result["warnings"])
    # 但名字照常给（没被掩盖），战报本身也没失败
    assert result["killmail"]["solar_system"]["name"] == AMAMAKE["name"]
    assert result["attackers"]["count"] == 79


def test_fit_killmail_refuses_fits_without_a_battle() -> None:
    """EFT / DNA / 结构化配装 → `not_a_killmail`（**诚实报错，不拿配装猜一条战报**）。"""
    eft = EFT_PATH.read_text(encoding="utf-8")
    for label, payload in (
        ("eft", eft),
        ("dna", "35683:2048;1::"),
        ("structured", {"ship": {"type_id": 35683}, "items": []}),
    ):
        out = api.fit_killmail(payload)
        assert out["ok"] is False, f"{label} 不该成功"
        assert out["error"]["kind"] == api.NOT_A_KILLMAIL_KIND == "not_a_killmail"
        assert out["error"]["message"], f"{label} 必须给人话"
        assert out["error"]["hint"], f"{label} 必须给「能传什么」的提示"
        assert "result" not in out, "失败信封里不该有 result"


def test_fit_killmail_network_failure_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    """联网失败 → `network_failed`（不抛异常），且失败不进缓存。"""

    def boom(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.URLError("no network in tests")

    monkeypatch.setattr(api.urllib.request, "urlopen", boom)
    api.clear_caches()
    out = api.fit_killmail({"killmail_id": KILLMAIL_ID, "hash": KILLMAIL_HASH})
    assert out["ok"] is False
    assert out["error"]["kind"] == "network_failed"
    assert out["error"]["url"].startswith("https://zkillboard.com/api/killID/")
    assert api.cache_stats()["entries"] == 0


def test_fit_killmail_never_fakes_system_or_security(monkeypatch: pytest.MonkeyPatch) -> None:
    """星系名 / 安全等级查不到 → `null` + `warnings`（**不伪造**，也不给 band）。"""
    text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")

    def offline(*_args: object, **_kwargs: object) -> dict:
        raise api._ApiFailure("network_failed", "offline in tests", url="x")

    monkeypatch.setattr(api, "universe_names", offline)
    monkeypatch.setattr(api, "universe_system", offline)
    api.clear_caches()

    out = api.fit_killmail(text)
    assert out["ok"] is True, out
    killmail = out["result"]["killmail"]
    assert killmail["solar_system"] == {"id": SOLAR_SYSTEM_ID, "name": None}
    assert killmail["security"] == {"status": None, "band": None}
    assert any("星系" in warning for warning in out["result"]["warnings"])


def test_security_band_rules() -> None:
    """band 的判据：highsec ≥0.5 / lowsec 0–0.5 / nullsec ≤0 / 虫洞按 `security_class`。"""
    assert api._security_band(0.5, "B", True) == api.SECURITY_BAND_HIGHSEC
    assert api._security_band(0.43876123428344727, "E", True) == api.SECURITY_BAND_LOWSEC
    assert api._security_band(0.0, "H6", True) == api.SECURITY_BAND_NULLSEC
    assert api._security_band(-0.7907073497772217, "H6", True) == api.SECURITY_BAND_NULLSEC
    # 拿不到 status：不给 band（不猜）
    assert api._security_band(None, "B", True) is None
    # 虫洞：ESI 给 security_class = null（实测 Thera / J220438 / Sentinel MZ）
    assert api._security_band(-0.99, None, True) == api.SECURITY_BAND_WORMHOLE
    # 响应里根本没有 security_class 这个键时**不**认定虫洞（退回数值规则）
    assert api._security_band(-0.99, None, False) == api.SECURITY_BAND_NULLSEC


def test_weapon_family_heuristics_do_not_guess() -> None:
    """武器族只看 SDE 名字 / 组名；认不出的（含「船名当武器」）一律 `other`。"""
    index = api.get_index()
    assert api.weapon_family(3057, index) == "lazors"  # Mega Pulse Laser II
    assert api.weapon_family(37306, index) == "projectile"  # Hexa 2500mm Repeating Cannon II
    assert api.weapon_family(24523, index) == "missile"  # Mjolnir Rage Torpedo
    assert api.weapon_family(28211, index) == "drone"  # Garde II
    assert api.weapon_family(448, index) == "other"  # Warp Scrambler II（不是武器）
    assert api.weapon_family(642, index) == "other"  # Apocalypse（ESI 把船名填进武器）
    assert api.weapon_family(None, index) == "other"
    assert api.weapon_family("1877", index) == "other", "类型不对时不猜"


def test_universe_names_batches_at_1000_and_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`POST /universe/names/` 单批 ≤ 1000；第二次调用**一次请求都不发**；查不到的不编。"""
    batches: list[list[int]] = []

    def fake_post(url: str, payload: object) -> object:
        ids = list(payload)  # type: ignore[arg-type]
        batches.append(ids)
        if any(identifier >= 999_999 for identifier in ids):
            return []
        return [{"id": identifier, "name": f"System {identifier}"} for identifier in ids]

    monkeypatch.setattr(api, "_post_json", fake_post)
    api.clear_caches()

    ids = list(range(1, 1201))
    names = api.universe_names(ids)
    assert len(names) == 1200
    assert [len(batch) for batch in batches] == [1000, 200], batches
    assert all(len(batch) <= api.UNIVERSE_NAMES_MAX_IDS for batch in batches)
    assert names[1] == "System 1"

    before = len(batches)
    assert api.universe_names(ids) == names
    assert len(batches) == before, "第二次应当全部命中进程内缓存"

    assert api.universe_names([999_999]) == {}, "ESI 没给名字时不能编一个"
    assert api.universe_names([]) == {}


def test_fit_killmail_cache_is_keyed_by_entry_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一条战报第二次调用走缓存（命中 +1），且与 `fit_index` **不共用**缓存槽。"""
    text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")
    _offline_system(monkeypatch)

    first = api.fit_killmail(text)
    assert first["ok"] is True, first
    hits = api.cache_stats()["hits"]
    second = api.fit_killmail(text)
    assert second == first, "缓存必须返回同一份结果（深拷贝）"
    assert api.cache_stats()["hits"] == hits + 1

    entries = api.cache_stats()["entries"]
    assert api.fit_index(text)["ok"] is True
    assert api.cache_stats()["entries"] == entries + 1, "两个入口各占自己的缓存槽"


# =============================================================================
# 七之后、七之前：T-52 身份名字（驾驶员 / 军团 / 联盟）
# =============================================================================
#
# 样本是**真实 ESI 响应**：`POST /universe/names/` 一次问 152 个 id（见
# `tests/fixtures/zkb/esi-universe-names.json` 与同目录 README）。这些 id 覆盖
# 139239127 这条战报（星系 + 受害者 + 79 名攻击者）与 zkb 列表样本的那 12 行。
#
# 三类断言缺一不可：名字**是非空真名**、请求**只发一次**、缓存与缺项都**不编**。

NAMES_FIXTURE = NOOBBAOZI_ROOT / "tests" / "fixtures" / "zkb" / "esi-universe-names.json"
"""真实 `POST /universe/names/` 响应（裁剪版：只留这两份样本要的 id，字段一个没造）。"""

VICTIM_CHARACTER = 2123771141
VICTIM_CORPORATION = 98797773
VICTIM_ALLIANCE = 99014589
FINAL_BLOW_CHARACTER = 94814255


def _names_entries() -> list[dict]:
    return json.loads(NAMES_FIXTURE.read_text(encoding="utf-8"))


def _serve_names(
    monkeypatch: pytest.MonkeyPatch, *, drop: "tuple[int, ...]" = ()
) -> list[list[int]]:
    """把 `POST /universe/names/` 换成真实样本（**不联网**），返回每次请求的 id 批次。

    `drop` 用来模拟「ESI 没给某个 id 的名字」（删号 / NPC / 缺项）。
    """
    hidden = set(drop)
    entries = [entry for entry in _names_entries() if entry["id"] not in hidden]
    calls: list[list[int]] = []

    def fake_post(url: str, payload: object) -> object:
        assert url == api.ESI_UNIVERSE_NAMES_API, f"意外的 POST：{url}"
        wanted = list(payload)  # type: ignore[arg-type]
        calls.append(wanted)
        return [entry for entry in entries if entry["id"] in set(wanted)]

    monkeypatch.setattr(api, "_post_json", fake_post)
    return calls


def _offline_identities(
    monkeypatch: pytest.MonkeyPatch, *, drop: "tuple[int, ...]" = ()
) -> list[list[int]]:
    """星系详情 + 实体名都离线（真实快照 / 真实 names 样本），返回 names 请求批次。"""
    calls = _serve_names(monkeypatch, drop=drop)
    monkeypatch.setattr(api, "universe_system", lambda system_id: dict(AMAMAKE))
    api.clear_caches()
    return calls


def test_fit_killmail_resolves_pilot_and_org_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """受害者 / 最终一击 / 伤害 Top 的驾驶员·军团·联盟名字都在，且**只发一次** names 请求。"""
    text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")
    calls = _offline_identities(monkeypatch)

    out = api.fit_killmail(text)
    assert out["ok"] is True, out
    result = out["result"]

    victim = result["victim"]
    assert victim["character"] == {"id": VICTIM_CHARACTER, "name": "Warlock Industries Event"}
    assert victim["corporation"] == {
        "id": VICTIM_CORPORATION,
        "name": "Warlock Industries Corporation",
    }
    assert victim["alliance"] == {
        "id": VICTIM_ALLIANCE,
        "name": "Warlock Industries Alliance",
    }
    assert victim["ship"] == {"type_id": 87381, "name": "Sarathiel", "group": "Dreadnought"}

    final = result["attackers"]["final_blow"]
    assert final["character"] == {"id": FINAL_BLOW_CHARACTER, "name": "Ignacious Deviant"}
    assert final["corporation"]["name"] == "Hierarch Apotheosis Consortium"
    assert final["alliance"]["name"] == "Ruinous Powers"

    pilots = result["attackers"]["by_pilot"]
    assert len(pilots) == api.BY_PILOT_ROWS == 10, "只给伤害最高的前 10 名"
    assert [row["damage"] for row in pilots] == sorted(
        (row["damage"] for row in pilots), reverse=True
    ), "by_pilot 必须按伤害降序"
    assert pilots[0]["damage"] == 1086009
    assert all(row["character"]["name"] for row in pilots), "前 10 名都该有名字"
    assert all(row["ship"]["name"] for row in pilots), "船名来自 SDE，不该是 null"
    assert pilots[0]["damage_pct"] is not None
    blown = [row for row in pilots if row["final_blow"] is True]
    assert [row["character"]["name"] for row in blown] == ["Ignacious Deviant"]

    # 批量：整条战报（星系 + 受害者 + 79 名攻击者，去重后 120 个 id）只发**一次**请求
    assert len(calls) == 1, f"一条战报只该查一批名字，实际 {[len(c) for c in calls]} 批"
    expected = api._killmail_name_ids(json.loads(text)[0])
    assert set(calls[0]) == set(expected)
    assert SOLAR_SYSTEM_ID in calls[0], "星系名和实体名进同一个批次（不额外多发一次）"
    assert len(calls[0]) == len(set(calls[0])), "请求体必须去重"
    assert not any("实体名未解析" in warning for warning in result["warnings"])


def test_identity_names_are_not_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """**断言非永真**：把 names 响应换成空列表 → 名字必须变 null + warnings 非空。

    如果上面那条「名字非空」的断言其实是永真的（比如代码里塞了默认名），这条不会红。
    """
    text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")
    _offline_identities(monkeypatch)
    monkeypatch.setattr(api, "_post_json", lambda url, payload: [])
    api.clear_caches()

    out = api.fit_killmail(text)
    assert out["ok"] is True, "名字查不到不该让整条战报失败"
    result = out["result"]
    assert result["victim"]["character"] == {"id": VICTIM_CHARACTER, "name": None}
    assert result["attackers"]["final_blow"]["character"]["name"] is None
    assert any("实体名未解析" in warning for warning in result["warnings"])
    assert result["victim"]["ship"]["name"] == "Sarathiel", "船名来自 SDE，与 ESI 无关"


def test_identity_names_are_cached_across_killmails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一批 id 的**第二条**战报不再发 names 请求（进程内缓存，未命中也不重问）。"""
    zkb_text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")
    esi_text = KILLMAIL_ESI_PATH.read_text(encoding="utf-8")
    calls = _offline_identities(monkeypatch)

    first = api.fit_killmail(zkb_text)
    assert first["ok"] is True
    assert len(calls) == 1

    # 同一批 id、不同内容 key → 结果缓存不命中，但实体名缓存应当全中
    second = api.fit_killmail(esi_text)
    assert second["ok"] is True, second
    assert second["result"]["victim"]["character"]["name"] == "Warlock Industries Event"
    assert len(calls) == 1, f"实体名缓存没生效：又发了 {len(calls) - 1} 次"

    # 同一份输入再调一次 → 结果缓存命中，同样不发请求
    assert api.fit_killmail(zkb_text) == first
    assert len(calls) == 1


def test_missing_entity_name_is_null_and_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    """names 响应缺某个 id → 该 `name` 为 `null`（**不编**），`warnings` 说清少了几个。"""
    text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")
    _offline_identities(monkeypatch, drop=(VICTIM_CORPORATION,))

    out = api.fit_killmail(text)
    assert out["ok"] is True, out
    victim = out["result"]["victim"]
    assert victim["corporation"] == {"id": VICTIM_CORPORATION, "name": None}
    assert victim["character"]["name"] == "Warlock Industries Event", "缺一个不该连累别人"
    assert any(
        "1 个实体名未解析" in warning for warning in out["result"]["warnings"]
    ), out["result"]["warnings"]


def test_missing_alliance_is_a_null_object_not_a_null_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有联盟 → **整个 `alliance` 对象是 `null`**（不是 `{"id": null, "name": null}`）。"""
    killmail = json.loads(KILLMAIL_ESI_PATH.read_text(encoding="utf-8"))
    killmail.pop("zkb", None)
    killmail["victim"].pop("alliance_id", None)
    killmail["solar_system_id"] = SOLAR_SYSTEM_ID
    _offline_identities(monkeypatch)

    out = api.fit_killmail(json.dumps(killmail, ensure_ascii=False))
    assert out["ok"] is True, out
    victim = out["result"]["victim"]
    assert victim["alliance"] is None
    assert victim["character"]["name"] == "Warlock Industries Event"


def test_universe_names_batches_respect_the_min_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超过 1000 个 id 会分批，批间隔 ≥ `UNIVERSE_NAMES_MIN_INTERVAL_S`（虚拟时钟，不真等）。"""
    clock_state = {"now": 500.0}
    starts: list[float] = []
    sleeps: list[float] = []

    def clock() -> float:
        return clock_state["now"]

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock_state["now"] += seconds

    def fake_post(url: str, payload: object) -> object:
        starts.append(clock_state["now"])
        return [{"id": identifier, "name": f"E{identifier}"} for identifier in payload]  # type: ignore[union-attr]

    monkeypatch.setattr(api, "_post_json", fake_post)
    api.clear_caches()

    names = api.universe_names(list(range(1, 1501)), clock=clock, sleeper=sleeper)
    assert len(names) == 1500
    assert len(starts) == 2, f"1500 个 id 应当分成 2 批，实际 {len(starts)} 批"
    assert starts[1] - starts[0] >= api.UNIVERSE_NAMES_MIN_INTERVAL_S - 1e-9, (
        f"两批只隔了 {starts[1] - starts[0]} s"
    )
    assert sleeps == [pytest.approx(api.UNIVERSE_NAMES_MIN_INTERVAL_S)]


# =============================================================================
# 七、`zkb_search`（T-50）：URL 拼装 / 合并去重 / 限流 / 名字解析（**全程离线**）
# =============================================================================
#
# 样本是**真实响应**（见 `tests/fixtures/zkb/README.md`）：170 条的列表裁成 12 条、
# 9 条的裁成 6 条，字段一个没造。所有请求都走注入的 `requester`，测试**不联网**。

ZKB_FIXTURES = NOOBBAOZI_ROOT / "tests" / "fixtures" / "zkb"
LOSSES_FIXTURE = ZKB_FIXTURES / "losses-shipTypeID-22444-pastSeconds-604800.json"
IDS_FIXTURE = ZKB_FIXTURES / "esi-ids-sleipnir-hecate.json"

LOSSES_EXPECTED_URL = (
    "https://zkillboard.com/api/losses/shipTypeID/22444/pastSeconds/604800/"
)
KILLS_EXPECTED_URL = (
    "https://zkillboard.com/api/kills/shipTypeID/22444/pastSeconds/604800/"
)


def _fixture(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def zkb_state() -> object:
    """每条 zkb 用例前后都清掉「限流时间戳 + 名字缓存」。

    不清会互相串味：上一条把 `_ZKB_LAST_START` 留在虚拟时刻上，下一条要么白等 1.2 s，
    要么根本测不到限流。
    """
    api._reset_zkb_state()
    yield None
    api._reset_zkb_state()


class _Recorder:
    """假请求器：记下每个 URL（与 POST 体），按 GET / POST 回固定 payload。

    `POST` 有两条不同的端点：`/universe/ids/`（名字 → id）与 `/universe/names/`（id → 名字）。
    不传 `names_payload` 时后者回空列表 —— 那正好等于「一个名字都查不到」，于是名字为
    `null`、`warnings` 有一条，而**战报本身照常返回**（这正是降级该有的样子）。
    """

    def __init__(
        self,
        *,
        get_payload: object = None,
        post_payload: object = None,
        names_payload: object = None,
    ) -> None:
        self.urls: list[str] = []
        self.bodies: list[object] = []
        self.names_batches: list[list[int]] = []
        self._get = get_payload
        self._post = post_payload
        self._names = names_payload

    def __call__(self, url: str, body: object | None = None) -> object:
        self.urls.append(url)
        self.bodies.append(body)
        if body is not None and url == api.ESI_UNIVERSE_NAMES_API:
            self.names_batches.append(list(body))  # type: ignore[arg-type]
            return [] if self._names is None else self._names
        return self._post if body is not None else self._get

    def get_urls(self) -> list[str]:
        """只保留战报查询（去掉两次 ESI 实体的解析请求）。"""
        return [
            url
            for url in self.urls
            if url not in (api.ESI_UNIVERSE_IDS_API, api.ESI_UNIVERSE_NAMES_API)
        ]


def test_zkb_url_orders_filters_and_never_uses_start_time(zkb_state: None) -> None:
    """复合条件的 URL：段序**固定**（身份 → 船型 → 空间 → 时间 → 分页），且不用起止时间。"""
    url = api.zkb_url(
        side=api.ZKB_SIDE_KILLS,
        filters={"ship": 22444, "solar_system": 30002537, "character": 2121910483},
        past_seconds=604800,
        page=2,
    )
    assert url == (
        "https://zkillboard.com/api/kills/characterID/2121910483/shipTypeID/22444/"
        "solarSystemID/30002537/pastSeconds/604800/page/2/"
    )
    assert "startTime" not in url and "endTime" not in url
    # page=1 不拼 page（`/page/1/` 与不带等价，少一段更干净）
    assert api.zkb_url(side="losses", filters={"ship": 22444}) == (
        "https://zkillboard.com/api/losses/shipTypeID/22444/"
    )


def test_zkb_search_builds_urls_with_past_seconds_and_both_sides(
    zkb_state: None,
) -> None:
    """名字 → ESI 解析成 id → 逐段拼 URL；`side="both"` = 两条独立路径（各请求一次）。"""
    recorder = _Recorder(
        get_payload=_fixture(LOSSES_FIXTURE), post_payload=_fixture(IDS_FIXTURE)
    )
    cached_before = api.cache_stats()["entries"]
    envelope = api.zkb_search(
        ship="Sleipnir", past_seconds=604800, side=api.ZKB_SIDE_BOTH, requester=recorder
    )
    assert envelope["ok"] is True, envelope
    assert recorder.bodies[0] == ["Sleipnir"], "名字解析的请求体就是名字数组"
    assert recorder.urls == [
        api.ESI_UNIVERSE_IDS_API,
        KILLS_EXPECTED_URL,
        LOSSES_EXPECTED_URL,
        api.ESI_UNIVERSE_NAMES_API,
    ], "名字 → id 先解析，战报两条路径各一次，身份名字最后一批"

    result = envelope["result"]
    assert result["query"] == {
        "resolved": {"ship": {"id": 22444, "name": "Sleipnir"}},
        "side": "both",
        "past_seconds": 604800,
        "page": 1,
    }
    # 两条路径回的是同一份样本 → 去重后仍是 12 条（不是 24）
    assert result["count"] == 12
    assert len(result["kills"]) == 12
    ids = [row["killmail_id"] for row in result["kills"]]
    assert ids == sorted(ids, reverse=True), "合并后必须按时间倒序（killmail_id 单调递增）"
    assert envelope["meta"]["source"] == "zkillboard"
    assert envelope["meta"]["urls"] == [KILLS_EXPECTED_URL, LOSSES_EXPECTED_URL]
    # 列表查询**不进结果缓存**（时效性强 + 有限流）：缓存条数一点没涨
    assert api.cache_stats()["entries"] == cached_before


def test_zkb_search_url_assertion_would_catch_a_missing_past_seconds(
    zkb_state: None,
) -> None:
    """**断言非永真**：把 `pastSeconds` 段去掉 → URL 立刻不等（这条断言真的在断言）。"""
    recorder = _Recorder(get_payload=_fixture(LOSSES_FIXTURE))
    envelope = api.zkb_search(
        ship=22444, past_seconds=604800, side="losses", requester=recorder
    )
    assert envelope["ok"] is True
    real = recorder.get_urls()[-1]
    without = api.zkb_url(side="losses", filters={"ship": 22444})
    assert real == LOSSES_EXPECTED_URL
    assert "pastSeconds/604800" in real
    assert without == "https://zkillboard.com/api/losses/shipTypeID/22444/"
    assert without != real, "两个 URL 相等就说明这条断言没在断言时间窗口"


def test_zkb_search_side_both_merges_and_dedupes(zkb_state: None) -> None:
    """`both` 把 kills / losses 合并：**重叠的 killmail_id 只留一条**，不重不漏。"""
    losses = _fixture(LOSSES_FIXTURE)
    overlap = losses[0]
    extra = {**losses[1], "killmail_id": 139000001, "killmail_time": "2026-10-19T00:00:01Z"}

    def requester(url: str, body: object | None = None) -> object:
        # kills 回「重叠的那一条 + 一条更新的」，losses 回「重叠的那一条 + 另一条」
        return [overlap, extra] if "/kills/" in url else [overlap, losses[1]]

    envelope = api.zkb_search(ship=22444, side="both", requester=requester)
    assert envelope["ok"] is True, envelope
    rows = envelope["result"]["kills"]
    ids = [row["killmail_id"] for row in rows]
    assert len(ids) == len(set(ids)) == 3, f"应去重到 3 条，实际 {ids}"
    assert envelope["result"]["count"] == 3
    assert rows[0]["killmail_id"] == 139000001, "去重后仍按时间倒序"
    assert rows[0]["killmail_time"] >= rows[-1]["killmail_time"]


def test_zkb_search_limit_only_truncates_the_list(zkb_state: None) -> None:
    """`limit` 只截断 `kills`；`count` / `aggregates` 仍按整页算，并在 `note` 里说明。"""
    payload = _fixture(LOSSES_FIXTURE)
    recorder = _Recorder(get_payload=payload)

    full = api.zkb_search(ship=22444, side="losses", limit=200, requester=recorder)
    short = api.zkb_search(ship=22444, side="losses", limit=3, requester=recorder)
    assert full["ok"] is True and short["ok"] is True

    assert full["result"]["count"] == short["result"]["count"] == 12
    assert len(full["result"]["kills"]) == 12
    assert len(short["result"]["kills"]) == 3
    assert short["result"]["aggregates"] == full["result"]["aggregates"], "聚合必须按整页算"
    assert "limit=3" in short["result"]["note"]
    assert "整页 12 条" in short["result"]["note"]
    assert "limit=" not in full["result"]["note"], "没截断时不该画蛇添足"

    # 聚合口径：总数 = 原样相加，中位数 = 真实中位数（不取整、不换算）
    values = [row["zkb"]["totalValue"] for row in payload]
    aggregates = full["result"]["aggregates"]
    assert aggregates["total_value"] == pytest.approx(sum(values))
    assert aggregates["median_value"] == pytest.approx(statistics.median(values))
    assert aggregates["by_ship"] == [
        {"name": "Sleipnir", "count": 12, "value": pytest.approx(sum(values))}
    ]
    times = [row["killmail_time"] for row in payload]
    assert aggregates["time_span"] == {"earliest": min(times), "latest": max(times)}
    assert full["result"]["note"].startswith("list 级数据来自 zkb（无 items）")


def test_zkb_search_respects_the_1_2s_gap(zkb_state: None) -> None:
    """**限流器真的生效**：两条请求的开始时刻相差 ≥ `ZKB_MIN_INTERVAL_S`（虚拟时钟，不真等）。"""
    clock_state = {"now": 1000.0}
    starts: list[float] = []
    sleeps: list[float] = []

    def clock() -> float:
        return clock_state["now"]

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        clock_state["now"] += seconds

    def requester(url: str, body: object | None = None) -> object:
        starts.append(clock_state["now"])
        return _fixture(LOSSES_FIXTURE)

    envelope = api.zkb_search(
        ship=22444, side="both", requester=requester, clock=clock, sleeper=sleeper
    )
    assert envelope["ok"] is True, envelope
    assert len(starts) == 2
    assert starts[1] - starts[0] >= api.ZKB_MIN_INTERVAL_S - 1e-9, (
        f"两次请求只隔了 {starts[1] - starts[0]} s（会被 zkb 限流）"
    )
    assert sleeps == [pytest.approx(api.ZKB_MIN_INTERVAL_S)]


def test_zkb_search_429_is_rate_limited(
    zkb_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """zkb 回 429 / 403 → `rate_limited`（**不是** `network_failed`，也不抛异常）。"""
    for status in (429, 403):

        def boom(*_args: object, **_kwargs: object) -> None:
            raise urllib.error.HTTPError(
                "https://zkillboard.com/api/losses/shipTypeID/22444/",
                status,
                "too fast",
                None,
                None,
            )

        monkeypatch.setattr(api.urllib.request, "urlopen", boom)
        api._reset_zkb_state()
        envelope = api.zkb_search(ship=22444, side="losses")
        assert envelope["ok"] is False
        assert envelope["error"]["kind"] == "rate_limited", envelope
        assert envelope["error"]["status"] == status
        assert envelope["error"]["hint"]["min_interval_s"] == api.ZKB_MIN_INTERVAL_S
        assert envelope["error"]["url"].startswith("https://zkillboard.com/api/losses/")


def test_zkb_search_unknown_entity_gives_candidates(zkb_state: None) -> None:
    """名字解析不出唯一答案 → `unknown_entity` + `candidates`（带 `kind`），**不出网查战报**。"""

    def make(payload: dict) -> _Recorder:
        api._reset_zkb_state()
        return _Recorder(get_payload=_fixture(LOSSES_FIXTURE), post_payload=payload)

    # ① 该分类多个同名候选 → 不猜
    recorder = make(
        {"systems": [{"id": 30000142, "name": "Jita"}, {"id": 30000143, "name": "Jita"}]}
    )
    ambiguous = api.zkb_search(solar_system="Jita", requester=recorder)
    assert ambiguous["ok"] is False
    assert ambiguous["error"]["kind"] == "unknown_entity"
    assert ambiguous["error"]["field"] == "solar_system"
    assert [row["id"] for row in ambiguous["error"]["candidates"]] == [30000142, 30000143]
    assert recorder.get_urls() == []

    # ② 名字只出现在**别的分类**里 → 仍然 unknown_entity（但把真实分类当候选给出来）
    recorder = make({"characters": [{"id": 282567669, "name": "Sleipnir"}]})
    wrong_kind = api.zkb_search(ship="Sleipnir", requester=recorder)
    assert wrong_kind["ok"] is False
    assert wrong_kind["error"]["kind"] == "unknown_entity"
    assert wrong_kind["error"]["candidates"] == [
        {"id": 282567669, "name": "Sleipnir", "kind": "character"}
    ]
    assert recorder.get_urls() == []

    # ③ 一个都没有 → 照样 unknown_entity（不给假候选）
    recorder = make({})
    nothing = api.zkb_search(ship="Nope", requester=recorder)
    assert nothing["ok"] is False
    assert nothing["error"]["kind"] == "unknown_entity"
    assert not nothing["error"].get("candidates")


def test_zkb_ship_name_picks_the_inventory_type_not_the_character(zkb_state: None) -> None:
    """`"Sleipnir"` 在 ESI 里同时是**角色**和**船型** → 必须取 `inventory_types`（22444）。"""
    recorder = _Recorder(
        get_payload=_fixture(LOSSES_FIXTURE), post_payload=_fixture(IDS_FIXTURE)
    )
    envelope = api.zkb_search(
        ship="Sleipnir", past_seconds=604800, side="losses", requester=recorder
    )
    assert envelope["ok"] is True, envelope
    resolved = envelope["result"]["query"]["resolved"]["ship"]
    assert resolved == {"id": 22444, "name": "Sleipnir"}
    assert resolved["id"] != 282567669, "282567669 是那个同名的**角色**"

    # 整数 id 走另一条路：不联网解析，名字从 SDE 拿
    api._reset_zkb_state()
    recorder_by_id = _Recorder(get_payload=_fixture(LOSSES_FIXTURE))
    by_id = api.zkb_search(ship=22444, side="losses", requester=recorder_by_id)
    assert by_id["ok"] is True
    assert by_id["result"]["query"]["resolved"]["ship"] == {"id": 22444, "name": "Sleipnir"}
    assert api.ESI_UNIVERSE_IDS_API not in recorder_by_id.urls


def test_zkb_name_resolution_is_cached(zkb_state: None) -> None:
    """同一个名字第二次只解析一次（进程内缓存）—— 有限流时这一条很值钱。"""
    recorder = _Recorder(
        get_payload=_fixture(LOSSES_FIXTURE), post_payload=_fixture(IDS_FIXTURE)
    )
    for _ in range(2):
        assert api.zkb_search(
            ship="Sleipnir", past_seconds=604800, side="losses", requester=recorder
        )["ok"] is True
    assert recorder.urls.count(api.ESI_UNIVERSE_IDS_API) == 1, recorder.urls


def test_zkb_search_rejects_bad_arguments_without_network(zkb_state: None) -> None:
    """参数不合法 → `invalid_argument`，且**一次请求都不发**（省配额 + 错误更直接）。"""
    cases = {
        "startTime": {"ship": 22444, "startTime": 20261010},
        "end_time": {"ship": 22444, "end_time": 20261012},
        "past_seconds": {"ship": 22444, "past_seconds": "last week"},
        "limit": {"ship": 22444, "limit": api.ZKB_PAGE_SIZE + 1},
        "page": {"ship": 22444, "page": 0},
        "side": {"ship": 22444, "side": "all"},
        "typo": {"shipp": 22444},
        "filters": {"past_seconds": 604800},
    }
    for name, kwargs in cases.items():
        recorder = _Recorder(get_payload=_fixture(LOSSES_FIXTURE))
        envelope = api.zkb_search(requester=recorder, **kwargs)
        assert envelope["ok"] is False, f"{name} 应该被拒绝"
        assert envelope["error"]["kind"] == "invalid_argument", (name, envelope)
        assert recorder.urls == [], f"{name} 被拒绝时不该出网：{recorder.urls}"


# ---- T-52：列表里的受害者身份（只查返回的行 / 一次批量 / 缺项不编）------------


def _zkb_identity_recorder(*, drop: "tuple[int, ...]" = ()) -> _Recorder:
    """战报列表 + 真实 names 样本；名字请求仍然走注入的 requester（不联网）。"""
    hidden = set(drop)
    return _Recorder(
        get_payload=_fixture(LOSSES_FIXTURE),
        post_payload=_fixture(IDS_FIXTURE),
        names_payload=[entry for entry in _names_entries() if entry["id"] not in hidden],
    )


def test_zkb_search_fills_victim_names(zkb_state: None) -> None:
    """每行 `victim` 都带驾驶员 / 军团 / 联盟名字（**非空真名**），并且只发一次 names 请求。"""
    api.clear_caches()
    recorder = _zkb_identity_recorder()

    envelope = api.zkb_search(
        ship="Sleipnir", past_seconds=604800, side="losses", requester=recorder
    )
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert len(result["kills"]) == 12
    for row in result["kills"]:
        victim = row["victim"]
        assert victim["character"]["name"], f"killmail {row['killmail_id']} 缺驾驶员名字"
        assert victim["corporation"]["name"], "军团名字必须有"
        assert victim["ship"]["name"], "受害者船名来自 SDE"
        assert victim["ship"]["type_id"] == row["ship"]["type_id"]
    first = result["kills"][0]
    assert first["killmail_id"] == 139265742
    assert first["victim"]["character"]["id"] != 0
    assert result["warnings"] == [], f"这批 id 全能解析，不该有 warning：{result['warnings']}"

    assert len(recorder.names_batches) == 1, "12 行的身份应当合成一批查完"
    expected = set(api._zkb_row_name_ids(result["kills"]))
    assert set(recorder.names_batches[0]) == expected


def test_zkb_search_only_resolves_names_for_returned_rows(zkb_state: None) -> None:
    """`limit=2` 时 names 请求体里只有那 2 行的 id（**不为整页 12 条都解析**）。"""
    api.clear_caches()
    recorder = _zkb_identity_recorder()

    envelope = api.zkb_search(
        ship="Sleipnir", past_seconds=604800, side="losses", limit=2, requester=recorder
    )
    assert envelope["ok"] is True, envelope
    shown = envelope["result"]["kills"]
    assert len(shown) == 2
    assert envelope["result"]["count"] == 12, "count 仍按整页算"

    assert len(recorder.names_batches) == 1, "一次批量，不是逐行查"
    asked = set(recorder.names_batches[0])
    assert asked == set(api._zkb_row_name_ids(shown))
    assert len(asked) <= 2 * 3, f"只该问这 2 行的身份，实际问了 {len(asked)} 个 id"
    assert all(row["victim"]["character"]["name"] for row in shown)


def test_zkb_search_missing_name_is_null_and_warned(zkb_state: None) -> None:
    """names 响应缺项 → 该 `name` 为 `null` + `warnings` 说清数量（**不编名字**）。"""
    api.clear_caches()
    rows = _fixture(LOSSES_FIXTURE)
    victim_character = rows[0]["victim"]["character_id"]
    recorder = _zkb_identity_recorder(drop=(victim_character,))

    envelope = api.zkb_search(
        ship="Sleipnir", past_seconds=604800, side="losses", requester=recorder
    )
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    victim = result["kills"][0]["victim"]
    assert victim["character"] == {"id": victim_character, "name": None}
    assert victim["corporation"]["name"], "缺一个不该连累别人"
    assert any("个实体名未解析" in warning for warning in result["warnings"]), result["warnings"]


# =============================================================================
# 十四、实体画像（T-55）：`zkb_stats`
# =============================================================================

STATS_CHARACTER = ZKB_FIXTURES / "stats-character-2117515414.json"
STATS_CORPORATION = ZKB_FIXTURES / "stats-corporation-98821127.json"
STATS_ALLIANCE = ZKB_FIXTURES / "stats-alliance-1354830081.json"
"""三份真实 `/api/stats/` 响应（**未裁剪任何字段**，见 `tests/fixtures/zkb/README.md`）。"""

STATS_CASES = (
    ("character", 2117515414, STATS_CHARACTER),
    ("corporation", 98821127, STATS_CORPORATION),
    ("alliance", 1354830081, STATS_ALLIANCE),
)


class _StatsRecorder:
    """假请求器：**任何 URL** 都回同一份 stats 文档；POST 直接判失败（stats 只该走 GET）。

    不区分 URL 是故意的：如果哪天本层错误地对 ESI 发了一次名字解析，`urls` 里会多一条，
    测试的「只发了一次请求」断言就会红。
    """

    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.urls: list[str] = []
        self.posts: list[object] = []

    def __call__(self, url: str, body: object | None = None) -> object:
        self.urls.append(url)
        if body is not None:
            self.posts.append(body)
        return self.payload


_FAKE_NOW: dict[str, float] = {"now": 0.0}
"""`zkb_stats` 测试共用的虚拟时钟游标（**跨调用单调**）。"""

_SLEPT: list[float] = []
"""虚拟睡眠记录（只记录、**不抛异常**）。

`_zkb_resolve_entity()` 现在会把 clock/sleeper 透传给限流闸门（T-55 修），所以名字解析
那次 `POST /universe/ids/` 也用虚拟时钟。这里保留记录是为了让个别测试能断言
「到底睡了多少」——真实时钟下那个数会是几千秒。
"""


def _instant() -> tuple[Callable[[], float], Callable[[float], None]]:
    """虚拟时钟 + 假睡眠：每次调用前进 10 s，于是**永远不需要真的等 1.2 s**。

    必须是**模块级共享**的游标，不能每次调用新建一个从 0 开始的时钟：限流闸门是全局的
    （`_ZKB_LAST_START`），第二个调用若拿到「又从 0 开始」的时刻，算出的等待会是 1.2 s，
    于是测试真的睡下去。

    基准值取 `0.0`（而不是某个大数）：万一某个测试忘了用 `zkb_state` 复位，留下的时间戳
    也只有十几秒，既不会让真实时钟的后续调用白等，也不会让虚拟时钟白等。
    """

    def clock() -> float:
        _FAKE_NOW["now"] += 10.0
        return _FAKE_NOW["now"]

    def sleeper(seconds: float) -> None:
        _SLEPT.append(seconds)

    return clock, sleeper


def _stats(
    entity: object,
    kind: str,
    path: Path,
    *,
    include: object = None,
    **kwargs: object,
) -> tuple[dict, _StatsRecorder]:
    """跑一次 `zkb_stats`（注入 fixture 请求器 + **虚拟时钟**），返回 `(信封, 记录器)`。"""
    recorder = _StatsRecorder(_fixture(path))
    virtual_clock, sleeper = _instant()
    envelope = api.zkb_stats(
        entity,
        entity_type=kind,
        requester=recorder,
        include=include,
        clock=virtual_clock,
        sleeper=sleeper,
        **kwargs,
    )
    return envelope, recorder


def _result_size(result: object) -> int:
    """紧凑 JSON 的字节数（= MCP 实际发出的量级）。"""
    return len(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def test_zkb_stats_url_always_ends_with_a_slash(zkb_state: None) -> None:
    """URL 形态：`/api/stats/<段名>/<id>/`，**结尾斜杠不可省**。

    实测：省了结尾 `/`，zkb 回 `200` + **0 字节空体**（不是报错）—— 所以这条不是洁癖，
    是唯一能让「空体」永不发生的结构性保证。三个类型段的映射也必须对。
    """
    assert api.zkb_stats_url("character", 2117515414) == (
        "https://zkillboard.com/api/stats/characterID/2117515414/"
    )
    assert api.zkb_stats_url("corporation", 98821127) == (
        "https://zkillboard.com/api/stats/corporationID/98821127/"
    )
    assert api.zkb_stats_url("alliance", 1354830081) == (
        "https://zkillboard.com/api/stats/allianceID/1354830081/"
    )
    for kind in api.ZKB_STATS_ENTITY_KINDS:
        url = api.zkb_stats_url(kind, 1)
        assert url.endswith("/"), f"{kind} 的 URL 少了结尾斜杠：{url}"
        assert f"/stats/{api.ZKB_STATS_SEGMENTS[kind]}/1/" in url


def test_zkb_stats_default_shape_is_every_key_present(zkb_state: None) -> None:
    """默认形状：**所有键恒在**（zkb 省空块是它的问题），且三实体都 ≤ 4 KB。

    zkb 三份样本的顶层键是 59 / 48 / 49（各不相同的省略方式），所以这里断言的是
    **我们补出来的固定形状**，不是 zkb 的键数。
    """
    for kind, entity, path in STATS_CASES:
        envelope, recorder = _stats(entity, kind, path)
        assert envelope["ok"] is True, envelope
        result = envelope["result"]
        assert recorder.urls == [api.zkb_stats_url(kind, entity)], "只该发一次 GET"
        assert recorder.posts == [], "stats 不该发 POST（名字解析在纯 id 下不该触发）"

        assert set(result) == {
            "entity", "combat", "style", "activity_7d", "top", "note", "warnings"
        }, f"{kind} 的顶层键不是固定形状：{sorted(result)}"
        assert set(result["entity"]) == {
            "type", "id", "name", "ticker", "security_status", "founded"
        }
        assert set(result["combat"]) == {
            "ships_destroyed", "ships_lost", "isk_destroyed", "isk_lost",
            "points_destroyed", "points_lost", "efficiency",
        }
        assert set(result["style"]) == {
            "danger_ratio", "gang_ratio", "solo_ratio", "avg_gang_size",
            "solo_kills", "solo_losses", "source", "formula", "notes",
        }
        assert set(result["activity_7d"]) == set(api.ZKB_STATS_ACTIVITY_KEYS)
        assert set(result["top"]) == {
            "characters", "corporations", "alliances", "ships", "systems", "locations"
        }, f"{kind} 的 top 不是 6 类"
        assert "regions" not in result["top"], "zkb 没有区域榜，不该编一个 regions 出来"
        assert result["style"]["formula"] is None, "四个 ratio 的公式未公开，必须是 null"

        size = _result_size(result)
        assert size <= 4096, f"{kind} 的默认形状 {size} B 超过 4 KB 预算"
        assert envelope["meta"]["bytes"] == size
        assert "blocks" not in result, "默认不该带任何大块"


def test_zkb_stats_transcribes_the_measured_values(zkb_state: None) -> None:
    """三实体的实测值逐项钉住（**改了任何一个都会红**）。

    `solo_ratio` / `avg_gang_size` 的**量纲**在这条里体现：`1.8` 而不是 `0.018`，
    `3.6` 而不是 `0.036`。
    """
    expected = {
        "character": dict(
            ships_destroyed=823, ships_lost=384, isk_destroyed=222758096336,
            isk_lost=45961938028, points_destroyed=3693, points_lost=1622,
            danger_ratio=69, gang_ratio=99, solo_ratio=1.8, avg_gang_size=3.6,
            solo_kills=14, solo_losses=50, name="SixtyEverSnake",
            ticker=None, security_status=4.3, founded="2020-09-03T21:05:25Z",
        ),
        "corporation": dict(
            ships_destroyed=734, ships_lost=187, isk_destroyed=94220569550,
            isk_lost=61029355087, points_destroyed=5595, points_lost=952,
            danger_ratio=84, gang_ratio=89, solo_ratio=14.7, avg_gang_size=2,
            solo_kills=85, solo_losses=17, name="The big boys",
            ticker="SE4E", security_status=None, founded="2025-12-15T23:19:12Z",
        ),
        "alliance": dict(
            ships_destroyed=3979640, ships_lost=3647189,
            isk_destroyed=793436069915270, isk_lost=465835324896055,
            points_destroyed=13796523, points_lost=16070650,
            danger_ratio=47, gang_ratio=89, solo_ratio=11.2, avg_gang_size=12,
            solo_kills=441760, solo_losses=489270, name="Goonswarm Federation",
            ticker="CONDI", security_status=None, founded="2010-06-01T05:36:00Z",
        ),
    }
    for kind, entity, path in STATS_CASES:
        want = expected[kind]
        envelope, _recorder = _stats(entity, kind, path)
        assert envelope["ok"] is True, envelope
        result = envelope["result"]
        entity_block = result["entity"]
        combat = result["combat"]
        style = result["style"]
        assert entity_block["type"] == kind
        assert entity_block["id"] == entity
        assert entity_block["name"] == want["name"]
        assert entity_block["ticker"] == want["ticker"], "角色没有 ticker，公司/联盟才有"
        assert entity_block["security_status"] == want["security_status"], (
            "安全等级只对角色有意义；公司/联盟必须是 null（不拿 0 冒充）"
        )
        assert entity_block["founded"] == want["founded"], (
            "角色读 info.birthday，公司/联盟读 info.date_founded"
        )
        for key in ("ships_destroyed", "ships_lost", "isk_destroyed", "isk_lost",
                    "points_destroyed", "points_lost"):
            assert combat[key] == want[key], f"{kind}.combat.{key} 变了"
        for key in ("danger_ratio", "gang_ratio", "solo_ratio", "avg_gang_size",
                    "solo_kills", "solo_losses"):
            assert style[key] == want[key], f"{kind}.style.{key} 变了"


def test_zkb_stats_danger_ratio_assertion_is_not_vacuous(
    zkb_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**非永真证明**：把 `danger_ratio` 写死成 `0`，上一条的断言必须红。

    不做这一步，「`danger_ratio == 69`」就不能证明是真的从响应里读出来的 —— 它也可能
    恒等于某个常量而永远通过（PROGRESS 第 180 / 196 条：空转的断言比没有断言更危险）。
    """
    original = api._stats_style_block

    def broken(payload: dict) -> dict:
        block = original(payload)
        block["danger_ratio"] = 0
        return block

    monkeypatch.setattr(api, "_stats_style_block", broken)
    envelope, _recorder = _stats(2117515414, "character", STATS_CHARACTER)
    assert envelope["ok"] is True, envelope
    got = envelope["result"]["style"]["danger_ratio"]
    assert got == 0, "注入没生效，这条证明无效"
    assert got != 69, "主断言在这条注入下必须红（否则它是永真的）"


def test_zkb_stats_efficiency_is_marked_derived_and_differs_from_danger_ratio(
    zkb_state: None,
) -> None:
    """`efficiency` 是我们按 isk 派生的，**与 zkb 的 `dangerRatio` 不是同一个数**。

    同一角色实测：isk 效率 0.829，而 dangerRatio 69 —— 把 82.9% 说成「威胁度」就是错。
    """
    envelope, _recorder = _stats(2117515414, "character", STATS_CHARACTER)
    efficiency = envelope["result"]["combat"]["efficiency"]
    assert efficiency["value"] == 0.829
    assert efficiency["kind"] == "derived"
    assert efficiency["formula"] == "isk_destroyed / (isk_destroyed + isk_lost)"
    assert "danger_ratio" in efficiency["note"], "必须点名它 ≠ danger_ratio"
    assert envelope["result"]["style"]["danger_ratio"] == 69
    assert round(efficiency["value"] * 100, 1) != 69, (
        "如果这两个数字相等，那么「它们不是同一个东西」这句话就没意义了"
    )


def test_zkb_stats_activity_not_applicable_is_silent(zkb_state: None) -> None:
    """`not_applicable` ≠ 「不知道」：本类型天生没有的键给 `null`，**不产生 warning**。

    每次查角色都报一条「characters/corporations 不适用」是噪音，不是信号 ——
    天天报警的告警等于没有告警。
    """
    envelope, _recorder = _stats(2117515414, "character", STATS_CHARACTER)
    result = envelope["result"]
    activity = result["activity_7d"]
    assert activity["ships"] == 4 and activity["systems"] == 5
    assert activity["regions"] == 4 and activity["kills"] == 8
    assert activity["characters"] is None, "角色自己没有 characters 这一栏"
    assert activity["corporations"] is None
    assert result["warnings"] == [], f"不该有任何 warning，实际 {result['warnings']}"
    assert "field_reasons" not in result, "not_applicable 不进 field_reasons"

    # 联盟两种都有（实测 activepvp 有 characters / corporations）
    alliance, _rec = _stats(1354830081, "alliance", STATS_ALLIANCE)
    assert alliance["result"]["activity_7d"]["characters"] == 4287
    assert alliance["result"]["activity_7d"]["corporations"] == 173
    assert alliance["result"]["warnings"] == []


def test_zkb_stats_activity_missing_on_a_required_key_is_reported(
    zkb_state: None,
) -> None:
    """**本该有却被省略**才进 `field_reasons`（与 `not_applicable` 分开）。

    用「角色文档 + 声称是联盟」造一个真缺失的现场：联盟本该有 `characters`。
    """
    document = _fixture(STATS_CHARACTER)
    recorder = _StatsRecorder(document)
    virtual_clock, sleeper = _instant()
    envelope = api.zkb_stats(
        1354830081, entity_type="alliance", requester=recorder,
        clock=virtual_clock, sleeper=sleeper,
    )
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert result["activity_7d"]["characters"] is None
    assert result["field_reasons"] == {
        "characters": "zkb_response_missing",
        "corporations": "zkb_response_missing",
    }, "本该有的键缺失才进 field_reasons"
    assert result["warnings"] == [], "field_reasons 不进 warnings"


def test_zkb_stats_top_keeps_empty_buckets_and_has_no_regions(zkb_state: None) -> None:
    """空榜返回 `[]` 且**键不省略**；`top` 是 6 类，**没有 `regions`**。

    角色 / 公司样本的 `alliances` 榜实测为空 —— 那不是错误，是形状。
    """
    for kind, entity, path in STATS_CASES:
        envelope, _recorder = _stats(entity, kind, path)
        top = envelope["result"]["top"]
        assert "alliances" in top, "空榜也要保留键"
        assert isinstance(top["alliances"], list)
    character_top = _stats(2117515414, "character", STATS_CHARACTER)[0]["result"]["top"]
    assert character_top["alliances"] == []
    assert len(character_top["characters"]) == 1
    assert len(character_top["systems"]) == 3
    # 每个元素的形状固定四键，且 count 就是 zkb 的 kills
    for bucket in character_top.values():
        for row in bucket:
            assert set(row) == {"id", "name", "count", "isk"}
    assert character_top["ships"][0] == {
        "id": 11198, "name": "Stiletto", "count": 3, "isk": 182068148.24
    }
    # locations 是「天体 / 站点」级，不是星系
    location_names = [row["name"] for row in character_top["locations"]]
    assert any("Asteroid Belt" in name or "(Star)" in name for name in location_names), (
        f"locations 应该是小行星带 / 恒星这一级，实际 {location_names}"
    )


def test_zkb_stats_top_is_capped_at_five(zkb_state: None) -> None:
    """每类最多前 5 条（联盟的 characters 榜原始有 10 条）。"""
    raw = _fixture(STATS_ALLIANCE)
    raw_count = len(
        next(e for e in raw["topLists"] if e["type"] == "character")["values"]
    )
    assert raw_count > api.ZKB_STATS_TOP_LIMIT, "样本本身要超过 5 条，这条才有意义"
    envelope, _recorder = _stats(1354830081, "alliance", STATS_ALLIANCE)
    top = envelope["result"]["top"]
    for bucket in top.values():
        assert len(bucket) <= api.ZKB_STATS_TOP_LIMIT
    assert len(top["characters"]) == api.ZKB_STATS_TOP_LIMIT


def test_zkb_stats_months_fills_only_internal_gaps(zkb_state: None) -> None:
    """`months` 只补**观测区间内**的空洞，**不向两端外扩**。

    实测角色：zkb 给 29 个月，区间 2021-05..2026-10；补齐后 66 条、其中 37 条是补的 0。
    区间外的月份**一条都不造** —— 「这个月他没活动」和「这个月他还不存在」是两件事。
    """
    envelope, _recorder = _stats(
        2117515414, "character", STATS_CHARACTER, include=["months"]
    )
    assert envelope["ok"] is True, envelope
    block = envelope["result"]["blocks"]["months"]
    assert block["range"] == {"first": "2021-05", "last": "2026-10"}
    assert block["observed_months"] == 29
    assert block["filled_internal_gaps"] == 37
    assert block["truncated"] is False
    data = block["data"]
    keys = sorted(data)
    assert len(keys) == 66, f"补齐后应是 66 个月（29 观测 + 37 空洞），实际 {len(keys)}"
    assert keys[0] == "202105" and keys[-1] == "202610", "端点必须与 range 一致"
    # 区间外一条都不许有
    assert all("202105" <= key <= "202610" for key in keys)
    # 补出来的 0 是「零」而不是「缺字段」：6 个数值键都在
    zero_filled = [key for key, row in data.items() if row["shipsDestroyed"] == 0]
    assert len(zero_filled) >= block["filled_internal_gaps"]
    for row in data.values():
        assert set(row) == {"year", "month", *api.ZKB_STATS_MONTH_FIELDS}
    # 有数据的那一个月原样保留
    assert data["202105"]["shipsDestroyed"] == 17
    assert data["202105"]["iskDestroyed"] == 1257701232


def test_zkb_stats_months_does_not_extend_outside_the_observed_range(
    zkb_state: None,
) -> None:
    """**非永真证明**：把 `months` 缩成两个月再看区间端点 —— 若实现会外扩，这里就红。

    做法：拿一份把 `months` 砍到只剩首尾两个月的文档，断言补齐后**只有中间那些月份**，
    既没有更早的、也没有更晚的。
    """
    document = _fixture(STATS_CHARACTER)
    raw = document["months"]
    keep = sorted(raw)
    document["months"] = {keep[0]: raw[keep[0]], keep[-1]: raw[keep[-1]]}
    recorder = _StatsRecorder(document)
    virtual_clock, sleeper = _instant()
    envelope = api.zkb_stats(
        2117515414, entity_type="character", include=["months"],
        requester=recorder, clock=virtual_clock, sleeper=sleeper,
    )
    block = envelope["result"]["blocks"]["months"]
    first, last = keep[0], keep[-1]
    assert block["observed_months"] == 2
    assert block["range"] == {
        "first": f"{int(first) // 100:04d}-{int(first) % 100:02d}",
        "last": f"{int(last) // 100:04d}-{int(last) % 100:02d}",
    }
    keys = sorted(block["data"])
    assert keys[0] == first and keys[-1] == last, "端点必须落在实测的首末月，不许外扩"
    assert all(first <= key <= last for key in keys)


def test_zkb_stats_include_whitelist_and_hints(zkb_state: None) -> None:
    """`include` 只认白名单；未知块名 → `invalid_argument` + 可用清单（**不静默忽略**）。"""
    document = _fixture(STATS_CHARACTER)
    recorder = _StatsRecorder(document)
    for bad in (["months", "nope"], "months", [123]):
        envelope = api.zkb_stats(
            2117515414, entity_type="character", include=bad, requester=recorder
        )
        assert envelope["ok"] is False, f"{bad!r} 应该被拒"
        error = envelope["error"]
        assert error["kind"] == "invalid_argument"
        assert error["field"] == "include"
        assert error["hint"]["accepted"] == list(api.ZKB_STATS_BLOCKS)
    assert recorder.urls == [], "参数错误不该发任何请求"

    # 重复的块名去重，顺序按给出顺序
    envelope = api.zkb_stats(
        2117515414, entity_type="character",
        include=["groups", "months", "groups"], requester=recorder,
    )
    assert envelope["ok"] is True, envelope
    assert list(envelope["result"]["blocks"]) == ["groups", "months"]


def test_zkb_stats_all_blocks_warn_but_never_truncate(zkb_state: None) -> None:
    """四块全要时：**绝不静默截断**，只在 > 64 KB 时加一条软警告。

    实测联盟四块约 80 KB；单块最大（`months` 33.6 KB）仍远低于 64 KB 硬上限。
    """
    envelope, _recorder = _stats(
        1354830081, "alliance", STATS_ALLIANCE,
        include=list(api.ZKB_STATS_BLOCKS),
    )
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    blocks = result["blocks"]
    assert set(blocks) == set(api.ZKB_STATS_BLOCKS)
    for name, block in blocks.items():
        assert block["truncated"] is False, f"{name} 不该被截断"
        assert "dropped_items" not in block
        assert isinstance(block["bytes"], int) and block["bytes"] > 0
    total = _result_size(result)
    assert total > api.ZKB_STATS_RESPONSE_SOFT_BYTES, "这个样本本来就该超 64 KB"
    assert any(warning.startswith("large_response") for warning in result["warnings"]), (
        f"超了 64 KB 必须有软警告，实际 {result['warnings']}"
    )
    # 软警告只说体积，不说「我截了」
    assert all("truncated" not in warning for warning in result["warnings"])

    # 单块加入后不超 64 KB 的情况**不该**有软警告
    small, _rec = _stats(
        98821127, "corporation", STATS_CORPORATION, include=["rankings"]
    )
    assert small["result"]["warnings"] == []


def test_zkb_stats_error_body_is_unknown_entity_despite_http_200(
    zkb_state: None,
) -> None:
    """`{"error": "Invalid type or id"}`（**HTTP 200**）→ `unknown_entity`，不是 network_failed。

    实测：id 越界、以及「拿公司 id 当联盟」都返回这个正文，状态码是 200 ——
    靠状态码判成败的实现会把它当成成功，然后交出一份空画像。
    """
    payload = api.zkb_stats(
        999999999999, entity_type="character",
        requester=_StatsRecorder({"error": "Invalid type or id"}),
    )
    assert payload["ok"] is False
    assert payload["error"]["kind"] == "unknown_entity"
    assert payload["error"]["raw"] == "Invalid type or id"

    # 类型错配同一个正文（zkb 自己校验 id 与类型）
    mismatched = api.zkb_stats(
        98821127, entity_type="alliance",
        requester=_StatsRecorder({"error": "Invalid type or id"}),
    )
    assert mismatched["error"]["kind"] == "unknown_entity"


def test_zkb_stats_empty_body_is_internal_error_with_warning(zkb_state: None) -> None:
    """`200` + **0 字节空体** → `internal_error` + 说明「URL 构造没问题」的 warning。

    这是最阴的一种：zkb 在 URL 少了结尾 `/` 时就是这样。本层 URL 恒以 `/` 结尾，
    所以线上出现它意味着上游异常，not 参数问题。
    """

    def empty_body(url: str, body: object | None = None) -> object:
        raise api._ApiFailure(
            "network_failed", f"{url} 返回的不是 UTF-8 JSON", url=url, payload_bytes=0
        )

    envelope = api.zkb_stats(
        2117515414, entity_type="character", requester=empty_body
    )
    assert envelope["ok"] is False
    error = envelope["error"]
    assert error["kind"] == "internal_error", error
    assert error["hint"]["url_ends_with_slash"] is True
    assert any("empty_body" in warning for warning in error["warnings"])


def test_zkb_stats_empty_shell_entity_is_unknown_entity(zkb_state: None) -> None:
    """空壳实体（`info` 无 name + 战斗字段全缺）→ `unknown_entity` + warning。

    实测 `stats/characterID/1/`：`200` + 702 B、只有 5 个键。那个 id 存在，但**没有任何
    可报告的画像** —— 不能当成「0 战绩」返回。
    """
    shell = {
        "activepvp": {},
        "info": {"type": "characterID", "id": 1, "corporationID": 0},
        "topIskKillIDs": [],
        "topLists": [],
        "topListsSort": "kills",
    }
    envelope = api.zkb_stats(1, entity_type="character", requester=_StatsRecorder(shell))
    assert envelope["ok"] is False
    error = envelope["error"]
    assert error["kind"] == "unknown_entity"
    assert any("empty_entity" in warning for warning in error["warnings"])


def test_zkb_stats_passes_numbers_through_without_any_rounding(zkb_state: None) -> None:
    """**只转述，不加工**：zkb 给 `12.5` 就必须回 `12.5`，不许变 `12`。

    实测缺陷（测试员抓到，判 high）：旧实现用 `int()` 收口，`dangerRatio: 12.5` 输出 `12`、
    `gangRatio: 45.5` 输出 `45` —— zkb 这几个指标**没有承诺是整数**，取整是静默精度损失。

    这里**一次查全同类字段**（不再只修一处）：
    `danger_ratio` / `gang_ratio` / `solo_ratio` / `avg_gang_size` / `solo_kills` /
    `solo_losses` / `combat` 六项 / `activity_7d` 六项 / `top.*.{count,isk}` / `entity.id` /
    `entity.security_status` / `blocks.months.*` —— 全部原样透传。
    """
    document = _fixture(STATS_CHARACTER)
    document["dangerRatio"] = 12.5
    document["gangRatio"] = 45.5
    document["soloRatio"] = 3.75
    document["avgGangSize"] = 2.5
    document["soloKills"] = 7.5
    document["soloLosses"] = 0.25
    document["shipsDestroyed"] = 100.5
    document["shipsLost"] = 0.5
    document["iskDestroyed"] = 1234.567
    document["iskLost"] = 0.125
    document["pointsDestroyed"] = 3.5
    document["pointsLost"] = 0.75
    document["activepvp"]["kills"]["count"] = 9.5
    document["topLists"][0]["values"][0]["isk"] = 1.2345678
    document["topLists"][0]["values"][0]["kills"] = 4.5
    document["info"]["security_status"] = 4  # int 就该保持 int，不许被 float() 改写成 4.0

    recorder = _StatsRecorder(document)
    virtual_clock, sleeper = _instant()
    envelope = api.zkb_stats(
        2117515414, entity_type="character", include=["months"],
        requester=recorder, clock=virtual_clock, sleeper=sleeper,
    )
    assert envelope["ok"] is True, envelope
    result = envelope["result"]

    style = result["style"]
    assert style["danger_ratio"] == 12.5, f"danger_ratio 被加工了：{style['danger_ratio']!r}"
    assert style["gang_ratio"] == 45.5
    assert style["solo_ratio"] == 3.75
    assert style["avg_gang_size"] == 2.5
    assert style["solo_kills"] == 7.5
    assert style["solo_losses"] == 0.25
    for key in ("danger_ratio", "gang_ratio", "solo_ratio", "avg_gang_size",
                "solo_kills", "solo_losses"):
        assert isinstance(style[key], float), f"{key} 的类型被改了：{type(style[key])}"

    combat = result["combat"]
    for key, want in (
        ("ships_destroyed", 100.5), ("ships_lost", 0.5),
        ("isk_destroyed", 1234.567), ("isk_lost", 0.125),
        ("points_destroyed", 3.5), ("points_lost", 0.75),
    ):
        assert combat[key] == want, f"combat.{key} 被加工了：{combat[key]!r} != {want!r}"

    assert result["activity_7d"]["kills"] == 9.5
    top_row = result["top"]["characters"][0]
    assert top_row["isk"] == 1.2345678, f"top.isk 被四舍五入了：{top_row['isk']!r}"
    assert top_row["count"] == 4.5

    assert result["entity"]["security_status"] == 4
    assert isinstance(result["entity"]["security_status"], int), (
        "zkb 给 int 4 就该是 int 4；`float()` 强转是我们改写了类型"
    )
    assert result["entity"]["id"] == 2117515414

    months = result["blocks"]["months"]
    assert months["data"]["202105"]["iskDestroyed"] == 1257701232


def test_zkb_stats_efficiency_declares_its_precision(zkb_state: None) -> None:
    """唯一允许加工的数是**我们派生的** `efficiency`：它必须声明精度，不能"静默 rounded"。

    与上一条的界线：zkb 的数原样透传；我们算的数要带 `kind` / `formula` / `decimals`。
    """
    envelope, _recorder = _stats(2117515414, "character", STATS_CHARACTER)
    efficiency = envelope["result"]["combat"]["efficiency"]
    assert efficiency["kind"] == "derived"
    assert efficiency["decimals"] == 4, "派生值可以有精度，但必须写出来"
    assert efficiency["formula"] == "isk_destroyed / (isk_destroyed + isk_lost)"
    isk_d = 222758096336
    isk_l = 45961938028
    assert efficiency["value"] == round(isk_d / (isk_d + isk_l), efficiency["decimals"])


def test_zkb_stats_rejects_bool_as_a_number(zkb_state: None) -> None:
    """`bool` 不是数值：`True` 若被当成 `1` 会让「有值」与「值为真」混在一起。"""
    document = _fixture(STATS_CHARACTER)
    document["dangerRatio"] = True
    document["shipsDestroyed"] = False
    recorder = _StatsRecorder(document)
    virtual_clock, sleeper = _instant()
    envelope = api.zkb_stats(
        2117515414, entity_type="character",
        requester=recorder, clock=virtual_clock, sleeper=sleeper,
    )
    assert envelope["ok"] is True, envelope
    assert envelope["result"]["style"]["danger_ratio"] is None
    assert envelope["result"]["combat"]["ships_destroyed"] is None


def test_zkb_stats_entity_type_is_required_and_validated(zkb_state: None) -> None:
    """`entity_type` 必填且三选一；**不靠 id 猜**（同一串数字在三个命名空间都可能存在）。"""
    recorder = _StatsRecorder(_fixture(STATS_CHARACTER))
    for bad in (None, "ship", "Character", 123):
        envelope = api.zkb_stats(2117515414, entity_type=bad, requester=recorder)
        assert envelope["ok"] is False, f"{bad!r} 应该被拒"
        error = envelope["error"]
        assert error["kind"] == "invalid_argument", error
        assert error["field"] == "entity_type"
        assert error["hint"]["accepted"] == list(api.ZKB_STATS_ENTITY_KINDS)
    assert recorder.urls == [], "参数错误不该发任何请求"

    missing_entity = api.zkb_stats(None, entity_type="character", requester=recorder)
    assert missing_entity["ok"] is False
    assert missing_entity["error"]["field"] == "entity"


def test_zkb_stats_rejects_time_range_parameters_without_network(
    zkb_state: None,
) -> None:
    """`/api/stats/` 不接受时间区间 —— 拒绝，且**不发请求**。

    提示语必须指向 stats 自己的语义（它返回全时期累计），`hint.accepted` 只能是那三个
    「无影响」的配装参数 —— 绝不能变成列表接口那份「用 past_seconds + page 回溯」。
    """
    recorder = _StatsRecorder(_fixture(STATS_CHARACTER))
    envelope = api.zkb_stats(
        2117515414, entity_type="character", startTime=20260101, requester=recorder
    )
    assert envelope["ok"] is False
    error = envelope["error"]
    assert error["kind"] == "invalid_argument"
    assert error["field"] == "startTime"
    assert "/api/stats/" in error["message"], "提示必须说清是 stats 接口的语义"
    assert "全时期" in error["message"], "stats 返回的是全时期累计，不是窗口"
    assert error["hint"]["accepted"] == list(api.ZKB_IGNORED_PARAMETERS), (
        "hint 不能指向 past_seconds / page —— stats 没有这两个参数"
    )
    assert recorder.urls == []


def test_zkb_stats_goes_through_the_shared_rate_limiter(zkb_state: None) -> None:
    """出网走**同一个**全局限流闸门（`_ZKB_LAST_START` 会被设置）。"""
    assert api._ZKB_LAST_START is None
    _envelope, _recorder = _stats(2117515414, "character", STATS_CHARACTER)
    assert api._ZKB_LAST_START is not None, "stats 必须复用 zkb 的限流闸门"


def test_zkb_stats_resolves_a_name_through_esi(zkb_state: None) -> None:
    """名字 → id 复用 `_zkb_resolve_entity`（走 ESI `/universe/ids/`），**不另写一份解析**。"""
    seen: list[tuple[str, object]] = []

    def requester(url: str, body: object | None = None) -> object:
        seen.append((url, body))
        if body is not None:
            return {"characters": [{"id": 2117515414, "name": "SixtyEverSnake"}]}
        return _fixture(STATS_CHARACTER)

    virtual_clock, sleeper = _instant()
    envelope = api.zkb_stats(
        "SixtyEverSnake", entity_type="character",
        requester=requester, clock=virtual_clock, sleeper=sleeper,
    )
    assert envelope["ok"] is True, envelope
    assert [url for url, _body in seen] == [
        api.ESI_UNIVERSE_IDS_API,
        "https://zkillboard.com/api/stats/characterID/2117515414/",
    ]
    assert seen[0][1] == ["SixtyEverSnake"], "名字解析的请求体就是名字数组"
    assert envelope["result"]["entity"]["name"] == "SixtyEverSnake"


def test_zkb_name_resolution_respects_the_injected_clock(zkb_state: None) -> None:
    """名字解析那次 `POST /universe/ids/` **也**要走注入的虚拟时钟（T-55 修）。

    修之前：`_zkb_resolve_entity()` 不透传 clock/sleeper，那次 POST 用**真实**时钟写
    `_ZKB_LAST_START`，随后虚拟时钟算出的等待量是几千秒 —— 任何「虚拟时钟 + 名字解析」的
    测试都会拿到荒谬的睡眠值（或真的睡下去）。

    这里让时钟每次只前进 1.0 s（< 1.2 s 的最小间隔），于是第二次请求只该补 0.2 s。
    如果仍用真实时钟，记录到的会是个大数（或几十秒），断言立刻红。
    """
    slept: list[float] = []
    cursor = {"now": 0.0}

    def clock() -> float:
        cursor["now"] += 1.0
        return cursor["now"]

    def sleeper(seconds: float) -> None:
        slept.append(seconds)

    def requester(url: str, body: object | None = None) -> object:
        if body is not None:
            return {"characters": [{"id": 2117515414, "name": "SixtyEverSnake"}]}
        return _fixture(STATS_CHARACTER)

    envelope = api.zkb_stats(
        "SixtyEverSnake", entity_type="character",
        requester=requester, clock=clock, sleeper=sleeper,
    )
    assert envelope["ok"] is True, envelope
    assert slept == [pytest.approx(0.2)], (
        f"名字解析没走注入的时钟：记录了 {slept}（期望只补 0.2 s）"
    )
    assert all(value < api.ZKB_MIN_INTERVAL_S for value in slept), (
        "任何一次睡眠都不该超过最小间隔 —— 超过了说明用了另一个时钟"
    )


# -----------------------------------------------------------------------------
# `zkb_search` 与 `zkb_stats` 的错误映射必须**同源**（不得各写一份判定）
# -----------------------------------------------------------------------------


def test_zkb_error_kind_is_shared_by_both_tools(zkb_state: None) -> None:
    """同一份上游正文 → 两个工具给**同一个** `kind`。

    这是真实存在过的不一致：`{"error": "Invalid type or id"}` 在 `zkb_search` 里被
    判 `network_failed`、在 `zkb_stats` 里被判 `unknown_entity`。同一类错误两个 kind，
    agent 无从判断该「换参数」还是「等一会儿重试」。现在共用 `_zkb_error_kind()`。
    """
    body = {"error": "Invalid type or id"}

    search = api.zkb_search(
        ship=22444, side="losses", requester=_Recorder(get_payload=body)
    )
    stats = api.zkb_stats(
        999999999999, entity_type="character", requester=_StatsRecorder(body)
    )
    assert search["error"]["kind"] == "unknown_entity", search
    assert stats["error"]["kind"] == "unknown_entity", stats
    assert search["error"]["kind"] == stats["error"]["kind"]

    # 说不出原因的拒绝 → 两边都 network_failed（**不猜成实体问题**）
    vague = {"error": "something odd happened"}
    search_vague = api.zkb_search(
        ship=22444, side="losses", requester=_Recorder(get_payload=vague)
    )
    stats_vague = api.zkb_stats(
        2117515414, entity_type="character", requester=_StatsRecorder(vague)
    )
    assert search_vague["error"]["kind"] == "network_failed", search_vague
    assert stats_vague["error"]["kind"] == "network_failed", stats_vague


def test_zkb_error_kind_table(zkb_state: None) -> None:
    """`_zkb_error_kind()` 的判定表（**只看正文，不看状态码**）。"""
    cases = {
        "Invalid type or id": "unknown_entity",
        "invalid type or id": "unknown_entity",  # 大小写不敏感
        "Too many requests": "rate_limited",
        "you are being rate limited": "rate_limited",
        "internal server oil": "network_failed",
        "": "network_failed",
    }
    for text, want in cases.items():
        kind, why = api._zkb_error_kind(text)
        assert kind == want, f"{text!r} 应判 {want}，实际 {kind}（{why}）"


def test_zkb_stats_transport_error_classes_are_distinct(
    zkb_state: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """四种上游/自身情况 → 四个**两两不同**的 kind，且 `internal_error` 只由我们的异常触发。

    | 上游/自身 | kind |
    |---|---|
    | 429 / 403 | `rate_limited`（+ `hint.min_interval_s`） |
    | 非 JSON / 被截断 | `network_failed` |
    | 0 字节空体 | `internal_error` + `empty_body` warning（我们自己 URL 构造的锅） |
    | 我们自己的异常 | `internal_error` |
    """
    url = "https://zkillboard.com/api/stats/characterID/2117515414/"

    # ① 429 / 403 → rate_limited，且带退避提示
    rate_kind = ""
    for status in (429, 403):

        def boom(*_args: object, **_kwargs: object) -> None:
            raise urllib.error.HTTPError(url, status, "too fast", None, None)

        monkeypatch.setattr(api.urllib.request, "urlopen", boom)
        api._reset_zkb_state()
        envelope = api.zkb_stats(2117515414, entity_type="character")
        assert envelope["ok"] is False
        assert envelope["error"]["kind"] == "rate_limited", envelope
        assert envelope["error"]["status"] == status
        assert envelope["error"]["hint"]["min_interval_s"] == api.ZKB_MIN_INTERVAL_S, (
            "限流必须告诉调用方「等多久再来」，否则 agent 只会当成我们坏了"
        )
        rate_kind = envelope["error"]["kind"]

    # ② 非 JSON（有字节）→ network_failed
    class _Broken:
        status = 200

        def read(self) -> bytes:
            return b"<html>502 Bad Gateway</html>"

        def __enter__(self) -> "_Broken":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(api.urllib.request, "urlopen", lambda *_a, **_k: _Broken())
    api._reset_zkb_state()
    broken = api.zkb_stats(2117515414, entity_type="character")
    assert broken["error"]["kind"] == "network_failed", broken

    # ③ 0 字节空体 → internal_error + empty_body warning
    def empty(url_: str, body: object | None = None) -> object:
        raise api._ApiFailure(
            "network_failed", "不是 JSON", url=url_, payload_bytes=0
        )

    api._reset_zkb_state()
    hollow = api.zkb_stats(2117515414, entity_type="character", requester=empty)
    assert hollow["error"]["kind"] == "internal_error", hollow
    assert any("empty_body" in w for w in hollow["error"]["warnings"])

    # ④ 我们自己的异常 → internal_error（**唯一**该用它的地方），并且带 detail
    def broken_internals(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("我们自己写崩了")

    monkeypatch.setattr(api, "_stats_payload", broken_internals)
    api._reset_zkb_state()
    own_bug = api.zkb_stats(
        2117515414, entity_type="character",
        requester=_StatsRecorder(_fixture(STATS_CHARACTER)),
    )
    assert own_bug["error"]["kind"] == "internal_error", own_bug
    assert "我们自己写崩了" in str(own_bug["error"].get("detail")), (
        "自己的异常要把 detail 带出来，否则排查时只知道「内部错误」"
    )

    # 三种**上游**情况两两不同：限流 / 非 JSON / 空体
    assert {
        rate_kind,
        broken["error"]["kind"],
        hollow["error"]["kind"],
    } == {"rate_limited", "network_failed", "internal_error"}, (
        "三种上游情况的 kind 必须两两不同（否则 agent 分不清该等、该重试、还是该报障）"
    )


# =============================================================================
# 五、L2 的形状与「不丢诊断」（4/5：代理层暴露的接口侧问题）
# =============================================================================


GHOST_FLAG = {
    "id": "ghost_rule_no_such_view",
    "view": "not_a_registered_view",
    "message": "一条 view 名字不存在的诊断规则（测试注入）",
    "evidence": {"injected": True},
}
"""一条 `view` 名**不存在**的诊断 → 归不进任何视角，只能进顶层兜底桶。"""


@pytest.fixture()
def ghost_rule(monkeypatch: pytest.MonkeyPatch) -> dict:
    """把所有诊断规则**再多加一条**「视角名不存在」的。

    打桩在 `noobbaozi.project.diagnostics.evaluate` 上：渲染层与 api 层的
    `_collect_flags()` 都调它，所以三层看到的是同一批 flag（这才是真现场）。
    """
    from noobbaozi.project import diagnostics as diagnostics

    original = diagnostics.evaluate

    def with_ghost(ctx: object) -> list[dict]:
        return [*original(ctx), dict(GHOST_FLAG)]

    monkeypatch.setattr(diagnostics, "evaluate", with_ghost)
    api.clear_caches()
    return GHOST_FLAG


def test_detail_keeps_flags_that_belong_to_no_view(
    ghost_rule: dict, eft_text: str
) -> None:
    """`fit_detail` 的顶层 `flags` 必须装「不属于任何视角」的诊断（**一条都不许丢**）。

    这是 L2 的真缺陷：`render_detail()` 只构建被请求的视角，它的 residual 在渲染层内部
    就被丢掉了 —— 于是「同一条诊断，L0 的顶层 flags 看得见、L2 深挖时**彻底消失**」。
    踩的是「不静默忽略」这条红线。
    """
    envelope = api.fit_detail(eft_text)
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert "flags" in result, f"detail 的 result 必须有顶层 flags 桶：{sorted(result)}"

    ids = [flag.get("id") for flag in result["flags"]]
    assert ghost_rule["id"] in ids, (
        f"孤儿诊断必须出现在 detail 顶层 flags 里，实际 {ids}"
    )
    # 形状 = 完整 flag 对象（与 L1 视角条目里的 flags 同形）
    picked = next(flag for flag in result["flags"] if flag.get("id") == ghost_rule["id"])
    assert set(picked) >= {"id", "view", "message"}
    assert picked["view"] == ghost_rule["view"]

    # 只查一个视角时**也一样**（顶层桶的语义与本次查哪个视角无关）
    one_view = api.fit_detail(eft_text, view="mobility")
    assert one_view["ok"] is True
    assert ghost_rule["id"] in [f.get("id") for f in one_view["result"]["flags"]]

    # L0 也能看到同一份东西（两层不再分叉）
    l0 = api.fit_index(eft_text)
    assert l0["ok"] is True
    assert ghost_rule["id"] in [f.get("id") for f in l0["result"]["flags"]]


def test_detail_flags_bucket_is_empty_by_default(eft_text: str) -> None:
    """没有孤儿诊断时顶层 `flags` 是 **`[]`**（形状恒定，调用方不必判类型）。"""
    api.clear_caches()
    envelope = api.fit_detail(eft_text)
    assert envelope["ok"] is True, envelope
    assert envelope["result"]["flags"] == []


def test_detail_orphan_flags_assertion_is_not_vacuous(
    ghost_rule: dict, eft_text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**配对证明**：把兜底桶摘掉（`_orphan_flags` 返回 `[]`），上一条立刻不成立。

    同一份输入、同一个注入，只去掉那一个函数 —— 所以它证明的不是「恰好没出现」，
    而是「这条断言真的依赖兜底桶」。
    """
    monkeypatch.setattr(api, "_orphan_flags", lambda ctx: [])
    api.clear_caches()
    envelope = api.fit_detail(eft_text)
    assert envelope["ok"] is True, envelope
    assert envelope["result"]["flags"] == []
    assert ghost_rule["id"] not in [f.get("id") for f in envelope["result"]["flags"]]


def test_envelope_skill_tier_hash_matches_l0(eft_text: str) -> None:
    """信封 `meta.skill_tier.hash` 与 L0 的 `result.meta.skill_tier.hash` **必须相同**。

    为什么信封也要 hash：`count` 相同**不代表内容相同**（同为 512 条，某个技能 4 级 vs
    5 级时 count 一模一样）。agent 判断「两次分析是不是同一个技能档」只能靠这个摘要。
    """
    api.clear_caches()
    l0 = api.fit_index(eft_text)
    assert l0["ok"] is True, l0
    l0_hash = l0["result"]["meta"]["skill_tier"]["hash"]
    assert isinstance(l0_hash, str) and len(l0_hash) > 0, (
        f"L0 的 skill_tier 必须带 hash，实际 {l0['result']['meta']['skill_tier']}"
    )

    envelopes = {
        "parse": api.parse_fit(eft_text),
        "index": l0,
        "view": api.fit_view(eft_text, "mobility"),
        "detail": api.fit_detail(eft_text),
    }
    for name, envelope in envelopes.items():
        assert envelope["ok"] is True, (name, envelope.get("error"))
        tier = envelope["meta"]["skill_tier"]
        assert tier.get("hash") == l0_hash, (
            f"{name} 的信封 skill_tier.hash 与 L0 不同源：{tier.get('hash')} != {l0_hash}"
        )

    # `fit_killmail` 的输入是**另一份东西**（战报原文），档位自然不同 —— 不能拿它跟
    # 上面那个 hash 比。它只要**带上** hash 就行；顺带用同一份输入走 L0 交叉验一次：
    # 同输入、不同入口 → 同一个技能档 → 同一个 hash。
    killmail_text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")
    killmail_envelope = api.fit_killmail(killmail_text)
    assert killmail_envelope["ok"] is True, killmail_envelope
    killmail_hash = killmail_envelope["meta"]["skill_tier"].get("hash")
    assert isinstance(killmail_hash, str) and killmail_hash, (
        "fit_killmail 的信封也必须带 skill_tier.hash"
    )
    same_input_l0 = api.fit_index(killmail_text)
    assert same_input_l0["ok"] is True, same_input_l0
    assert same_input_l0["result"]["meta"]["skill_tier"]["hash"] == killmail_hash, (
        "同一份杀报输入：L0 与 fit_killmail 信封的技能档 hash 必须一致"
    )

    # hash 对**内容**敏感（不是常量）：换技能档必须换 hash
    all5 = api.fit_index(eft_text, skill_tier=api.SKILL_TIER_ALL5)
    assert all5["ok"] is True
    assert all5["result"]["meta"]["skill_tier"]["hash"] != l0_hash, (
        "换到 all5 档 hash 却没变 —— 那这个摘要证明不了任何事"
    )


def test_envelope_skill_tier_hash_assertion_is_not_vacuous(
    eft_text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**配对证明**：把 `hash` 从 skill_tier 块摘掉，上一条立刻不成立。"""

    def without_hash(name: str, skills: dict) -> dict:
        return {"name": name, "count": len(skills)}

    monkeypatch.setattr(api, "_skill_tier_block", without_hash)
    api.clear_caches()
    envelope = api.fit_index(eft_text)
    assert envelope["ok"] is True, envelope
    assert "hash" not in envelope["meta"]["skill_tier"]



# =============================================================================
# 二十、归因（P3 explain）：fit_explain 的三类口径 / 参数错误 / 幂等
# =============================================================================
#
# 为什么这三类必须分开断言：`Σ contribution == value` 只对 A 可加类成立。B 乘性类
# 给的是因子（factor），把因子相加没有意义；C 投影类是按公式回放的（`层 HP ÷ 共振率`）。
# 用一条断言糊住三类，验收会立刻变成永真或永假（`project/explain.py` 模块 docstring）。

EXPLAIN_A_METRIC = "survivability.ehp_mixed"
EXPLAIN_B_METRIC = "mobility.speed_mps"
EXPLAIN_C_METRIC = "survivability.thinnest.armor.ehp"
EXPLAIN_METRIC_CASES = (
    (EXPLAIN_A_METRIC, "additive"),
    (EXPLAIN_B_METRIC, "multiplicative"),
    (EXPLAIN_C_METRIC, "multiplicative"),
)


def _explain_close(left: float, right: float, rel: float = 1e-9) -> bool:
    """`explain` 自带的容差口径（相对 1e-9 + 绝对 1e-9）。"""
    return abs(left - right) <= max(rel * abs(right), 1e-9)


def _explain_sum(result: dict) -> float:
    """A 类：所有非空 `contribution` 之和（`None` 是「这类没有贡献量」，不是 0）。"""
    return sum(
        float(entry["contribution"])
        for entry in result["sources"]
        if entry.get("contribution") is not None
    )


@pytest.mark.parametrize(("metric", "combine"), EXPLAIN_METRIC_CASES)
def test_fit_explain_covers_all_three_classes(eft_text: str, metric: str, combine: str) -> None:
    """A / B / C 各一个指标：信封成功、`combine` 与类一致、源链非空。"""
    api.clear_caches()
    envelope = api.fit_explain(eft_text, metric)
    assert envelope["ok"] is True, envelope.get("error")
    result = envelope["result"]
    assert result["metric"] == metric
    assert result["combine"] == combine
    assert result["sources"], f"{metric} 必须有源链"
    assert result["value"] is not None
    # 形状：`explain` 的契约字段一个都不许被接口层裁掉
    for key in (
        "view", "unit", "includes_base", "base", "unexplained", "truncated_at_depth",
        "truncated_nodes", "minimality", "has_offsets", "tolerance", "provenance",
    ):
        assert key in result, f"{metric} 缺字段 {key}"


def test_fit_explain_additive_terms_sum_to_value(eft_text: str) -> None:
    """A 可加类：**各项 contribution 之和 == value**（相对 1e-9），`unexplained` 是真 0。"""
    api.clear_caches()
    result = api.fit_explain(eft_text, EXPLAIN_A_METRIC)["result"]
    total = _explain_sum(result)
    assert _explain_close(total, result["value"]), (
        f"可加类加不上：Σ={total!r} value={result['value']!r}"
    )
    assert result["sum_of_contributions"] == result["value"]
    assert result["unexplained"] == 0.0
    assert all(entry["factor"] is None for entry in result["sources"]), "可加类不许出现因子"


def test_fit_explain_multiplicative_is_a_factor_chain_not_a_sum(eft_text: str) -> None:
    """B 乘性类：给 `base` + 因子链，**因子不能相加**（contribution 一律 None）。"""
    api.clear_caches()
    result = api.fit_explain(eft_text, EXPLAIN_B_METRIC)["result"]
    assert result["base"] is not None
    factors = [entry for entry in result["sources"] if entry["factor"] is not None]
    assert factors, "乘性类必须有因子"
    assert all(entry["contribution"] is None for entry in factors), (
        "因子不是贡献量：contribution 必须是 None（否则 agent 会把因子加成一个和）"
    )
    assert result["replay_ok"] is True, "回放必须落回 value"
    assert _explain_close(result["replayed"], result["value"])


def test_fit_explain_projection_gives_formula_and_two_terms(eft_text: str) -> None:
    """C 投影类：`value = 层 HP ÷ 共振率`，两条源 = 被除数 + 除数，且带 `formula`。"""
    api.clear_caches()
    result = api.fit_explain(eft_text, EXPLAIN_C_METRIC)["result"]
    assert result["formula"], "投影类必须给 formula（否则读者只能猜公式）"
    assert len(result["sources"]) == 2, "投影类恒为两项（层 HP + 共振率）"
    hp_entry, resonance_entry = result["sources"]
    assert hp_entry["contribution"] is not None, "第一项是被除的那个数"
    assert resonance_entry["factor"] is not None, "第二项是除数"
    assert _explain_close(hp_entry["contribution"] / resonance_entry["factor"], result["value"])
    assert result["replay_ok"] is True


def test_fit_explain_value_is_full_precision(eft_text: str) -> None:
    """**`value` 是全精度**（不是 L1 的 3 位小数）：这是 A 类加和判据能成立的前提。"""
    api.clear_caches()
    result = api.fit_explain(eft_text, EXPLAIN_A_METRIC)["result"]
    value = result["value"]
    assert isinstance(value, float)
    assert value != round(value, 3), (
        "value 被舍成 3 位了 —— A 类的 Σ == value 会立刻失效（这才是要钉的那件事）"
    )


def test_fit_explain_meta_carries_the_version_triplet(eft_text: str) -> None:
    """信封 `meta` 与其它入口一致：版本三元组 + 技能档 hash + 两个回显参数 + 输入形态。"""
    api.clear_caches()
    envelope = api.fit_explain(eft_text, EXPLAIN_A_METRIC)
    assert envelope["ok"] is True
    meta = envelope["meta"]
    assert meta["spec_version"] == api.SPEC_VERSION
    assert meta["engine_version"] and meta["sde_build"]
    assert meta["source_format"] == "eft"
    assert meta["damage_profile"] == "uniform"
    assert meta["state_policy"] == "engine_default"
    assert meta["skill_tier"]["count"] > 0 and meta["skill_tier"]["hash"]
    # 三元组必须与 L0 是**同一份引擎事实**（不是各写一份）
    l0_meta = api.fit_index(eft_text)["meta"]
    for key in ("spec_version", "engine_version", "sde_build"):
        assert meta[key] == l0_meta[key], f"meta.{key} 与 fit_index 不同源"
    assert meta["skill_tier"]["hash"] == api.fit_index(eft_text)["result"]["meta"]["skill_tier"]["hash"]


def test_fit_explain_accepts_every_registered_metric(eft_text: str) -> None:
    """登记表里的每个指标在 f1 上都可归因（**一个都不许漏**，也不许有半成品）。

    不写死条数：条数属于 `explain.supported_metrics()`，这里只要求「登记了就必须能算」。
    写死数字就成了第二份清单（加一个指标要改两处，迟早漏一处）。
    """
    assert api.EXPLAIN_METRICS, "登记表不能为空"
    for metric in api.EXPLAIN_METRICS:
        api.clear_caches()
        envelope = api.fit_explain(eft_text, metric)
        assert envelope["ok"] is True, f"{metric} 失败：{envelope.get('error')}"
        assert envelope["result"]["metric"] == metric


@pytest.mark.parametrize("depth", [0, 3, -1, 99, True, False, "2", 2.0, None])
def test_fit_explain_bad_depth_is_invalid_argument(eft_text: str, depth: object) -> None:
    """`depth ∉ 1..2`（含 bool / 字符串 / 浮点）→ `invalid_argument` + `field=depth`。"""
    api.clear_caches()
    envelope = api.fit_explain(eft_text, EXPLAIN_A_METRIC, depth=depth)  # type: ignore[arg-type]
    assert envelope["ok"] is False, f"depth={depth!r} 不该成功"
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["field"] == "depth"
    assert envelope["error"]["hint"] == [1, 2]


@pytest.mark.parametrize("metric", ["nope", "survivability", "mobility.speed", "survivability.ehp_mixed[]"])
def test_fit_explain_unknown_metric_is_invalid_path_with_hint(eft_text: str, metric: str) -> None:
    """不在登记表 → `invalid_path`，`hint` **就是**可用指标全集（agent 据此换参数）。"""
    api.clear_caches()
    envelope = api.fit_explain(eft_text, metric)
    assert envelope["ok"] is False
    error = envelope["error"]
    assert error["kind"] == "invalid_path", error
    assert error["path"] == metric
    assert error["hint"] == list(api.EXPLAIN_METRICS), "hint 必须是登记表全集"
    assert error["message"]


def test_fit_explain_empty_metric_is_invalid_path_with_hint(eft_text: str) -> None:
    """空 metric 也是 `invalid_path`（不是 `value:null`，也不是泛化 internal_error）。"""
    api.clear_caches()
    envelope = api.fit_explain(eft_text, "")
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "invalid_path"
    assert envelope["error"]["hint"] == list(api.EXPLAIN_METRICS)


def test_fit_explain_is_idempotent_and_does_not_share_state(eft_text: str) -> None:
    """同一 metric 两次调用结果相同；**就地改第一次的 result 不许污染缓存**。"""
    api.clear_caches()
    first = api.fit_explain(eft_text, EXPLAIN_A_METRIC)
    second = api.fit_explain(eft_text, EXPLAIN_A_METRIC)
    assert first["result"] == second["result"], "两次调用结果必须逐位相同"

    # 模拟「调用方原地改了一把」——explain 的 LRU 是共享对象，接口层必须已经深拷贝过
    first["result"]["value"] = -1.0
    first["result"]["sources"].clear()
    third = api.fit_explain(eft_text, EXPLAIN_A_METRIC)
    assert third["result"]["value"] == second["result"]["value"], (
        "改坏第一次的返回影响了后续调用 —— 说明接口层没把 explain 的共享缓存隔离开"
    )
    assert third["result"]["sources"]


def test_api_clear_caches_also_clears_the_explain_lru(
    eft_text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`api.clear_caches()` 必须**连 `explain` 的 LRU 一起清**（否则拿到上一条用例的旧结果）。"""
    calls: list[str] = []
    real = api._explain.explain

    def counted(ctx, metric, *, depth=api.EXPLAIN_MAX_DEPTH):  # type: ignore[no-untyped-def]
        calls.append(metric)
        return real(ctx, metric, depth=depth)

    monkeypatch.setattr(api._explain, "explain", counted)
    api.clear_caches()
    api.fit_explain(eft_text, EXPLAIN_A_METRIC)
    assert len(calls) == 1
    api.fit_explain(eft_text, EXPLAIN_A_METRIC)  # 这次应该命中某一层缓存
    assert len(calls) == 1, "第二次调用不该重新构建"
    api.clear_caches()
    api.fit_explain(eft_text, EXPLAIN_A_METRIC)
    assert len(calls) == 2, "api.clear_caches 没有清 explain 的 LRU（旧结果会串到新用例里）"


def test_fit_explain_additive_sum_guard_is_not_vacuous(
    eft_text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**配对证明**：把 `value` 舍到 3 位，A 类的「Σ == value」必须立刻不成立。

    这正是 `explain._entry` 里那句「**不做 3 位小数舍入**」的守卫：舍入会让残差升到
    1e-7 量级，验收判据当场失效。若本测试里 `_explain_close` 仍然为真，说明那条断言
    是永真的（舍不舍入都过），A 类的契约就是空话。
    """
    real = api._explain.explain

    def rounded(ctx, metric, *, depth=api.EXPLAIN_MAX_DEPTH):  # type: ignore[no-untyped-def]
        result = real(ctx, metric, depth=depth)
        if result is None:
            return None
        broken = dict(result)
        broken["value"] = round(float(result["value"]), 3)
        return broken

    monkeypatch.setattr(api._explain, "explain", rounded)
    api.clear_caches()
    envelope = api.fit_explain(eft_text, EXPLAIN_A_METRIC)
    assert envelope["ok"] is True, envelope.get("error")
    result = envelope["result"]
    assert not _explain_close(_explain_sum(result), result["value"]), (
        "value 被舍成 3 位后，Σ 与 value 仍然相等 —— A 类的加和判据是永真的"
    )


# =============================================================================
# 二十一、灵敏度（P3 sensitivity）：fit_sensitivity 的 combine 三态 / 四个计数 / 错误映射
# =============================================================================
#
# 与 `explain` 的关键区别：这里**没有** `Σ == value` 那类判据（它给的是 op 前后的差）。
# 唯一可自证的等式是逐条的 `delta == value - baseline` —— 四个计数（planned / executed /
# skipped / truncated）各自独立，混成一个数会立刻说谎。

SENSITIVITY_CASES = (
    ("survivability.ehp_mixed", "additive"),
    ("mobility.speed_mps", "multiplicative"),
    ("resources.cpu.free", "single"),
)


@pytest.mark.parametrize(("metric", "combine"), SENSITIVITY_CASES)
def test_fit_sensitivity_covers_all_combine_values(
    eft_text: str, metric: str, combine: str
) -> None:
    """`combine` 的三种取值（additive / multiplicative / **single**）都能取到，且形状完整。"""
    api.clear_caches()
    envelope = api.fit_sensitivity(eft_text, metric)
    assert envelope["ok"] is True, envelope.get("error")
    result = envelope["result"]
    assert result["metric"] == metric
    assert result["combine"] == combine
    assert isinstance(result["baseline"], float)
    assert result["changes"], "自动计划必须给出至少一个 change"
    for key in (
        "warnings", "ops_planned", "ops_executed", "skipped", "truncated", "max_ops",
        "provenance", "tolerance",
    ):
        assert key in result, f"{metric} 缺字段 {key}"


def test_fit_sensitivity_delta_equals_value_minus_baseline(eft_text: str) -> None:
    """每条 `delta` 必须**逐位**等于 `value - baseline`（这是唯一可自证的等式）。"""
    api.clear_caches()
    result = api.fit_sensitivity(eft_text, "survivability.ehp_mixed")["result"]
    baseline = result["baseline"]
    for change in result["changes"]:
        assert change["delta"] == change["value"] - baseline, (
            f"delta 与 value-baseline 不一致：{change!r}（baseline={baseline!r}）"
        )
        assert change["op"]["kind"] in {"disable_item", "set_state", "set_mode"}
        assert result["ops_executed"] == len(result["changes"])


def test_fit_sensitivity_delta_pct_is_a_ratio_and_null_when_baseline_is_zero(
    eft_text: str,
) -> None:
    """`delta_pct` 是**比例**（`delta/baseline`）；基线为 0 时是 `null`（不是 0/inf）。"""
    api.clear_caches()
    result = api.fit_sensitivity(eft_text, "resources.cpu.free")["result"]
    baseline = result["baseline"]
    assert baseline != 0.0, "反证：这个样本的基线不是 0，才有比例可看"
    for change in result["changes"]:
        assert change["delta_pct"] == pytest.approx(change["delta"] / baseline)

    api.clear_caches()
    zero = api.fit_sensitivity(eft_text, "stability.capacitor.stable_pct")["result"]
    assert zero["baseline"] == 0.0, "反证：这个样本的基线是 0"
    assert zero["changes"], "反证：有 change 才谈得上 delta_pct"
    assert all(change["delta_pct"] is None for change in zero["changes"]), (
        "基线为 0 时 delta_pct 必须是 null（拿 0 或 inf 冒充就是把「不适用」说成「没变化」）"
    )


def test_fit_sensitivity_max_ops_truncates_and_says_so(eft_text: str) -> None:
    """`max_ops` 截断：`changes` 只留前 N 条、`truncated=true`、**另给一条 warning**。"""
    api.clear_caches()
    full = api.fit_sensitivity(eft_text, "resources.cpu.free")["result"]
    assert full["ops_planned"] > 3, "反证：计划数大于 3 才谈得上截断"
    assert full["truncated"] is False

    api.clear_caches()
    cut = api.fit_sensitivity(eft_text, "resources.cpu.free", max_ops=3)["result"]
    assert len(cut["changes"]) == 3
    assert cut["ops_executed"] == 3
    assert cut["ops_planned"] == full["ops_planned"], "计划数不因 max_ops 而变"
    assert cut["truncated"] is True
    assert cut["max_ops"] == 3
    kinds = [warning["kind"] for warning in cut["warnings"]]
    assert "truncated" in kinds, f"截断必须说出来，实际 warnings={kinds}"


def test_fit_sensitivity_explicit_ops_and_bad_ops(eft_text: str) -> None:
    """显式 `ops` 只跑给的那一条；**坏 op 是数据问题**（warnings + 跳过），不是错误信封。"""
    api.clear_caches()
    single = api.fit_sensitivity(
        eft_text, "resources.cpu.free", ops=[{"kind": "disable_item", "item_index": 0}]
    )
    assert single["ok"] is True, single.get("error")
    result = single["result"]
    assert result["ops_planned"] == 1 and result["ops_executed"] == 1
    assert result["changes"][0]["op"]["kind"] == "disable_item"
    assert result["changes"][0]["op"]["item_index"] == 0
    assert result["changes"][0]["op"]["label"], "op 描述里要有人看得懂的名字"

    api.clear_caches()
    broken = api.fit_sensitivity(
        eft_text,
        "resources.cpu.free",
        ops=[{"kind": "bogus"}, {"kind": "disable_item", "item_index": 999}],
    )
    assert broken["ok"] is True, "坏 op 不该让整个调用失败（它是数据问题）"
    assert broken["result"]["ops_executed"] == 0
    assert broken["result"]["changes"] == []
    kinds = [warning["kind"] for warning in broken["result"]["warnings"]]
    assert kinds.count("invalid_op") == 2, f"两个坏 op 各报一条，实际 {kinds}"


def test_fit_sensitivity_cache_key_separates_ops_and_max_ops(eft_text: str) -> None:
    """自动计划 / 显式 ops / 不同 `max_ops` 是**三个不同的缓存条目**（不串味）。"""
    api.clear_caches()
    auto = api.fit_sensitivity(eft_text, "resources.cpu.free")["result"]
    explicit = api.fit_sensitivity(
        eft_text, "resources.cpu.free", ops=[{"kind": "disable_item", "item_index": 0}]
    )["result"]
    cut = api.fit_sensitivity(eft_text, "resources.cpu.free", max_ops=2)["result"]
    assert auto["ops_planned"] != explicit["ops_planned"]
    assert explicit["ops_planned"] == 1
    assert cut["max_ops"] == 2 and len(cut["changes"]) == 2
    # 再把自动计划要一遍：必须还是原样（没被后两次调用污染）
    again = api.fit_sensitivity(eft_text, "resources.cpu.free")["result"]
    assert again == auto


@pytest.mark.parametrize("max_ops", [0, -1, True, False, "8", 1.5, None])
def test_fit_sensitivity_bad_max_ops_is_invalid_argument(eft_text: str, max_ops: object) -> None:
    """`max_ops` 非正整数（含 bool / 字符串 / 浮点）→ `invalid_argument` + `field=max_ops`。"""
    api.clear_caches()
    envelope = api.fit_sensitivity(
        eft_text, "resources.cpu.free", max_ops=max_ops  # type: ignore[arg-type]
    )
    assert envelope["ok"] is False, f"max_ops={max_ops!r} 不该成功"
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["field"] == "max_ops"
    assert envelope["error"]["hint"]["min"] == 1


@pytest.mark.parametrize("metric", ["nope", "resources.cpu", "ehp_mixed"])
def test_fit_sensitivity_unknown_metric_is_invalid_path_with_hint(
    eft_text: str, metric: str
) -> None:
    """不在登记表 → `invalid_path`，`hint` **就是** 14 个可用指标（agent 据此换参数）。"""
    api.clear_caches()
    envelope = api.fit_sensitivity(eft_text, metric)
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "invalid_path"
    assert envelope["error"]["path"] == metric
    assert envelope["error"]["hint"] == list(api.SENSITIVITY_METRICS)


def test_fit_sensitivity_accepts_every_registered_metric(eft_text: str) -> None:
    """登记表里每个指标在 f1 上都能算出基线（**一个都不许漏**）；条数不写死。"""
    assert api.SENSITIVITY_METRICS, "登记表不能为空"
    assert set(api.SENSITIVITY_METRICS) >= set(api.EXPLAIN_METRICS), (
        "sensitivity 的指标表必须包含 explain 的那 9 个（它就建在上面）"
    )
    for metric in api.SENSITIVITY_METRICS:
        api.clear_caches()
        envelope = api.fit_sensitivity(eft_text, metric, max_ops=2)
        assert envelope["ok"] is True, f"{metric} 失败：{envelope.get('error')}"
        assert envelope["result"]["metric"] == metric


def test_fit_sensitivity_meta_carries_the_version_triplet(eft_text: str) -> None:
    """信封 `meta` 与其它入口一致（版本三元组 + 技能档 hash + 输入形态）。"""
    api.clear_caches()
    envelope = api.fit_sensitivity(eft_text, "resources.cpu.free", max_ops=2)
    assert envelope["ok"] is True
    meta = envelope["meta"]
    for key in ("spec_version", "engine_version", "sde_build"):
        assert meta.get(key), f"信封 meta 缺 {key}"
    assert meta["source_format"] == "eft"
    assert meta["skill_tier"]["hash"] == (
        api.fit_index(eft_text)["result"]["meta"]["skill_tier"]["hash"]
    )


def test_fit_sensitivity_is_idempotent_and_isolated(eft_text: str) -> None:
    """两次调用结果相同；就地改第一次的返回**不许**污染后面的调用。"""
    api.clear_caches()
    first = api.fit_sensitivity(eft_text, "resources.cpu.free", max_ops=3)
    second = api.fit_sensitivity(eft_text, "resources.cpu.free", max_ops=3)
    assert first["result"] == second["result"]

    first["result"]["baseline"] = -1.0
    first["result"]["changes"].clear()
    third = api.fit_sensitivity(eft_text, "resources.cpu.free", max_ops=3)
    assert third["result"]["baseline"] == second["result"]["baseline"]
    assert third["result"]["changes"] == second["result"]["changes"]


def test_fit_sensitivity_delta_guard_is_not_vacuous(
    eft_text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**配对证明**：把每条 `value` 舍到 3 位，`delta == value - baseline` 必须立刻不成立。"""
    real = api._sensitivity.sensitivity

    def rounded(ctx, metric, *, ops=None, max_ops=api.SENSITIVITY_MAX_OPS):  # type: ignore[no-untyped-def]
        result = real(ctx, metric, ops=ops, max_ops=max_ops)
        if result is None:
            return None
        broken = dict(result)
        broken["changes"] = [
            {**change, "value": round(float(change["value"]), 3)} for change in result["changes"]
        ]
        return broken

    monkeypatch.setattr(api._sensitivity, "sensitivity", rounded)
    api.clear_caches()
    envelope = api.fit_sensitivity(eft_text, "survivability.ehp_mixed", max_ops=4)
    assert envelope["ok"] is True, envelope.get("error")
    result = envelope["result"]
    baseline = result["baseline"]
    assert any(change["delta"] != change["value"] - baseline for change in result["changes"]), (
        "value 被舍成 3 位后 delta 仍然等于 value-baseline —— 这条自证等式是永真的"
    )


# =============================================================================
# 二十二、sensitivity 的计数恒等式与 warning kind（契约同步：dev-engine-3 裁决）
# =============================================================================
#
# 旧行为：`max_ops=3` 时报 `planned=20 / exec=3 / skipped=0` —— 17 条**无声消失**。
# 新契约：`ops_planned == ops_executed + skipped` 恒成立，`skipped` 是总数，逐条原因在
# `warnings[].kind`。这三条断言钉的就是「别再回到旧口径」。

SENSITIVITY_WARNING_KINDS: tuple[str, ...] = (
    "noop",
    "truncated",
    "variant_error",
    "metric_missing_after_change",
    "mode_not_available",
    "modes_unknown",
    "no_index",
    "invalid_op",
    "invalid_ops",
)
"""`sensitivity` 会发出的 `warnings[].kind` 全集（README §4.11 的表格必须与它一致）。"""


def _sensitivity_identity(result: dict) -> None:
    """断言计数恒等式，并把三个数的关系写进失败信息（排查时不用回读代码）。"""
    planned = result["ops_planned"]
    executed = result["ops_executed"]
    skipped = result["skipped"]
    assert planned == executed + skipped, (
        f"计数恒等式不成立：planned={planned} != executed={executed} + skipped={skipped}"
        f"（少掉的 op 必须落在 skipped 里，不能无声消失）"
    )
    assert executed == len(result["changes"]), "ops_executed 必须等于真的产出的 change 条数"


def test_fit_sensitivity_counts_identity_holds(eft_text: str) -> None:
    """恒等式在**四种形态**下都成立：自动计划 / 截断 / 重复 op / 模式不可用。"""
    scenarios = (
        ("自动计划", {}),
        ("截断", {"max_ops": 3}),
        ("重复 op", {"ops": [{"kind": "disable_item", "item_index": 0}] * 2}),
        ("模式不可用", {"ops": [{"kind": "set_mode", "mode_id": 999999}]}),
    )
    for label, kwargs in scenarios:
        api.clear_caches()
        envelope = api.fit_sensitivity(eft_text, "resources.cpu.free", **kwargs)
        assert envelope["ok"] is True, (label, envelope.get("error"))
        _sensitivity_identity(envelope["result"])

    # 反证：截断场景下 skipped 真的是 17（旧口径是 0）—— 写死具体数会把「恒等式」变成
    # 「恰好这一次相等」，所以只断言「截断后 skipped > 0」+ 与 planned/exec 的关系。
    api.clear_caches()
    cut = api.fit_sensitivity(eft_text, "resources.cpu.free", max_ops=3)["result"]
    _sensitivity_identity(cut)
    assert cut["truncated"] is True
    assert cut["skipped"] > 0, "被 max_ops 砍掉的 op 必须进 skipped（旧口径是 0）"
    assert cut["skipped"] == cut["ops_planned"] - 3


def test_fit_sensitivity_noop_warning_counts_skipped_noops(eft_text: str) -> None:
    """空操作进 `ops_planned`、计入 `skipped`，并由一条带 `count` 的 `noop` warning 汇总。"""
    api.clear_caches()
    result = api.fit_sensitivity(
        eft_text,
        "resources.cpu.free",
        ops=[{"kind": "disable_item", "item_index": 0}] * 2,
    )["result"]
    _sensitivity_identity(result)
    assert result["ops_planned"] == 2, "重复 op 也在计划里（不是被悄悄丢掉）"
    assert result["ops_executed"] == 1 and result["skipped"] == 1
    noops = [w for w in result["warnings"] if w["kind"] == "noop"]
    assert len(noops) == 1, f"空操作要有且只有一条汇总 warning：{result['warnings']}"
    assert noops[0]["count"] == 1


def test_fit_sensitivity_mode_warnings_say_what_was_not_covered() -> None:
    """两类模式 warning 的口径：**没覆盖**（`modes_unknown`）≠ **不适用**（`mode_not_available`）。"""
    fixtures = (NOOBBAOZI_ROOT / "acceptance" / "fixtures")
    legion = (fixtures / "f7-t3c-legion.json").read_text(encoding="utf-8")
    hecate = (fixtures / "f1-hecate-killmail.eft").read_text(encoding="utf-8")

    # T3C（Legion）：本地索引枚举不到模式 → 自动计划**不含 set_mode**，并明说这一维没覆盖
    api.clear_caches()
    t3c = api.fit_sensitivity(legion, "resources.cpu.free")["result"]
    assert "modes_unknown" in [w["kind"] for w in t3c["warnings"]]
    assert all(change["op"]["kind"] != "set_mode" for change in t3c["changes"]), (
        "枚举不到模式时不许编造 set_mode 变化行"
    )

    # 有模式表的船（Hecate）给一个不存在的 mode → mode_not_available，且计入 skipped
    api.clear_caches()
    bogus = api.fit_sensitivity(
        hecate, "resources.cpu.free", ops=[{"kind": "set_mode", "mode_id": 999999}]
    )["result"]
    kinds = [w["kind"] for w in bogus["warnings"]]
    assert "mode_not_available" in kinds, kinds
    assert bogus["ops_executed"] == 0
    _sensitivity_identity(bogus)


def test_all_offline_equals_empty_fit_except_dps_base(eft_text: str) -> None:
    """全 offline ≡ 同型空配装（9 个指标里 8 个逐位相同）；唯一例外 `dps.base`。

    `dps.base`：全 offline 是 **0.0**（有武器但没生效），空配装是 **取不到值**
    （`explain` 给 `None` → 接口层 `invalid_path`）。所以「全关掉」不能当「空船基线」。
    """
    parsed = api.parse_fit(eft_text)["result"]["fit"]
    offline = copy.deepcopy(parsed)
    for item in offline["items"]:
        item["state"] = "offline"
    empty = api.parse_fit("[Hecate, empty]\n")["result"]["fit"]

    for metric in api.EXPLAIN_METRICS:
        api.clear_caches()
        left = api.fit_explain(offline, metric)
        api.clear_caches()
        right = api.fit_explain(empty, metric)
        if metric == "firepower.dps.base":
            assert left["ok"] is True and left["result"]["value"] == 0.0
            assert right["ok"] is False and right["error"]["kind"] == "invalid_path", (
                "空配装的 dps.base 是「取不到」（None），不是 0.0"
            )
            continue
        assert left["ok"] is True and right["ok"] is True, metric
        assert left["result"]["value"] == right["result"]["value"], (
            f"{metric}: 全 offline 与空配装必须逐位相同（差的是「有没有模块」这件事）"
        )
