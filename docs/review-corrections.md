# 交付后复核报告 / 0.1.1 候选版

日期：2026-09-10。项目：`D:\Codes\eas-hmi`。

发布补记：本报告完成后，应用户要求将源码公开到 [FelixVollerei/eas-hmi](https://github.com/FelixVollerei/eas-hmi)。下述测试结果保持原记录；GitHub 不包含本机原始日志和 build 证据目录，相关链接需在完整本地交付包中查看。

**结论：读放大、INFO、CLI 说明和文档问题已修订，实验协议已交付；原生崩溃仍未解决，稳定性验收未通过。不能标记本轮“全部通过”。**

## 逐项处理

| 反馈 | 处理与当前状态 |
|---|---|
| 历史读放大 | 普通 history/diff/events 复用已校验的 JSONL 投影，只解析所需 operation；空 events 仅校验当前提交；正常发布不再重扫历史模型。保留 `history --audit` 全链校验。详见 [复杂度和完整性边界](history-scaling.md) |
| INFO | 新增默认 `EMPTY_PAGE` INFO 提示；普通/strict 都不因此失败；实测越界策略 ERROR/WARNING/INFO 的 CLI 输出与退出码。原来的 INFO 可配置路径确实存在，但缺少默认规则及明确验收证据 |
| 0xC0000005 | 本轮旧版基线捕获 2 次，修订版全量测试捕获 1 次。已保留栈、Windows 事件和一致性检查；未自动重试写命令。根因未确定，不能宣称修复 |
| 文档硬错误 | 当前命令数 24；旧阶段 6 脚本确实只测了 23 个 help，遗漏 status，因此保留历史事实并补验全部 24 个；context 默认 30；阶段 4/5 的未完成项改为明确的历史记录；更新 Git/远端状态 |
| --json | 保留兼容参数，由公共 guarded 包装器统一消费并设定格式状态；所有 help 明确大多数命令默认 JSON、diff 默认文本、watch 为 JSONL。没有暗示普遍存在人类可读模式 |
| 仓库卫生 | 补建本地 Git，诚实记录交付基线和后续变更；未设置远端或上传。归档旧安装环境/副本约 162.9 MB（十进制），两个长历史工程和基线源码约 119.1 MB，保留已核验 ZIP。当前 .venv 与验收证据保留并从 Git 排除 |
| Agent 对照实验 | 已给题目、两套可直接粘贴的 prompt、计量口径、记录模板及评分脚本；**没有运行两组 Agent 实验** |

## 长历史实测

合成工程 672 节点、390 点。使用同一 Python 3.12.7 / 本机依赖，在同一工程上交替启动旧版/修订版 CLI。每项 1 次预热、10 次计划测量；包含进程启动和完整输出，p95 用 nearest-rank（10 次时即最大值）。不是冷盘测试。query 返回 20 张 pump card；validate 使用默认模式，保留原始合成工程的 3 个缺 fault WARNING。两边成功调用的 stdout SHA256 一致。

| revision / 提交数 | 提交库 MB | diff p95，旧→新 ms | history 最近10条，旧→新 ms | events 最近1 revision，旧→新 ms | 空 watch --once，旧→新 ms |
|---|---:|---:|---:|---:|---:|
| 7 / 8 | 5.91 | 408.5 → 292.9 | 434.1 → 324.4 | 418.2 → 308.6 | 473.8 → 342.7 |
| 42 / 43 | 23.48 | 999.0 → 308.5 | 946.7 → 311.0 | 913.5 → 291.9 | 944.4 → 351.7 |
| 106 / 107，独立补测 | 55.61 | 2016.2 → 304.0 | 2022.8 → 313.5 | 2080.7 → 327.0 **†** | 2000.7 → 322.4 |

**† 旧版 events 为 9 次成功 +1 次访问违规，新版为 10 次成功。表中耗时仅对成功返回样本计算，失败没有删除或补样本替换。** 首轮测量还在 r106 的旧版 diff 第一个正式样本中崩溃并停止，该轮不是通过记录。补测为独立的固定次数只读测量，也明确标为 `completed_with_failures`。

补测 query p95 334.4→327.1 ms，validate 333.0→323.1 ms。主要收益在去除重复历史模型读取，并非加速当前模型本身。原始数据：[首轮（包含失败）](../build/review/history/performance.json)、[独立补测（包含失败）](../build/review/history-followup/performance.json)。测量工程已打包到各目录的 store-evidence.zip，校验和与归档位置在 archive.json。

**仍有规模上限**：r106 两份投影合计 3.77 MB，非空历史读仍需读入并校验它们；写入仍整文件重写投影；提交仍嵌入完整模型。存储/恢复不是常数复杂度，没有实现日志分段或 checkpoint+delta。不得将本轮结果外推成长期历史规模保障。

## 验证证据与失败

| 验证 | 真实结果 |
|---|---|
| 针对性历史/INFO/CLI 与原事务测试 | 47 passed，包括 7 个真实子进程中断边界、并发写、投影损坏恢复；没有取消原有断言 |
| 首次全量回归 | **244 passed，1 failed**，125.67 秒；失败是新评分工具把 render 返回的 bytes 交给 write_text。已修为 write_bytes；旧日志/JUnit 保留 |
| 评分器最终针对性复测 | **2 passed**，包括满分结果、额外修改扣分、SVG 不兼容计 NA、缺输出计 0；是工具单元测试，不是 Agent 实验 |
| 修订后的全量回归 | **未完成，0xC0000005 中断**。当前版本也受影响，不能称“245 项全部通过” |
| 覆盖率 | 首次完整运行 94.91%（1920/2023），但该次含上述评分器失败；中断的最终运行没有完整覆盖率报告，不能借用旧数字称最终 gate 通过 |
| 新 wheel 安装 | 0.1.1 新建独立运行环境安装、pip check、模块路径/版本检查通过 |
| CLI help | 新 wheel 顶层和全部 **24 个子命令**通过，包含此前遗漏的 status |
| A–G | 新 wheel 完整 **75 次真实 CLI 调用**通过；严格校验、3 页 SVG、清单及 PNG/BMP 导出、undo/hash 均符合原工作流断言 |
| wheel 对应源码 | wheel 中 26 个 Python 文件与当前 src 字节一致，摘要见 wheel-source-check.json |
| 静态检查 | Ruff 的 F/E9 检查通过；未把它描述为额外代码风格认证 |

证据均位于 `build/review/`：pytest.log/tests.xml/coverage.json 是首次完整但有一个失败的运行；pytest-final.log 是原生崩溃的最终运行；scorer-final.log 是修正后的工具测试；install/result.json 与 wheel-demo/report.json 是独立 wheel 验证。**不得忽略文件名中的 final 后直接推断通过，必须读取结果。**

## 原生崩溃结论

第一起本轮旧版 diff 崩溃的栈位于 Pydantic model_dump，Windows 事件将故障模块记录为 `_pydantic_core.cp312-win_amd64.pyd`；第二起旧版 events 的栈位于 JSON decoder；当前版 pytest 中断时栈位于 pathlib/Store.lock。异常落点不等于根因，不据此归因于杀软、Pydantic 或某一条业务命令。

两个长历史测量工程在异常后的独立 status/完整 audit 检查通过，HEAD 摘要不变，revision=106。当前测试使用临时工程，崩溃日志保留；这不能推出所有原生崩溃都不会损坏数据。

下一项待完成工作是**稳定性专项排查**：固定最小复现及随机触发轨迹，在隔离环境逐一比较解释器与原生依赖版本，必要时分析原生转储，并复核失败时的提交边界。不得同时更换多个组件后声称找到根因。诊断工具及使用方法见 [原生崩溃记录](native-crash-diagnostics.md)。本轮没有提交未经证据支持的“崩溃修复”，也没有做自动写重试。

## 手动 Agent 实验入口

- [题目、运行流程、计量与 100 分规则](agent-comparison/protocol.md)
- [EAS-HMI 组 prompt](agent-comparison/prompt-semantic.md)
- [传统视觉组 prompt](agent-comparison/prompt-visual.md)
- [单次运行记录模板](agent-comparison/run-ledger.json)

主任务为 20 张卡片的平移、1 张卡片的宽度和标题修改；绑定任务列为需等价传统接口的扩展项。token 必须取平台真实 usage，缺失填 NA，不以字符数、截图数或账户用量百分比冒充 token。运行前由评估者验证 Inkscape 保存兼容性，本次尚未做真实 GUI 预检。

完整源码包包含本报告、原始失败证据、测量工程 ZIP、0.1.1 wheel 和 Git bundle；历史阶段 6 的旧 wheel/测试结果保留为历史，不是新版本的通过证明。解压后可以重建环境；本地 Git 增量评审以 `a5460a34e80725fa3c17937dda8d5542caab1800` 为交付前基线。**本轮至此交付候选修订，不发布为稳定版。**
