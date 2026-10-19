"""EFT 渲染（`emit`）：把**引擎 fit 结构**还原成可直接复制粘贴的 EFT 文本（T-54）。

为什么需要它（T-54 缺陷 2）
--------------------------
项目此前**只有解析、没有渲染**：agent 拿到六维度数值后，要自己把配装写成 EFT 文本，
于是「模块名 / 件数 / 槽位」全靠它凭记忆拼——实测战报 139274776（Osprey Navy Issue）
被拼成「`Rapid Light Missile Launcher II ×5` + `50MN Micromassdrive II` + 若干根本没装的
低槽件」，而真实装配是 **3 门 RLML II + 1 件隐身 + 4 低槽 / 6 中槽 / 3 改装**。
拼错的方向不是随机的：它总是往「看起来更合理」的方向漂。所以规范文本必须由**我们**给。

格式（EVE 惯例，逐条对齐；本模块的**唯一**职责就是这个形状）
-----------------------------------------------------------
1. 首行 `[船名, 配装名]`；配装名为空时写 `[船名]`；
2. **按槽位分组、组间空一行**：低槽 → 中槽 → 高槽 → 改装槽 →（子系统，若有）；
   首行与第一组之间也空一行；
3. 同型模块**每件单独一行**（**不写 `×N`** —— EFT 的件数由行数表达）；
   所以 `quantity > 1` 的已装模块要写满 `quantity` 行（一行一件），**不许只写一行丢件**；
   **装填了弹药的模块行写成 `武器名, 弹药名`**（EFT 惯例；见下面纪律 2）；
4. 之后空一行 + 无人机 / 舰载机行（`Warrior II x5`）；
5. 再空一行 + 货舱 / 弹药行（`Scourge Fury Light Missile x379`）；
6. 末尾**不带**多余空行，换行一律 `\\n`（不是 `\\r\\n`）。

三条纪律（每条都对应一条红线）
------------------------------
1. **只输出真实存在的模块**：名字一律来自 SDE 索引（`index.get_type`），查不到名字就
   **失败**（`EftError`），绝不用 `type_id N` 或「看着像」的名字顶替（红线 N2 / N4）。
   失败由调用方降级成 `eft: null` + 一条 `warnings`，**不假装渲染成功**。
2. **装填的弹药必须跟着模块行走**（`武器名, 弹药名`）。这是**独立验收实测出来的缺陷**：
   旧实现把 `charge` 整块丢掉（只在货舱段写弹药），于是 `emit` 出来的文本**本身是错的** ——
   复制回游戏是空炮，复制回 `parse_fit` 五门炮的 `charge` 全变 `None`，DPS / 弹药续航按空炮算，
   而且**没有任何 warning**（渲染「成功」）。三条口径：
   - **不带数量**：不写 `, 弹药名 x100`。实测引擎解析器**拒绝**这个形态
     （`Light Neutron Blaster II, Void S x100` → `unknown type …`），EVE 剪贴板也不带；
     引擎解析出的 `charge` 本身只有 `{"type_id": …}`，没有数量可写；
   - 弹药名同样来自 SDE 索引，查不到 → `EftError`（**绝不静默丢**，那正是这个缺陷的形态）；
   - 模块名或弹药名**含逗号** → `EftError`：EFT 用逗号分隔，含逗号的实体在文本里无法无歧义表达
     （SDE 实测 18 个含逗号的已发布类型全是商品/服饰/箱子，不是模块也不是弹药；这是护栏，不是常态）。
3. **往返一致**（`tests/test_t54_audit.py` / `tests/test_eft.py` 钉住）：`parse(emit(fit))` 的
   **已装模块集合**（`type_id` + `数量`）**与每个模块的装填弹药 `type_id`** 都必须与原始 fit 一致。
   所以要按 `slot.index` 排序、一个槽位一行、无人机 / 货舱带 `x数量` 后缀（解析器认这个后缀）。
   `charge` 的**数量**不在 EFT 里表达（引擎解析出来也没有），往返比较只比 `type_id`。

边界
----
- **不负责**「这套配装合不合理」：本模块只把结构写回文本，一个判词都不给；
- 槽位类型不在 `SLOT_ORDER` / `DRONE_SLOT_TYPES` / `CARGO_SLOT_TYPE` 里 → `EftError`
  （「没有 EFT 表示的槽位」不许偷偷塞进别的段，那会让往返不一致）；
- **无人机 / 货舱条目带上 `charge` → `EftError`**：EFT 没有「货舱里的装填关系」这种表示，
  塞进 `Name, Ammo xN` 会被解析成一团乱；宁可失败，也不静默丢；
- 不读引擎、不算任何数值：入参就是 `engine.normalize` 给的 `fit` 结构 + 一个名字索引。
"""

from __future__ import annotations

