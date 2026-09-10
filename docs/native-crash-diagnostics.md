# 原生崩溃记录与复核

用户在约 90 次 CLI 调用中观察到一次 `move` 以 3221225477 (`0xC0000005`, ACCESS_VIOLATION) 退出，stdout/stderr 为空。用户检查后 revision 仍为 39，后续操作正常，重试成功。该信息是用户报告，尚无原始命令、时间、转储或可重复触发条件。

**状态：已在本轮捕获同类异常，根因未确定、未修复。** 在修复前基线源码对 107 条提交运行只读 `diff --json` 时出现 3221225477；faulthandler 栈为 `canonical_hash → Pydantic.model_dump`。Windows Application Error/Windows Error Reporting 将故障模块记录为 `_pydantic_core.cp312-win_amd64.pyd`（2026-09-10 14:10，本机时区 +07:00）。这定位了异常落点，尚不能区分依赖缺陷、解释器兼容、内存破坏等原因，更不能归因于杀软。

记录在 `build/review/history/performance.json` 和 `build/review/windows-events.json`。该轮测量遇错停止，未自动重试。随后独立执行 status 和 `history --audit --limit 1 --json` 均退出 0，HEAD 前后摘要一致、revision=106；见 `build/review/crash-status` / `crash-audit`。原有故障注入测试验证的是提交前后的一致性恢复，不证明进程不会发生原生崩溃。

后续独立固定次数的只读补测又在**旧版 events** 中捕获一次访问违规，Python 栈当时位于 JSON decoder。该项保留 9 个成功计时和 1 次失败，没有补样本替换。随后对归档工程做完整 audit 成功。

**修订版的最终全量 pytest 也发生访问违规**，进程退出 -1073741819（同为 `0xC0000005`），栈当时位于 `pathlib → Store.lock → set` 的测试调用。见 `build/review/pytest-final.log`。由此不能把故障限定为旧版全链读取，不能把单个栈顶当作根因，也不能宣称读放大修复消除了崩溃。本轮稳定性验收未通过，未反复重跑以挑选通过记录。

需要排查时，对下一次单独调用启用记录：

```powershell
python scripts/diagnose_cli.py --project <合成测试工程> --output <新的诊断目录> -- move <对象ID> --dx 1
```

此脚本只执行一次，不重试；启用 Python faulthandler，保留完整 argv、Python 版本、UTC 时间、退出码及十六进制值、stdout/stderr、调用前后 HEAD 内容与摘要。原生崩溃不保证能产生 Python 栈；超时终止也可能发生在已提交之后。工程写入成功但响应丢失时自动重试相对位移会重复修改，因此这里不做重试。

发生异常后，先保存整个测试工程及诊断目录，再执行只读 status 和 `history --audit --json` 检查；记录诊断后的人工操作。若知道大致时间，可在 Windows 事件查看器的“Windows 日志 → 应用程序”查找 Application Error/Windows Error Reporting 的对应记录，保留异常模块、版本和异常偏移。不要只凭某模块被加载就判断它是根因。

不应对真实 AVEVA 工程复现故障。当前提供的是记录工具与核查流程，不是已经复现或修复该访问违规的证据。
