# EAS-HMI 验收矩阵

本文件完成需求到验证的映射。用例名为计划标识，不代表测试文件已经存在或通过。
本表为阶段1的完整需求映射；后续实测状态以各阶段报告为准，不能把部分阶段通过当作完整功能验收。
阶段2见stage-2-report.md，阶段3见stage-3-report.md。设计文档不能替代运行证据。

**阶段 6 历史结果**：F01–F16 的当时用例通过；性能只测了 8 条提交的 query/validate，不覆盖长历史。复核发现 INFO 默认规则/验收覆盖和命令计数问题，修订结果见 [复核报告](review-corrections.md)。
最终逐项判定见 [final-acceptance.md](final-acceptance.md)，数据及限制见 [stage-6-report.md](stage-6-report.md)。
本文件末尾的各阶段状态保留为历史记录，不覆盖最终判定。

## 需求章节映射

| ID / 原始章节 | 模块与命令 | 验证及证据 | 阶段 |
|---|---|---|---|
| R01 / §1 原则 | 全模块 | A–G CLI 记录、单一事实来源、零视觉/GUI | 3/5/6 |
| R02 / §2 技术约束 | pyproject/环境 | Python>=3.12、独立环境、依赖无禁止项 | 1/6 |
| R03 / §3 模块结构 | model/operations/svg/validation/query/export | 依赖方向及职责审查 | 1/4/6 |
| R04 / §4 模型 | Project/Page/Node、9 kind | serialization/schema/stable_ids/node_kinds/非固定分辨率 | 3/4 |
| R05 / §5 设备/点/绑定 | Equipment/Point/Binding | 结构化 role/ref/expression、引用完整性、expression 不执行 | 3/4 |
| R06 / §6 模板 | Template/validate | required/optional、不同模板要求不混用 | 3/4 |
| R07 / §7 Opaque | svg/move/resize/set/bind | 未知 path、defs/mask/use 不静默删除，几何操作有效 | 2/4 |
| R08 / §8 Semantic SVG | renderer | 稳定 ID/kind/ref、无全模型 attribute | 2/3 |
| R09 / §9 Import | import-svg | 普通/semantic 导入、稳定顶层 ID、源 hash 不变、保留/warning | 2/4 |
| R10 / §10 Render | render | byte-identical、有效 SVG、模型 hash 不变 | 2/3 |
| R11 / §11 Round trip | sync-from-svg | no-op、translation/x/y/resize/rotation/text/visibility/opaque/unsafe 拒绝 | 2/3/4 |
| R12 / §12 CLI | init/inspect/query/context | help、JSON、AND filters、局部关系、depth 上限/截断 | 3/4 |
| R13 / §13 Edit | set/move/resize/bind/unbind/align/distribute/batch | 单/批量、4种对齐、x/y gap、无 eval、事务化 | 3/4 |
| R14 / §14 Transaction | transaction | rollback/revision conflict/批量原子性/故障恢复 | 3 |
| R15 / §15 History | operations.jsonl | 必需字段、3类 actor、affected IDs、真实 before/after | 3 |
| R16 / §16 Diff | diff/--json | 最新及指定事务、实体字段 old/new，无 raw XML diff | 3/4 |
| R17 / §17 Undo | undo/hash | 语义 hash 恢复、opaque 恢复、审计保留 | 3 |
| R18 / §18 Validate | validate/--json | schema、重复、引用、循环、几何、越界、模板、绑定全部规则 | 3/4/5 |
| R19 / §19 Manifest | export-manifest | CSV/JSON 10字段逐行对应 registry、稳定顺序 | 4/5 |
| R20 / §20 Assets | export-assets | node/template/page、SVG/PNG/BMP、透明/尺寸/边界、失败无损坏 | 2/4/5 |
| R21 / §21 Events | events.jsonl、watch可选 | 成功提交有事件、失败/no-op无事件、delta可解析 | 3/4 |
| R22 / §22 Synthetic | 发生器/examples/fixtures | 3页、设备/点/节点数量、种类和全部错误最小数量 | 5 |
| R23 / §23 Demo | demo_agent_workflow.py | A–G实际subprocess、零GUI、全部build产物 | 5/6 |
| R24 / §24 总验收 | 完整包/报告 | 下方F01–F16及非功能条件，不省略失败项 | 6 |
| R25 / §25 Tests | pytest/pytest-cov | 下方用例清单、包覆盖率>=85% | 2–6 |
| R26 / §26 README | README | problem/architecture/CLI first/demo/scope、完整复现命令 | 6 |
| R27 / §27 Non-goals | 代码/依赖 | 无SCADA/PLC/runtime/GUI/网站/MCP/模型API | 每阶段 |
| R28 / §28 决策顺序 | design-contract | 完整性→确定性→可检查→可逆→保留优先 | 每阶段 |
| R29 / §29 Deliverables | 交付清单 | 包/CLI/所有模块/synthetic/tests/README/一键脚本实际存在 | 6 |
| R30 / §30 Final verification | 最终报告 | 实测tests/demo/validate/render/export、覆盖率、计数、错误、命令、路径、限制 | 6 |

## Functional 16 项

