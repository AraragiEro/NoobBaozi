"""视角注册表（**只放注册与再导出，不放任何业务逻辑**）。

六个视角各自一个模块，统一接口（见 `evefit/project/views/base.py`）：

```python
def build(ctx: ViewContext) -> dict:
    return {"view": "firepower", "overview": {...}, "flags": [...]}
```

注册方式（在自己的模块里一行即可，不要改本文件）：

```python
from evefit.project.views import register

@register("firepower")
def build(ctx):
    ...
```

为什么用注册表而不是在这里 import 六个模块：本文件是**并行开发的汇合点**，
import 一个还没写好的模块会让整个包 import 失败，六个视角就没法各自独立开发与测试。
"""

from __future__ import annotations

from typing import Callable

from evefit.project.views.base import (
    BASE,
    OFFLINE,
    OVERLOAD,
    VARIANTS,
    ViewContext,
    item_attributes,
    ship_attributes,
)

VIEW_ORDER: tuple[str, ...] = (
    "firepower",
    "survivability",
    "mobility",
    "resources",
    "constraints",
    "stability",
)
"""六个视角的规范名字与输出顺序（字段规格 §1–§6）。注册表不限制只能是这些名字，
新视角（如后续的对算视图）可以自行加名，但本顺序是渲染层的默认顺序。"""

_BUILDERS: dict[str, Callable[[ViewContext], dict]] = {}


def register(name: str) -> Callable[[Callable[[ViewContext], dict]], Callable[[ViewContext], dict]]:
    """把 `build(ctx)` 注册到 `name` 名下（装饰器）。

    Raises:
        ValueError: `name` 不是非空字符串。
        RuntimeError: `name` 已被注册（静默覆盖会让「谁的版本在跑」变成谜）。
    """

    if not isinstance(name, str) or not name:
        raise ValueError(f"视角名必须是非空字符串，实际是 {name!r}")

    def decorator(builder: Callable[[ViewContext], dict]) -> Callable[[ViewContext], dict]:
        if name in _BUILDERS:
            raise RuntimeError(f"视角 {name!r} 已注册，不允许静默覆盖")
        if not callable(builder):
            raise TypeError(f"视角 {name!r} 的 builder 不可调用")
        _BUILDERS[name] = builder
        return builder

    return decorator


def available_views() -> tuple[str, ...]:
    """已注册的视角名（按 `VIEW_ORDER` 顺序在前，其余按注册顺序在后）。"""
    ordered = [name for name in VIEW_ORDER if name in _BUILDERS]
    return tuple(ordered) + tuple(name for name in _BUILDERS if name not in VIEW_ORDER)


def build_view(name: str, ctx: ViewContext) -> dict:
    """调用某个已注册视角的 `build(ctx)`。未注册则 `KeyError`（不返回空壳）。"""
    builder = _BUILDERS.get(name)
    if builder is None:
        raise KeyError(f"视角 {name!r} 未注册；已注册：{list(available_views())}")
    return builder(ctx)


def build_all(ctx: ViewContext, names: tuple[str, ...] | None = None) -> dict[str, dict]:
    """按需批量构建（默认构建全部已注册视角），返回 `{name: view dict}`。"""
    targets = names if names is not None else available_views()
    return {name: build_view(name, ctx) for name in targets}


__all__ = [
    "BASE",
    "OFFLINE",
    "OVERLOAD",
    "VARIANTS",
    "VIEW_ORDER",
    "ViewContext",
    "available_views",
    "build_all",
    "build_view",
    "item_attributes",
    "register",
    "ship_attributes",
]