SLOT_ORDER: tuple[str, ...] = ("low", "medium", "high", "rig", "subsystem")
"""模块行的分组顺序（EVE 惯例：低 → 中 → 高 → 改装 → 子系统）。"""

DRONE_SLOT_TYPES: tuple[str, ...] = ("drone_bay", "fighter_tube", "fighter_bay")
"""无人机 / 舰载机段的槽位类别。

`fighter_tube`（已发射）与 `fighter_bay`（机库）在 EFT 里都写成 `Name xN` 的同一段
（pyfa / EVE 剪贴板都是这个形状）；两者的**语义差别**由六视角表达，不需要污染文本。
"""

CARGO_SLOT_TYPE: str = "cargo"
"""货舱 / 弹药段的槽位类别（`views.base.SLOT_CARGO` 的同一个值，这里不 import 以免循环）。"""

MODULE_SLOT_TYPES: frozenset[str] = frozenset(SLOT_ORDER)
"""模块行允许出现的槽位类别（不在其中的「已装」物 → `EftError`，不猜）。"""

NEWLINE: str = "\n"
"""换行符：**只用 `\\n`**（`\\r\\n` 会让「逐字节比对」在不同平台上分叉）。"""

QUANTITY_MARK: str = "x"
"""数量后缀前缀（解析器认 `x5` 这种写法，`_QUANTITY_RE`）。"""


class EftError(Exception):
    """渲染失败（名字查不到 / 槽位类型没有 EFT 表示 / fit 结构不合法）。

    调用方（`api.parse_fit`）把它降级成 `eft: null` + 一条 `warnings`：**不猜、不半成品**。
    """


