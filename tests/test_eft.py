"""T-54 测试：炮位口径（高槽 ≠ 炮位）与 EFT 渲染（`project/eft.py`）。

两个真实缺陷的回归钉子（都来自战报 139274776，受害者 **Osprey Navy Issue**，type 29340）：

1. **高槽被当成发射器挂点**：旧 `hardpoints_free` 直接把 102 / 101（船体**基准炮位数**）
   当「剩余」，装 0 / 3 / 5 门 `Rapid Light Missile Launcher II` 都输出同一个数，
   于是 agent 得出「5 个发射器挂点只装 3 门，白扔两个」。实测该船 **5 高槽 /
   2 turret / 3 launcher**。
2. **没有 EFT 输出**：agent 只能自己拼，结果拼出「5 门 RLML + 50MN Micromassdrive」
   这种**根本不存在的配装**（真实是 3 门 RLML + 1 件隐身）。

本文件**无网络**（全部用内联 EFT 文本 + 本地 SDE 索引），且不钉任何数值阈值：
断言的只有「三段口径的代数关系」「件数」「往返一致」。
"""

from __future__ import annotations

import pytest

from noobbaozi import api
from noobbaozi.config import attributes as A
from noobbaozi.project import eft
from noobbaozi.project.views import resources as R

LAUNCHER = "Rapid Light Missile Launcher II"
CLOAK = "Improved Cloaking Device II"

# 真实装配（战报 139274776 的 SDE 名字，顺序即战报低 → 中 → 高 → 改装）
OSPREY_REAL_LOW = ("Damage Control II", "Nanofiber Internal Structure II",
                   "Ballistic Control System II", "Ballistic Control System II")
OSPREY_REAL_MED = ("Multispectrum Shield Hardener II", "Multispectrum Shield Hardener II",
                   "Warp Disruptor II", "100MN Y-S8 Compact Afterburner",
                   "Large F-S9 Regolith Compact Shield Extender",
                   "Large F-S9 Regolith Compact Shield Extender")
OSPREY_REAL_HIGH = (LAUNCHER, LAUNCHER, LAUNCHER, CLOAK)
OSPREY_REAL_RIG = ("Medium Ancillary Current Router I",
                   "Medium Polycarbon Engine Housing II",
                   "Medium Auxiliary Thrusters II")
OSPREY_REAL_CARGO = (
    "100MN Y-S8 Compact Afterburner x1",
    "Caldari Navy Scourge Light Missile x1232",
    "Scourge Fury Light Missile x379",
    "Navy Cap Booster 3200 x3",
)


def _eft(modules: tuple[str, ...] | list[str], *, name: str = "Test",
         cargo: tuple[str, ...] = ()) -> str:
    """把一串模块名拼成 EFT 文本（模块之间留空行也无所谓，解析器按槽位归类）。"""
    lines = [f"[Osprey Navy Issue, {name}]", ""]
    lines.extend(modules)
    if cargo:
        lines.append("")
        lines.extend(cargo)
    return "\n".join(lines) + "\n"


OSPREY_REAL_TEXT = _eft(
    (*OSPREY_REAL_LOW, *OSPREY_REAL_MED, *OSPREY_REAL_HIGH, *OSPREY_REAL_RIG),
    name="Real",
    cargo=OSPREY_REAL_CARGO,
)
"""真实装配的 EFT 文本（低/中/高/改装混在一起无所谓：解析器按模块自己的槽位归类）。"""


def _launchers(count: int) -> str:
    """Osprey NI 装 `count` 门 RLML（0 / 3 / 5 三档是本轮的核心用例）。"""
    return _eft([*([LAUNCHER] * count), "Damage Control II"], name=f"RLML{count}")


@pytest.fixture(scope="module", autouse=True)
def _clean_api_cache() -> None:
    """每个模块开始前清缓存（api 是 LRU + 进程级单例，缓存串味会让「重复计算结果不同」失真）。"""
    api.clear_caches()


def _resources(text: str) -> dict:
    """`fit_view(..., "resources")` 的成功信封（失败直接炸，别把失败当数据）。"""
    envelope = api.fit_view(text, "resources")
    assert envelope["ok"] is True, envelope.get("error")
    return envelope["result"]


