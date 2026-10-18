"""视角上下文与视角契约（L3）—— **六个视角抽取器的共同底座**。

本文件定义别人照抄的接口：

```python
def build(ctx: ViewContext) -> dict:
    \"\"\"返回 {"view": "<name>", "overview": {...}, "flags": [...]} \"\"\"
```

三条纪律（每一句都对应一条红线）：

1. **视角只读事实**：`ctx.attrs()` / `ctx.items()` 给的都是**已换算成物理单位**的值
   （秒 / 米 / hp / GJ …）。视角里**不得**再出现毫秒、×1000 原值或自己的缩放表
   （红线 N5；缩放只有 `sde/attribute_dict.py` 一份）。
2. **默认假设只来自 Assumption Engine**：`ctx.assumptions` 是
   `engine.assumptions.default_assumptions()` 的结果，视角**不得**另设默认值
   （红线 N9）。视角需要什么假设，就去读这个 dict，并在输出里回显。
3. **同名变体只算一次**：所有变体结果缓存在本对象内（`base` / `overload` / `offline`
   与 `calculate_variant()` 造出的临时变体同一套缓存）。视角可以放心多地取用，
   不必自己维护 memo。

变体语义由 L2 的 `engine/scenarios.py` 决定，本文件只做「取名 → 缓存」：

- `"base"`：保持原状（`{"all": "keep"}`）；
- `"overload"`：所有已装模块 `overload`；
- `"offline"`：所有已装模块 `offline`。

引擎返回的 `ship.attributes` 是 `{attribute_id(int): {"base": float, "value": float}}`，
`items[i].attributes` 也是 int 键；且 `items` 与输入 `fit["items"]` **按顺序一一对应
但不再带 `type_id`**（实测 31/31）。所以 `attrs()` 取 `value` 并转成 `{int: float}`，
`items()` 把输入侧的 `type_id` / `slot` / `quantity` 与计算侧的 `attributes` / `state`
合并——这两件常被误以为「引擎直接给了」，其实都要投影层自己做。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evefit import contracts
from evefit.engine import scenarios
from evefit.project import units

SLOT_CARGO = "cargo"
"""唯一不算「已装」的槽位类别（软件设计 §7.3 的合法 slot.type 里的一个）。"""

BASE = "base"
OVERLOAD = "overload"
OFFLINE = "offline"

VARIANTS: dict[str, contracts.ScenarioSpec] = {
    BASE: {"name": BASE, "states": {scenarios.ALL_KEY: scenarios.KEEP}, "mode_id": None},
    OVERLOAD: {"name": OVERLOAD, "states": {scenarios.ALL_KEY: "overload"}, "mode_id": None},
    OFFLINE: {"name": OFFLINE, "states": {scenarios.ALL_KEY: "offline"}, "mode_id": None},
}
"""预置变体名 → `ScenarioSpec`。`mode_id=None` 表示**不修改**输入里已有的模式
（模式是独立 type，见 PROGRESS §3 第 3 条；不是我们要替用户改的假设）。"""


def ship_attributes(calculation: dict) -> dict[int, float]:
    """从原始 `calculation` 里取船体属性并换算成物理值 `{attribute_id: float}`。

    - 键统一转成 `int`（引擎实测就是 int；这里再转一次是防御性的：万一上游某天
      改成字符串键，视角层不该因此炸掉）；
    - 取 `value`（叠加完的最终值），不是 `base`（基础值）；
    - **缺值不补零**：`None` 直接跳过，让调用方看到「没有这个键」而不是一个假的 0
      （红线 N4：不用默认值冒充 unknown）。
    """
    raw = (calculation.get("ship") or {}).get("attributes") or {}
    result: dict[int, float] = {}
    for key, entry in raw.items():
        attribute_id = int(key)
        value = entry.get("value") if isinstance(entry, dict) else entry
        if value is None:
            continue
        result[attribute_id] = units.physical(attribute_id, float(value))
    return result


def item_attributes(calculation_item: dict) -> dict[int, float]:
    """单个装备的属性 → 物理值 `{attribute_id: float}`（键同样做 int 归一）。"""
    raw = (calculation_item or {}).get("attributes") or {}
    result: dict[int, float] = {}
    for key, entry in raw.items():
        attribute_id = int(key)
        value = entry.get("value") if isinstance(entry, dict) else entry
        if value is None:
            continue
        result[attribute_id] = units.physical(attribute_id, float(value))
    return result


def _charge_type_id(fit_item: dict) -> int | None:
    """输入侧弹药的 type_id；`None` 表示空弹匣。两种形态都兼容（dict / 裸 int）。"""
    charge = fit_item.get("charge")
    if isinstance(charge, dict):
        type_id = charge.get("type_id")
    else:
        type_id = charge
    return int(type_id) if isinstance(type_id, int) else None


def _merge_items(calculation: dict, fit: dict, index: object | None) -> list[dict]:
    """计算侧 items 与输入侧 items 按序合并（引擎不再回 type_id，实测按序对应）。"""
    calc_items = calculation.get("items") or []
    fit_items = fit.get("items") or []
    merged: list[dict] = []

    for position, calc_item in enumerate(calc_items):
        fit_item = fit_items[position] if position < len(fit_items) else {}
        type_id = fit_item.get("type_id")
        type_row = index.get_type(type_id) if index is not None and isinstance(type_id, int) else None
        slot = fit_item.get("slot")
        merged.append(
            {
                "type_id": int(type_id) if isinstance(type_id, int) else None,
                "name": (type_row or {}).get("name"),
                "group_id": (type_row or {}).get("group_id"),
                "group_name": (type_row or {}).get("group_name"),
                "slot": dict(slot) if isinstance(slot, dict) else None,
                "slot_type": (slot or {}).get("type") if isinstance(slot, dict) else None,
                "quantity": int(fit_item.get("quantity") or 1),
                "charge_type_id": _charge_type_id(fit_item),
                "state": calc_item.get("state"),
                "max_state": calc_item.get("max_state"),
                "attributes": item_attributes(calc_item),
            }
        )
    return merged


@dataclass(slots=True)
class ViewContext:
    """一个配装的完整投影上下文（视角模块的唯一入参）。

    字段与契约逐字对应，`_fits` / `_calcs` 是本对象私有的变体缓存（不参与构造）。
    """

    engine: object
    index: object | None
    fit: dict
    assumptions: dict
    meta: dict
    _fits: dict[str, dict] = field(default_factory=dict, init=False, repr=False)
    _calcs: dict[str, dict] = field(default_factory=dict, init=False, repr=False)

    # ---- 变体（带缓存）----

    def variant(self, name: str = BASE) -> dict:
        """取某个预置变体的**原始** calculation dict（同名只算一次）。"""
        return self._pair(name)[1]

    def variant_fit(self, name: str = BASE) -> dict:
        """取某个预置变体对应的 **fit**（视角需要看「这套变体的输入长什么样」时用）。"""
        return self._pair(name)[0]

    def calculate_variant(self, key: str, fit: dict) -> dict:
        """计算任意临时变体并缓存（同 key 只算一次）。

        预留给投影层内部（例如可疑点规则要「移除某个模块」而 `engine/scenarios.py`
        的 `{"all": state}` 语义表达不了）。视角模块正常只需要 `attrs()` / `items()`。

        Raises:
            ValueError: `key` 与预置变体名冲突（那会让两套语义共用一个缓存槽）。
        """
        if key in VARIANTS:
            raise ValueError(f"临时变体 key 不得与预置变体名冲突：{key!r}")
        cached = self._calcs.get(key)
        if cached is not None:
            return cached
        calculation = self.engine.calculate(fit)
        self._fits[key] = fit
        self._calcs[key] = calculation
        return calculation

    def _pair(self, name: str) -> tuple[dict, dict]:
        cached = self._calcs.get(name)
        if cached is not None:
            return self._fits[name], cached

        spec = VARIANTS.get(name)
        if spec is None:
            raise KeyError(f"未知变体名 {name!r}；预置变体：{sorted(VARIANTS)}")
        variant_fit = scenarios.build_variant(self.fit, spec)
        calculation = scenarios.run_variants(self.fit, [spec], self.engine)[name]
        self._fits[name] = variant_fit
        self._calcs[name] = calculation
        return variant_fit, calculation

    # ---- 投影视图 ----

    def attrs(self, variant: str = BASE) -> dict[int, float]:
        """**已换算成物理单位**的船体属性字典 `{attribute_id: float}`。"""
        return ship_attributes(self.variant(variant))

    def items(self, variant: str = BASE) -> list[dict]:
        """已装 + 货舱物品的合并视图（含 `type_id` / `slot_type` / 物理属性）。

        视角一般先按 `slot_type != "cargo"` 过滤出「已装模块」；货舱物品保留是为了
        弹药与消耗品能查到（例如弹药四系伤害）。
        """
        return _merge_items(self.variant(variant), self.variant_fit(variant), self.index)
