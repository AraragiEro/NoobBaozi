"""重写第 6 轮合成样本：只用**已验证可解析**的模块名（从 f1/f2 EFT 里取）。

原理：EFT 引擎不接受 cargo/drone 段（cargo 与无人机在 EFT 里无法表达），
所以「极端货舱 / 极端无人机」改用可表达的替代形态，并在报告里记为真实缺口。
"""
from __future__ import annotations

from pathlib import Path

FIXTURES = Path("noobbaozi/acceptance/fixtures")


def module_pool() -> list[str]:
    """从已有 EFT 样本里收集模块行（保证名字与格式都能被解析）。"""
    pool: list[str] = []
    for name in ("f1-hecate-killmail.eft", "f2-hecate-tier-violation.eft"):
        path = FIXTURES / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("["):
                continue
            pool.append(line)
    return pool


def build(stem: str, lines: list[str]) -> Path:
    text = "\n".join([f"[Hecate, QA {stem}]", ""] + lines).rstrip() + "\n"
    path = FIXTURES / f"{stem}.eft"
    path.write_text(text, encoding="utf-8")
    return path


def main() -> int:
    pool = module_pool()
    print(f"可复用模块行 {len(pool)} 条，前 6 条：{pool[:6]}")
    if not pool:
        raise SystemExit("没有可复用模块行")

    seen = set()
    unique = []
    for line in pool:
        if line in seen:
            continue
        seen.add(line)
        unique.append(line)

    # f59：所有槽位尽量填满
    build("f59-all-slots", unique[:17])
    # f60：无推进模块（验证 propulsion_states 四态相等时仍自洽）
    no_prop = [line for line in unique
               if "Microwarpdrive" not in line and "Afterburner" not in line]
    build("f60-no-propulsion", no_prop[:8])
    # f66：单模块极简
    build("f66-single-module", unique[1:2])

    for stem in ("f59-all-slots", "f60-no-propulsion", "f66-single-module"):
        path = FIXTURES / f"{stem}.eft"
        print(f"  {path.name}: {len(path.read_text(encoding='utf-8').splitlines())} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
