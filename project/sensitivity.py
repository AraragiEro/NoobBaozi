"""灵敏度引擎（L3 / P3 `sensitivity`）：**改一个输入，指标怎么变**。

设计依据：`docs/配装理解-软件设计-v1.md` §3.3
（`fit_sensitivity(fit, ...) → 逐模块关闭 / state 切换 / mode 切换 → 指标 delta`）。
P3 三件里的第三件（`detail` / `explain` 已完成，本模块补 `sensitivity`）。

## 三种 op

| `op.kind` | 语义 | 实现 | 说明 |
|---|---|---|---|
| `disable_item` | 关掉第 N 件已装模块 | `items[N].state = "offline"` | 与「拆掉」数值等价（实测 f1/f12 上 `stable_pct` / `cap_load` 逐位相同），但**不删件**：模块仍在 fit 里，只是不生效 |
| `set_state` | 把第 N 件设为 `offline` / `online` / `active` / `overload` | 同上（`off` 接受为 `offline` 的别名） | 状态语义与 `views/base.VARIANTS["overload"]` / `engine/scenarios.py` 同一套 |
| `set_mode` | T3C/T3D 模式切换 | `ship["mode"] = mode_id`（走 `engine/scenarios.build_variant` 或直接写 ship） | 模式清单来自 `Index.modes_for_ship()`（与 `render._modes_block` 同源） |

三种 op 都**只改输入、不改引擎**：每个变体都交给 `engine.calculate()` 独立重算一次，
所以 `changes[i].value` 是「真的到引擎里跑出来的值」，不是算术推的。

## 与三个既有碎片的关系（复用，不重写）

| 既有碎片 | 它做的事 | 本模块怎么对上 |
|---|---|---|
| `stability.cap_sensitivity[]` | **摘掉**耗电最高的几件，看 `stable_pct` | `disable_item` 就是单件版；实测同一模块 `remove` 与 `offline` 的 `stable_pct` / `cap_load` **逐位相同** → 交叉验证可过 |
| `mobility.propulsion_states.single` | 只留一个推进启用（**联合操作**：其余全 offline） | 本模块是**逐件** op，不合成联合操作；单件结果是它的分量（`propulsion_states.single` 的语义「同时只开一个」无法用逐件 op 表达，如实说明、不统一） |
| `firepower.overload.off/on` | **全部模块**同时过载 | 同上：`set_state(scope=全件)` 才能对齐；本模块 `set_state` 是逐件的。一次 `set_state` 单件过载的结果是它的分量 |

`set_mode` 的**可应用性**由本模块先判（不靠引擎兜底）：模式必须在该船 `_modes()` 的可枚举清单里。
不在清单里（含「该船在本 SDE 索引里根本枚举不到模式」，如 T3C 未收录模式）→ **跳过**并记
`warnings[].kind = "mode_not_available" / "modes_unknown"`，**不**产出 delta 0 的假变化行。
没有 SDE 索引（`ctx.index is None`）时无从判断 → 照原样交给引擎（不知道的事不替它决定）。

## 计数口径（三个数必须自洽）

`ops_planned == ops_executed + skipped` **恒成立**：

- `ops_planned` = 本次**考虑过**的 op 总数（计划执行数 + 空操作数）；
- `ops_executed` = 真的产出一条 `changes[]` 的 op 数；
- `skipped` = 没产出的 op 数 = **空操作**（模块已 offline / 状态或模式已是目标值 / 重复 op）
  + **被 `max_ops` 截断** + **变体算不出来**（`variant_error` / `metric_missing_after_change`）。

为什么空操作也进 `ops_planned`：否则「计划 40 / 执行 32 / skipped 0」三个数互相打架，调用方
无法回答「少掉的 8 条去哪了」。每一类跳过原因都在 `warnings[].kind`（`noop` / `truncated` /
`variant_error` / `metric_missing_after_change` / `mode_not_available` / `modes_unknown` /
`duplicate_op`），`skipped` 只是总数。

## 硬纪律

1. **每个 delta 可自证**：`value` 来自对**变体 fit** 的独立 `engine.calculate()`；测试再用同一步骤
   在测试侧重算一遍对比（不是拿本模块的中间量自证）。
2. **不编**：指标不在支持表 / 基线取不到 → `None`（接线层报 `invalid_path`）；
   变体算不出来 → 写进 `warnings` 并跳过该 op（不猜一个 0）。
3. **性能护栏**：每个 op 一次重算（实测 `calculate` 约 0.1–0.33 ms/次），`max_ops` 默认 32，
   超了截断 + `truncated: true` + warning。
4. **不判好坏**：只给 `delta` / `delta_pct`，不给「该不该拆」的判词（红线：多视角、无判据）。
"""

