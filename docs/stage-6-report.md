# 阶段 6 / EAS-HMI MVP 最终验收报告

日期：2026-09-10。项目：`D:\Codes\eas-hmi`。版本：0.1.0。

## 结论

**六阶段实验 MVP 的实现和本机验收已完成。**

干净 editable 安装、wheel 构建与另一新环境安装、完整测试、两次 A–G、严格校验、渲染、
清单/位图导出和正式 CLI 性能测量均通过。没有遗留未执行的必需 MVP 验收项。
验收以本文声明的环境及 SVG/编辑支持范围为边界，不代表生产 SCADA 或任意 SVG 格式兼容性认证。

最终证据目录：[20260910T061929Z-f55e5f94](../build/stage6/20260910T061929Z-f55e5f94/)。
逐项判定见 [最终验收表](final-acceptance.md)。

## 测试、安装与重复性

| 检查 | 实际结果 |
|---|---|
| 全量 pytest | **237 passed，0 failures，0 errors，0 skipped** |
| 测试时间 | JUnit **96.640 秒** |
| 整包行覆盖率 | **94.84%**（1893/1996 行），0 excluded，实际执行 ≥85% 门槛 |
| 新 editable 环境 | 从独立源码副本安装 `.[dev]` 成功；pip check 通过 |
| CLI help | editable/wheel 顶层 help + 23 个子命令 help 全部 exit 0 |
| wheel | 实际构建 `eas_hmi-0.1.0-py3-none-any.whl`，新 venv 仅安装 wheel 和运行依赖 |
| 模块路径 | editable 来自复制的 src；wheel 来自独立 venv 的 site-packages；user site 均禁用 |
| A–G | 两环境各 **75 次 CLI**，共 **150 次工作流调用**，A–G 均通过 |
| 工程重复性 | 两环境 source statistics/hash 与 final status/hash/revision 完全相同 |
| 导出重复性 | 3 semantic SVG +2 binding files +3 node assets +3 page PNG，**11 文件逐字节相同** |
| wheel/交付源码 | **26 个 Python 源文件**逐字节相同 |
| 检查步骤日志 | 主验收脚本 40 个外部步骤均 exit 0，原始命令、cwd、stdout/stderr 保存 |

原有事务/校验/同步/导出以及 7 个真实进程崩溃边界、并发和恢复测试均在新环境重新执行。
AST 检查覆盖运行包与 Demo 的 27 个文件，未发现 GUI/OCR/工业控制/模型 API 导入或 eval/exec 调用；
实际工作流的命令记录同时保留。这是代码路径审查，不声称监控了操作系统中的其他应用。

## 测量环境与性能

- Windows 11，报告的版本字符串 `Windows-11-10.0.26200-SP0`。
- Intel64 Family 6 Model 183 Stepping 1，32 logical CPUs。
- Python 3.12.7（本机 Anaconda 发行版基础解释器）；两个 venv 无 system/user site-packages 继承。
- Inkscape 1.4.2（f4327f4，2025-05-13），独立 CLI 后端。
- 工程：672 nodes、390 points、3 pages，保留 8 条真实审计提交。

每种命令先预热 3 次，再交替测量 query/validate，各 20 个新进程。
时间包括 Python 启动、模型/历史读取、操作和完整 JSON 输出捕获；不调用 LLM 或 rasterization。
p50 使用中位数，p95 使用 nearest-rank（20 个样本中排序后的第 19 个）。

| 命令 | 样本 | p50 | p95 | 最大值 | 判定 |
|---|---:|---:|---:|---:|---|
| query：cooling/pump/equipment_card，20 个完整 card JSON | 20 | **295.787 ms** | **303.188 ms** | 304.242 ms | 通过 p95≤1000ms |
| validate --strict，0 issues | 20 | **302.586 ms** | **309.641 ms** | 317.373 ms | 通过 p95≤1000ms |

原始样本的 p50/p95 另外重新计算核对过。测量前后 HEAD、operations/events 文件 hash 和工程 status 均不变。
性能门槛沿用阶段 1 的暂定 p95≤1 秒；这里报告的是本机规模下的结果，不外推到任意机器/任意长历史。

## 合成工程、错误样本与 Demo

三页面为 overview、cooling、electrical，1920×1080。
包含 **20 pumps、20 valves、30 sensors、12 UPS、8 generators**，共 **90 设备、390 点位、672 节点**。
5 种模板、全部 9 kind、95 opaque SVG 图标；点位 tag 均以 `SYN.DC.` 开头。
所有图形/数据由生成器创建，不读取客户工程或真实 AVEVA 文件。

每个安装环境执行相同 A–G：

1. query/inspect/context 查明 20 个 cooling pump cards、缺 fault 的 CHWP_CARD_03/07/14、CHWP_07 的 6 点位和关联。
2. 根据 registry 选择 fault point，经 bind 修复三条 card required binding；strict validate 0 issues。
3. batch/distribute 将 20 cards 排为 10 列×2 行、164×94、横纵 gap=20。
4. 三页 no-op sync 不提交；在 SVG 中模拟 CHWP_CARD_07 (+12,+8) 与 TITLE_COOLING 文字改动，明确以 human 事务同步。
5. 分别保留 B/C/D 的全部实体语义 diff 与可读 diff。
6. undo 最近 human 操作，恢复 D 前语义 hash，B/C 修改保留，最终 revision=7。
7. 导出三页 semantic SVG、660 条 binding CSV/JSON、节点 SVG/PNG/BMP 与三页 PNG。

