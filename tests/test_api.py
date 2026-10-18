"""L5 接口层（`evefit/api.py`）的契约测试 —— 只测**契约**，不重算任何数值。

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
import urllib.error
from pathlib import Path

import pytest

from evefit import api

EVEFIT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = EVEFIT_ROOT / "acceptance" / "fixtures"
EFT_PATH = FIXTURES_DIR / "f1-hecate-killmail.eft"
KILL_PATH = EVEFIT_ROOT / "spike" / "kill.json"
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
    assert missing["result"]["views"]["stability"]["missing"] == ["no_such_field"]

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

KILLMAIL_FIXTURES = EVEFIT_ROOT / "tests" / "fixtures" / "killmail"
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


def test_fit_killmail_merges_zkb_and_esi(monkeypatch: pytest.MonkeyPatch) -> None:
    """联网形态：zkillboard 给价值块、ESI 给伤害详情，**两个都拉**再合并。"""
    zkb_payload = json.loads(KILLMAIL_ZKB_PATH.read_text(encoding="utf-8"))
    esi_payload = json.loads(KILLMAIL_ESI_PATH.read_text(encoding="utf-8"))
    seen: list[str] = []

    def fake_http(url: str) -> object:
        seen.append(url)
        return zkb_payload if "zkillboard" in url else esi_payload

    _offline_system(monkeypatch)
    monkeypatch.setattr(api, "_http_json", fake_http)

    out = api.fit_killmail({"killmail_id": KILLMAIL_ID, "hash": KILLMAIL_HASH})
    assert out["ok"] is True, out
    result = out["result"]
    assert result["killmail_source"] == "network"
    # 价值块来自 zkillboard
    assert result["killmail"]["value"]["total"] == 76942042803.52
    assert "capinv" in result["killmail"]["labels"]
    # 伤害详情来自 ESI
    assert result["victim"]["damage_taken"] == 6558773
    assert result["attackers"]["count"] == 79
    assert len(seen) == 2, f"应当恰好两个请求（zkb + ESI），实际 {seen}"
    assert any("zkillboard" in url for url in seen)
    assert any("esi.evetech.net" in url for url in seen)


def test_fit_killmail_uses_the_zkb_hash_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只给 `killmail_id`（不给 hash）也能跑：hash 从 zkillboard 的 `zkb.hash` 拿。"""
    zkb_payload = json.loads(KILLMAIL_ZKB_PATH.read_text(encoding="utf-8"))
    esi_payload = json.loads(KILLMAIL_ESI_PATH.read_text(encoding="utf-8"))
    seen: list[str] = []

    def fake_http(url: str) -> object:
        seen.append(url)
        return zkb_payload if "zkillboard" in url else esi_payload

    _offline_system(monkeypatch)
    monkeypatch.setattr(api, "_http_json", fake_http)

    out = api.fit_killmail({"killmail_id": KILLMAIL_ID})
    assert out["ok"] is True, out
    esi_urls = [url for url in seen if "esi.evetech.net" in url]
    assert esi_urls == [
        f"https://esi.evetech.net/latest/killmails/{KILLMAIL_ID}/{KILLMAIL_HASH}/"
    ], esi_urls


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

NAMES_FIXTURE = EVEFIT_ROOT / "tests" / "fixtures" / "zkb" / "esi-universe-names.json"
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

ZKB_FIXTURES = EVEFIT_ROOT / "tests" / "fixtures" / "zkb"
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