# =============================================================================
# 一、炮位口径：`hardpoints` 三段 + `unattributed`
# （该键 2026-10-19 由 `unclassified` 改名：旧名与 `reasons.UNKNOWN_REASONS` 的兜底值撞名）
# =============================================================================


@pytest.mark.parametrize(
    ("count", "used", "free"),
    [(0, 0, 3), (3, 3, 0), (5, 5, -2)],
)
def test_osprey_launcher_used_follows_the_fit(count: int, used: int, free: int) -> None:
    """**核心回归**：装 0 / 3 / 5 门 → `used` 0 / 3 / 5、`free` 3 / 0 / **−2**。

    旧口径在这三种装配下都输出 `{"turret": 2, "launcher": 3}`（常数），
    所以这条断言在 T-54 之前必红 —— 它验的正是「`free` 随装配变化」这一件事。
    """
    hardpoints = _resources(_launchers(count))["overview"]["hardpoints"]

    assert hardpoints["launcher"] == {"total": 3, "used": used, "free": free}
    # 炮位是**另一种**硬点：这一套里没装炮，所以 turret 的 used 恒为 0
    assert hardpoints["turret"]["used"] == 0
    assert hardpoints["turret"]["free"] == hardpoints["turret"]["total"]


def test_high_slots_are_not_hardpoints() -> None:
    """**高槽 5 个 ≠ 5 个发射器挂点**：`slots.high` 与 `hardpoints` 是两件事。"""
    overview = _resources(OSPREY_REAL_TEXT)["overview"]

    assert overview["slots"]["high"]["total"] == 5, "Osprey NI 有 5 个高槽"
    assert overview["hardpoints"]["launcher"]["total"] == 3, "但发射器挂点只有 3 个"
    assert overview["hardpoints"]["turret"]["total"] == 2
    # 真实装配：3 门 RLML（+1 件隐身）。隐身是**确定不占硬点**，进 `non_hardpoint`
    # （T-54 审查①把它从「分不清」那个计数里拆出来了，见 views/resources.py 的硬点文档）
    assert overview["hardpoints"]["launcher"] == {"total": 3, "used": 3, "free": 0}
    assert overview["hardpoints"]["non_hardpoint"] == 1, "隐身：确定不占硬点"
    assert overview["hardpoints"]["unattributed"] == 0, "没有「认不出」的东西"


def test_over_install_raises_hardpoint_violation() -> None:
    """装 5 门（> 3 个挂点）必须出 `hardpoint_violation`，evidence 形状按任务书。"""
    result = _resources(_launchers(5))
    flags = {flag["id"]: flag for flag in result["flags"]}

    assert "hardpoint_violation" in flags, "free 为负不能静默通过"
    violation = flags["hardpoint_violation"]
    assert violation["view"] == "resources"
    assert violation["evidence"] == {
        "kind": "launcher",
        "total": 3,
        "used": 5,
        "over": 2,
        # T-54 审查补：预渲染的「哪几类超了几个」——单类时就是这一类，
        # 两类同时超时是 "炮位超1+发射器超1"（见 tests/test_t54_audit.py）
        "over_kinds": "发射器超2",
    }
    # 合法装配（0 / 3 门）不许出这条 flag
    for count in (0, 3):
        assert "hardpoint_violation" not in {
            flag["id"] for flag in _resources(_launchers(count))["flags"]
        }


def test_hardpoint_violation_hint_is_generated_from_evidence() -> None:
    """L0 的 `hint` 跟着当次 evidence 走（`发射器超2，硬点超装`），不是一张写死的文案表。"""
    envelope = api.fit_index(_launchers(5))
    assert envelope["ok"] is True, envelope.get("error")
    hints = {flag["id"]: flag["hint"] for flag in envelope["result"]["flags"]}
    assert hints["hardpoint_violation"] == "发射器超2，硬点超装"


