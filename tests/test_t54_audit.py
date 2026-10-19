"""T-54 独立审查（`dev-engine` 补的钉子）：炮位分类的完备性 + EFT 渲染的边界。

与 `tests/test_eft.py` 的分工：那边是 T-54 实现者给自己写的验收；本文件是**外部审查**视角补的
缺口，只钉「实现者容易忽略、且会静默出错」的东西，每条失败信息都要说得清是哪个判据破了。

本文件**无网络**（内联 EFT + 本地 SDE 索引）。

三条新增钉子：

1. **误分类诱惑物**：`Smartbomb` / `Nosferatu` / `Neutralizer` / `Cloaking Device` /
   `Remote Armor Repairer` / 末日武器 / 牵引光束 / 探针发射器 —— 它们出现在高槽，且名字/组名
   含 `launcher` / `beam` / `laser` / `miner` 之类的词。把它们算成炮台或发射器会让 `free`
   变负、**误报** `hardpoint_violation`，比漏分类更糟。
2. **关键词表非永真**：把 `LAUNCHER_KEYWORDS` 抹成空表，装 3 门 RLML 的断言必须变红
   （证明「`used == 3`」真的是关键词表数出来的，不是碰巧）。
3. **EFT 不许静默丢件**：`quantity > 1` 的已装模块必须写满行数、非对象条目必须报错，
   而不是「渲染成功但少一件」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noobbaozi import api
from noobbaozi.config import attributes as A
from noobbaozi.engine import normalize
from noobbaozi.project import eft
from noobbaozi.project import reasons as RS
from noobbaozi.project.views import resources as R

OSPREY_NI = "Osprey Navy Issue"
LAUNCHER = "Rapid Light Missile Launcher II"


def _eft(*groups: tuple[str, ...], name: str = "Audit", ship: str = OSPREY_NI) -> str:
    """按「低 / 中 / 高 / 改装」的顺序把若干组模块拼成 EFT 文本（组间空行）。"""
    lines = [f"[{ship}, {name}]"]
    for group in groups:
        if not group:
            continue
        lines.append("")
        lines.extend(group)
    return "\n".join(lines) + "\n"


def _resources(text: str) -> dict:
    """`fit_view(..., "resources")` 的成功信封（失败直接炸：失败不是数据）。"""
    envelope = api.fit_view(text, "resources")
    assert envelope["ok"] is True, envelope.get("error")
    return envelope["result"]


_HIGH_BEAM: dict = {
    "type_id": 1,
    "name": "Heavy Beam Laser II",
    "group_name": "Energy Weapon",
    "slot_type": "high",
    "attributes": {},
}
"""一件真炮（高槽）：给桩上下文当「已装模块」用。"""


class _StubCtx:
    """最小 duck-type `ViewContext`（`diagnostics._Facts` 只用 attrs/items/index/fit）。

    用来构造**真实配装拿不到**的缺值情形（槽位总量属性缺失、硬点总量缺失），
    这是 `unknown 不是 0` 那几条规矩唯一的验法 —— 真实船体不会缺 14/13/12/1137。
    """

    index = None
    fit: dict = {"ship": {"type_id": 1}, "items": []}

    def __init__(self, attrs: dict[int, float], items: tuple[dict, ...] = ()) -> None:
        self._attrs = attrs
        self._items = list(items)

    def attrs(self, variant: str = "base") -> dict[int, float]:  # noqa: ARG002 - 桩
        return self._attrs

    def items(self, variant: str = "base") -> list[dict]:  # noqa: ARG002 - 桩
        return self._items


def _stub_ctx(attrs: dict[int, float], items: tuple[dict, ...] = (_HIGH_BEAM,)) -> _StubCtx:
    """带一件高槽炮的桩上下文（`fitted()` 非空，`slot_usage` 的 `used` 才不是恒 0）。"""
    return _StubCtx(attrs, items)


@pytest.fixture(scope="module", autouse=True)
def _clean_api_cache() -> None:
    api.clear_caches()


# =============================================================================
# 一、误分类诱惑物：出现在高槽、名字/组名带关键词，但**不占**炮位
# =============================================================================


@pytest.mark.parametrize(
    ("name", "group"),
    [
        # 组名/名字带 beam / laser / miner / launcher，但都不是炮也不是发射器
        ("Large EMP Smartbomb II", "Smart Bomb"),
        ("Heavy Energy Nosferatu II", "Energy Nosferatu"),
        ("Heavy Energy Neutralizer II", "Energy Neutralizer"),
        ("Improved Cloaking Device II", "Cloaking Device"),
        ("Small Remote Armor Repairer II", "Remote Armor Repairer"),
        ("Pulse Activated Nexus Invulnerability Core", "Super Weapon"),
        ("Small Tractor Beam I", "Tractor Beam"),
        ("Core Probe Launcher I", "Scan Probe Launcher"),
    ],
)
def test_non_weapon_high_slot_modules_are_never_counted(name: str, group: str) -> None:
    """这些模块一律**不进 `turret` / `launcher`**：误分类会让 `free` 变负、**误报超装**。

    它们现在落在 `non_hardpoint`（确定不占硬点）而不是 `unattributed`（认不出）——
    但这条测试只关心一件事：**一件都不许算成武器**。
    """
    kind = R._hardpoint_kind(name, group)
    assert kind not in ("turret", "launcher"), f"{name!r} 被判成了武器：{kind}"
    assert kind == "non_hardpoint", "SDE 实测这些组都是高槽且确定不占硬点"


def test_analysis_modules_hit_the_keyword_but_never_count_because_they_are_mid_slot() -> None:
    """**已知的过宽命中，钉住它、并证明它无害**：`Data Analyzer I` 落在 SDE group `Data Miners`，
    名字里带 `miner` → 关键词表把它判成 `turret`。

    但它是**中槽**模块，而 `hardpoints()` 只看高槽（`HARDPOINT_SLOT_TYPE`）——所以
    一件也数不进去。这条测试的作用是**不许假装关键词表很干净**：分类函数的输出确实是
    `turret`，挡住它的是「只看高槽」这一层。哪天有人把 `hardpoints()` 改成扫全槽位，
    下面那几条断言就会变红。
    """
    assert R._hardpoint_kind("Data Analyzer I", "Data Miners") == "turret", "关键词确实过宽"

    text = _eft(("Damage Control II",), ("Data Analyzer I",), ())
    overview = _resources(text)["overview"]
    assert overview["slots"]["medium"]["used"] == 1, "分析仪装在中槽"
    assert overview["slots"]["high"]["used"] == 0, "高槽是空的"
    assert overview["hardpoints"]["turret"]["used"] == 0, "中槽模块一件都不许数进炮位"


def test_a_smartbomb_fit_never_reports_a_hardpoint_violation_on_a_zero_hardpoint_hull() -> None:
    """**端到端**：Rorqual（8 高槽、**0 turret / 0 launcher**）装 3 个智能炸弹。

    若 `Smart Bomb` 被误判成 turret（`smartbomb` 里没有诱饵词，这里防的是以后加词出错），
    `used=3 > total=0` 会凭空造出一条 `hardpoint_violation`——非法装配的假指控。
    真实事实是：智能炸弹**不消耗**炮位，两个硬点类必须都是 0/0/0，且不许出超装 flag。
    """
    text = _eft(
        ("Damage Control II",),
        ("Large Shield Extender II",),
        ("Large EMP Smartbomb II",) * 3,
        ship="Rorqual",
    )
    result = _resources(text)
    overview = result["overview"]

    assert overview["slots"]["high"]["total"] == 8, "Rorqual 有 8 个高槽"
    assert overview["hardpoints"]["turret"] == {"total": 0, "used": 0, "free": 0}
    assert overview["hardpoints"]["launcher"] == {"total": 0, "used": 0, "free": 0}
    assert overview["hardpoints"]["non_hardpoint"] == 3, "3 个智能炸弹 = 确定不占硬点"
    assert overview["hardpoints"]["unattributed"] == 0, "智能炸弹是**确定**的，不是「认不出」"
    assert "hardpoint_violation" not in {flag["id"] for flag in result["flags"]}


# =============================================================================
# 二、关键词表**非永真**：抹掉 `launcher` 关键词，断言必须红
# =============================================================================


def test_launcher_keyword_is_what_counts_the_launchers(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LAUNCHER_KEYWORDS` 是 `used` 的唯一来源 —— 抹成空表后 `used` 必须变 0。

    这是「`used == 3` 不是永真」的证明：数出 3 门发射器的**就是**那张关键词表。
    抹掉之后 3 门 RLML 会全落进 `unattributed`（**不猜**），`free` 也随之变回 3。
    """
    text = _eft(("Damage Control II",), (), (LAUNCHER,) * 3, name="KeywordProbe")
    baseline = _resources(text)["overview"]["hardpoints"]
    assert baseline["launcher"] == {"total": 3, "used": 3, "free": 0}

    monkeypatch.setattr(R, "LAUNCHER_KEYWORDS", ())
    api.clear_caches()  # api 是 LRU：不清缓存会直接返回**抹表之前**的结果，测试变成永真
    mutated = _resources(text)["overview"]["hardpoints"]

    assert mutated["launcher"] == {"total": 3, "used": 0, "free": 3}, "抹掉关键词后必须数不出门数"
    assert mutated["unattributed"] == 3, "数不出来的必须进 unattributed，不许猜成 0 门也不许猜成炮"