def _type_name(index: object, type_id: object) -> str:
    """`type_id` → SDE 名字；索引缺失 / 查不到 → `EftError`（**不编名字**）。"""
    if not isinstance(type_id, int):
        raise EftError(f"type_id 不是整数：{type_id!r}")
    getter = getattr(index, "get_type", None) if index is not None else None
    if not callable(getter):
        raise EftError("没有 SDE 索引，渲染不出模块名")
    row = getter(type_id)
    name = row.get("name") if isinstance(row, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise EftError(f"索引里查不到 type_id {type_id} 的名字")
    return name.strip()


def _header(fit: dict, index: object) -> str:
    """首行 `[船名, 配装名]`（配装名为空 → `[船名]`）。"""
    ship = fit.get("ship")
    if not isinstance(ship, dict):
        raise EftError("fit 里没有 ship 块")
    ship_name = _type_name(index, ship.get("type_id"))
    raw_name = fit.get("name")
    fit_name = raw_name.strip() if isinstance(raw_name, str) else ""
    return f"[{ship_name}, {fit_name}]" if fit_name else f"[{ship_name}]"


def _slot_of(item: dict) -> tuple[str | None, int | None]:
    """`item["slot"]` → `(槽位类别, 槽位下标)`；结构不对给 `(None, None)`。"""
    slot = item.get("slot") if isinstance(item.get("slot"), dict) else {}
    slot_type = slot.get("type")
    index = slot.get("index")
    return (
        slot_type if isinstance(slot_type, str) else None,
        index if isinstance(index, int) and not isinstance(index, bool) else None,
    )


def _quantity(item: dict) -> int:
    """条目数量（缺省 1，`< 1` 也按 1 —— 0 件的模块不存在）。"""
    value = item.get("quantity")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 1


def _with_quantity(name: str, item: dict) -> str:
    """`Name xN`（无人机 / 货舱行用；N=1 也写，保证往返时数量逐位相同）。"""
    return f"{name} {QUANTITY_MARK}{_quantity(item)}"


COMMA: str = ","
"""模块行里「武器 / 弹药」的分隔符（EFT 惯例，也是我们解析器 `split(",", 1)` 认的那个）。"""


def _reject_comma(name: str, where: str) -> str:
    """名字里含逗号 → `EftError`（EFT 用逗号分隔，含逗号的实体在文本里无法无歧义表达）。"""
    if COMMA in name:
        raise EftError(f"{where} {name!r} 含逗号，EFT 文本里无法无歧义表达")
    return name


def _module_line(index: object, item: dict) -> str:
    """一个已装模块 → `武器名` 或 `武器名, 弹药名`（装填了弹药时才写后半截）。

    弹药同样走 `_type_name()`：查不到名字就 `EftError` —— **静默丢掉 charge 正是这个缺陷的形态**
    （渲染「成功」、文本却是空炮）。**不写数量**：引擎解析器拒绝 `, 弹药名 x100`
    （实测 `unknown type …`），EVE 剪贴板也不带，且引擎解析出的 `charge` 本身没有数量。
    """
    name = _reject_comma(_type_name(index, item.get("type_id")), "模块名")
    charge = _charge_of(item, name)
    if charge is None:
        return name
    try:
        charge_name = _type_name(index, charge.get("type_id"))
    except EftError as exc:
        # 带上模块名：否则「查不到 type_id N 的名字」在几十行里没法定位是哪门炮的弹药
        raise EftError(f"模块 {name!r} 的装填弹药渲染失败：{exc}") from exc
    return f"{name}{COMMA} {_reject_comma(charge_name, '弹药名')}"


def _charge_of(item: dict, module_name: str) -> dict | None:
    """已装模块的 `charge` 块；没有装填 → `None`；形状不对 → `EftError`（不猜）。"""
    charge = item.get("charge")
    if charge is None:
        return None
    if not isinstance(charge, dict) or not charge:
        # 有 charge 字段但不是一个对象：形状不明，不猜它是不是弹药
        raise EftError(f"模块 {module_name!r} 的 charge 不是对象：{charge!r}")
    if "type_id" not in charge:
        raise EftError(f"模块 {module_name!r} 的 charge 缺 type_id：{charge!r}")
    return charge


def _reject_charge_on_non_module(item: dict, where: str) -> None:
    """无人机 / 货舱条目**不许**带 `charge`：EFT 没有这种表示（宁可失败，不静默丢）。"""
    if item.get("charge"):
        type_id = item.get("type_id")
        raise EftError(
            f"{where}条目（type_id {type_id}）带了 charge：EFT 没有「货舱/机库里的装填关系」"
            "这种表示，不能静默丢掉它"
        )


def _lines(value: object, where: str) -> list:
    """取一个列表字段；缺失按空列表，类型不对 → `EftError`（不猜形状）。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise EftError(f"fit[{where!r}] 必须是列表，实际是 {type(value).__name__}")
    return value


def emit(fit: dict, index: object) -> str:
    """引擎 fit 结构 → EFT 文本（见模块 docstring 的六条格式与三条纪律）。

    Args:
        fit: `engine.normalize.normalize_fit()` 给的 `fit` 结构（**原样**，不加工）。
        index: SDE 索引（鸭子类型：有 `get_type(type_id) -> {name, ...}` 即可）。

    Returns:
        以 `\\n` 连接的 EFT 文本，末尾**没有**空行。

    Raises:
        EftError: 名字查不到 / 槽位类型没有 EFT 表示 / 结构不合法（**绝不出半成品文本**）。
    """
    if not isinstance(fit, dict):
        raise EftError(f"fit 必须是对象，实际是 {type(fit).__name__}")
    items = _lines(fit.get("items"), "items")
    # **不许静默丢件**：非对象的条目不跳过，直接失败（跳过等于悄悄少一件，
    # 而调用方只会看到「渲染成功」——那正是本模块要消灭的那类半成品）。
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            raise EftError(
                f"fit['items'][{position}] 不是对象（{type(item).__name__}），"
                "拒绝静默丢件"
            )

    modules: dict[str, list[tuple[int, int, dict]]] = {name: [] for name in SLOT_ORDER}
    drones: list[dict] = []
    cargo: list[dict] = []
    unknown: set[str] = set()

    for position, item in enumerate(items):
        slot_type, slot_index = _slot_of(item)
        if slot_type in MODULE_SLOT_TYPES:
            modules[slot_type].append(
                (-1 if slot_index is None else slot_index, position, item)
            )
        elif slot_type in DRONE_SLOT_TYPES:
            drones.append(item)
        elif slot_type == CARGO_SLOT_TYPE:
            cargo.append(item)
        else:
            unknown.add(str(slot_type))
    if unknown:
        raise EftError(f"这些槽位类型没有 EFT 表示：{sorted(unknown)}")

    lines: list[str] = [_header(fit, index)]

    for slot_type in SLOT_ORDER:
        rows = sorted(modules[slot_type], key=lambda row: (row[0], row[1]))
        if not rows:
            continue
        lines.append("")
        # 同型模块**每件单独一行**：一行一件，件数由行数表达（不写 ×N）。
        # `quantity > 1` 的已装模块必须写满行数 —— 只写一行等于**悄悄丢件**。
        # 装填了弹药的写 `武器名, 弹药名`（`_module_line()` 负责，名字查不到就失败）。
        for _index, _position, item in rows:
            lines.extend([_module_line(index, item)] * _quantity(item))

    if drones:
        lines.append("")
        for item in drones:
            _reject_charge_on_non_module(item, "无人机")
            lines.append(_with_quantity(_type_name(index, item.get("type_id")), item))

    if cargo:
        lines.append("")
        for item in cargo:
            _reject_charge_on_non_module(item, "货舱")
            lines.append(_with_quantity(_type_name(index, item.get("type_id")), item))

    return NEWLINE.join(lines)