| 编号 | 操作 | 通过标准 |
|---|---|---|
| F01 | 干净环境 pip install -e . | exit 0、依赖一致 |
| F02 | eas-hmi --help、子命令help | exit 0、入口及必需命令可用 |
| F03 | synthetic generator | 全新目录可重复生成且数量达标 |
| F04 | query/inspect/context --json | 可解析、关系正确、context不输出全工程 |
| F05 | 单set/move/resize | 属性正确、revision增长、有历史 |
| F06 | batch/align/distribute | 所有目标正确、非目标不变、固定gap |
| F07 | bind/unbind | 引用正确、unknown point拒绝、模板生效 |
| F08 | validate broken fixtures | 检出全部预设错误，issue字段齐全 |
| F09 | render | SVG有效、模型不变 |
| F10 | metadata | ID/kind/equipment/binding refs稳定 |
| F11 | sync-from-svg | 规定5类编辑及x/y/常规matrix正确，unsafe整体拒绝 |
| F12 | diff | 按实体字段old/new，支持JSON |
| F13 | undo | 恢复原语义hash、保留审计 |
| F14 | export-manifest | CSV/JSON有效、10字段齐全 |
| F15 | export-assets | PNG/BMP实际解码、尺寸及alpha/背景正确 |
| F16 | 全新项目运行A–G | 全部断言成功、exit 0、产物存在 |

## 非功能条件

| 类别 | 条件/证据 |
|---|---|
| Agent-native | screenshot/OCR/mouse/keyboard GUI=0；subprocess记录及代码审查，不能仅硬编码计数 |
| Safety | 所有Canonical写入事务化、ERROR无partial commit、有affected IDs；hash/head/history比较 |
| Round-trip | 未修改sync的hash/revision/history均不变，含零尺寸及opaque |
| Determinism | 连续render字节比较一致 |
| Performance | 500+nodes/300+points，CLI query/validate各20次p50/p95，暂定p95<=1秒 |
| Recovery | 提交前故障HEAD不变，提交后历史/事件可恢复；故障注入后新Store重开 |
| Coverage | pytest --cov=eas_hmi --cov-report=term-missing --cov-report=xml；记录退出码/测试数/>=85% |

## pytest 覆盖清单

| 用例族 | 内容 |
|---|---|
| model_serialization/schema | JSON往返、schema、NaN/Infinity/负尺寸 |
| duplicate_id/reference_integrity | node/page/registry重复、parent/page/equipment/point/connection引用与循环 |
| template_required_binding | 缺required、optional、role不允许、重复绑定 |
| query/inspect/context | AND、空集、不存在、直接/连接关系、depth/truncated |
| single_edit/batch_edit | 所有命令、locked、无效选择、混合坐标系 |
| transaction_rollback | 校验、revision冲突、部分修改异常、写入故障、恢复 |
| diff/undo/canonical_hash | 字段/role、opaque、revision排除、初始化保护 |
| render/semantic_metadata | 有效SVG、稳定bytes、9kind、模型不变 |
| svg_import/opaque_preservation | 原文件不变、未知内容、defs引用、ID、registry缺失warning |
| sync_no_op/sync_geometry/sync_text | 全编辑类型、矩阵、父子组合、tspan、visibility、stale/unsafe |
| binding_manifest | CSV/JSON字段/值/顺序、无绑定工程 |
| asset_export | SVG自包含、PNG/BMP解码/尺寸/alpha/背景/边界、后端缺失 |

## Synthetic fixtures

| 错误 | 最低数量 | 位置与预期 |
|---|---|---|
| missing required bindings | 3 | 主Demo默认WARNING，B修复pump fault后为0 |
| invalid equipment refs | 2 | 独立broken fixture，ERROR，事务拒绝 |
| invalid point refs | 2 | 独立broken fixture，ERROR，事务拒绝 |
| duplicate semantic ID | 1 fixture | 独立模型/SVG，ERROR，不覆盖/去重 |
| out-of-bounds nodes | 2 | fixture，默认WARNING、配置ERROR则阻断 |
| invalid geometry | 1 fixture | 独立raw JSON，schema ERROR，不能先修正再声称检出 |

## Demo A–G

| Task | 通过标准 |
|---|---|
| A | cooling的pump card数量、缺fault名单、CHWP_07直接对象/连接/points均来自query/inspect/context |
| B | 按registry选择fault point，经CLI bind修复，复查0个pump缺required fault |
| C | CLI batch统一宽度、align/distribute或有限columns网格、固定gap，不逐项手改JSON |
| D | 脚本仅在SVG模拟tagged位移与文字，经sync入模、记录human事务 |
| E | B/C/D各自semantic diff留存，不以最近一次diff代替全部 |
| F | undo D后hash等于D前，revision继续审计增长 |
| G | build中semantic SVG、bindings CSV/JSON、至少一个BMP，另验证PNG |

## 阶段 4 历史记录（不是当前待办）

完整 query/context、align/distribute/batch、9 kind render、template rules、SVG import、
manifest、asset selectors 与 events/watch 已由 `test_stage4_features.py` / `test_stage4_svg.py` 覆盖。
阶段 2/3 测试继续全量运行；当前共 222 项，整包覆盖率 94.34%，45 次安装 CLI 验收调用。
详细证据与限制见 [stage-4-report.md](stage-4-report.md)。
F16、Synthetic fixtures 的最低规模/数量、A–G 大型 Demo 和性能条件在阶段 4 结束时尚未执行；随后由阶段 5/6 补验。

## 阶段 5 历史记录（不是当前待办）

F16、大型 Demo 数量和独立 broken fixtures、Task A–G 已完成：3 页、672 nodes、390 points、90 equipment。
`test_stage5_demo.py` 新增 15 项；总计 237 项通过，整包覆盖率 94.84%。
`scripts/demo_agent_workflow.py` 通过 75 次真实 CLI 调用验收 A–G 与失败保护。
结果和限制见 [stage-5-report.md](stage-5-report.md)。
阶段 5 结束时尚未执行干净安装和正式 p50/p95 性能测量；随后阶段 6 完成了其报告中限定的测试。