# =============================================================================
# 三、EFT 渲染：静默丢件是红线
# =============================================================================


def test_eft_expands_quantity_into_one_line_per_module() -> None:
    """`quantity > 1` 的已装模块必须写满行数（EFT 的件数由行数表达）。

    只写一行会让「2 件」在文本里变成「1 件」——渲染成功、件数悄悄少半，属于静默丢件。
    """
    text = _eft(("Damage Control II",), (), ("200mm AutoCannon II",), name="Qty", ship="Rifter")
    fit = normalize.normalize_fit(text)["fit"]
    assert fit is not None
    index = api.get_index()

    high_item = next(
        item for item in fit["items"] if (item.get("slot") or {}).get("type") == "high"
    )
    doubled = {**fit, "items": [{**high_item, "quantity": 2}]}
    emitted = eft.emit(doubled, index)

    module_lines = [line for line in emitted.split("\n") if line == "200mm AutoCannon II"]
    assert len(module_lines) == 2, f"2 件必须写 2 行，实际：{emitted!r}"

    again = normalize.normalize_fit(emitted)["fit"]
    assert again is not None
    counted = [
        item for item in again["items"] if (item.get("slot") or {}).get("type") == "high"
    ]
    assert len(counted) == 2, "重解析必须还原成 2 件（件数不能靠 quantity 字段偷偷保留）"


def test_eft_refuses_to_silently_drop_a_malformed_item() -> None:
    """`fit['items']` 里的非对象条目 → `EftError`（**不许静默跳过**）。

    静默跳过的后果不是报错，而是「渲染成功、少一件」——调用方只看到 `eft` 有值，
    不会知道少了东西。所以宁可整体失败、由接口层降级成 `eft: null` + `warnings`。
    """
    text = _eft(("Damage Control II",), (), ("200mm AutoCannon II",), ship="Rifter")
    fit = normalize.normalize_fit(text)["fit"]
    assert fit is not None

    with pytest.raises(eft.EftError) as excinfo:
        eft.emit({**fit, "items": [*fit["items"], "垃圾"]}, api.get_index())
    assert "拒绝静默丢件" in str(excinfo.value)
    assert "items'][2]" in str(excinfo.value), "错误信息必须指出是第几件坏了"


def test_every_rendered_line_resolves_back_to_a_fitted_type_id() -> None:
    """**逐行可反查**：除首行与空行外，每一行都必须能按 SDE 名字查回**原始配装里的**同一个 `type_id`。

    整段往返一致只能证明**总量**对；逐行反查才能挡住「名字真、但张冠李戴」
    （把 `Rapid Light Missile Launcher II` 写成同族的另一件模块）。
    `武器名, 弹药名` 两段都要反查：模块段必须在**已装集合**里，弹药段必须在**原始 charge 集合**里
    （弹药是模块的装填，不是已装模块 —— 拿它去比已装集合会张冠李戴）。
    数量后缀 `xN` 先剥掉；无人机/货舱段也在内（它们同样是真名字）。
    """
    text = _eft(
        ("Damage Control II", "Nanofiber Internal Structure II"),
        ("Multispectrum Shield Hardener II",),
        (
            LAUNCHER,
            f"{LAUNCHER}, Scourge Fury Light Missile",  # 带装填：逐行反查要覆盖弹药段
            LAUNCHER,
            "Improved Cloaking Device II",
        ),
        ("Medium Ancillary Current Router I",),
        name="LineByLine",
    )
    first = api.parse_fit(text)
    assert first["ok"] is True, first.get("error")
    emitted = first["result"]["eft"]

    index = api.get_index()
    original = normalize.normalize_fit(text)["fit"]
    assert original is not None
    original_ids = {
        int(item["type_id"])
        for item in original["items"]
        if (item.get("slot") or {}).get("type") != "cargo"
    }
    assert original_ids, "原始配装不能是空集，否则下面的断言永真"

    def _resolve(segment: str) -> int:
        name = segment.rsplit(" x", 1)[0] if " x" in segment else segment
        hits = [row["type_id"] for row in index.search_types(name, 5) if row["name"] == name]
        assert hits, f"渲染出的这一段在 SDE 里查不到同名类型：{name!r}（整行 {emitted!r}）"
        return int(hits[0])

    checked = 0
    for line in emitted.split("\n")[1:]:
        if not line.strip():
            continue
        module_part, *charge_parts = [part.strip() for part in line.split(",")]
        assert _resolve(module_part) in original_ids, f"{line!r} 的模块名不在已装集合里"
        for charge_part in charge_parts:
            resolved = _resolve(charge_part)
            assert resolved in _charge_ids(original), f"{line!r} 的弹药名不在原始 charge 集合里"
        checked += 1
    assert checked >= 7, f"至少要逐行检查 7 行（本样本已装 7 件），实际 {checked}"