def test_hardpoints_is_visible_in_l0_next_to_slots() -> None:
    """**L0 必须能看到炮位**（和 `slots` 并列）：只读 L0 也不能把高槽当挂点数。"""
    envelope = api.fit_index(OSPREY_REAL_TEXT)
    assert envelope["ok"] is True, envelope.get("error")
    block = envelope["result"]["resources"]

    assert set(block) == {"cpu", "powergrid", "feasible", "slots", "hardpoints"}
    assert block["slots"]["high"] == "4/5"
    assert block["hardpoints"]["turret"] == {"total": 2, "used": 0, "free": 2}
    assert block["hardpoints"]["launcher"] == {"total": 3, "used": 3, "free": 0}
    assert block["hardpoints"]["non_hardpoint"] == 1
    assert block["hardpoints"]["unattributed"] == 0
    # L0 的值必须能原路取回（文档 §1 的关键约束）
    answer = api.fit_get(OSPREY_REAL_TEXT, "resources.hardpoints.launcher.free")
    assert answer["ok"] is True, answer.get("error")
    assert answer["result"]["value"] == 0


def test_hardpoints_provenance_three_states() -> None:
    """`total` 是船体属性（`measured`），`used` / `free` 是我们数出来的（`assumed`）。"""
    result = _resources(OSPREY_REAL_TEXT)
    provenance = result["provenance"]

    assert provenance["hardpoints.turret.total"] == "measured"
    assert provenance["hardpoints.turret.used"] == "assumed"
    assert provenance["hardpoints.turret.free"] == "assumed"
    assert provenance["hardpoints.launcher.used"] == "assumed"
    assert provenance["hardpoints.unattributed"] == "assumed"


def test_empty_hardpoint_uses_the_same_free() -> None:
    """`empty_hardpoint` 与 `hardpoints` 的口径**必须同一份**（不许各算一套）。

    **T-54 审查改判（原断言已过时）**：这条规则的门槛被收窄为「**已经在用**（`used > 0`）
    且没装满（`free > 0`）」的硬点类。本样本是纯导弹船：3 门 RLML 把发射器用满
    （`launcher free = 0`），炮位一件没装（`turret used = 0`）。
    按旧口径它每次都出 flag，而 L0 的 hint 只有 `"高槽空1/5"`——「哪一类硬点有余」一个字都没说，
    那是噪音；新判据下**不该出**。正反样本在 `tests/test_t54_audit.py` 里逐条钉着。
    """
    result = _resources(OSPREY_REAL_TEXT)
    overview = result["overview"]
    flag_ids = {flag["id"] for flag in result["flags"]}

    assert overview["hardpoints"]["turret"] == {"total": 2, "used": 0, "free": 2}
    assert overview["hardpoints"]["launcher"] == {"total": 3, "used": 3, "free": 0}
    assert "empty_hardpoint" not in flag_ids, (
        "纯导弹船（炮位 used=0、发射器用满）不报「有空炮位」："
        f"hardpoints={overview['hardpoints']}"
    )


# =============================================================================
# 二、分类规则（`HARDPOINT_*` 常量表）：命中 / 排除 / 分不清
# =============================================================================


@pytest.mark.parametrize(
    ("name", "group", "expected"),
    [
        # launcher：名字带 Launcher 的全族
        ("Rapid Light Missile Launcher II", "Missile Launcher Rapid Light", "launcher"),
        ("Heavy Missile Launcher II", "Missile Launcher Heavy", "launcher"),
        ("Torpedo Launcher II", "Missile Launcher Torpedo", "launcher"),
        ("Rocket Launcher II", "Missile Launcher Rocket", "launcher"),
        ("Cruise Missile Launcher II", "Missile Launcher Cruise", "launcher"),
        ("Bomb Launcher I", "Missile Launcher Bomb", "launcher"),
        # turret：三族炮 + 采掘 + 粒子炮
        ("200mm AutoCannon II", "Projectile Weapon", "turret"),
        ("1400mm Howitzer Artillery II", "Projectile Weapon", "turret"),
        ("250mm Railgun II", "Hybrid Weapon", "turret"),
        ("Light Neutron Blaster II", "Hybrid Weapon", "turret"),
        ("Heavy Ion Blaster II", "Hybrid Weapon", "turret"),
        ("Electron Blaster Cannon II", "Hybrid Weapon", "turret"),
        ("Heavy Beam Laser II", "Energy Weapon", "turret"),
        ("Mega Pulse Laser II", "Energy Weapon", "turret"),
        ("Light Entropic Disintegrator II", "Precursor Weapon", "turret"),
        ("Strip Miner I", "Strip Miner", "turret"),
        ("Miner II", "Mining Laser", "turret"),
        # 确定不占硬点（T-54 审查①：它们**不再**混进「分不清」计数）→ 独立桶 non_hardpoint
        ("Improved Cloaking Device II", "Cloaking Device", "non_hardpoint"),
        ("Scan Probe Launcher II", "Scan Probe Launcher", "non_hardpoint"),
        ("Expanded Probe Launcher I", "Scan Probe Launcher", "non_hardpoint"),
        ("Survey Probe Launcher I", "Survey Probe Launcher", "non_hardpoint"),
        ("Tractor Beam II", "Tractor Beam", "non_hardpoint"),
        # 真分不清 → unattributed（只计数，不许猜）
        ("Festival Launcher I", "Festival Launcher", "unattributed"),
        ("Interdiction Sphere Launcher I", "Interdiction Sphere Launcher", "unattributed"),
        # 反例：`ion` 必须**整词**匹配，否则 447 个「-tion」结尾的普通模块全会中招
        ("Reaction Control Unit", "Miscellaneous", "unattributed"),
        ("Compression Module", "Miscellaneous", "unattributed"),
        (None, None, "unattributed"),
    ],
)
def test_hardpoint_kind_classification(name: object, group: object, expected: str) -> None:
    """分类规则逐条钉住（表在 `views/resources.py` 的 `HARDPOINT_*`）。"""
    assert R._hardpoint_kind(name, group) == expected


