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
