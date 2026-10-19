"""`docker/redeploy.sh` 的 data 卷保护（T-57）—— 用**真实 git** 复现「索引库被 reset 删掉」那个事故。

为什么这个文件必须存在（不是为了覆盖率）
----------------------------------------
2026-10-20：NAS 上 `git reset --hard origin/main` 把 `data/noobbaozi-3607705.sqlite3`（14.3 MB）
删了，容器以退出码 2 起不来。机制不是 bug，是 git 的既定语义：**「旧历史跟踪、新历史里不存在」
的文件，`reset --hard` 会删掉**。部署源从「快照仓」切回开发仓（`data/` 进 `.gitignore`）之后，
索引库正好落进这一类。

这里钉三件事：

1. **事故可复现**（纯 git，Windows 上也能跑）——旧历史跟踪 `data/**`，对齐到不含 `data/`
   的新历史 → 文件消失。这条断言证明「保护」不是可选项。
2. **脚本结构正确**——保护发生在 fetch/reset **之前**、恢复在之后、空数据卷在 build **之前**
   就退出。断言按**出现顺序**逐行判（不是「有这个关键字就行」），删掉任一段都会红。
3. **端到端**（需要 POSIX shell）——拿真正的 `redeploy.sh` 跑 `--sync-only`，伪造一份旧历史
   和一个新远端，索引库必须**逐字节还在**，日志里必须有「保护了 1 个文件 / 恢复了 1 个文件」；
   再把这个保护动作改成空操作 → 数据必须**丢失**且脚本以退出码 3 拦住（证明守卫不是永真）。

两条环境注记（别以为是测试写错了）
----------------------------------
- 沙箱只允许写工作区：**不能用 `tmp_path`**（pytest 的临时目录在系统 TEMP，写不进去：
  `PermissionError: [WinError 5]`）。所以这里在自己的 `.tmp/docker-deploy-tests/` 里造仓库，
  跑完删掉（`.tmp/` 已在 `.gitignore` 里）。
- 第 3 条在 Windows 开发机上会 **skip**：git-bash / WSL 的 cygwin 运行时启动时要建信号管道，
  本机沙箱禁止命名管道（`*** fatal error - couldn't create signal pipe, Win32 error 5`），
  `sh.exe` / `bash.exe` 都起不来。skip 理由写在这里，免得有人把「绿了」当成「验证过」。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from functools import lru_cache
from pathlib import Path

import pytest

NOOBBAOZI_ROOT = Path(__file__).resolve().parents[1]
REDEPLOY = NOOBBAOZI_ROOT / "docker" / "redeploy.sh"
SYNC_DEPLOY = NOOBBAOZI_ROOT / "docker" / "sync-deploy.ps1"
DOCKER_README = NOOBBAOZI_ROOT / "docker" / "README.md"
SANDBOX_ROOT = NOOBBAOZI_ROOT / ".tmp" / "docker-deploy-tests"

SHELL_SKIP_REASON = (
    "本机没有可用的 POSIX shell：git-bash / WSL 的 cygwin 运行时需要创建信号管道，"
    "而本机沙箱禁止命名管道（fatal error - couldn't create signal pipe, Win32 error 5）。"
    "端到端守卫会在 NAS / Linux CI 上执行。"
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="需要 git 才能复现 reset --hard 的删除语义"
)


# =============================================================================
# 小工具
# =============================================================================


@pytest.fixture()
def sandbox() -> Path:
    """工作区内的临时沙箱（系统 TEMP 在本机沙箱里不可写，见模块 docstring）。"""
    path = SANDBOX_ROOT / f"case-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        # git 的对象文件是只读的，Windows 上 rmtree 会失败 —— 忽略即可（.tmp 不入库）
        shutil.rmtree(path, ignore_errors=True)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _init_repo(path: Path, files: dict[str, str], branch: str = "main") -> Path:
    """建一个真仓库：写文件 → add → commit（本地 file:// transport 够用，不联网）。"""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", branch)
    _git(path, "config", "user.name", "noobbaozi-test")
    _git(path, "config", "user.email", "test@local")
    _git(path, "config", "commit.gpgsign", "false")
    for rel, content in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _executable_lines(text: str) -> list[str]:
    """去掉空行与整行注释后的**可执行行**（顺序断言只看它们，注释里出现关键字不算）。"""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _find(lines: list[str], needle: str, *, start: int = 0) -> int:
    for index in range(start, len(lines)):
        if needle in lines[index]:
            return index
    return -1