from __future__ import annotations

from typing import Mapping, Sequence

from noobbaozi.config import attributes as A
from noobbaozi.engine import scenarios
from noobbaozi.project import explain, summary
from noobbaozi.project.views.base import SLOT_CARGO, BASE, ViewContext, ship_attributes

MAX_OPS: int = 32
"""一次 `sensitivity` 最多执行几个 op（每个 op 一次引擎重算）。"""

_STATE_ALIASES: dict[str, str] = {
    "off": "offline",
    "offline": "offline",
    "online": "online",
    "active": "active",
    "overload": "overload",
}
"""op 里接受的状态名 → 引擎状态（`off` 是给调用方的别名，引擎只认 `offline`）。"""

_EXTRA_METRICS: dict[str, int] = {
    "stability.capacitor.stable_pct": A.ATTR_CAP_STABLE_PCT,
    "stability.capacitor.peak_load": A.ATTR_CAP_PEAK_LOAD,
    "stability.capacitor.capacity": A.ATTR_CAPACITOR_CAPACITY,
    "resources.cpu.free": A.ATTR_CPU_FREE,
    "resources.powergrid.free": A.ATTR_POWER_FREE,
}
"""`explain` 的 9 个指标之外，`sensitivity` 额外支持的**单值**指标（都是引擎属性直读）。

为什么需要：`cap_sensitivity` 的交叉验证要 `stability.capacitor.stable_pct`；
explain 没有它（它不可「归因成贡献项」，但完全可以「改输入看它怎么变」）。
"""

_OP_KINDS: frozenset[str] = frozenset({"disable_item", "set_state", "set_mode"})

_MODE_CAPABLE_GROUPS: frozenset[str] = frozenset({"Tactical Destroyer", "Strategic Cruiser"})
"""该有模式的船体 group（T3D / T3C）。用来区分「这船本来没有模式」与「有模式但索引没收录」：
后者必须留 warning（`modes_unknown`），否则「模式维度没覆盖」会变成隐身能力。"""


# =============================================================================
# 对外入口
# =============================================================================


def supported_metrics() -> tuple[str, ...]:
    """本模块能算灵敏度的指标（explain 的 9 个 + 5 个单值指标）。"""
    return tuple(explain.supported_metrics()) + tuple(_EXTRA_METRICS)


