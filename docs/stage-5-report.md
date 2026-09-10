# 阶段 5 验收报告 — 合成数据中心与 A–G 工作流

日期：2026-09-10。项目：`D:\Codes\eas-hmi`。

## 结论

**阶段 5 已实现并实测通过，提交用户验收，本次停止。阶段 6 尚未开始。**

合成工程满足所有规定的数量下限；A–G 使用实际安装版 CLI 从新目录运行成功。
已提供错误样本、逐次命令记录、语义差异、撤销证明、完整 Canonical 工程和导出产物。

## 实际验收结果

最终运行：[20260910T061304Z-894fbc0d](../build/stage5/20260910T061304Z-894fbc0d/)。

| 项目 | 结果 |
|---|---|
| 全量 pytest | **237 passed，0 failures，0 errors，0 skipped** |
| 本阶段新增测试 | 15 项；之前 222 项同时通过 |
| 测试耗时 | JUnit 记录 **96.679 秒** |
| 整包行覆盖率 | **94.84%**，1893/1996 行，未排除代码；实际 ≥85% 门槛通过 |
| 安装版 CLI | **75 次调用**，所有实际退出码与预期一致 |
| 完整工作流耗时 | **38.703 秒**，包括 A–G、错误样本及最终审计核对 |
| A–G | 七项全部通过 |
| 最终 validate --strict | **0 issues** |
| Revision / audit | 0 → 7，8 条提交（含 init），42 条非初始化事件 |
| 源输入保护 | 所有输入 JSON 的 SHA-256 运行前后相同 |
| 错误操作 | 非法批量负宽度、stale revision 后 HEAD 字节/hash/revision 不变 |
| GUI/OCR | screenshot、OCR、mouse、keyboard GUI action 均为 0；脚本为 CLI/subprocess/XML 路径 |
| pip check / Ruff | 通过；本阶段文件 E4/E7/E9/F/I 与格式检查通过 |

PNG 为 Inkscape CLI 的导出产物，设备/点位/关系的理解与修改通过 query/inspect/context/事务完成。
Task D 使用 lxml 模拟 tagged SVG 变换，未通过 GUI 操作 Inkscape。

## 合成工程规模

| 内容 | 要求 | 实际 |
|---|---:|---:|
| 页面 | overview / cooling / electrical | 3 页，均为 1920×1080 |
| pump | ≥20 | 20 |
| valve | ≥20 | 20 |
| sensor | ≥30 | 30 |
| UPS | ≥12 | 12 |
| generator | ≥8 | 8 |
| equipment 合计 | ≥90 | 90 |
| points | ≥300 | **390** |
| nodes | ≥500 | **672** |

页面节点数为 overview 35、cooling 493、electrical 144。
全部 9 kind 均存在，包括 95 opaque SVG 图标、180 状态指示器、90 数值槽、79 连接。
97 个 equipment_card 中，90 个关联设备，另外 7 个是概览信息卡。
图形由原创几何生成，点位以 `SYN.DC.` 为前缀，不使用客户资产、真实点表或 AVEVA 工程。

## A–G 逐项结果

| Task | 验收结果 |
|---|---|
| A — Understand | cooling 的 pump equipment cards = **20**；缺 fault：**CHWP_CARD_03、07、14**。CHWP_07 的 6 个关联节点、6 点位和两侧连接通过 CLI 查明；context 限制 40 节点并标记 truncated。 |
| B — Bind | 从 inspect 返回的 equipment point registry 选择唯一 role=fault 的点，执行 3 次 bind；剩余缺 fault = **0**，strict validation = 0 issues。 |
| C — Layout | 1 次 batch set width=164，1 次 distribute 10 列；20 cards 成为 2 行，尺寸 164×94，横纵 gap 均 **20**。核对了每个节点坐标。 |
| D — Human edit | 三页 no-op sync 均无提交；CHWP_CARD_07 的 (+12,+8) 与 TITLE_COOLING 的文字编辑仅影响这 2 个节点，以 **human** 事务写入。 |
| E — Diff | 分别保留 B 的 3 份、C 的 2 份、D 的 1 份语义 diff，并保存 CLI 可读版本。 |
| F — Undo | 撤销 D，hash 精确恢复至 D 前值；revision 从 D 前的 5 经 6 增至 7；B/C 改动保留。 |
| G — Export | 3 页 semantic SVG，CSV/JSON 各 **660 条 binding**，CHWP_CARD_07 的 SVG/PNG/BMP，另有三页 1920×1080 PNG 预览。文件可解析/解码、尺寸和格式核对通过。 |

CHWP_07 直接关联：CHWP_CARD_07、CHWP_07_ICON、CHWP_07_RUN_IND、CHWP_07_FAULT_IND、
CHWP_07_VALUE、CHWP_07_DIVIDER；连接对象 CHWP_LINK_06_07、CHWP_LINK_07_08，
相邻 card 为 CHWP_CARD_06 和 CHWP_CARD_08。
点位角色为 run/fault/local/remote/command/speed，完整 tag 见 [A/answers.json](../build/stage5/20260910T061304Z-894fbc0d/workflow/A/answers.json)。

Task F 的前后语义 hash 均为：

```text
9d4dbdcb0fcb4a0a1a47d45f03562496ee5e2b32545d40bfde6b4a3643d1ebcc
```

