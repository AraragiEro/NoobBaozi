"""T-31 守护测试：**视角用到的属性 ID 必须全都在常量表里**（这是踩过四次的坑）。

为什么要有这份测试（真实事故清单，不是假想）：

- `281 explosionDelay`（导弹射程）
- `56 chargeRate`（弹药续航）
- `1272 droneBandwidthUsed`（无人机带宽）
- `1367 maxSubSystems`（T3C 子系统槽）

四次都是「某个视角用了未登记的属性 ID，事后才发现」。而**漏掉的那个 ID 恰恰是没人用的
那个**：没人用 → 不会报错 → 输出里没有它 → 谁都看不见。所以不能靠「下次注意」，
只能靠机器。这份测试就是那台机器，三条硬门对应 `tools/check_attr_coverage.py` 的三项检查：

A. **`FIELD_SOURCES` 覆盖（硬失败）**：六个视角的每条来源都必须是已登记属性 ID。
   值为 `None` 是「合成值」的合法登记；值为 `int` 必须在 `ATTRIBUTES` 里；其它类型直接失败。

B. **裸属性 ID（白名单后硬失败）**：视角与渲染层里「被当成属性 ID 用」的整数字面量。
   这条天生有误报可能，所以收口方式是显式白名单 `ALLOWED_BARE_IDS`（**每条写理由**），
   白名单之外的任何疑似项都让测试失败；白名单里**过期**条目同样失败（防止只增不减、
   把检查慢慢稀释成假绿）。**T-32 起白名单是空表**：`render.py` 的三条 `MODE_*_ATTR`
   已改成从常量表引用，按设计销账；空表就是这个白名单唯一健康的终态。

C. **常量表自洽（硬失败）**：`ATTR_*` 常量 ↔ `ATTRIBUTES` 元数据行 ↔ `SCALE_BY_ATTR`
   双向对齐，并复用 `sde/attribute_dict.audit()` 的结论。

**每条断言都证明自己非永真（变异自检）**：用 `monkeypatch` 往真数据结构里注入一个假 ID /
假行 / 假常量，断言检查器**确实抓到了**。只有「注入后能红」的断言，才配当守护测试——
否则它可能一直在空转（本项目 `FIELD_SOURCES` 覆盖率测试就是这么假绿过一轮的，
见 `test_render.py` 的实例级覆盖一节）。

写入范围：本文件 + `evefit/tools/check_attr_coverage.py`。`project/**` 的问题只登记、
不在这里修（红线：本任务禁改视角与渲染层）。
"""

from __future__ import annotations

import json

import pytest

from evefit.config import attributes
from evefit.project.views.loader import VIEW_MODULES
from evefit.tools import check_attr_coverage as CHK

# =============================================================================
# 白名单 / 债务台账（每条都必须写理由）
# =============================================================================

ALLOWED_BARE_IDS: dict[str, str] = {}
"""`evefit/project/views/*.py` + `render.py` 里**允许**的裸属性 ID（键 = `suspect_key`）。

键形态：`<相对仓库根的路径>::<常量名或角色>::<数值>`（不含行号——行号会随无关改动漂移）。
**T-32 起为空表**：原先登记的三条债务（`render.py` 的 `MODE_EHP_ATTR = -43` /
`MODE_DPS_ATTR = -12` / `MODE_ALIGN_ATTR = -1`）已改成从 `config/attributes.py` 引用
（`ATTR_EHP` / `ATTR_DAMAGE_DPS` / `ATTR_ALIGN_TIME`），按设计**销账**（第 202 条）。
空表就是「一条都不许裸写」——这是这张白名单唯一健康的终态。
"""

KNOWN_BARE_ID_DEBTS: dict[str, str] = {}
"""`project/` 下**其它**模块（B2 附注扫描）里登记的裸属性 ID 债务。

这些文件不在 T-31 的写入范围内，所以它们不参与硬门；但必须被**点名**而不是被忽略。
**T-32 起为空表**：`project/provenance.py` 的 `ATTR_MAX_TARGETS_UNVERIFIED = -70` 已改成
引用常量表（`config/attributes.py::ATTR_MAX_TARGETS`，派生负 ID，SDE 无记录）。
台账与实测不一致时本测试失败（双向）：冒出新的裸 ID 会红，修好却没销账也会红。
"""


def _keys(suspects: list[dict]) -> set[str]:
    return {CHK.suspect_key(suspect) for suspect in suspects}


# =============================================================================
# A. FIELD_SOURCES 覆盖 —— 硬失败
# =============================================================================