# =============================================================================
# 四、`Gas Cloud Harvester` 漏分类（T-54 只收 `miner`，整族落进 unattributed）
# =============================================================================


@pytest.mark.parametrize(
    ("name", "group"),
    [
        ("Gas Cloud Harvester I", "Gas Cloud Harvesters"),
        ("Gas Cloud Harvester II", "Gas Cloud Harvesters"),
        ("ORE Gas Cloud Harvester", "Gas Cloud Harvesters"),
        ("Abyssal Gas Cloud Harvester", "Gas Cloud Harvesters"),
    ],
)
def test_gas_cloud_harvesters_are_turrets(name: str, group: str) -> None:
    """采气器**占炮位**：`Gas Cloud Harvester` 一族必须判成 `turret`（不是 `unattributed`）。"""
    assert R._hardpoint_kind(name, group) == "turret"


def test_prospect_gas_harvesters_are_counted_and_over_install_is_caught() -> None:
    """**端到端**：Prospect（2 turret 硬点）装 2 门采气器 → `used 2 / free 0`；装 3 门 → 超装 1。

    改之前：`turret` 恒 `{total:2, used:0, free:2}`（虚报 2 个空炮位），装 3 门时**一条 flag 都不出**
    —— 真实的非法装配被静默放过。这是「漏分类会给错事实」的最短复现。
    """
    two = _eft(("Gas Cloud Harvester II",) * 2, ship="Prospect", name="GasTwo")
    overview = _resources(two)["overview"]
    assert overview["hardpoints"]["turret"] == {"total": 2, "used": 2, "free": 0}
    assert overview["hardpoints"]["unattributed"] == 0

    three = _eft(("Gas Cloud Harvester II",) * 3, ship="Prospect", name="GasThree")
    result = _resources(three)
    assert result["overview"]["hardpoints"]["turret"]["free"] == -1
    flags = {flag["id"]: flag for flag in result["flags"]}
    assert "hardpoint_violation" in flags, "3 门采气器装 2 个炮位 = 非法装配，必须报"
    assert flags["hardpoint_violation"]["evidence"]["over_kinds"] == "炮位超1"


def test_harvester_keyword_is_what_makes_the_harvesters_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """`harvester` 是 `Gas Cloud Harvester` 被数出来的**唯一**原因（抹掉 → `used` 归 0）。"""
    two = _eft(("Gas Cloud Harvester II",) * 2, ship="Prospect", name="GasKeyword")
    assert _resources(two)["overview"]["hardpoints"]["turret"]["used"] == 2

    keywords = tuple(word for word in R.HARDPOINT_TURRET_KEYWORDS if word != "harvester")
    monkeypatch.setattr(R, "HARDPOINT_TURRET_KEYWORDS", keywords)
    api.clear_caches()
    mutated = _resources(two)["overview"]["hardpoints"]

    assert mutated["turret"] == {"total": 2, "used": 0, "free": 2}, "抹掉关键词后必须数不出采气器"
    assert mutated["unattributed"] == 2, "数不出来的一律进 unattributed，不许猜"


# =============================================================================
# 五、`empty_hardpoint` 的判据（**独立审查改判**：只用「已在用且没装满」的那一类）
# =============================================================================

MISSILE_BOAT_TEXT = _eft(
    (
        "Damage Control II",
        "Ballistic Control System II",
    ),
    (
        "Multispectrum Shield Hardener II",
        "Warp Disruptor II",
        "100MN Y-S8 Compact Afterburner",
    ),
    (
        LAUNCHER,
        LAUNCHER,
        LAUNCHER,
        "Improved Cloaking Device II",
    ),
    ("Medium Ancillary Current Router I",),
    name="MissileBoat",
)
"""Osprey NI 纯导弹船：发射器用满（3/3）、炮位一件没装、高槽 4/5。"""


def test_empty_hardpoint_does_not_fire_when_the_free_class_was_never_used() -> None:
    """纯导弹船：`turret used = 0` → 不报。这是**噪音**，不是装配问题。"""
    result = _resources(MISSILE_BOAT_TEXT)
    overview = result["overview"]

    assert overview["hardpoints"]["turret"] == {"total": 2, "used": 0, "free": 2}
    assert overview["hardpoints"]["launcher"] == {"total": 3, "used": 3, "free": 0}
    assert overview["slots"]["high"]["total"] - overview["slots"]["high"]["used"] == 1, "高槽确实空 1 个"
    assert "empty_hardpoint" not in {flag["id"] for flag in result["flags"]}


def test_empty_hardpoint_does_not_fire_on_a_drone_boat_with_empty_high_slots() -> None:
    """Dominix 纯无人机：6 个炮位一件不用、6 个高槽全空 —— 这是**设计选择**，不报。"""
    text = _eft(("Drone Damage Amplifier II",), (), (), name="DroneBoat", ship="Dominix")
    result = _resources(text)

    assert result["overview"]["hardpoints"]["turret"] == {"total": 6, "used": 0, "free": 6}
    assert "empty_hardpoint" not in {flag["id"] for flag in result["flags"]}


def test_empty_hardpoint_fires_with_the_class_that_is_in_use() -> None:
    """**真问题**：发射器已经在用（2/3）却没装满 + 高槽还有空位 → 报，且**点名是发射器**。"""
    text = _eft(
        ("Ballistic Control System II",),
        ("Multispectrum Shield Hardener II",),
        (LAUNCHER, LAUNCHER),
        name="HalfLaunchers",
    )
    result = _resources(text)
    flags = {flag["id"]: flag for flag in result["flags"]}

    assert "empty_hardpoint" in flags
    evidence = flags["empty_hardpoint"]["evidence"]
    assert evidence["launcher_free"] == 1
    assert evidence["launcher_used"] == 2
    assert evidence["turret_free"] == 2
    assert evidence["turret_used"] == 0
    assert evidence["free_kinds"] == "发射器1", "只点名「已经在用」的那一类"
    assert evidence["high_empty"] == 3
    assert "发射器1" in flags["empty_hardpoint"]["message"]


def test_empty_hardpoint_names_the_turret_class_on_a_gun_boat() -> None:
    """反过来：炮船（Rifter 3 炮位只装 2 门）→ `free_kinds` 必须是「炮位1」。"""
    text = _eft(("200mm AutoCannon II",) * 2, ship="Rifter", name="HalfGuns")
    result = _resources(text)
    flags = {flag["id"]: flag for flag in result["flags"]}

    assert result["overview"]["hardpoints"]["turret"] == {"total": 3, "used": 2, "free": 1}
    assert flags["empty_hardpoint"]["evidence"]["free_kinds"] == "炮位1"


