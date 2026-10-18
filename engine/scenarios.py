"""情景运行器（L2）：按视角声明的变体批量计算。

设计依据：软件设计 §7.3「按需变体，不设全局情景表」——本层**不定义**哪些情景有意义，
只按调用方给的 `ScenarioSpec` 列表算，结果按 `spec.name` 返回。

本阶段支持的语义只有两种（刻意做到最小，避免发明语义）：

    {"all": "<offline|online|active|overload>"}   # 所有已装模块设为该状态
    {"all": "keep"}                               # 保持原状
    {}                                            # 没写 = 保持原状

出现其它键（例如 `{"high": "only_weapons"}`）一律 `ValueError` 明确拒绝——那是
后续阶段要做的事，现在猜一个近似语义只会产出看起来对、其实是错的数字。

`mode_id` 非 None 时写入 `fit["ship"]["mode"]`；None 表示**不修改**输入里已有的模式
（不静默删除用户给的模式）。

结果缓存由 `Engine` 的 LRU 统一负责（所有变体最终都走 `engine.calculate`），
本层不再造第二套缓存。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只为类型标注，运行时不产生 import 依赖
    from evefit import contracts
    from evefit.engine.adapter import Engine

ALL_KEY = "all"
KEEP = "keep"

ITEM_STATES = ("offline", "online", "active", "overload")
"""`state` 的合法值（软件设计 §7.3 的已确认可建模维度）。"""


def _spec_states(spec: "contracts.ScenarioSpec") -> dict[str, str]:
    states = spec.get("states")
    if not isinstance(states, dict):
        raise ValueError(
            f"scenario {spec.get('name')!r}: `states` 必须是 dict，实际是 "
            f"{type(states).__name__}"
        )
    return states


def _target_state(spec: "contracts.ScenarioSpec") -> str | None:
    """解析出「所有模块统一设成什么状态」；返回 None 表示保持原状。"""
    name = spec.get("name")
    states = _spec_states(spec)

    unsupported = sorted(set(states) - {ALL_KEY})
    if unsupported:
        raise ValueError(
            f"scenario {name!r}: 不支持的 states 键 {unsupported}；"
            f"本阶段只支持 {{{ALL_KEY!r}: '<offline|online|active|overload>|keep'}}"
        )
    if not states:
        return None

    target = states[ALL_KEY]
    if not isinstance(target, str):
        raise ValueError(f"scenario {name!r}: states[{ALL_KEY!r}] 必须是字符串")
    if target == KEEP:
        return None
    if target not in ITEM_STATES:
        raise ValueError(
            f"scenario {name!r}: 非法的 state {target!r}；合法值 {list(ITEM_STATES)} 或 {KEEP!r}"
        )
    return target


def _apply_states(items: list[dict], target: str | None) -> list[dict]:
    """返回新的 items 列表（浅拷贝，不改入参）。"""
    if target is None:
        return [dict(item) for item in items]
    return [{**item, "state": target} for item in items]


def _apply_mode(fit: dict, mode_id: int | None) -> dict:
    """应用模式到 `ship.mode`（模式是独立 type，见 PROGRESS §3 第 3 条）。"""
    ship = dict(fit.get("ship") or {})
    if mode_id is not None:
        ship["mode"] = int(mode_id)
    return {**fit, "ship": ship}


def build_variant(fit: dict, spec: "contracts.ScenarioSpec") -> dict:
    """按 spec 生成 fit 变体（纯函数，不改入参）。"""
    if not isinstance(fit, dict):
        raise TypeError(f"fit 必须是 dict，实际是 {type(fit).__name__}")
    if not isinstance(spec, dict):
        raise TypeError(f"ScenarioSpec 必须是 dict，实际是 {type(spec).__name__}")
    if not isinstance(fit.get("ship"), dict):
        raise ValueError("fit 缺少 `ship` 对象，无法建变体")

    items = fit.get("items") or []
    if not isinstance(items, list):
        raise ValueError("fit 的 `items` 必须是数组")

    variant = _apply_mode(fit, spec.get("mode_id"))
    variant["items"] = _apply_states(items, _target_state(spec))
    return variant


def run_variants(
    fit: dict, specs: "list[contracts.ScenarioSpec]", engine: "Engine"
) -> dict[str, dict]:
    """逐 spec 计算变体，返回 `{spec.name: 原始 calculation dict}`。

    原始 calculation dict 由 `engine.calculate` 给出，本层不解析、不改写
    （单位换算与字段投影属于 `project/`）。
    """
    if not isinstance(specs, list):
        raise TypeError(f"specs 必须是 list，实际是 {type(specs).__name__}")

    results: dict[str, dict] = {}
    for spec in specs:
        if not isinstance(spec, dict):
            raise TypeError(f"ScenarioSpec 必须是 dict，实际是 {type(spec).__name__}")
        name = spec.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("ScenarioSpec.name 必须是非空字符串")
        if name in results:
            # 同名会静默覆盖前一条结果，宁可炸出来
            raise ValueError(f"重复的 ScenarioSpec.name: {name!r}")
        results[name] = engine.calculate(build_variant(fit, spec))
    return results