@lru_cache(maxsize=1)
def _posix_shell() -> tuple[str, ...] | None:
    """找一个**真的能跑起来**的 POSIX shell（文件存在 ≠ 能跑：cygwin 在本机起不来）。"""
    candidates: list[tuple[str, ...]] = []
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            git_root = Path(git).resolve().parent.parent
            for rel in ("usr/bin/sh.exe", "bin/sh.exe", "bin/bash.exe"):
                exe = git_root / rel
                if exe.is_file():
                    candidates.append((str(exe),))
    for name in ("sh", "bash"):
        found = shutil.which(name)
        if found:
            candidates.append((found,))
    for candidate in candidates:
        try:
            done = subprocess.run(
                [*candidate, "-c", "echo SHELL_OK"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0 and "SHELL_OK" in done.stdout:
            return candidate
    return None


def _deploy_sandbox(sandbox: Path, payload: bytes) -> tuple[Path, Path]:
    """伪造 NAS 上的部署目录 + 一个「新历史」远端。

    返回 `(deploy, origin)`；deploy 的当前 HEAD **跟踪** `data/*.sqlite3`，
    而 origin（reset 的目标）里没有 data/ —— 这正是出事那天的形状。
    """
    deploy = _init_repo(
        sandbox / "deploy",
        {"README.md": "old snapshot (data/ is TRACKED here)\n", "docker/keep": ""},
    )
    (deploy / "data").mkdir()
    (deploy / "data" / "noobbaozi-3607705.sqlite3").write_bytes(payload)
    _git(deploy, "add", "-A")
    _git(deploy, "commit", "-q", "-m", "snapshot: track data/*.sqlite3")

    origin = _init_repo(
        sandbox / "origin",
        {
            "README.md": "dev repo (data/ is IGNORED here)\n",
            ".gitignore": "data/\n",
            "docker/keep": "",
        },
    )
    _git(deploy, "remote", "add", "origin", str(origin))
    _git(deploy, "fetch", "-q", "origin")
    return deploy, origin


# =============================================================================
# 一、事故机制本身：reset --hard 会删掉「旧历史跟踪、新历史没有」的文件
# =============================================================================


def test_reset_hard_deletes_data_tracked_by_the_old_history(sandbox: Path) -> None:
    """**这就是 14.3 MB 索引库消失的那一下**（不跑脚本，纯 git）。

    这条断言是「必须保护 data/」的**证据**：只要部署源从「跟踪 data/ 的历史」换成
    「忽略 data/ 的历史」，`reset --hard` 就必然删文件。脚本里的保护段就是为了对抗它。

    为什么不用「remote + fetch」造第二个历史：本机沙箱里 `git fetch <本地路径>` 会拉起
    `sh.exe` 去跑 `upload-pack`，而 cygwin 在本机起不来（与端到端测试同一个限制）。
    用**同一仓库的两个提交**表达「旧历史 / 新历史」在语义上完全等价：HEAD 停在跟踪
    `data/` 的提交上，目标是一个不含 `data/` 的提交。
    """
    payload = b"SQLITE" * 1024
    repo = _init_repo(
        sandbox / "deploy",
        {"README.md": "old snapshot (data/ is TRACKED here)\n", "data/noobbaozi-3607705.sqlite3": "placeholder"},
    )
    index_path = repo / "data" / "noobbaozi-3607705.sqlite3"
    index_path.write_bytes(payload)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "snapshot: track data/*.sqlite3")

    # 「新历史」：不再跟踪 data/（开发仓的样子）
    index_path.unlink()
    (repo / ".gitignore").write_text("data/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "dev: stop tracking data/")
    assert not index_path.exists()

    # 把工作区切回**旧历史**（data/ 又被跟踪、文件回来），然后 reset 到新历史
    _git(repo, "checkout", "-q", "HEAD~1")
    assert index_path.read_bytes() == payload, "前提：旧历史里 data/ 是被跟踪的"
    _git(repo, "reset", "--hard", "-q", "main")

    assert not index_path.exists(), (
        "git reset --hard 居然没删掉它 —— 那这个事故机制就不成立，"
        "保护 data/ 的必要性也要重新论证（这正是本条断言的意义）"
    )
    assert (repo / "README.md").read_text(encoding="utf-8").startswith("old"), (
        "新历史里 README 没变过，reset 应把它保持在旧内容"
    )
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "data/\n"


# =============================================================================
# 二、脚本结构：保护在 reset 之前、恢复在之后、空卷在 build 之前退出
# =============================================================================


def test_redeploy_protects_data_before_reset_and_restores_after() -> None:
    """按**出现顺序**钉住那条安全链（删掉任一段 → 这里变红）。"""
    lines = _executable_lines(REDEPLOY.read_text(encoding="utf-8"))

    trap = _find(lines, "trap 'on_exit' EXIT")
    protect_copy = _find(lines, 'for path in "$DATA_DIR"/*.sqlite3')
    fetch = _find(lines, "git -c core.hooksPath=/dev/null fetch")
    reset = _find(lines, 'git reset --hard "origin/$BRANCH"')
    restore_def = _find(lines, "restore_data() {")
    restore_call = _find(lines, "if ! restore_data; then")
    fail_fast = _find(lines, "exit 3")
    build = _find(lines, "$COMPOSE build")

    found = {
        "异常兜底 trap": trap,
        "保护循环": protect_copy,
        "fetch": fetch,
        "reset --hard": reset,
        "restore_data() 定义": restore_def,
        "恢复调用": restore_call,
        "空卷退出(exit 3)": fail_fast,
        "compose build": build,
    }
    missing = [name for name, index in found.items() if index < 0]
    assert not missing, f"redeploy.sh 缺了这些关键段落：{missing}"

    assert trap < protect_copy < fetch < reset, (
        "顺序必须是：装兜底 trap → 保护 → fetch → reset；"
        "保护放到 reset 之后就等于没保护（文件那时已经没了）"
    )
    assert reset < restore_call, "恢复必须在 reset **之后**"
    assert restore_call < fail_fast < build, (
        "空数据卷的退出必须发生在恢复之后、build 之前 —— 建一个必然起不来的容器没有意义"
    )


def test_redeploy_logs_how_many_files_it_protected_and_restored() -> None:
    """出问题时必须**看得出来**保护了什么（写成 N 个文件，不是一句「已保护」）。"""
    text = REDEPLOY.read_text(encoding="utf-8")
    assert "保护了 $PROTECT_COUNT 个文件" in text
    assert "恢复了 $RESTORED 个文件" in text
    assert "已保护: $name ($source_bytes 字节)" in text, "逐个文件也要 log（名字 + 字节数）"
    assert text.count('PROTECT_DIR=""') >= 2, (
        "恢复成功后必须清空 PROTECT_DIR，否则 EXIT trap 会在正常退出时又拷一遍"
    )
    # 「cp 成功」不等于「备份完整」：字节数对不上必须拒绝继续（磁盘满 / 截断）
    assert 'protected_bytes' in text and '"$protected_bytes" != "$source_bytes"' in text, (
        "保护后必须校验字节数 —— 半份备份 + reset = 又一次丢库"
    )


def test_redeploy_derives_paths_and_never_hardcodes_the_nas_path() -> None:
    """data 卷位置从仓库目录推导（与 compose 的 `../data` 同源），**不写死 NAS 绝对路径**。"""
    text = REDEPLOY.read_text(encoding="utf-8")
    code = "\n".join(_executable_lines(text))
    assert "/volume1/" not in code, "脚本正文不许出现 /volume1 这类 NAS 绝对路径（改过名的坑）"
    assert 'DATA_DIR="${NOOBBAOZI_DATA_DIR:-$REPO_DIR/data}"' in code
    assert "SCRIPT_DIR=$(CDPATH='' cd -- \"$(dirname -- \"$0\")\" && pwd)" in code


def test_sync_deploy_is_a_tombstone_that_refuses_to_run() -> None:
    """`sync-deploy.ps1` 已弃用：只留墓碑，最后一行 `exit 1`，旧实现必须删干净。"""
    text = SYNC_DEPLOY.read_text(encoding="utf-8")
    assert "DEPRECATED" in text
    code_lines = _executable_lines(text)
    assert code_lines[-1] == "exit 1", f"墓碑必须以 exit 1 结束，实际最后一行：{code_lines[-1]}"
    code = "\n".join(code_lines)
    for marker in ("git push", "gh auth token", ".tmp\\deploy", "clone"):
        assert marker not in code, f"墓碑里还留着旧实现：{marker}"


def test_docker_readme_documents_the_deploy_flow_and_the_guard() -> None:
    """README 必须讲清「部署源是开发仓」+「data/ 会被保护」，并标出 sync-deploy 已停用。"""
    text = DOCKER_README.read_text(encoding="utf-8")
    assert "redeploy.sh" in text
    assert "sync-deploy.ps1" in text and "DEPRECATED" in text
    for anchor in ("保护", "data/", "reset"):
        assert anchor in text


# =============================================================================
# 三、端到端：跑真正的 redeploy.sh --sync-only（需要 POSIX shell）
# =============================================================================


def _run_sync_only(
    shell: tuple[str, ...], script: Path, deploy: Path
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["NOOBBAOZI_SKIP_GIT_CONFIG"] = "1"  # 被测脚本不许改宿主机全局 gitconfig
    return subprocess.run(
        [*shell, str(script), "--sync-only", str(deploy)],
        cwd=str(deploy),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


def test_sync_only_keeps_the_index_through_a_real_reset(sandbox: Path) -> None:
    """端到端：真脚本 `--sync-only` → reset 之后索引库**逐字节还在**，且日志有两个计数。"""
    shell = _posix_shell()
    if shell is None:
        pytest.skip(SHELL_SKIP_REASON)

    payload = b"SQLITE-INDEX-PAYLOAD" * 2048
    deploy, _origin = _deploy_sandbox(sandbox, payload)
    index_path = deploy / "data" / "noobbaozi-3607705.sqlite3"

    done = _run_sync_only(shell, REDEPLOY, deploy)
    assert done.returncode == 0, f"stdout=\n{done.stdout}\nstderr=\n{done.stderr}"
    assert index_path.exists(), "reset 之后索引库没了 —— 保护段没生效"
    assert index_path.read_bytes() == payload, "索引库被改写了（保护只该拷贝，不该改写）"
    assert (deploy / "README.md").read_text(encoding="utf-8").startswith("dev repo"), (
        "reset 没生效？远端的新 README 应该在"
    )

    log = (deploy / "last-deploy.log").read_text(encoding="utf-8")
    assert "保护了 1 个文件" in log, f"保护日志不对：\n{log}"
    assert "恢复了 1 个文件" in log, f"恢复日志不对：\n{log}"
    assert "数据卷 OK：index=1" in log


def test_without_the_protection_the_index_is_lost_and_the_script_refuses_to_build(
    sandbox: Path,
) -> None:
    """**非永真证明（端到端）**：把保护块**整段删掉** → 数据必然丢，且脚本以退出码 3 拦住。

    同一条路径、同一份伪造历史，只去掉 `# >>> DATA-PROTECT` / `# <<< DATA-PROTECT`
    之间的内容（那就是「保护逻辑」本身）：`test_sync_only_keeps_the_index_through_a_real_reset`
    保护的那个文件就没了，而脚本在 build 之前就退出。
    """
    shell = _posix_shell()
    if shell is None:
        pytest.skip(SHELL_SKIP_REASON)

    text = REDEPLOY.read_text(encoding="utf-8")
    pattern = r"# >>> DATA-PROTECT.*?# <<< DATA-PROTECT\n"
    assert "# >>> DATA-PROTECT" in text and "# <<< DATA-PROTECT" in text, (
        "保护块必须带测试标记，否则这条非永真证明没有锚点"
    )
    broken_text, removed = re.subn(pattern, "", text, count=1, flags=re.S)
    assert removed == 1 and broken_text != text, "没能整段移除保护块"
    assert "PROTECT_COUNT" not in broken_text, "保护块没删干净"

    broken_script = sandbox / "redeploy-no-protect.sh"
    broken_script.write_text(broken_text, encoding="utf-8")

    payload = b"SQLITE-INDEX-PAYLOAD" * 2048
    deploy, _origin = _deploy_sandbox(sandbox, payload)
    index_path = deploy / "data" / "noobbaozi-3607705.sqlite3"

    done = _run_sync_only(shell, broken_script, deploy)
    assert done.returncode == 3, (
        f"保护被删掉后脚本必须以退出码 3 拒绝部署，实际 {done.returncode}：\n"
        f"stdout=\n{done.stdout}\nstderr=\n{done.stderr}"
    )
    assert not index_path.exists(), "保护被删掉后索引库居然还在 —— 事故复现失败"
    log = (deploy / "last-deploy.log").read_text(encoding="utf-8")
    assert "部署前检查失败" in log
