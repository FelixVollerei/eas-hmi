# 阶段 6 最终验收进度

工作目录：`D:\Codes\eas-hmi`。阶段 5 基线：237 tests、94.84%、75 次 CLI A–G、672 nodes / 390 points。

- [x] 准备隔离源码复制、全新 editable 和 wheel 环境的验收脚本
- [x] 准备 CLI 完整进程耗时测量脚本（各 20 次 + 各 3 次预热，p95 ≤1s）
- [x] 干净 editable 安装、pip check、全部命令 help
- [x] 237 tests passed，94.84%，隔离环境 A–G 通过
- [x] wheel 新环境 A–G 通过；final hash 一致；11 导出文件 byte-identical
- [x] query p95 303.188ms、validate p95 309.641ms，均通过 ≤1s
- [x] 最终 README、验收矩阵、报告及完整交付包

环境和源码副本位于 `build/environments/`、`build/clean-checkouts/`，与原 `.venv` 隔离。
安装日志、模块加载路径、测试、两次 Demo、wheel 与 performance 原始样本保留在 `build/stage6/<run-id>/`。

最终状态：完成。结果见 [stage-6-report.md](stage-6-report.md)，无后续必需 MVP 工作。
