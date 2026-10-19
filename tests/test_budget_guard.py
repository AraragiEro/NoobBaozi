"""`render_overview` 的体量守卫（T-55 / 2-5）。

背景：`render_overview` 在**产品面没有调用方** —— MCP 的 8 个工具与 CLI 一处都不调它
（L0 走 `summary.render_index`、L1 走 `render_view`、L2 走 `render_detail`），在
`docs/输出分层与工具集-v1.md` 里已标 deprecated。**但** `acceptance/` 的期望写着
「overview 装进预算（`degraded=False`），覆盖完整列表」—— 它是**回归门的基线**，
不是死代码。

19,000 时代三个样本的实测余量：

| 样本 | `budget.chars` | 余量 |
|---|---:|---:|
| `f12-logistics-oneiros` | **18,962** | **38** |
| `f47-random-raptor` | 17,626 | 1,374 |
| `f67-spotcheck-139255942` | 17,498 | 1,502 |

**38 字符不是「留了预算」，是「没有预算」**：下一个叶子就会触发降级，而第一步降级就是
**整块丢掉 `flags.evidence`**（诊断证据消失，不是「少几行」）。所以预算已提到 **20,500**
（理由与撤销条件写在 `project/render.py` 的 `BUDGET_CHARS` docstring 里）。

本文件的作用：谁往 overview 里加字段、把余量吃掉了，这里立刻红，并**把实际 `chars` 与
余量打出来** —— 不用等验收跑一遍才发现降级。
"""

from __future__ import annotations

import json
from pathlib import Path

import esf_dogma_engine as dogma
import pytest

from noobbaozi.config.paths import db_path
from noobbaozi.engine.adapter import Engine
from noobbaozi.engine.assumptions import default_assumptions, derive_skill_tier
from noobbaozi.project import render
from noobbaozi.project.views.base import ViewContext
from noobbaozi.sde.index import Index

# 三个视角模块必须显式 import 才会注册（注册表刻意不 import 它们）
import noobbaozi.project.views.constraints  # noqa: F401  （导入即注册）
import noobbaozi.project.views.resources  # noqa: F401
import noobbaozi.project.views.stability  # noqa: F401

NOOBBAOZI_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = NOOBBAOZI_ROOT / "acceptance" / "fixtures"

BUDGET_SAMPLES: tuple[str, ...] = (
    "f12-logistics-oneiros.json",
    "f47-random-raptor.json",
    "f67-spotcheck-139255942.json",
)
"""三个「最重」的验收样本：它们决定 `BUDGET_CHARS` 的下限。

`f12` 是历史最大者（19,000 时代余量只剩 38 字符）；`f47` / `f67` 是 T-44 判定过
「降级即产品缺陷」的那两个。
"""

BUDGET_CEILING: int = 23000
"""预算的**上限**（护栏自己的护栏）。

按 `render.py` docstring 里的旧公式 `ceil(实测 × 1.2 ÷ 1000) × 1000`，`f12` 会落到
**23,000** 档 —— 超过 23,000 就等于「比 20% 余量的宽松值还宽」，`degraded is False`
会退化成永真。改这个数字必须同时改这段说明。
"""

MIN_HEADROOM_RATIO: float = 0.05
"""最重样本至少要剩下的余量比例。

5% 不是随手取的：19,000 时代 `f12` 只剩 **0.2%**（38/19000），那已经等于「随机看运气
是否降级」；20,500 下 `f12` 余 **1,538 / 20,500 = 7.5%**。
"""


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine()


@pytest.fixture(scope="module")
def index():
    try:
        path = db_path()
    except FileNotFoundError:  # 索引库是构建产物，没建就跳过
        pytest.skip("索引库未构建：先跑 noobbaozi.sde.index.build_index()")
    opened = Index.open(path)
    yield opened
    opened.close()


def _context_for(engine: Engine, index, name: str) -> ViewContext:
    """按文件名加载验收样本 + 「全 4 + 补最低」技能档（与验收脚本同一条构造路径）。"""
    raw = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        raw = raw[0]
    raw.pop("zkb", None)  # zkillboard 附加字段，不是 killmail 字段
    fit = dogma.load_killmail(raw)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return ViewContext(
        engine=engine,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )


def _measure(engine: Engine, index, name: str) -> dict:
    """渲染一次并返回 `budget` 块（外加样本名，方便报错时定位）。"""
    rendering = render.render_overview(_context_for(engine, index, name))
    return {"sample": name, **rendering["budget"]}