def test_empty_hardpoint_leaves_over_install_to_hardpoint_violation() -> None:
    """超装（4 门 RLML / 3 个挂点）不归 `empty_hardpoint` —— 否则会写出「余 -1」这种自相矛盾的措辞。"""
    text = _eft(
        ("Ballistic Control System II",),
        ("Multispectrum Shield Hardener II",),
        (LAUNCHER,) * 4,
        name="OverLaunchers",
    )
    result = _resources(text)
    flag_ids = {flag["id"] for flag in result["flags"]}

    assert result["overview"]["hardpoints"]["launcher"]["free"] == -1
    assert "empty_hardpoint" not in flag_ids
    assert "hardpoint_violation" in flag_ids, "超装由 violation 报，两条规则不许抢同一件事"


def test_empty_hardpoint_says_nothing_when_a_hardpoint_total_is_unknown() -> None:
    """**unknown 不是 0**：另一个硬点类的总量缺失时，不许拿 0 顶上去出一条结论。

    正反两向都验：同样的一门炮 + 4 个高槽，**只在两个 total 都有值时才报**；
    把 `launcher` 的 101 属性抽掉 → `launcher_free` 从 0 变成 `None` → 必须**不出**结论。
    """
    from noobbaozi.project import diagnostics as D

    rule = D.Rule(id="empty_hardpoint", view="resources", enabled=True, message="{free_kinds}")
    both = {
        A.ATTR_TURRET_SLOTS_LEFT: 2,
        A.ATTR_LAUNCHER_SLOTS_LEFT: 0,
        A.ATTR_HI_SLOTS: 4,
    }
    fired = D.evaluate(_stub_ctx(both), rules=[rule])
    assert fired and fired[0]["evidence"]["free_kinds"] == "炮位1", "两个 total 都有值时该报"

    only_turret = {A.ATTR_TURRET_SLOTS_LEFT: 2, A.ATTR_HI_SLOTS: 4}
    assert D.evaluate(_stub_ctx(only_turret), rules=[rule]) == [], "总量未知 → 不出结论（不补 0）"


# =============================================================================
# 六、`hardpoint_violation`：两类同时超装时，L1 message 与 L0 hint 都要说到两类
# =============================================================================


def test_hardpoint_violation_reports_both_classes_in_message_and_hint() -> None:
    """Osprey NI 装 3 门炮 + 4 门发射器 → `炮位超1` **且** `发射器超1`，一个都不许丢。

    改之前：`evidence` 里只有最严重的那一类 + `others`，而 `message` / `hint` 都不渲染 `others`
    —— 读文案的人会以为只超了一种。
    """
    text = _eft(
        ("Heavy Beam Laser II", "Heavy Beam Laser II", "Heavy Beam Laser II"),
        (),
        (LAUNCHER, LAUNCHER, LAUNCHER, LAUNCHER),
        name="BothOver",
    )
    result = _resources(text)
    flags = {flag["id"]: flag for flag in result["flags"]}
    evidence = flags["hardpoint_violation"]["evidence"]

    assert evidence["kind"] == "turret", "最严重（并列时取靠前的一类）"
    assert evidence["over_kinds"] == "炮位超1+发射器超1"
    assert [row["kind"] for row in evidence["others"]] == ["launcher"], "另一类仍在 others（机器可读）"
    assert "炮位超1" in flags["hardpoint_violation"]["message"]
    assert "发射器超1" in flags["hardpoint_violation"]["message"], "message 必须两类都说到"

    envelope = api.fit_index(text)
    assert envelope["ok"] is True, envelope.get("error")
    hints = {flag["id"]: flag["hint"] for flag in envelope["result"]["flags"]}
    assert hints["hardpoint_violation"] == "炮位超1+发射器超1", "L0 hint 必须两类都说到"

# =============================================================================
# 七、EFT 首行的两种写法 + 空槽标记（Lead 修了 engine 解析器，这里只钉行为）
# =============================================================================

RIFTER = 587
"""Rifter 的 type_id（`[Rifter]` 系列用例的锚点）。"""


@pytest.mark.parametrize("header", ["[Rifter]", "[Rifter, ]", "[Rifter, name]"])
def test_engine_accepts_both_header_forms(header: str) -> None:
    """`[Ship]` 与 `[Ship, ]` 都得能解析出**同一艘船**（EMIT 写的是无逗号那种）。"""
    text = f"{header}\n\n200mm AutoCannon II\n"
    result = normalize.normalize_fit(text)

    assert result["fit"] is not None, result.get("errors")
    assert result["fit"]["ship"]["type_id"] == RIFTER
    assert [(item["type_id"], item["slot"]["type"]) for item in result["fit"]["items"]] == [
        (2889, "high")
    ]


def test_empty_slot_marker_is_not_mistaken_for_the_ship_name() -> None:
    """`[Empty High slot]` 是**空槽标记**，不是船名行：船必须是 Rifter，炮必须还在。

    这条防的是「没逗号就当船名」的老判据 —— 它会把 `[Empty High slot]` 当成船，
    于是整段配装解析失败或船型错乱。反过来，**首行**只有 `[Empty High slot]` 时
    确实没有船，必须失败（不许猜一艘船）。
    """
    text = "[Rifter]\n\n[Empty High slot]\n\n200mm AutoCannon II\n"
    result = normalize.normalize_fit(text)
    assert result["fit"] is not None, result.get("errors")
    assert result["fit"]["ship"]["type_id"] == RIFTER
    assert len(result["fit"]["items"]) == 1

    no_ship = normalize.normalize_fit("[Empty High slot]\n\n200mm AutoCannon II\n")
    assert no_ship["fit"] is None, "没有船名行就不许猜一艘船"


def test_ship_only_fit_round_trips_through_emit() -> None:
    """**只有船的配装**：`emit` 写 `[Rifter]`，再解析必须成功且没有模块（往返闭死）。

    这是 T-54 审查挖出来的缺陷的**回归钉子**：以前 `emit` 写 `[Rifter]`，而我们自己的
    解析器把它当 JSON → 往返断裂。修法在 engine（无逗号头也接受），这里钉住闭环。
    """
    fit = normalize.normalize_fit("[Rifter, ]\n")["fit"]
    assert fit is not None
    assert fit["items"] == []

    emitted = eft.emit(fit, api.get_index())
    assert emitted == "[Rifter]", "无名配装写 [船名]（EFT 惯例），末尾不留空行"

    again = normalize.normalize_fit(emitted)
    assert again["fit"] is not None, again.get("errors")
    assert again["fit"]["ship"]["type_id"] == RIFTER
    assert again["fit"]["items"] == []


