"""视角加载器：**唯一**允许 import 六个视角模块的地方（副作用即注册）。

为什么要单独一个文件：`noobbaozi/project/views/__init__.py` 是并行开发的汇合点，
不能在里面 import 六个视角——任何一个还没写完，整个包就会 import 失败，六个视角
就没法各自独立开发与测试（见该文件 docstring）。但渲染层又必须**确定**六视角都已
注册：`available_views()` 在没人 import 过视角模块时是空元组，那会渲染出一个
「一个视角都没有」的空壳，而这种静默少几个视角的失败最难发现。

所以把「为了注册而 import」这件事集中到这里，且只做两件事（无任何业务逻辑）：

1. import 六个视角模块（`@register(...)` 在 import 时执行）；
2. 暴露 `VIEW_MODULES`（视角名 → 模块对象）与 `load_views()`（已注册视角名）。

`VIEW_MODULES` 用**模块对象**而不是模块名，是因为渲染层要读各视角自己导出的
`FIELD_SOURCES`（overview 点路径 → 来源属性 ID）来逐字段打 provenance；
跳一层 `importlib` 就没有「唯一 import 点」的意义了。
"""

from __future__ import annotations

from types import ModuleType

from noobbaozi.project.views import available_views
from noobbaozi.project.views import constraints as _constraints
from noobbaozi.project.views import firepower as _firepower
from noobbaozi.project.views import mobility as _mobility
from noobbaozi.project.views import resources as _resources
from noobbaozi.project.views import stability as _stability
from noobbaozi.project.views import survivability as _survivability

VIEW_MODULES: dict[str, ModuleType] = {
    "firepower": _firepower,
    "survivability": _survivability,
    "mobility": _mobility,
    "resources": _resources,
    "constraints": _constraints,
    "stability": _stability,
}
"""六个视角名 → 模块对象（顺序 = `VIEW_ORDER`，见 `views/__init__.py`）。

导入本文件即完成注册；这也是七处（六个视角 + 渲染层）里唯一允许 import 视角模块的位置。
"""


def load_views() -> tuple[str, ...]:
    """已注册的视角名（顺序 = `VIEW_ORDER` 在前，其余按注册顺序在后）。

    返回的就是 `views.available_views()`：本函数不自己维护名单，避免「注册表里
    有、加载器里漏了」这种两份名单漂移。
    """
    return available_views()


__all__ = ["VIEW_MODULES", "load_views"]
