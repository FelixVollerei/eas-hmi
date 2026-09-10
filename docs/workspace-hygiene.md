# 源码、证据与本机环境

本地 Git 从交付现状开始记录：基线 `a5460a34e80725fa3c17937dda8d5542caab1800`。它不是六阶段开发过程的重建。后续复核修复与实验协议可以相对该基线评审。未设置 GitHub remote，也没有上传。

提交范围是 src、tests、scripts、docs、examples、schemas 和根配置。`.venv`、build、dist、deliverables、Python/pytest/ruff 缓存、coverage 和 egg-info 均不进入 Git。`.venv` 是仍在使用的本机依赖环境，体积不等于源码体积；不把它放进交付 ZIP。

`scripts/archive_scratch.ps1` 默认只列出计划；加 `-Apply` 把旧 `build/environments` 和 `build/clean-checkouts` 移到相邻的 `eas-hmi-local-artifacts/archived-时间/`，记录路径与字节数。它不删除测试历史、用户生成工程或当前 `.venv`。移动后的历史 venv 不保证入口脚本可执行，只作为旧安装产物保留；需要复测时重建。

脚本还会归档源码下的 Python 缓存、pytest/ruff 缓存、coverage 工作文件、egg-info 和构建临时副本；不遍历当前 .venv。自动审批曾拒绝批量删除缓存，因此本轮采用保留原文件的归档方式，不删除这些文件。后续运行 Python/测试/构建时缓存可能重新生成，但继续被 Git 和交付包排除。

新的 `verify_stage6.py` 默认在相邻 `eas-hmi-local-artifacts` 建立安装环境和源码副本，也可设置 `EAS_HMI_SCRATCH`；验收报告仍写本项目 build/stage6。压缩交付排除这些环境及缓存。

复核测量中的 107 提交工程会封装为经 CRC/逐文件摘要核验的 ZIP，原目录移到外部归档；原始性能 JSON 和原生崩溃记录仍保留在 build/review。没有删去失败测量来使报告变好。

历史文档中的 venv 绝对路径是当时的安装证据，不是移动归档后的可运行入口。当前启动仍可使用项目 `.venv/Scripts/eas-hmi.exe`；新解压环境请按 README 重建依赖。