def test_hardpoints_counts_only_high_slots() -> None:
    """**只看高槽**：低槽的 `Mining Laser Upgrade II`（名字含 Laser）不是炮位。"""
    attrs = {A.ATTR_TURRET_SLOTS_LEFT: 2, A.ATTR_LAUNCHER_SLOTS_LEFT: 3}

    def item(name: str, group: str, slot: str) -> tuple[int, dict]:
        return (0, {"name": name, "group_name": group, "slot_type": slot})

    fitted = [
        item("Heavy Beam Laser II", "Energy Weapon", "high"),  # turret
        item("Mining Laser Upgrade II", "Mining Upgrade", "low"),  # 低槽 → 不计
        item("Torpedo Launcher II", "Missile Launcher Torpedo", "high"),  # launcher
        item("Small Shield Extender II", "Shield Extender", "medium"),  # 中槽 → 不计
        item("Improved Cloaking Device II", "Cloaking Device", "high"),  # non_hardpoint
    ]
    block = R.hardpoints(attrs, fitted)

    assert block["turret"] == {"total": 2, "used": 1, "free": 1}
    assert block["launcher"] == {"total": 3, "used": 1, "free": 2}
    assert block["non_hardpoint"] == 1, "隐身是确定不占硬点（不是「认不出」）"
    assert block["unattributed"] == 0


def test_hardpoints_never_invents_a_total() -> None:
    """船体属性缺失 → `total` / `free` 都是 `None`（缺值不补 0），`used` 仍是数出来的真值。"""
    fitted = [(0, {"name": LAUNCHER, "group_name": "Missile Launcher Rapid Light", "slot_type": "high"})]
    block = R.hardpoints({}, fitted)

    assert block["launcher"] == {"total": None, "used": 1, "free": None}
    assert block["turret"] == {"total": None, "used": 0, "free": None}


# =============================================================================
# 三、EFT 渲染：格式、往返一致、失败要诚实
# =============================================================================


def test_eft_format_matches_eve_convention() -> None:
    """逐条对齐模块 docstring 的六条格式（首行 / 分组空行 / 一件一行 / 两段 / 无尾空行）。"""
    text = api.parse_fit(OSPREY_REAL_TEXT)["result"]["eft"]
    lines = text.split("\n")

    assert lines[0] == "[Osprey Navy Issue, Real]"
    assert lines[1] == "", "首行与第一组之间空一行"
    assert not text.endswith("\n"), "末尾不许有多余空行"
    assert "\r" not in text, "换行一律 \\n"

    # 分组：组间恰好一个空行，组内没有空行；顺序 低 → 中 → 高 → 改装
    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines[1:]:
        if line == "":
            if current:
                groups.append(current)
                current = []
            continue
        current.append(line)
    if current:
        groups.append(current)

    assert groups[0] == list(OSPREY_REAL_LOW)
    assert groups[1] == list(OSPREY_REAL_MED)
    assert groups[2] == list(OSPREY_REAL_HIGH)
    assert groups[3] == list(OSPREY_REAL_RIG)
    assert groups[4] == list(OSPREY_REAL_CARGO)
    # 同型模块**每件单独一行**：不写 ×N / xN（件数由行数表达）
    assert groups[2].count(LAUNCHER) == 3
    assert not any("×" in line for group in groups for line in group)
    assert not any(line.startswith(LAUNCHER + " x") for group in groups for line in group)