def test_a_field_sources_only_reference_registered_attribute_ids() -> None:
    """六个视角的每条 `FIELD_SOURCES` 来源都必须是已登记属性 ID（A 硬门）。

    这条一旦红了，说明有人给视角登记了一个「常量表里没有」的属性 →
    下游 `provenance.tag()` 只能给它 `attr_<id>` 这种无名标签，等于输出一个没人认识的值。
    修法：`config/attributes.py` 补 `ATTR_*` + `ATTRIBUTES` 行（名字用 `Index.attribute(id)` 核实）。
    """
    report = CHK.check_field_sources()

    # 非空转前置：真扫了六个视角、真读了一批登记项
    assert report["view_count"] == len(VIEW_MODULES) == 6
    assert report["field_count"] >= 150, f"只扫到 {report['field_count']} 条登记，结构变了？"
    assert report["int_count"] >= 80, f"来源里只有 {report['int_count']} 条属性 ID，结构变了？"
    assert report["none_count"] >= 50, f"合成值登记只有 {report['none_count']} 条，结构变了？"

    assert report["views_without_sources"] == []
    assert report["unregistered"] == [], (
        "FIELD_SOURCES 引用了未登记的属性 ID（补进 config/attributes.py）：\n"
        + "\n".join(
            f"  {item['view']}.{item['path']} → {item['value']}" for item in report["unregistered"]
        )
    )
    assert report["invalid"] == [], (
        "FIELD_SOURCES 的来源类型非法（只允许 int 或 None，红线 N12）：\n"
        + "\n".join(f"  {item['view']}.{item['path']} → {item['value']}" for item in report["invalid"])
    )
    assert report["ok"] is True


@pytest.mark.parametrize(
    ("injected", "kind"),
    [
        (987654, "unregistered"),  # 未登记的正 ID（典型的「菜单里多写了一个」）
        (-9999, "unregistered"),  # 未登记的派生负 ID（引擎 pass 4 会吐出来）
        ("ATTR_MAX_VELOCITY", "invalid"),  # 写了常量**名**而不是值（N12 的另一种写法）
        (True, "invalid"),  # bool 是 int 的子类，不能放行
    ],
)
def test_a_detects_injected_bad_sources(
    monkeypatch: pytest.MonkeyPatch, injected: object, kind: str
) -> None:
    """**变异自检**：往真视角里注入一个坏来源，A 检查必须抓到。

    没有这条，「A 通过」可能只是因为它什么都没检查（本项目踩过这种假绿）。
    """
    module = VIEW_MODULES["mobility"]
    patched = dict(module.FIELD_SOURCES)
    patched["__mutation_probe__"] = injected
    monkeypatch.setattr(module, "FIELD_SOURCES", patched)

    report = CHK.check_field_sources()

    assert report["ok"] is False, "注入了坏来源，A 检查却说通过 —— 断言是永真的"
    findings = report[kind]
    probe = [item for item in findings if item["path"] == "__mutation_probe__"]
    assert len(probe) == 1, f"注入的 {injected!r} 没被报成 {kind}：{findings}"
    assert probe[0]["view"] == "mobility"


def test_a_uses_the_attribute_table_not_a_hardcoded_set() -> None:
    """A 的判据必须是「在不在 `ATTRIBUTES` 里」——用空集合注入时**全部**都要报未登记。

    这条是上一条的另一半：证明检查器查的是那张真表，不是某种写死的白名单。
    """
    report = CHK.check_field_sources(known_ids=set())

    assert len(report["unregistered"]) == report["int_count"] > 0
    assert report["ok"] is False


# =============================================================================
# B. 裸属性 ID 扫描 —— 白名单后才硬失败
# =============================================================================


def test_b_bare_ids_in_views_and_render_are_all_whitelisted() -> None:
    """视角 + 渲染层的裸属性 ID 疑似项必须全部在白名单里（每条有理由）。"""
    report = CHK.scan_bare_ids()

    # 非空转前置：真扫了 10 个视角文件 + render.py
    assert report["file_count"] >= 10, f"只扫到 {report['file_count']} 个文件：{report['files']}"
    assert "evefit/project/render.py" in report["files"]

    unlisted = sorted(_keys(report["suspects"]) - set(ALLOWED_BARE_IDS))
    assert unlisted == [], (
        "出现白名单之外的裸属性 ID（红线 N12：属性 ID 必须用常量名引用）。\n"
        "要么改成 `from evefit.config.attributes import ATTR_*`，要么在 ALLOWED_BARE_IDS 里"
        "写清为什么它不能改：\n" + "\n".join(f"  {key}" for key in unlisted)
    )

    for key, reason in ALLOWED_BARE_IDS.items():
        assert isinstance(reason, str) and len(reason.strip()) >= 20, f"白名单条目缺理由：{key}"