def sensitivity(
    ctx: ViewContext,
    metric: str,
    *,
    ops: Sequence[Mapping[str, object]] | None = None,
    max_ops: int = MAX_OPS,
) -> dict | None:
    """某指标对「逐件关闭 / 状态切换 / 模式切换」的灵敏度。

    Args:
        ctx: 视角上下文（与 `explain` / `render_view` 同一个）。
        metric: L0/L1 点路径；不在 `supported_metrics()` 里 → `None`。
        ops: 显式 op 列表；`None` = 自动计划（全部已装模块各 `disable_item` 一次 +
             每个非当前模式 `set_mode` 一次）。
        max_ops: 最多执行几个 op（默认 32）。

    Returns:
        `None`（指标不支持 / 基线取不到），或：
        `{metric, combine, baseline, changes[], warnings[], ops_planned, ops_executed,
          skipped, truncated, max_ops, provenance, tolerance}`；
        每条 change：`{op: {...}, value, delta, delta_pct, note}`。

        **计数恒等式**：`ops_planned == ops_executed + skipped`（见模块 docstring「计数口径」）。
        截断只影响覆盖面，**不影响已执行项的 `value`**（前 N 条与完整跑逐位相同）。

    Raises:
        ValueError: `max_ops` 不是正整数。
    """
    if not isinstance(max_ops, int) or isinstance(max_ops, bool) or max_ops < 1:
        raise ValueError(f"max_ops 必须是正整数，收到 {max_ops!r}")

    text = str(metric or "").strip()
    if text not in supported_metrics():
        return None

    base_calculation = ctx.calculate_variant(_key("base", text), ctx.fit)
    baseline = _metric_value(base_calculation, text)
    if baseline is None:
        return None  # 基线都不知道，谈不上「变化」

    warnings: list[dict] = []
    if ops is None:
        planned, noops = _auto_ops(ctx, warnings)
    else:
        planned, noops = _explicit_ops(ctx, ops, warnings)

    if noops:
        warnings.append(
            {
                "kind": "noop",
                "message": (
                    f"{noops} 个 op 是空操作（模块已 offline / 状态或模式已是目标值 / 重复 op），"
                    "未执行"
                ),
                "count": noops,
            }
        )

    selected = planned[:max_ops]
    truncated = len(planned) > max_ops
    if truncated:
        warnings.append(
            {
                "kind": "truncated",
                "message": (
                    f"计划 {len(planned)} 个可执行 op（另有 {noops} 个空操作），"
                    f"按 max_ops={max_ops} 只执行前 {max_ops} 个"
                ),
                "planned": len(planned),
                "executed": max_ops,
                "noops": noops,
            }
        )

    changes: list[dict] = []
    for op in selected:
        variant, description = _apply(ctx, op)
        if variant is None:
            warnings.append(
                {"kind": "invalid_op", "message": f"op 无法应用：{op}", "op": dict(op)}
            )
            continue
        try:
            calculation = ctx.calculate_variant(_key_for(op), variant)
        except Exception as exc:  # noqa: BLE001 - 引擎拒绝是这个场景的正常输出
            warnings.append(
                {
                    "kind": "variant_error",
                    "message": f"{type(exc).__name__}: {exc}",
                    "op": dict(op),
                }
            )
            continue
        value = _metric_value(calculation, text)
        if value is None:
            warnings.append(
                {
                    "kind": "metric_missing_after_change",
                    "message": "变体算出来了但这个指标取不到（不补 0）",
                    "op": dict(op),
                }
            )
            continue

        delta = value - baseline
        changes.append(
            {
                "op": description,
                "value": value,
                "delta": delta,
                "delta_pct": None if baseline == 0.0 else delta / baseline,
                "note": _note(description),
            }
        )

    executed = len(changes)
    planned_total = len(planned) + noops
    return {
        "metric": text,
        "combine": _combine(text),
        "baseline": baseline,
        "changes": changes,
        "warnings": warnings,
        # 恒等式：ops_planned == ops_executed + skipped（理由见模块 docstring「计数口径」）
        "ops_planned": planned_total,
        "ops_executed": executed,
        "skipped": planned_total - executed,
        "truncated": truncated,
        "max_ops": max_ops,
        "provenance": _provenance(ctx, text),
        "tolerance": dict(explain.TOLERANCE),
    }


# =============================================================================
# op 计划
# =============================================================================