def test_eft_keeps_loaded_charge_on_the_module_line() -> None:
    """带弹药的发射器：模块行写 `武器名, 弹药名`，货舱段只放**真的在货舱里**的弹药。

    **T-54 追加缺陷的回归钉子**（独立验收实测）：旧实现把 `charge` 整块丢掉，`emit` 出来
    的文本是空炮 —— 复制回游戏装不上弹、复制回 `parse_fit` 五门炮 `charge` 全变 `None`，
    而渲染还「成功」。EFT 惯例就是 `Weapon, Ammo Name`，本模块必须照写。
    """
    text = api.parse_fit(
        _eft([f"{LAUNCHER}, Scourge Fury Light Missile"], name="Loaded",
             cargo=("Scourge Fury Light Missile x379",))
    )["result"]["eft"]
    lines = text.split("\n")

    assert lines[2] == f"{LAUNCHER}, Scourge Fury Light Missile", "模块行必须带回装填弹药"
    # 弹药数量**不写在模块行上**（引擎解析器拒绝 `, 弹药名 x100`，EVE 剪贴板也不带）
    assert " x" not in lines[2], "模块行不带数量后缀"
    # 货舱段只放货舱里的那堆（379 发），不与装填重复
    assert lines[-1] == "Scourge Fury Light Missile x379"
    assert sum(1 for line in lines[1:] if line.startswith(LAUNCHER)) == 1

    # 往返：charge 的 type_id 必须回来
    from noobbaozi.engine import normalize

    first = normalize.normalize_fit(
        _eft([f"{LAUNCHER}, Scourge Fury Light Missile"], name="Loaded",
             cargo=("Scourge Fury Light Missile x379",))
    )["fit"]
    second = normalize.normalize_fit(text)["fit"]
    assert first is not None and second is not None

    def charges(fit: dict) -> list:
        return [
            (item["type_id"], (item.get("charge") or {}).get("type_id"))
            for item in fit["items"]
            if (item.get("slot") or {}).get("type") == "high"
        ]

    assert charges(second) == charges(first), "装填弹药必须在往返后保持一致"
    assert [charge for _module, charge in charges(second) if charge], "至少有一门炮带着弹药"


ROUNDTRIP_FITS: tuple[str, ...] = (
    OSPREY_REAL_TEXT,
    # 无人机 + 弹药（数量后缀必须往返）
    _eft(["10MN Afterburner II", "Drone Damage Amplifier II", "Medium Armor Repairer II"],
         name="Droney", cargo=("'Integrated' Warrior x5", "Warrior II x3",
                               "Nanite Repair Paste x50", "Fusion S x1000")),
    # 纯被动小船（无弹药、无无人机）
    _eft(["Damage Control II"], name="Bare"),
)


@pytest.mark.parametrize("text", ROUNDTRIP_FITS)
def test_eft_roundtrip_keeps_fitted_modules(text: str) -> None:
    """**必须可往返**：`parse(emit(fit))` 的已装模块集合（type_id + 数量）与原始一致。"""
    from noobbaozi.engine import normalize

    first = normalize.normalize_fit(text)
    assert first["fit"] is not None, first.get("errors")
    index = api.get_index()

    emitted = eft.emit(first["fit"], index)
    second = normalize.normalize_fit(emitted)
    assert second["fit"] is not None, (second.get("errors"), emitted)

    def fitted(fit: dict) -> list[tuple[int, int]]:
        return sorted(
            (int(item["type_id"]), int(item.get("quantity") or 1))
            for item in fit["items"]
            if item.get("slot") and item["slot"].get("type") != "cargo"
        )

    assert fitted(second["fit"]) == fitted(first["fit"])
    # 渲染是**幂等**的：再渲染一次得到逐字节相同的文本（没有随机的名字/顺序）
    assert eft.emit(second["fit"], index) == emitted


