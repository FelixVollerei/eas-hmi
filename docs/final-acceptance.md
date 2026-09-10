# 最终验收逐项结果

**本表是 0.1.0 历史验收。当前 0.1.1 稳定性验收未通过，详见 [复核报告](review-corrections.md)。**

证据基线：2026-09-10，`build/stage6/20260910T061929Z-f55e5f94/`。
范围为 [原始实验 MVP 要求](requirements-original.txt) 及已明确的 SVG/布局支持子集。
详细环境、测量数据、文件路径与限制见 [最终报告](stage-6-report.md)。

最新更正见 [复核报告](review-corrections.md)。以下保留阶段 6 原始测量，不视为修订源码的新安装证据。

## Functional 16 项

| 项 | 判定 | 实际证据 |
|---|---|---|
| F01 安装 | 通过 | 新 venv 的 `pip install -e .[dev]` exit 0；另一个新 venv 仅安装构建 wheel 与运行依赖；两边 pip check 通过 |
| F02 CLI help | 通过 | editable/wheel 顶层 help 与 editable 的 当时抽查的 23/24 个子命令 help exit 0（遗漏 status；修订后补验全部 24 个） |
| F03 生成项目 | 通过 | 两环境从新目录生成 3 页、672 nodes、390 points、90 equipment，source hash 与统计一致 |
| F04 query/inspect/context | 通过 | A 正确返回 20 个 pump cards、CHWP_07 的节点/点位/连接；有限 context 标记截断 |
| F05 单对象编辑 | 通过 | 全量 tests 的 set/move/resize、锁定、非法参数、记录与 hash 用例 |
| F06 batch/layout | 通过 | C 两条结构化命令完成 20 cards 的 164×94、10×2 网格、双向 gap=20；非目标局部数据不变 |
| F07 bind/unbind | 通过 | B 按 registry 修复 3 条 required fault；未知 point/非法角色拒绝；unbind 与模板规则测试 |
| F08 校验 | 通过 | 3 missing required、2 equipment refs、2 point refs、1 duplicate、2 bounds、1 geometry；独立 fixture 准确检出 |
| F09 render | 通过 | 三页有效、自包含 semantic SVG；模型不变；独立安装产物 byte-identical |
| F10 metadata | 通过 | 9 kind stable ID、equipment/binding/template/endpoints；未嵌入整个模型 |
| F11 sync | 通过 | no-op、translation、x/y/frame resize、rotation/matrix、text、visibility；unsupported 改动整体拒绝；D human 事务 |
| F12 diff | 通过 | B/C/D 每次事务保留实体字段 JSON diff 及 CLI 可读输出 |
| F13 undo | 通过 | D 后 undo 精确恢复 D 前 hash，保留 B/C 与新增审计；崩溃/恢复/opaque 测试 |
| F14 manifest | 通过 | CSV/JSON 各 660 行、10 必需字段，逐行一致、顺序与 Unicode/CSV 转义测试 |
| F15 assets | 通过 | node/template/page 选择器测试；真实 PNG/BMP 解码、尺寸、alpha/RGB、裁剪；失败不发布部分资产批次 |
| F16 A–G | 通过 | editable 与 wheel 各 75 次实际 CLI，七任务全通过，final hash/revision 一致 |

## 非功能验收

| 项 | 判定与证据 |
|---|---|
| 覆盖率 | 237 passed、0 skipped，94.8397%，1996 语句行、0 excluded lines，≥85% gate 通过 |
| 性能 | 672 nodes/390 points/8 提交，每种命令 3 次预热 +20 样本；query p95=303.188ms、validate p95=309.641ms，均≤1000ms |
| Determinism | 相同模型重复 render 测试；两个独立安装的 11 个 SVG/CSV/JSON/PNG/BMP 导出文件逐字节一致 |
| Data integrity | ERROR/revision 冲突/锁定/非法批量后无部分工程提交；原始输入 hash 不变 |
| Recovery | 7 个实际进程中断边界、历史投影损坏恢复、并发写入、HEAD/父链/校验和用例全部在干净环境重跑 |
| Agent-native | A–G 代码路径为 subprocess CLI；只有 D 模拟修改 tagged XML；无 OCR/GUI/模型 API 集成。AST 导入/动态执行检查与原始命令记录佐证 |
| Package | 实际 wheel 安装运行；26 个 wheel 源码文件与交付源码字节一致；完整 ZIP 逐项 hash 与 CRC 核对 |

## 原始章节对应

| 需求 | 最终判定与落点 |
|---|---|
| R01–R03 原则、技术、结构 | 通过；Canonical/CLI/事务主通道，Python 技术栈，模块职责分离，无禁止框架或服务 |
| R04–R06 模型、设备点位绑定、模板 | 通过；Pydantic schema、9 kind、独立 registry、结构化 role/ref/expression、required/optional |
| R07–R11 Opaque、semantic SVG、import/render/sync | 在声明子集内通过；unsupported 明确拒绝或 composite opaque 保留，SVG 不承担完整模型备份 |
| R12–R13 CLI、编辑 | 通过；24 个命令；当时 help 脚本抽查 23 个，遗漏 status、完整查询与有界 context、single/batch/layout，无 eval |
| R14–R17 Transaction/history/diff/undo | 通过；原子 HEAD、不可变提交、可恢复日志、语义 diff、最近事务撤销 |
| R18–R21 validate/manifest/assets/events | 通过；全部规则用例、实际文件、原始 events 与 watch 用例 |
| R22–R23 Synthetic/Demo | 通过；数量超下限、全部错误最小数量、两次新环境 A–G |
| R24–R25 总验收/测试 | 通过；F01–F16、上表非功能项、完整 pytest/coverage |
| R26 README | 完成；问题、架构、CLI-first、ZIP/安装、wheel、Demo、测试、恢复、范围与限制 |
| R27–R28 非目标/决策顺序 | 遵守；未加入工业控制/SCADA写入/GUI/网站/MCP/模型服务；完整性和可逆性优先 |
| R29–R30 交付与实际复验 | 完成；源码ZIP、wheel、所有模块、三页Demo、测试、完整README、一键脚本、实际验收报告 |

无未执行的必需 MVP 验收项。未覆盖的平台/外部系统，以及 SVG 非支持特性，均列在最终报告的限制中，
不作为已验证能力。当前性能结果也不外推到无限增长的历史和所有硬件。