@pytest.fixture(scope="module")
def budgets(engine: Engine, index) -> dict[str, dict]:
    """三个样本各渲染**一次**并缓存（渲染不便宜，别在每条测试里重来）。"""
    return {name: _measure(engine, index, name) for name in BUDGET_SAMPLES}


@pytest.mark.parametrize("name", BUDGET_SAMPLES)
def test_overview_fits_the_budget(budgets: dict[str, dict], name: str) -> None:
    """三个最重样本都必须 `degraded is False`，且把实际 `chars` / 余量打出来。

    为什么「只断言不降级」还不够：降级是**分档**的（先去 `evidence`、再截三个明细列表、
    最后清 `modes`），`degraded is False` 才能保证**一档都没触发**。
    """
    budget = budgets[name]
    margin = render.BUDGET_CHARS - budget["chars"]
    print(
        f"\n[budget] {name:32} chars={budget['chars']:>7,} "
        f"limit={render.BUDGET_CHARS:,} margin={margin:>5,} "
        f"({margin / render.BUDGET_CHARS:.1%})"
    )
    assert budget["degraded"] is False, (
        f"{name} 触发了降级：chars={budget['chars']} > limit={render.BUDGET_CHARS}"
        f"（余量 {margin}）。第一步降级会整块丢掉 flags.evidence。"
    )
    assert budget["chars"] <= render.BUDGET_CHARS
    assert margin >= 0


def test_budget_has_room_for_the_next_field(budgets: dict[str, dict]) -> None:
    """余量必须**看得见**：三样本一起打表，最坏那个至少留 `MIN_HEADROOM_RATIO`。"""
    print("\n[budget] 三样本余量表")
    for name in BUDGET_SAMPLES:
        budget = budgets[name]
        margin = render.BUDGET_CHARS - budget["chars"]
        print(
            f"  {name:32} chars={budget['chars']:>7,} "
            f"margin={margin:>5,} ({margin / render.BUDGET_CHARS:5.1%})"
        )
    worst = max(budgets[name]["chars"] for name in BUDGET_SAMPLES)
    headroom = (render.BUDGET_CHARS - worst) / render.BUDGET_CHARS
    assert headroom >= MIN_HEADROOM_RATIO, (
        f"最重样本余量只有 {headroom:.2%}（chars={worst}, limit={render.BUDGET_CHARS}）——"
        f"低于 {MIN_HEADROOM_RATIO:.0%} 就等于「没有预算」，加字段前先把预算或体量处理掉"
    )


def test_budget_constant_is_not_inflated(budgets: dict[str, dict]) -> None:
    """护栏自己的护栏：预算不许被无脑调大，否则 `degraded is False` 会退化成永真。"""
    assert render.BUDGET_CHARS <= BUDGET_CEILING, (
        f"BUDGET_CHARS={render.BUDGET_CHARS} 超过 {BUDGET_CEILING}："
        "按旧公式（实测×1.2）f12 才落 23,000 档，再高就等于放弃这条护栏"
    )
    worst = max(budgets[name]["chars"] for name in BUDGET_SAMPLES)
    assert worst > BUDGET_CEILING * 0.7, (
        f"最重样本只有 {worst} chars，远小于上限 {BUDGET_CEILING} 的 70% ——"
        "说明样本集没覆盖到边界，这条守卫已经失去意义"
    )


def test_budget_guard_is_not_vacuous(engine: Engine, index, budgets: dict[str, dict]) -> None:
    """**配对断言**：把预算调到比实际用量少 1 → 必须真的降级，且真的少掉内容。

    不做这一步，「`degraded is False`」就不能证明守卫在起作用 —— 它也可能恒真
    （PROGRESS 第 180 / 196 条：空转的断言比没有断言更危险）。
    """
    name = BUDGET_SAMPLES[0]
    reference = budgets[name]
    assert reference["degraded"] is False, "前提：当前预算下这个样本是不降级的"

    original = render.BUDGET_CHARS
    render.BUDGET_CHARS = reference["chars"] - 1
    try:
        squeezed = _measure(engine, index, name)
    finally:
        render.BUDGET_CHARS = original

    assert squeezed["degraded"] is True, (
        f"预算调到 {reference['chars'] - 1}（< 实际 {reference['chars']}）却没降级 —— 守卫是空的"
    )
    assert squeezed["chars"] < reference["chars"], (
        "降级只是把布尔翻成 True，却没有真的去掉内容（chars 没变小）——"
        "这说明 _apply_budget 的降级动作没接上"
    )