def test_b_whitelist_has_no_stale_entries() -> None:
    """白名单**不得只增不减**：某条裸 ID 被修好后，对应条目必须删掉。

    单独一条测试（而不是并进上面那条硬门）：它红了意味着「修得漂亮，记得销账」，
    与「有人新写了裸 ID」是两种完全不同的信号，不该混在同一个失败里。
    """
    found = _keys(CHK.scan_bare_ids()["suspects"])
    stale = sorted(set(ALLOWED_BARE_IDS) - found)

    assert stale == [], (
        "白名单里有过期条目（那条裸 ID 已经不存在了）——请删掉，别让白名单只增不减：\n"
        + "\n".join(f"  {key}" for key in stale)
    )


def test_b_detects_the_dangerous_bare_id_shapes() -> None:
    """**变异自检**：四种危险写法必须被抓到，三种无害整数必须**不**被抓到。

    误报率决定这条检查能不能活着：真数字（`round(x, 3)`、下标 `[0]`、计数）一旦被报，
    维护者会直接关掉检查。所以这里两个方向都钉住。

    源码直接以**字符串**喂给扫描器（`scan_source_for_bare_ids`）：不写临时文件、
    不依赖 tmp 目录，也就证明了扫描器本身没有 IO 依赖。
    """
    source = (
        'FIELD_SOURCES = {"bare": 50, "synthetic": None}\n'
        "MODE_ALIGN_ATTR: int = -1\n"
        "BARE_KEY_MAP = {1271: 'droneBandwidth'}\n"
        "\n"
        "def probe(attrs):\n"
        "    found = attrs.get(1272)\n"
        "    rounded = round(2.5, 3)\n"
        "    first = [7][0]\n"
        "    return found, rounded, first\n"
    )

    suspects = CHK.scan_source_for_bare_ids(source, "<probe_view>")
    found = {(item["role"], item["value"]) for item in suspects}

    # 四种必须抓到的形态
    assert (CHK.ROLE_FIELD_SOURCE_VALUE, 50) in found, f"FIELD_SOURCES 值位的裸 ID 漏了：{found}"
    assert ("assign->MODE_ALIGN_ATTR", -1) in found, f"`_ATTR = -1` 漏了：{found}"
    assert (CHK.ROLE_DICT_KEY, 1271) in found, f"`{{1271: ...}}` 的整数键漏了：{found}"
    assert ("call->attrs.get", 1272) in found, f"`attrs.get(1272)` 漏了：{found}"

    # 三种无害整数必须被放行（否则这条检查会被误报关掉）
    flagged = {item["value"] for item in suspects}
    assert 3 not in flagged, "`round(x, 3)` 的精度被误报成属性 ID"
    assert 0 not in flagged, "下标 `[0]` 被误报成属性 ID"
    assert 2 not in flagged, "计数被误报成属性 ID"


def test_b_does_not_flag_negative_values_it_cannot_prove() -> None:
    """反向自检：**不带属性语义**的负整数不算裸 ID（启发式不能变成「见数字就报」）。"""
    source = 'BUDGET: int = -1\nOFFSET = -4\n\n\ndef f(x):\n    return max(0, x - 8)\n'

    assert CHK.scan_source_for_bare_ids(source, "<probe_plain>") == []


def test_b2_other_project_bare_ids_match_the_debt_ledger() -> None:
    """B2 附注：`project/` 下其它模块的裸 ID 必须与债务台账**双向**一致。

    这些文件不在本任务写入范围，所以不参与硬门；但它们不能被忽略——台账漂移
    （冒出新的裸 ID，或旧债务被修好却没销账）一律失败，让「没人用的那个 ID」无处可躲。
    """
    report = CHK.scan_other_project_modules()

    assert report["file_count"] >= 1
    found = _keys(report["suspects"])
    assert found == set(KNOWN_BARE_ID_DEBTS), (
        "B2 债务台账与实测不一致：\n"
        f"  新出现（要登记或修掉）：{sorted(found - set(KNOWN_BARE_ID_DEBTS))}\n"
        f"  已修好（要销账）：{sorted(set(KNOWN_BARE_ID_DEBTS) - found)}"
    )
    for key, reason in KNOWN_BARE_ID_DEBTS.items():
        assert isinstance(reason, str) and len(reason.strip()) >= 20, f"债务条目缺理由：{key}"