def test_eft_roundtrip_through_the_api() -> None:
    """走接口层：`parse_fit` 的 `eft` 能被自己再解析回来（agent 复制的是可读回的真文本）。"""
    first = api.parse_fit(OSPREY_REAL_TEXT)
    assert first["ok"] is True, first.get("error")
    emitted = first["result"]["eft"]
    assert isinstance(emitted, str) and emitted.startswith("[Osprey Navy Issue")

    second = api.parse_fit(emitted)
    assert second["ok"] is True, second.get("error")
    assert second["result"]["modules"]["fitted"] == first["result"]["modules"]["fitted"]
    assert second["result"]["modules"]["by_slot"] == first["result"]["modules"]["by_slot"]


def test_eft_header_without_fit_name() -> None:
    """配装名为空 → 首行只写 `[船名]`（任务书 §格式 1），**且这条形态能往返**。

    `[船名]`（无逗号）是任务书要求的写法；解析侧对它的接受由
    `engine/normalize._match_eft_header()` 保证（`[Ship]` 与 `[Ship, name]` 都认，
    但 `[Empty High slot]` 这类占位行不会被当成船名行）。没有这一步，
    `parse_fit()["eft"]` 对空名配装会输出一份**自己读不回来**的文本。
    """
    from noobbaozi.engine import normalize

    fit = normalize.normalize_fit("[Osprey Navy Issue, ]\nDamage Control II\n")["fit"]
    index = api.get_index()
    emitted = eft.emit(fit, index)
    assert emitted.split("\n")[0] == "[Osprey Navy Issue]"

    back = normalize.normalize_fit(emitted)
    assert back["fit"] is not None, back.get("errors")
    assert eft.emit(back["fit"], index) == emitted, "空名配装也必须往返稳定"


def test_eft_refuses_to_invent_names() -> None:
    """没有索引 / 名字查不到 / 槽位类型没有 EFT 表示 → `EftError`（**绝不出半成品**）。"""
    from noobbaozi.engine import normalize

    fit = normalize.normalize_fit(OSPREY_REAL_TEXT)["fit"]
    with pytest.raises(eft.EftError):
        eft.emit(fit, None)

    with pytest.raises(eft.EftError):
        eft.emit({**fit, "items": [{"type_id": 999999999, "slot": {"type": "high"}}]},
                 api.get_index())

    with pytest.raises(eft.EftError):
        eft.emit({**fit, "items": [{"type_id": 2048, "slot": {"type": "service_slot"}}]},
                 api.get_index())

    with pytest.raises(eft.EftError):
        eft.emit({"items": []}, api.get_index())


def test_parse_fit_degrades_to_null_when_rendering_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """渲染失败 → `eft: null` + `warnings` 一条（**不猜、不编行**），其余字段照常。"""
    api.clear_caches()

    def boom(_fit: object, _index: object) -> str:
        raise eft.EftError("测试注入的失败")

    monkeypatch.setattr(api, "eft_emit", boom)
    envelope = api.parse_fit(OSPREY_REAL_TEXT)
    assert envelope["ok"] is True
    assert envelope["result"]["eft"] is None
    assert any("EFT 渲染失败" in warning for warning in envelope["result"]["warnings"])
    assert envelope["result"]["modules"]["fitted"] > 0, "其余字段不因渲染失败而变空"
    api.clear_caches()


# =============================================================================
# 四、样本自身的真实槽位（硬结论，供报告引用）
# =============================================================================


def test_osprey_ni_real_hull_shape() -> None:
    """Osprey NI（29340）真实形状：**5 高 / 6 中 / 4 低 / 3 改装**，硬点 **2 turret / 3 launcher**。

    这是本轮所有口径的锚点：高槽 5 个与发射器挂点 3 个**不是同一个数**。
    """
    overview = _resources(OSPREY_REAL_TEXT)["overview"]

    assert overview["slots"]["high"] == {"used": 4, "total": 5}
    assert overview["slots"]["medium"] == {"used": 6, "total": 6}
    assert overview["slots"]["low"] == {"used": 4, "total": 4}
    assert overview["slots"]["rig"] == {"used": 3, "total": 3}
    assert overview["hardpoints"]["turret"]["total"] == 2
    assert overview["hardpoints"]["launcher"]["total"] == 3