def test_unnamed_fit_publishes_a_reparseable_eft() -> None:
    """接口层：**没有配装名**的输入也要给出一段能再解析的 EFT。

    `parse_fit` 的 `eft` 字段是给 agent 复制粘贴用的；无名配装（`[Ship]` 头，以及 DNA / ESF
    这种本来就没有名字的输入）emit 出来是 `[Ship]`，以前那段文本自己都读不回来。
    """
    envelope = api.parse_fit("[Rifter]\n\n200mm AutoCannon II\n\nDamage Control II\n")
    assert envelope["ok"] is True, envelope.get("error")
    emitted = envelope["result"]["eft"]
    assert emitted.startswith("[Rifter]"), emitted

    again = api.parse_fit(emitted)
    assert again["ok"] is True, again.get("error")
    assert again["result"]["modules"]["fitted"] == envelope["result"]["modules"]["fitted"]


# =============================================================================
# 八、炮位事实**只有一个出处**（T-54 审查追加：firepower 里的 hardpoints_free 必须死）
# =============================================================================

ALL_VIEWS = ("firepower", "survivability", "mobility", "resources", "constraints", "stability")


def _paths_with(node: object, needle: str, prefix: str = "") -> list[str]:
    """递归找出**键名里含 `needle`** 的路径（用来扫「哪个视角还藏着 hardpoint*」）。"""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if needle in str(key):
                found.append(path)
            found.extend(_paths_with(value, needle, path))
    elif isinstance(node, list):
        for position, value in enumerate(node):
            found.extend(_paths_with(value, needle, f"{prefix}[{position}]"))
    return found


def test_only_resources_exposes_hardpoint_facts() -> None:
    """六视角里**只有 `resources` 有硬点事实**，而且没有第二套 `hardpoints_free`。

    旧实现的问题不是「算错了一个数」，是**同一产品里同时存在错误的老数字和正确的新数字**：
    `firepower.hardpoints_free` 直读船体 102/101（基准炮位数、与装配无关），
    `resources.hardpoints` 才是扣掉已装模块的真值 —— agent 走 `fit_view("firepower")`
    就会拿到老的那个，而修这一轮正是为了它。
    """
    text = MISSILE_BOAT_TEXT
    hits: dict[str, list[str]] = {}
    for view in ALL_VIEWS:
        envelope = api.fit_view(text, view)
        assert envelope["ok"] is True, envelope.get("error")
        # L1（overview）与 L0（index）两个入口都要扫：路径一律带上视角前缀，便于读
        paths = [f"{view}.{path}" for path in _paths_with(envelope["result"]["overview"], "hardpoint")]
        index_body = (api.fit_index(text)["result"] or {}).get(view)
        paths += [f"{view}.{path}" for path in _paths_with(index_body, "hardpoint")]
        if paths:
            hits[view] = paths

    assert set(hits) == {"resources"}, f"硬点事实只许出现在 resources，实际：{hits}"
    assert "resources.hardpoints" in hits["resources"], "反证：扫描确实看得见 resources 的硬点块"
    assert not any("hardpoints_free" in path for path in hits["resources"]), "旧字段一个都不许留"


def test_hardpoints_free_is_unreachable_from_every_view() -> None:
    """`fit_get` 在任何视角都取不到 `hardpoints_free`（L0/L1 都没有这条路）。"""
    text = MISSILE_BOAT_TEXT
    for view in ALL_VIEWS:
        answer = api.fit_get(text, f"{view}.hardpoints_free")
        assert answer["ok"] is False, f"{view} 仍然取得到 hardpoints_free：{answer}"
        assert answer["error"]["kind"] == "invalid_path"

    # 反证：正确的那条路取得到（否则上面的循环可能只是「什么都取不到」而空转）
    good = api.fit_get(text, "resources.hardpoints.launcher.free")
    assert good["ok"] is True and good["result"]["value"] == 0


# =============================================================================
# 九、`unattributed` 拆分：确定不占硬点 vs 真分不清
# （该键 2026-10-19 由 `unclassified` 改名：旧名与 `unknown_reasons` 的兜底值撞名）
# =============================================================================


@pytest.mark.parametrize(
    ("name", "group"),
    [
        ("Improved Cloaking Device II", "Cloaking Device"),
        ("Covert Ops Cloaking Device II", "Cloaking Device"),
        ("Large EMP Smartbomb II", "Smart Bomb"),
        ("Heavy Energy Nosferatu II", "Energy Nosferatu"),
        ("Heavy Energy Neutralizer II", "Energy Neutralizer"),
        ("Small Remote Armor Repairer II", "Remote Armor Repairer"),
        ("Small Remote Shield Booster II", "Remote Shield Booster"),
        ("Small Remote Hull Repairer I", "Remote Hull Repairer"),
        ("Small Remote Capacitor Transmitter I", "Remote Capacitor Transmitter"),
        ("Salvager I", "Salvager"),
        ("Small Tractor Beam I", "Tractor Beam"),
        ("Core Probe Launcher I", "Scan Probe Launcher"),
        ("Shield Command Burst I", "Command Burst"),
        ("Bastion Module I", "Siege Module"),
    ],
)
def test_confirmed_non_hardpoint_modules_are_their_own_bucket(name: str, group: str) -> None:
    """**确定不占硬点**的进 `non_hardpoint`，不许再混进 `unclassified`（那是「认不出」）。"""
    assert R._hardpoint_kind(name, group) == "non_hardpoint"


@pytest.mark.parametrize(
    ("name", "group"),
    [
        ("Interdiction Sphere Launcher I", "Interdiction Sphere Launcher"),
        ("Festival Launcher I", "Festival Launcher"),
        # 关键词表没命中、也不在确定白名单里的，一律算「分不清」（历史教训：Gas Cloud Harvester）
        ("Reaction Control Unit", "Miscellaneous"),
        ("Compression Module", "Miscellaneous"),
    ],
)
def test_unknown_modules_stay_unattributed(name: str, group: str) -> None:
    """**真分不清**的留在 `unattributed`：这一类 `> 0` 就是「可能漏收了一族」的维护信号。"""
    assert R._hardpoint_kind(name, group) == "unattributed"


def test_osprey_cloak_is_non_hardpoint_and_unattributed_is_zero() -> None:
    """Osprey 真实装配：`non_hardpoint: 1`（那件隐身）+ 样例名字；`unattributed: 0`。

    拆之前两类混在一个计数里（旧名 `unclassified`），读起来像「有一件武器我们认不出」，
    实际是确定不占硬点的隐身。
    """
    hardpoints = _resources(MISSILE_BOAT_TEXT)["overview"]["hardpoints"]

    assert hardpoints["non_hardpoint"] == 1
    assert hardpoints["non_hardpoint_modules"] == ["Improved Cloaking Device II"]
    assert hardpoints["unattributed"] == 0
    # 硬点的 used 不受影响（隐身一件都不加）
    assert hardpoints["launcher"] == {"total": 3, "used": 3, "free": 0}