# =============================================================================
# C. 常量表自洽 —— 硬失败
# =============================================================================


def test_c_constant_table_is_self_consistent() -> None:
    """`ATTR_*` 常量 ↔ `ATTRIBUTES` 行 ↔ `SCALE_BY_ATTR` 三方对齐（C 硬门）。"""
    report = CHK.check_constant_table()

    assert report["constant_count"] == report["attribute_count"] >= 140
    assert report["scale_count"] >= 10

    assert report["orphan_constants"] == [], (
        "这些常量没有 `ATTRIBUTES` 行（下游拿不到名字/单位）：\n"
        + "\n".join(f"  {name}" for name in report["orphan_constants"])
    )
    assert report["orphan_rows"] == [], (
        "这些属性有 `ATTRIBUTES` 行却没有常量名（红线 N12：必须能被常量引用）：\n"
        + "\n".join(f"  {attr_id}" for attr_id in report["orphan_rows"])
    )
    assert report["duplicate_values"] == [], (
        f"常量撞值（复制粘贴写错）：{report['duplicate_values']}"
    )
    assert report["non_int_constants"] == []
    assert report["scale_orphans"] == [], (
        "缩放表里有、元数据表里没有：" + str(report["scale_orphans"])
    )

    # 复用 attribute_dict.audit() 的独立结论（撞值 / 单位标签 / 计数）
    audit = report["audit"]
    assert audit["consistent"] is True, f"audit() 报不自洽：{audit}"
    assert audit["constant_count"] == report["constant_count"]
    assert audit["attribute_count"] == report["attribute_count"]
    assert report["ok"] is True


def test_c_detects_a_constant_without_a_metadata_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """**变异自检**：删掉一个真常量的元数据行，C 必须报「孤儿常量」。"""
    monkeypatch.delitem(attributes.ATTRIBUTES, attributes.ATTR_ALIGN_TIME)

    report = CHK.check_constant_table()

    assert "ATTR_ALIGN_TIME" in report["orphan_constants"], report["orphan_constants"]
    assert report["ok"] is False
    assert report["audit"]["consistent"] is False


def test_c_detects_a_metadata_row_without_a_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """**变异自检**：塞一个没有常量名的元数据行，C 必须报「孤儿行」。"""
    monkeypatch.setitem(attributes.ATTRIBUTES, 987654, ("mutationProbe", "x", None))

    report = CHK.check_constant_table()

    assert 987654 in report["orphan_rows"], report["orphan_rows"]
    assert report["ok"] is False
    assert report["audit"]["consistent"] is False


def test_c_detects_a_scale_entry_without_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """**变异自检**：给缩放表塞一个未登记属性，C 必须报出来（缩放表不能自成一体）。"""
    monkeypatch.setitem(attributes.SCALE_BY_ATTR, 987655, attributes.SCALE_MILLI)

    report = CHK.check_constant_table()

    assert 987655 in report["scale_orphans"], report["scale_orphans"]
    assert report["ok"] is False


def test_c_detects_a_duplicate_constant_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """**变异自检**：制造撞值，C 必须报（撞值 = 某处复制粘贴写错了 ID）。"""
    monkeypatch.setattr(attributes, "ATTR_MUTATION_PROBE", attributes.ATTR_HULL_HP, raising=False)

    report = CHK.check_constant_table()

    assert attributes.ATTR_HULL_HP in report["duplicate_values"]
    assert report["ok"] is False


# =============================================================================
# 四、命令行入口（验收命令跑的就是它）
# =============================================================================


def test_tool_runs_clean_and_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    """`python -m evefit.tools.check_attr_coverage` 的两种输出都可读、可解析、退出码 0。"""
    assert CHK.main([]) == 0
    text = capsys.readouterr().out
    assert "硬门结论（A + C）: 通过" in text
    assert "[A] FIELD_SOURCES 覆盖" in text
    assert "[B] 裸属性 ID 扫描" in text
    assert "[C] 常量表自洽" in text

    assert CHK.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) >= {"A", "B", "C", "B2", "ok", "strict_bare"}
    assert payload["ok"] is True
    assert payload["A"]["unregistered"] == []
    assert payload["C"]["orphan_rows"] == []
    assert payload["strict_bare"] is False


def test_strict_bare_flag_turns_suspects_into_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """`--strict-bare` 的语义：有疑似项就非 0（默认不这样，否则误报会逼人关掉检查）。"""
    code = CHK.main(["--strict-bare"])
    capsys.readouterr()
    assert code == (1 if CHK.scan_bare_ids()["suspects"] else 0)
