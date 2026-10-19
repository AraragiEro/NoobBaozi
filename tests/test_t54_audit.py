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

import pytest

from noobbaozi import api
from noobbaozi.config import attributes as A
from noobbaozi.engine import normalize
from noobbaozi.project import eft
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
    """这些模块一律不进 `turret` / `launcher`：误分类会让 `free` 变负、**误报超装**。"""
    assert R._hardpoint_kind(name, group) is None


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
    assert overview["hardpoints"]["unclassified"] == 3, "3 个智能炸弹进「未计入」，不是 0"
    assert "hardpoint_violation" not in {flag["id"] for flag in result["flags"]}


# =============================================================================
# 二、关键词表**非永真**：抹掉 `launcher` 关键词，断言必须红
# =============================================================================


def test_launcher_keyword_is_what_counts_the_launchers(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LAUNCHER_KEYWORDS` 是 `used` 的唯一来源 —— 抹成空表后 `used` 必须变 0。

    这是「`used == 3` 不是永真」的证明：数出 3 门发射器的**就是**那张关键词表。
    抹掉之后 3 门 RLML 会全落进 `unclassified`（**不猜**），`free` 也随之变回 3。
    """
    text = _eft(("Damage Control II",), (), (LAUNCHER,) * 3, name="KeywordProbe")
    baseline = _resources(text)["overview"]["hardpoints"]
    assert baseline["launcher"] == {"total": 3, "used": 3, "free": 0}

    monkeypatch.setattr(R, "LAUNCHER_KEYWORDS", ())
    api.clear_caches()  # api 是 LRU：不清缓存会直接返回**抹表之前**的结果，测试变成永真
    mutated = _resources(text)["overview"]["hardpoints"]

    assert mutated["launcher"] == {"total": 3, "used": 0, "free": 3}, "抹掉关键词后必须数不出门数"
    assert mutated["unclassified"] == 3, "数不出来的必须进 unclassified，不许猜成 0 门也不许猜成炮"


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
    数量后缀 `xN` 先剥掉；无人机/货舱段也在内（它们同样是真名字）。
    """
    text = _eft(
        ("Damage Control II", "Nanofiber Internal Structure II"),
        ("Multispectrum Shield Hardener II",),
        (LAUNCHER, LAUNCHER, LAUNCHER, "Improved Cloaking Device II"),
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

    checked = 0
    for line in emitted.split("\n")[1:]:
        if not line.strip():
            continue
        name = line.rsplit(" x", 1)[0] if " x" in line else line
        hits = [row["type_id"] for row in index.search_types(name, 5) if row["name"] == name]
        assert hits, f"渲染出的这一行在 SDE 里查不到同名类型：{line!r}"
        assert hits[0] in original_ids, f"{line!r} 反查回 {hits[0]}，不在原始配装的已装集合里"
        checked += 1
    assert checked >= 7, f"至少要逐行检查 7 行（本样本已装 7 件），实际 {checked}"

# =============================================================================
# 四、`Gas Cloud Harvester` 漏分类（T-54 只收 `miner`，整族落进 unclassified）
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
    """采气器**占炮位**：`Gas Cloud Harvester` 一族必须判成 `turret`（不是 `unclassified`）。"""
    assert R._hardpoint_kind(name, group) == "turret"


def test_prospect_gas_harvesters_are_counted_and_over_install_is_caught() -> None:
    """**端到端**：Prospect（2 turret 硬点）装 2 门采气器 → `used 2 / free 0`；装 3 门 → 超装 1。

    改之前：`turret` 恒 `{total:2, used:0, free:2}`（虚报 2 个空炮位），装 3 门时**一条 flag 都不出**
    —— 真实的非法装配被静默放过。这是「漏分类会给错事实」的最短复现。
    """
    two = _eft(("Gas Cloud Harvester II",) * 2, ship="Prospect", name="GasTwo")
    overview = _resources(two)["overview"]
    assert overview["hardpoints"]["turret"] == {"total": 2, "used": 2, "free": 0}
    assert overview["hardpoints"]["unclassified"] == 0

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
    assert mutated["unclassified"] == 2, "数不出来的一律进 unclassified，不许猜"


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

    class _Ctx:
        """最小 duck-type `ViewContext`（`_Facts` 只用 attrs/items/index/fit）。"""

        index = None
        fit = {"ship": {"type_id": 1}, "items": []}

        def __init__(self, attrs: dict[int, float]) -> None:
            self._attrs = attrs

        def attrs(self, variant: str = "base") -> dict[int, float]:  # noqa: ARG002 - 桩
            return self._attrs

        def items(self, variant: str = "base") -> list[dict]:  # noqa: ARG002 - 桩
            return [
                {
                    "type_id": 1,
                    "name": "Heavy Beam Laser II",
                    "group_name": "Energy Weapon",
                    "slot_type": "high",
                    "attributes": {},
                }
            ]

    rule = D.Rule(id="empty_hardpoint", view="resources", enabled=True, message="{free_kinds}")
    both = {
        A.ATTR_TURRET_SLOTS_LEFT: 2,
        A.ATTR_LAUNCHER_SLOTS_LEFT: 0,
        A.ATTR_HI_SLOTS: 4,
    }
    fired = D.evaluate(_Ctx(both), rules=[rule])
    assert fired and fired[0]["evidence"]["free_kinds"] == "炮位1", "两个 total 都有值时该报"

    only_turret = {A.ATTR_TURRET_SLOTS_LEFT: 2, A.ATTR_HI_SLOTS: 4}
    assert D.evaluate(_Ctx(only_turret), rules=[rule]) == [], "总量未知 → 不出结论（不补 0）"


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
