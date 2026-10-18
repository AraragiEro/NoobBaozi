"""离线分析工具（`noobbaozi/tools/`）—— **不是产品链路，不对外提供 API**。

与 `noobbaozi/spike/` 的区别：

- `spike/` 是一次性的探针脚本，跑完把结论抄进 `PROGRESS.md` §3；
- `tools/` 是**可复跑的推导器**：它把某个结论（目前只有 provenance 三态表）
  从"手写"变成"跑出来"，产物直接进产品代码，并且**必须能在 SDE / 引擎升级后重跑**。

本包内的模块允许 import 引擎（`noobbaozi.engine`），这是与 `project/` 的硬边界：
`project/` 不得 import 本包（红线 N1：计算链路不得引入不可复现的外部状态）。
"""