最终两个工程的 canonical hash 均为：

```text
9d4dbdcb0fcb4a0a1a47d45f03562496ee5e2b32545d40bfde6b4a3643d1ebcc
```

每次工作流还验证以下故意错误及回滚：

| 预留错误 | 检出/处理 |
|---|---|
| 主工程 3 missing required | 默认 WARNING、strict ERROR；B 后清零 |
| 2 invalid equipment refs | 两条准确 ERROR，初始化拒绝 |
| 2 invalid point refs | 两条准确 ERROR，初始化拒绝 |
| 1 duplicate ID fixture | ERROR，初始化拒绝 |
| 2 out-of-bounds | 默认 WARNING；单元测试另验证 ERROR 策略 |
| 1 invalid geometry fixture | 负 width 导致 schema ERROR，初始化拒绝 |
| 非法 batch/stale revision | HEAD 字节、hash、revision 均不变 |

所有输入 JSON 的 hash 在工作流前后相同；两环境各有 8 条审计提交、42 条非初始化事件。

## 最终交付与证据

- [完整 README 与快速开始](../README.md)
- [逐项验收表](final-acceptance.md)、[最终复验步骤](final-verification-guide.md)
- [结构化最终结果](../build/stage6/20260910T061929Z-f55e5f94/report.json)
- [全部安装/检查步骤](../build/stage6/20260910T061929Z-f55e5f94/steps.json) 与 [原始日志](../build/stage6/20260910T061929Z-f55e5f94/logs/)
- [JUnit](../build/stage6/20260910T061929Z-f55e5f94/tests.xml)、[完整 pytest 输出](../build/stage6/20260910T061929Z-f55e5f94/logs/pytest.log)、[覆盖率](../build/stage6/20260910T061929Z-f55e5f94/coverage.json)
- [正式性能原始样本](../build/stage6/20260910T061929Z-f55e5f94/performance.json)
- [导出重复性检查](../build/stage6/20260910T061929Z-f55e5f94/export-repeatability.json)、[源码一致性/范围审查](../build/stage6/20260910T061929Z-f55e5f94/source-and-scope-audit.json)
- [editable A–G 记录](../build/stage6/20260910T061929Z-f55e5f94/editable-demo/report.json)、[wheel A–G 记录](../build/stage6/20260910T061929Z-f55e5f94/wheel-demo/report.json)
- [wheel 的 75 次 CLI 命令与输出](../build/stage6/20260910T061929Z-f55e5f94/wheel-demo/cli-transcript.json)
- [最终可读模型快照](../build/stage6/20260910T061929Z-f55e5f94/wheel-demo/final-model.json)、[完整提交工程](../build/stage6/20260910T061929Z-f55e5f94/wheel-demo/project/)
- [SVG/PNG/BMP/CSV/JSON 文件索引](../build/stage6/20260910T061929Z-f55e5f94/wheel-demo/G/exports.json)
- [实际安装 wheel](../build/stage6/20260910T061929Z-f55e5f94/dist/eas_hmi-0.1.0-py3-none-any.whl)
- [实测依赖版本](../requirements-tested.txt)、[JSON Schema](../schemas/project.schema.json)

wheel 的 SHA-256：

```text
3c025bc1463494d60f175006805080fb3c46ebaf2986410b7c41518a4ca9fe44
```

完整项目 ZIP 由 `deliverables/latest.json` 指向。包内包含源码、测试、脚本、schema、示例、文档、
实测版本和阶段 2–6 的全部最终成功证据、真实 Canonical 提交链及 wheel。
每个文件都记录并核对 SHA-256，另检查 ZIP CRC；外部提供 ZIP 的 SHA-256。
虚拟环境、隔离源码副本、缓存、被取代的运行与旧 ZIP 不打包，依照 README 可重建。

## 已知限制与明确边界

- 已验收的是本机 Windows/Python/Inkscape 组合。新环境是基于本机 Python 创建的干净 venv，
  不是全新 Windows 虚拟机，也未覆盖全部平台、字体或编辑器版本。
- SVG importer/sync 支持声明的子集。复杂 CSS、外部资源、活动内容、未知语义结构、skew/reflection、
  多行富文本或结构重排可能被明确拒绝；共享图形依赖可保留为整体 opaque。
- Semantic SVG 没有承载完整 registry/metadata/expression。缺失的 tag/datatype/template required roles
  会显式占位并警告，不能把 SVG import 当作无损恢复整个工程备份。
- 节点连接采用显式线几何，不自动布线；布局只处理同 parent 的未旋转对象；状态和数值都是设计态。
- Undo 是最近一次事务撤销，并记录新事务；连续 undo 会撤销上一次 undo。
- 故障验收覆盖进程中断和并发，不声称硬件断电保证。当前 p95 结果不保证无限增长历史下的延迟。
- 未接入或写入真实 Plant SCADA、PLC、OPC UA、生产设备或模型 API；没有 GUI automation、Web/MCP 服务。
- 未发布 GitHub 远端。交付方式为完整本地工程、源码 ZIP 和可安装 wheel。

所有必需 MVP 验收项已完成。本次在最终报告与交付包完成后结束，不继续扩展非目标功能。