def test_non_hardpoint_modules_list_caps_at_three() -> None:
    """样例名字**最多 3 个**（再多只是噪音）；计数仍是真实的 4。"""
    text = _eft(
        (),
        (),
        (
            "Improved Cloaking Device II",
            "Salvager I",
            "Small Tractor Beam I",
            "Large EMP Smartbomb II",
        ),
        name="FourUtility",
    )
    hardpoints = _resources(text)["overview"]["hardpoints"]

    assert hardpoints["non_hardpoint"] == 4
    assert len(hardpoints["non_hardpoint_modules"]) == 3
    assert hardpoints["unattributed"] == 0


def test_both_counts_are_in_l0_with_provenance() -> None:
    """两条计数都进 L0，且 `fit_get` 能原路取回（形状是契约）。"""
    envelope = api.fit_index(MISSILE_BOAT_TEXT)
    assert envelope["ok"] is True, envelope.get("error")
    block = envelope["result"]["resources"]["hardpoints"]

    assert block["non_hardpoint"] == 1
    assert block["unattributed"] == 0
    assert block["non_hardpoint_modules"] == ["Improved Cloaking Device II"]

    for path, expected in (
        ("resources.hardpoints.non_hardpoint", 1),
        ("resources.hardpoints.unattributed", 0),
    ):
        answer = api.fit_get(MISSILE_BOAT_TEXT, path)
        assert answer["ok"] is True, answer
        assert answer["result"]["value"] == expected

    provenance = _resources(MISSILE_BOAT_TEXT)["provenance"]
    assert provenance["hardpoints.non_hardpoint"] == "assumed"
    assert provenance["hardpoints.non_hardpoint_modules"] == "assumed"
    assert provenance["hardpoints.unattributed"] == "assumed"