def _auto_ops(ctx: ViewContext, warnings: list[dict]) -> tuple[list[dict], int]:
    """默认计划：每件已装模块 `disable_item` + 每个非当前模式 `set_mode`。

    Returns:
        `(要执行的 op, 空操作数)`；空操作**也计入** `ops_planned`（见模块 docstring「计数口径」）。
    """
    ops: list[dict] = []
    noops = 0
    for index, item in _fitted_indexes(ctx):
        state = str(item.get("state") or "")
        if state == "offline":
            noops += 1  # 已经是 offline，关掉它是个空操作
            continue
        ops.append({"kind": "disable_item", "item_index": index})

    modes = _modes(ctx)
    current = (ctx.fit.get("ship") or {}).get("mode")
    if modes:
        for row in modes:
            mode_id = row.get("type_id")
            if not isinstance(mode_id, int):
                continue
            if mode_id == current:
                noops += 1  # 已经是这个模式
                continue
            ops.append({"kind": "set_mode", "mode_id": mode_id})
    elif ctx.index is None:
        warnings.append(
            {"kind": "no_index", "message": "没有 SDE 索引 → 无法枚举模式，只给逐件 op"}
        )
    elif _ship_group_name(ctx) in _MODE_CAPABLE_GROUPS:
        # T3C（Strategic Cruiser）的模式在本 SDE 索引里没有映射。**静默丢掉这一维**会让
        # 调用方以为「模式也测过了」—— 给一条事实性 warning，别让它变成隐身能力。
        warnings.append(
            {
                "kind": "modes_unknown",
                "message": (
                    f"{_ship_group_name(ctx)} 在本地 SDE 索引里没有可枚举的模式，"
                    "自动计划不含 set_mode（该维度未覆盖，不等于「切模式没有影响」）"
                ),
            }
        )
    return ops, noops


def _explicit_ops(
    ctx: ViewContext, ops: Sequence[Mapping[str, object]], warnings: list[dict]
) -> tuple[list[dict], int]:
    """调用方给的 op 列表：逐条校验，坏 op 进 warnings（不猜、不静默丢）。

    Returns:
        `(要执行的 op, 空操作数)`。三类**空操作**在这里判出来（合法但无意义：计入
        `ops_planned`、不执行、留 warning）：

        1. 目标状态 == 当前状态（`disable_item` 与 `set_state offline` 同一判据）；
        2. `set_mode` 的目标就是当前模式；
        3. 与前面某条 op **效果完全相同**（同一件的同一状态 / 同一个 mode）—— 重复 op 只跑一次。

    与「坏 op」的区别：坏 op（未知 kind / 越界 / 货舱件 / 非法 state / 模式不可用）是**输入
    不合法**，压根没成为计划的一部分；空操作是**合法但无意义**，它算在计划里。
    """
    if isinstance(ops, (str, bytes)) or not isinstance(ops, Sequence):
        warnings.append({"kind": "invalid_ops", "message": "ops 必须是数组"})
        return [], 0

    planned: list[dict] = []
    noops = 0
    seen: set[str] = set()
    modes = _modes(ctx)
    known_modes = {row.get("type_id") for row in modes if isinstance(row.get("type_id"), int)}
    current_mode = (ctx.fit.get("ship") or {}).get("mode")
    items = ctx.fit.get("items") or []

    for raw in ops:
        if not isinstance(raw, Mapping):
            warnings.append({"kind": "invalid_op", "message": f"op 不是对象：{raw!r}"})
            continue
        kind = str(raw.get("kind") or "")
        if kind not in _OP_KINDS:
            warnings.append({"kind": "invalid_op", "message": f"未知 op.kind：{kind!r}"})
            continue
        if kind in ("disable_item", "set_state"):
            index = raw.get("item_index")
            if not isinstance(index, int) or isinstance(index, bool):
                warnings.append({"kind": "invalid_op", "message": "item_index 必须是整数"})
                continue
            if not 0 <= index < len(items):
                warnings.append({"kind": "invalid_op", "message": f"item_index 越界：{index}"})
                continue
            if _slot_type(items[index]) == SLOT_CARGO:
                warnings.append({"kind": "invalid_op", "message": f"第 {index} 件在货舱里"})
                continue
            if kind == "disable_item":
                state = "offline"
            else:
                state = _STATE_ALIASES.get(str(raw.get("state") or "").lower())
                if state is None:
                    warnings.append(
                        {"kind": "invalid_op", "message": f"非法 state：{raw.get('state')!r}"}
                    )
                    continue
            key = f"state:{index}:{state}"
            if str(items[index].get("state") or "") == state or key in seen:
                noops += 1
                continue
            seen.add(key)
            planned.append(dict(raw))
        else:  # set_mode
            mode_id = raw.get("mode_id")
            if not isinstance(mode_id, int) or isinstance(mode_id, bool):
                warnings.append({"kind": "invalid_op", "message": "mode_id 必须是整数"})
                continue
            key = f"mode:{mode_id}"
            if mode_id == current_mode or key in seen:
                noops += 1
                continue
            if modes and mode_id not in known_modes:
                warnings.append(
                    {
                        "kind": "mode_not_available",
                        "message": (
                            f"模式 {mode_id} 不在本船的可切换模式里（{sorted(known_modes)}），跳过"
                        ),
                        "mode_id": mode_id,
                    }
                )
                noops += 1
                continue
            if not modes and ctx.index is not None:
                # 枚举不到 ≠ 不适用，但我们**无法判断**它是否适用 → 跳过并说明，不给 delta 0 的假变化
                warnings.append(
                    {
                        "kind": "modes_unknown",
                        "message": (
                            "本船在本地 SDE 索引里没有可枚举的模式 → 无法判断该 mode 是否适用，跳过"
                        ),
                        "mode_id": mode_id,
                    }
                )
                noops += 1
                continue
            seen.add(key)
            planned.append(dict(raw))
    return planned, noops