主工程初始预留的缺项位于 card 模板；fault indicator 子节点已有点位引用。
660 是实际 binding 行数，多节点可以引用同一个 point，因此不等于 390 个唯一点位数。

## 故意错误与保护验证

| 样本 | 实际检出 |
|---|---|
| 主工程缺 required | 3 × MISSING_REQUIRED_BINDING；默认 WARNING，strict ERROR；Task B 全修复 |
| invalid-equipment.json | 2 × INVALID_EQUIPMENT_REFERENCE，ERROR |
| invalid-point.json | 2 × INVALID_POINT_REFERENCE，ERROR |
| duplicate-id.json | 1 × DUPLICATE_ID，ERROR |
| out-of-bounds.json | 2 × OUT_OF_BOUNDS，默认 WARNING；单元测试验证改用 ERROR 策略 |
| invalid-geometry.json | 1 × SCHEMA_VALIDATION（负 width），ERROR |

5 个独立样本以 required bindings 完整的基线生成，准确隔离相应规则。
4 个 ERROR 样本通过实际 `init --from-model` 被拒绝，没有创建已提交 HEAD。
全部校验记录见 [fixture-validation.json](../build/stage5/20260910T061304Z-894fbc0d/workflow/fixture-validation.json)。

## 文件与证据

- [结构化验收结果](../build/stage5/20260910T061304Z-894fbc0d/report.json)
- [pytest 日志](../build/stage5/20260910T061304Z-894fbc0d/tests.log)、[JUnit](../build/stage5/20260910T061304Z-894fbc0d/tests.xml)、[覆盖率](../build/stage5/20260910T061304Z-894fbc0d/coverage.json)
- [75 次 CLI 完整命令/输出/退出码](../build/stage5/20260910T061304Z-894fbc0d/workflow/cli-transcript.json)
- [A–G 完整结果](../build/stage5/20260910T061304Z-894fbc0d/workflow/report.json)
- [B/C/D 语义 diff 集合](../build/stage5/20260910T061304Z-894fbc0d/workflow/E/semantic-diffs.json)
- [Undo hash 证明](../build/stage5/20260910T061304Z-894fbc0d/workflow/F/undo.json)
- [最终 Canonical 快照](../build/stage5/20260910T061304Z-894fbc0d/workflow/final-model.json)
- [完整 Canonical 提交工程](../build/stage5/20260910T061304Z-894fbc0d/workflow/project/)
- [导出文件索引](../build/stage5/20260910T061304Z-894fbc0d/workflow/G/exports.json)
- [绑定 CSV](../build/stage5/20260910T061304Z-894fbc0d/workflow/G/bindings/bindings.csv)、[绑定 JSON](../build/stage5/20260910T061304Z-894fbc0d/workflow/G/bindings/bindings.json)
- [初始合成模型](../examples/datacenter/model.json)、[错误样本索引](../examples/datacenter/broken/index.json)
- [生成器](../scripts/generate_demo.py)、[A–G 脚本](../scripts/demo_agent_workflow.py)、[阶段 5 一键验收](../scripts/verify_stage5.py)
- [运行与恢复说明](stage-5-demo.md)、[依赖版本](environment-stage-5.txt)

页面 PNG：
[overview](../build/stage5/20260910T061304Z-894fbc0d/workflow/G/page-previews/r7-1639e28b6f714994994967e554bf67f8/page-6f76657276696577.png)、
[cooling](../build/stage5/20260910T061304Z-894fbc0d/workflow/G/page-previews/r7-1639e28b6f714994994967e554bf67f8/page-636f6f6c696e67.png)、
[electrical](../build/stage5/20260910T061304Z-894fbc0d/workflow/G/page-previews/r7-1639e28b6f714994994967e554bf67f8/page-656c656374726963616c.png)。

完整项目 ZIP 由 `deliverables/latest.json` 指向，包含所有源码、测试、脚本、schema、示例、文档，
以及阶段 2–5 的最终成功验收产物和原审计工程。包内 `PACKAGE-MANIFEST.json` 提供逐文件 hash，
打包时检查 ZIP CRC 和每个文件 SHA-256。`.venv`、缓存、被取代的运行和旧 ZIP 不打包；
解压后按 README 重建环境。完整源工程保留在 `D:\Codes\eas-hmi`。

## 限制与下一阶段

本阶段无遗留的必需 Demo 工作；阶段 4 的 SVG 子集、导入占位字段、显式 connection 几何限制继续适用。
页面是工程设计态，不连接真实设备；没有执行 Plant SCADA 写入或 GitHub 发布。

阶段 6 尚待用户授权，内容为：

1. 新建干净环境，验证安装、CLI help 和依赖文档，避免依赖当前 editable 环境中的隐含状态。
2. 在最终代码上重跑全量测试、覆盖率 ≥85%、三页 Demo、严格校验和导出。
3. 对本 Demo 的 query/validate 各执行至少 20 次，记录 p50/p95、测量条件和判定结果；
   当前日志中的单次 CLI 耗时不冒充正式性能验收。
4. 完成最终 README、完整验收矩阵、最终交付报告与项目包，明确所有实际支持范围。

本次止于阶段 5，不自动进入阶段 6。