def test_moving_a_group_out_of_the_whitelist_sends_it_to_unattributed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**非永真证明**：把 `Cloaking Device` 从确定白名单里挪走 → 隐身必须落回 `unattributed`。

    这是「`non_hardpoint: 1` 真的是白名单判出来的」的唯一验法：抹掉那一行，
    同一套配装的结论必须翻转（`non_hardpoint` 0 / `unattributed` 1），否则断言在空转。
    """
    baseline = _resources(MISSILE_BOAT_TEXT)["overview"]["hardpoints"]
    assert baseline["non_hardpoint"] == 1 and baseline["unattributed"] == 0

    mutated_whitelist = frozenset(
        group
        for group in R.HARDPOINT_NON_HARDPOINT_GROUPS_FOLDED
        if group != "cloaking device"
    )
    monkeypatch.setattr(R, "HARDPOINT_NON_HARDPOINT_GROUPS_FOLDED", mutated_whitelist)
    api.clear_caches()
    mutated = _resources(MISSILE_BOAT_TEXT)["overview"]["hardpoints"]

    assert mutated["non_hardpoint"] == 0, "白名单被抹掉后不许还算「确定不占硬点」"
    assert mutated["non_hardpoint_modules"] == []
    assert mutated["unattributed"] == 1, "数不出来的必须落回 unattributed（不猜）"


def test_unattributed_key_is_gone_under_its_old_name() -> None:
    """改名必须**一次改干净**：`unclassified` 这个键在 L0 / L1 / `get_path` 三处都得消失。

    旧名与 `reasons.UNKNOWN_REASONS` 的兜底值 `unclassified`（=「不知道为什么不知道」）同字
    不同义：agent 在 L0 同时看到 `hardpoints.unclassified: 1` 与 `unknown_reasons: {…:
    "unclassified"}` 会以为是同一件事。**不留别名**——同时输出新旧两个键正是 `hardpoints_free`
    的反面教材（一份数据说两遍就会漂移）。
    """
    overview = _resources(MISSILE_BOAT_TEXT)["overview"]["hardpoints"]
    assert "unattributed" in overview, "新键必须在"
    assert "unclassified" not in overview, "旧键不许留（哪怕值一样）"

    # L0 是同一份块的透传：必须只剩新名
    index_block = api.fit_index(MISSILE_BOAT_TEXT)["result"]["resources"]["hardpoints"]
    assert "unattributed" in index_block and "unclassified" not in index_block

    # 旧路径取不到（不是「取到 None」，是**根本没这条路**）
    old = api.fit_get(MISSILE_BOAT_TEXT, "resources.hardpoints.unclassified")
    assert old["ok"] is False, f"旧路径仍然取得到：{old}"
    assert old["error"]["kind"] == "invalid_path"
    new = api.fit_get(MISSILE_BOAT_TEXT, "resources.hardpoints.unattributed")
    assert new["ok"] is True and new["result"]["value"] == 0
    assert new["result"]["provenance"] == "assumed"


def test_unattributed_count_is_counted_not_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """**非永真证明**：这个数真是「数」出来的 —— 抹掉「分不清」组表就必须归 0。

    `Festival Launcher` 落进 `unattributed` 的唯一原因是它在 `HARDPOINT_UNATTRIBUTED_GROUPS`
    里。抹空那张表后，名字里的 `launcher` 会被关键词表接住 → 算进 `launcher.used`、
    `unattributed` 归 0。所以「`unattributed == 1`」不是永真：把它写死 0 的实现会在第一条
    断言上红；把组表写死进函数体的实现在第二条上红。
    """
    text = _eft(("Festival Launcher",), name="FestivalProbe")
    baseline = _resources(text)["overview"]["hardpoints"]
    assert baseline["unattributed"] == 1, "节日发射器是「分不清」，必须计数"
    assert baseline["launcher"]["used"] == 0, "分不清的一件都不许猜进 used"

    monkeypatch.setattr(R, "HARDPOINT_UNATTRIBUTED_GROUPS_FOLDED", frozenset())
    api.clear_caches()  # api 是 LRU：不清缓存会直接返回**抹表之前**的结果，测试变成永真
    mutated = _resources(text)["overview"]["hardpoints"]
    assert mutated["unattributed"] == 0, "抹掉组表后不许还算「分不清」"
    assert mutated["launcher"]["used"] == 1, "名字里的 launcher 由关键词表接住（不猜）"


def test_hardpoint_state_never_reuses_an_unknown_reason_value() -> None:
    """硬点第四态**不许再撞** `unknown_reasons` 的取值（改名的唯一目的，钉成不变式）。

    `reasons.UNKNOWN_REASONS` 是「为什么不知道」的合法取值集合；`HARDPOINT_UNATTRIBUTED` 是
    「这件模块归不了类」。两者同字时，L0 的两块数据会被读成同一件事 —— 这里钉住**集合不相交**，
    以后谁把名字改回去都会立刻红。
    """
    assert R.HARDPOINT_UNATTRIBUTED not in RS.UNKNOWN_REASONS, (
        "硬点第四态又与 unknown 原因表撞名了 —— 本次改名的意义就是让这两个词不重合"
    )
    assert R.HARDPOINT_UNATTRIBUTED == "unattributed"
    assert RS.FALLBACK_REASON == "unclassified", "reason 侧不该跟着改（它的语义没有歧义）"


# =============================================================================
# 十、`HARDPOINT_LABELS` 单一出处（summary 从 resources 导入，不另抄一份）
# =============================================================================


def test_hardpoint_labels_are_imported_not_copied() -> None:
    """`summary.HARDPOINT_LABELS` 与 `resources.HARDPOINT_LABELS` 必须是**同一个对象**。"""
    from noobbaozi.project import summary

    assert summary.HARDPOINT_LABELS is R.HARDPOINT_LABELS, "两份表迟早漂移；必须是同一个对象"
    assert summary.HARDPOINT_LABELS == {"turret": "炮位", "launcher": "发射器"}
    # 行为不变：hint 仍要用中文短名（导入替换不能把文案打回英文）
    envelope = api.fit_index(
        _eft(("Heavy Beam Laser II",) * 3, (), (LAUNCHER,) * 4, name="LabelsProbe")
    )
    assert envelope["ok"] is True
    hints = {flag["id"]: flag["hint"] for flag in envelope["result"]["flags"]}
    assert hints["hardpoint_violation"] == "炮位超1+发射器超1"


# =============================================================================
# 十一、`_Facts.slot_usage()`：槽位总量未知时**不许当 0**（T-54 审查③）
# =============================================================================

_SLACK_ATTRS: dict[int, float] = {
    A.ATTR_CPU_OUTPUT: 500.0,
    A.ATTR_CPU_FREE: 400.0,  # 400 > 0.5 × 500 → 余量过半
    A.ATTR_POWER_OUTPUT: 1000.0,
    A.ATTR_POWER_FREE: 800.0,  # 800 > 0.5 × 1000
}
"""CPU / PG 余量都过半的船体属性（`resource_slack` 的前置条件）。"""


def test_slot_usage_keeps_unknown_totals_as_none() -> None:
    """槽位总量属性缺失 → `total` / `empty` 都是 `None`，**不许补 0**（unknown 不是 0）。

    旧实现用 `_as_count(_num(...))`：属性缺失返回 0，于是「不知道有几格」被输出成
    「0 格、0 格空」——`resource_slack` 的 `empty_slots_by_slot` 会写出「rig 0」这种假事实。
    """
    from noobbaozi.project import diagnostics as D

    facts = D._Facts(
        _stub_ctx(
            {
                A.ATTR_HI_SLOTS: 4,
                A.ATTR_MED_SLOTS: 6,
                A.ATTR_LOW_SLOTS: 4,
                # 故意不给 ATTR_RIG_SLOTS：rig 的格数是**未知**
            }
        )
    )
    usage = facts.slot_usage()

    assert usage["rig"] == {"used": 0, "total": None, "empty": None}, "未知必须是 None，不是 0"
    assert usage["high"] == {"used": 1, "total": 4, "empty": 3}, "已知的照常算（used 是数出来的）"

    total, summary = D._usage_summary(usage)
    assert total is None, "任一未知 → 总数不可知"
    assert "rig ?" in summary, "摘要里必须写 ?, 不许写成 0"


def test_resource_slack_refuses_to_conclude_when_a_slot_total_is_unknown() -> None:
    """正反两向：rig 未知 → `resource_slack` **不出结论**；四个总量都给出 → 出结论（含真 0）。"""
    from noobbaozi.project import diagnostics as D

    rule = D.Rule(id="resource_slack", view="resources", enabled=True, message="x")

    unknown_rig = _stub_ctx(
        {**_SLACK_ATTRS, A.ATTR_HI_SLOTS: 4, A.ATTR_MED_SLOTS: 6, A.ATTR_LOW_SLOTS: 4}
    )
    assert D.evaluate(unknown_rig, rules=[rule]) == [], (
        "rig 格数未知时不许下「还有空槽」的结论（否则会写出 rig 0 这种假事实）"
    )

    all_known = _stub_ctx(
        {
            **_SLACK_ATTRS,
            A.ATTR_HI_SLOTS: 4,
            A.ATTR_MED_SLOTS: 6,
            A.ATTR_LOW_SLOTS: 4,
            A.ATTR_RIG_SLOTS: 3,
        }
    )
    fired = D.evaluate(all_known, rules=[rule])
    assert fired, "四个总量都在时这条规则必须能命中（证明上面那次「不报」不是因为规则死了）"
    evidence = fired[0]["evidence"]
    # 高 4-1=3 / 中 6 / 低 4 / 改装 3
    assert evidence["empty_slots"] == 3 + 6 + 4 + 3
    assert "rig 3" in evidence["empty_slots_by_slot"], "已知的槽位要报真数"


def test_empty_hardpoint_refuses_to_conclude_when_high_total_is_unknown() -> None:
    """高槽总量未知时 `empty_hardpoint` 同样不出结论（硬点已知也不许凑一条 flag）。"""
    from noobbaozi.project import diagnostics as D

    rule = D.Rule(id="empty_hardpoint", view="resources", enabled=True, message="{free_kinds}")
    attrs = {
        A.ATTR_TURRET_SLOTS_LEFT: 4,
        A.ATTR_LAUNCHER_SLOTS_LEFT: 0,
        # 故意不给 ATTR_HI_SLOTS
    }
    assert D.evaluate(_stub_ctx(attrs), rules=[rule]) == []

    with_high = {**attrs, A.ATTR_HI_SLOTS: 4}
    fired = D.evaluate(_stub_ctx(with_high), rules=[rule])
    assert fired and fired[0]["evidence"]["free_kinds"] == "炮位3", "补上高槽总量就能报（反证）"


# =============================================================================
# 十二、装填弹药（charge）：必须跟着模块行往返（T-54 追加缺陷，independent 实测）
# =============================================================================

_NOOBBAOZI_ROOT = Path(__file__).resolve().parents[1]
KILLMAIL_ESI = _NOOBBAOZI_ROOT / "tests" / "fixtures" / "killmail" / "139239127-esi.json"
F1_EFT = _NOOBBAOZI_ROOT / "acceptance" / "fixtures" / "f1-hecate-killmail.eft"
"""真实样本：前者是仓库内的 killmail fixture，后者是独立验收用的 f1（5 门中子炮 + Void S）。"""


def _charges(fit: dict) -> list[tuple[int, int | None]]:
    """已装条目的 `(模块 type_id, 弹药 type_id)`，**排序后**比较（不依赖 item 顺序）。"""
    return sorted(
        (int(item["type_id"]), (item.get("charge") or {}).get("type_id"))
        for item in fit["items"]
        if (item.get("slot") or {}).get("type") != "cargo"
    )


def _charge_ids(fit: dict) -> set[int]:
    """原始 fit 里全部**装填弹药**的 type_id（逐行反查弹药段时用）。"""
    return {
        int(charge["type_id"])
        for item in fit["items"]
        if isinstance(item.get("charge"), dict) and isinstance(item["charge"].get("type_id"), int)
        for charge in [item["charge"]]
    }


def test_eft_roundtrip_preserves_every_loaded_charge() -> None:
    """**核心回归**：真实 killmail 配装的 7 个装填弹药必须逐个回来（不只是模块集合一致）。

    改之前：模块行只写 `Light Neutron Blaster II`，弹药被丢进货舱段，再解析时
    **5 门炮的 `charge` 全变 `None`** —— 而且渲染「成功」、没有任何 warning。
    """
    from noobbaozi.engine import adapter

    payload = json.loads(KILLMAIL_ESI.read_text(encoding="utf-8"))
    api.clear_caches()
    fit = adapter.load_killmail(payload)
    charged_before = [charge for _module, charge in _charges(fit) if charge]
    assert len(charged_before) >= 3, "反证：这个样本确实带装填弹药，否则断言永真"

    emitted = eft.emit(fit, api.get_index())
    again = adapter.load_eft(emitted)

    assert _charges(again) == _charges(fit), "装填弹药（charge type_id）必须逐个一致"
    assert [charge for _module, charge in _charges(again) if charge] == charged_before
    # 渲染幂等：再渲染一次逐字节相同
    assert eft.emit(again, api.get_index()) == emitted


def test_f1_sample_keeps_all_five_blaster_charges() -> None:
    """独立验收用的 f1 样本：**5 门 `Light Neutron Blaster II` 各带 `Void S`**，往返后 type_id 不变。"""
    if not F1_EFT.is_file():
        pytest.skip(f"缺少 f1 样本：{F1_EFT}")

    text = F1_EFT.read_text(encoding="utf-8-sig")
    api.clear_caches()
    fit = normalize.normalize_fit(text)["fit"]
    assert fit is not None

    blasters_before = [
        (item["type_id"], (item.get("charge") or {}).get("type_id"))
        for item in fit["items"]
        if item["type_id"] == 3178  # Light Neutron Blaster II
    ]
    assert len(blasters_before) == 5, f"f1 应有 5 门中子炮，实际 {len(blasters_before)}"
    assert all(charge == 12612 for _module, charge in blasters_before), (
        "5 门炮都必须装填 Void S（12612）"
    )

    emitted = eft.emit(fit, api.get_index())
    again = normalize.normalize_fit(emitted)["fit"]
    assert again is not None
    assert _charges(again) == _charges(fit), "f1 的 7 个 charge 必须逐个回来"
    assert eft.emit(again, api.get_index()) == emitted, "幂等：逐字节稳定"


def test_charge_line_has_no_quantity_and_cargo_is_not_duplicated() -> None:
    """弹药行的三条口径：`武器名, 弹药名` / **不带数量** / 货舱堆不被装填复制。

    数量不写是实测结论：引擎解析器**拒绝** `Weapon, Ammo x100`（`unknown type …`），
    EVE 剪贴板也不带，且引擎解析出的 `charge` 本身只有 `type_id`。
    """
    text = "[Hecate, loaded]\n\nLight Neutron Blaster II, Void S\n\nVoid S x1044\n"
    api.clear_caches()
    fit = normalize.normalize_fit(text)["fit"]
    assert fit is not None

    emitted = eft.emit(fit, api.get_index())
    lines = emitted.split("\n")
    module_line = next(line for line in lines if line.startswith("Light Neutron Blaster II"))

    assert module_line == "Light Neutron Blaster II, Void S"
    assert " x" not in module_line, "模块行不带数量后缀"
    assert lines.count("Void S x1044") == 1, "货舱里那一堆只出现一次（不与装填重复）"

    again = normalize.normalize_fit(emitted)["fit"]
    assert again is not None
    high = [
        (item["type_id"], (item.get("charge") or {}).get("type_id"))
        for item in again["items"]
        if (item.get("slot") or {}).get("type") == "high"
    ]
    assert high == [(3178, 12612)], "装填回来"
    cargo = [
        (item["type_id"], item.get("quantity"))
        for item in again["items"]
        if (item.get("slot") or {}).get("type") == "cargo"
    ]
    assert cargo == [(12612, 1044)], "货舱数量没被装填吃掉"


def test_unknown_charge_type_fails_loudly() -> None:
    """弹药名查不到 → `EftError`（**绝不静默丢**），且错误里带上门户模块名。"""
    text = "[Hecate, loaded]\n\nLight Neutron Blaster II, Void S\n"
    fit = normalize.normalize_fit(text)["fit"]
    assert fit is not None
    high = next(i for i in fit["items"] if (i.get("slot") or {}).get("type") == "high")

    broken = {**fit, "items": [{**high, "charge": {"type_id": 999_999_999}}]}
    with pytest.raises(eft.EftError) as excinfo:
        eft.emit(broken, api.get_index())
    message = str(excinfo.value)
    assert "装填弹药" in message and "999999999" in message
    assert "Light Neutron Blaster II" in message, "错误信息必须指出是哪门炮的弹药"

    # 结构不对（不是对象 / 缺 type_id）同样失败，不猜它是「没装弹」
    for bad in ({"charge": "Void S"}, {"charge": {"name": "Void S"}}):
        with pytest.raises(eft.EftError):
            eft.emit({**fit, "items": [{**high, **bad}]}, api.get_index())


def test_comma_in_a_name_fails_loudly() -> None:
    """模块名/弹药名**含逗号** → `EftError`：EFT 用逗号分隔，含逗号的实体在文本里无法无歧义表达。

    SDE 实测 18 个含逗号的已发布类型全是商品/服饰/箱子（不是模块也不是弹药）—— 这是护栏。
    """

    class _CommaIndex:
        def get_type(self, type_id: int) -> dict:
            return {"name": {1: "Hecate", 2: "Weird, Module", 3: "Void S"}[type_id]}

    base = {"ship": {"type_id": 1}, "items": [
        {"type_id": 2, "slot": {"type": "high", "index": 0}},
    ]}
    with pytest.raises(eft.EftError) as excinfo:
        eft.emit(base, _CommaIndex())
    assert "逗号" in str(excinfo.value)

    charge_with_comma = {
        "ship": {"type_id": 1},
        "items": [{"type_id": 3, "slot": {"type": "high", "index": 0}, "charge": {"type_id": 2}}],
    }
    with pytest.raises(eft.EftError) as excinfo:
        eft.emit(charge_with_comma, _CommaIndex())
    assert "弹药名" in str(excinfo.value) or "逗号" in str(excinfo.value)


def test_charge_on_a_cargo_or_drone_item_fails_loudly() -> None:
    """无人机 / 货舱条目带 `charge` → `EftError`：EFT 没有这种表示，不许静默丢掉。"""
    fit = {
        "ship": {"type_id": 1},
        "items": [{"type_id": 2, "slot": {"type": "cargo"}, "quantity": 5,
                   "charge": {"type_id": 3}}],
    }

    class _Index:
        def get_type(self, type_id: int) -> dict:
            return {"name": {1: "Hecate", 2: "Void S", 3: "Nanite Repair Paste"}[type_id]}

    with pytest.raises(eft.EftError) as excinfo:
        eft.emit(fit, _Index())
    assert "货舱" in str(excinfo.value) or "charge" in str(excinfo.value)