# =============================================================================
# op 应用
# =============================================================================


def _apply(ctx: ViewContext, op: Mapping[str, object]) -> tuple[dict | None, dict]:
    """op → 变体 fit + 给读者看的 op 描述。失败返回 `(None, {})`。"""
    kind = str(op.get("kind") or "")
    fit = ctx.fit

    if kind in ("disable_item", "set_state"):
        index = int(op["item_index"])  # 调用方已校验
        state = "offline" if kind == "disable_item" else _STATE_ALIASES[str(op["state"]).lower()]
        items = [_copy_item(item) for item in (fit.get("items") or [])]
        items[index] = {**items[index], "state": state}
        variant = {**fit, "items": items}
        label = _item_label(ctx, index)
        description = {"kind": kind, "item_index": index, "label": label}
        if kind == "set_state":
            description["state"] = state
        else:
            description["state"] = "offline"
        return variant, description

    if kind == "set_mode":
        mode_id = int(op["mode_id"])
        # 复用 engine/scenarios 的 mode 语义（与 `VARIANTS` / `render._modes_block` 同一条）
        variant = scenarios.build_variant(
            fit,
            {
                "name": "sensitivity",
                "states": {scenarios.ALL_KEY: scenarios.KEEP},
                "mode_id": mode_id,
            },
        )
        name = _mode_name(ctx, mode_id)
        return variant, {"kind": kind, "mode_id": mode_id, "label": name, "mode_name": name}

    return None, {}


# =============================================================================
# 取值 / 杂项
# =============================================================================


def _metric_value(calculation: Mapping[str, object], metric: str) -> float | None:
    """统一取值：额外单值指标读属性，其余交给 `explain.metric_value`（A/B/C 同一口径）。"""
    attribute = _EXTRA_METRICS.get(metric)
    if attribute is not None:
        value = ship_attributes(calculation).get(attribute)
        return None if value is None else float(value)
    return explain.metric_value(calculation, metric)


def _combine(metric: str) -> str:
    """指标的组合方式；额外单值指标给 `"single"`（既不是相加也不是相乘，是单值）。"""
    if metric in _EXTRA_METRICS:
        return "single"
    return explain.metric_combine(metric) or "single"


