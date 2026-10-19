#!/usr/bin/env pwsh
# =============================================================================
# ⛔ DEPRECATED（2026-10-20）—— 本脚本已停用，**运行即退出**（exit 1）。
#
# 为什么停用
# ----------
# 部署源已经改回**开发仓本身**（`AraragiEro/NoobBaozi`，线性历史）：
#   - 开发机：正常 commit + push（`data/` 已被 `.gitignore` 排除，见仓库根 `.gitignore`）
#   - NAS   ：`sudo sh docker/redeploy.sh` 直接对齐开发仓，由它负责保护/恢复 `data/` 卷
#
# 继续用本脚本的后果（不是理论风险，是 2026-10-20 的真实事故）：
#   它会把 `data/noobbaozi-<build>.sqlite3` 用一份**独立历史**重新入库。NAS 上的
#   `git reset --hard origin/main` 于是把「旧历史跟踪、新历史不存在」的文件删掉 ——
#   14.3 MB 的索引库没了，容器以退出码 2 起不来（entrypoint：既没有索引库也没有 SDE zip）。
#
# 为什么保留文件而不是删掉
# ------------------------
# `docker/README.md`、历史提交与主人的笔记里都还指向这个文件名。留一块**墓碑**（能运行、
# 但立刻拒绝并说明原因）比留一个死链更能讲清「为什么不该再推快照仓」。旧实现已删除：
# 没有可被误改回去的代码，只剩结论。
#
# 现在该做什么
# ------------
#   开发机：git commit && git push origin main
#   NAS   ：sudo sh <仓库目录>/docker/redeploy.sh     # 自动保护/恢复 data/ 卷，空卷直接退出
# =============================================================================
Write-Host "⛔ [DEPRECATED] sync-deploy.ps1 已停用。" -ForegroundColor Red
Write-Host "   部署源就是开发仓本身：开发机 push 到 AraragiEro/NoobBaozi，NAS 上跑 docker/redeploy.sh。" -ForegroundColor Yellow
Write-Host "   本脚本会推送一份跟踪 data/*.sqlite3 的独立历史 —— NAS 上的 git reset --hard 会把索引库删掉" -ForegroundColor Yellow
Write-Host "   （2026-10-20 真实事故：data/noobbaozi-3607705.sqlite3 被删，容器退出码 2）。" -ForegroundColor Yellow
exit 1