def _provenance(ctx: ViewContext, metric: str) -> str | None:
    answer = summary.get_path(ctx, metric)
    state = answer.get("provenance") if isinstance(answer, dict) else None
    return state if isinstance(state, str) else None


def _note(description: Mapping[str, object]) -> str | None:
    """给人看的补充说明（只陈述事实，不评价好坏）。"""
    if description.get("kind") == "set_mode":
        return f"切换到 {description.get('mode_name')} 模式"
    return None


def _key(tag: str, metric: str) -> str:
    return f"sensitivity:{tag}:{metric}"


def _key_for(op: Mapping[str, object]) -> str:
    """变体缓存 key：op 的三要素唯一（与预置变体名不会撞：都以 `sensitivity:` 开头）。"""
    kind = str(op.get("kind") or "")
    if kind == "set_mode":
        return f"sensitivity:mode:{int(op['mode_id'])}"
    index = int(op["item_index"])
    if kind == "disable_item":
        return f"sensitivity:disable:{index}"
    return f"sensitivity:state:{index}:{_STATE_ALIASES[str(op['state']).lower()]}"


def _fitted_indexes(ctx: ViewContext) -> list[tuple[int, dict]]:
    """已装模块 `(在 fit["items"] 里的下标, 原始 item)`；货舱不算。"""
    return [
        (index, item)
        for index, item in enumerate(ctx.fit.get("items") or [])
        if isinstance(item, dict) and _slot_type(item) != SLOT_CARGO
    ]


def _slot_type(item: Mapping[str, object]) -> str:
    slot = item.get("slot")
    return str(slot.get("type") or "") if isinstance(slot, dict) else ""


def _copy_item(item: Mapping[str, object]) -> dict:
    copied = dict(item)
    if isinstance(copied.get("slot"), dict):
        copied["slot"] = dict(copied["slot"])
    return copied


def _item_label(ctx: ViewContext, index: int) -> str | None:
    """模块名：优先用 `ctx.items()` 的合并视图（带 name），没有就退回 type_id。"""
    items = ctx.items(BASE)
    if 0 <= index < len(items) and isinstance(items[index], dict):
        row = items[index]
        name = row.get("name")
        if isinstance(name, str) and name:
            return name
        type_id = row.get("type_id")
        if isinstance(type_id, int):
            return f"type_id {type_id}"
    return None


def _modes(ctx: ViewContext) -> list[dict]:
    """该船可用模式（`Index.modes_for_ship()`；无索引/无模式 → `[]`）。"""
    if ctx.index is None:
        return []
    ship = ctx.fit.get("ship")
    type_id = ship.get("type_id") if isinstance(ship, dict) else None
    if not isinstance(type_id, int):
        return []
    return list(ctx.index.modes_for_ship(type_id) or [])


def _ship_group_name(ctx: ViewContext) -> str | None:
    """船体 group 名（无索引 / 查不到 → `None`，不猜）。"""
    if ctx.index is None:
        return None
    ship = ctx.fit.get("ship")
    type_id = ship.get("type_id") if isinstance(ship, dict) else None
    if not isinstance(type_id, int):
        return None
    row = ctx.index.get_type(type_id) or {}
    group = row.get("group_name")
    return group if isinstance(group, str) else None


def _mode_name(ctx: ViewContext, mode_id: int) -> str | None:
    """模式短名：`Hecate Defense Mode` → `Defense`（只去前后缀，不翻译）。"""
    for row in _modes(ctx):
        if row.get("type_id") != mode_id:
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            return None
        ship_name = ""
        ship = ctx.fit.get("ship")
        if isinstance(ship, dict) and isinstance(ship.get("type_id"), int) and ctx.index is not None:
            ship_row = ctx.index.get_type(int(ship["type_id"])) or {}
            ship_name = str(ship_row.get("name") or "")
        if name.lower().endswith(" mode"):
            name = name[: -len(" mode")].strip()
        if ship_name and name.lower().startswith(ship_name.lower()):
            name = name[len(ship_name) :].strip()
        return name or None
    return None
