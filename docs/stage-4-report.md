# 阶段 4 验收报告 — 完整功能与项目交付

日期：2026-09-10。独立工程：`D:\Codes\eas-hmi`。

## 结论

**阶段 4 功能实现及本地验收通过，提交用户验收，本次停止。**

本次完成了整个阶段 4，无需因上下文长度而中途交接。另行提供完整项目包、
可读 Canonical 快照、验收记录与下一阶段任务清单，便于独立恢复工作。
阶段 5、6 尚未开始；不把当前小样本测试称为完整数据中心 Demo 或最终产品验收。

## 实测结果

最终运行目录：[20260910T060131Z-12a73398](../build/stage4/20260910T060131Z-12a73398/)。

| 检查 | 实际结果 |
|---|---|
| 全量 pytest | **222 passed，0 failures，0 errors，0 skipped** |
| 阶段 4 新增测试 | 78 项；阶段 2/3 的 144 项全部回归通过 |
| 全量测试耗时 | 约 84 秒，精确值见 tests.xml / report.json |
| 整包行覆盖率 | **94.34%**，实际执行 `--cov-fail-under=85`，通过 |
| 安装版 CLI 验收 | **45 次调用**，成功与预期拒绝退出码均核对 |
| 小样本规模 | 完全合成：2 页、11 节点、3 设备、5 点位、2 模板，覆盖全部 9 kind |
| 主验收工程 revision | 0 → 7，8 条审计提交（含 init） |
| 清单 | 5 条实际 binding，CSV/JSON 10 字段对应、UTF-8 字符与 CSV 转义测试通过 |
| 模板资产批次 | 2 个 pump card × SVG/PNG/BMP，共 6 文件；另验收 page SVG 和 image PNG |
| 位图 | 真实 Inkscape CLI 生成，Pillow 解码验证尺寸、PNG alpha、BMP RGB |
| SVG 导入 | 9 kind 语义恢复、普通 opaque、复杂共享 CSS/defs/跨对象引用保留、源文件不变 |
| 像素比较 | 原 SVG 与导入重绘实际栅格对比通过；组合透明度容许 2/255 的量化差 |
| Human round-trip | 九种 tagged 节点几何/visibility、组件文字；真实 Inkscape 保存再导入通过 |
| 批量失败 | 无效选择、锁定、负尺寸、非法属性、混合坐标系、revision 冲突不产生部分工程提交 |
| 事件 | 提交事件、排除式 revision 游标、watch JSONL、真实子进程监听提交通过 |
| pip check / Ruff | 通过；Ruff 范围为 E4/E7/E9/F/I 与本阶段文件格式 |
| GUI/OCR/鼠标键盘 | 0；代码仅使用模型、XML、文件、CLI 和 Inkscape 命令行 |

本阶段的 coverage 门槛已经实际通过，但阶段 6 仍需在最终代码与干净环境重新验收。

## 交付功能

1. **完整查询与 context**：AND 过滤；节点/设备/点位入口；有界 BFS、深度 0–3、数量限制，
   保留目标优先级，省略大型 payload/image/任意 metadata。context 不把整个模型送给 Agent。
2. **align / distribute / batch**：选择与编辑在同一事务内；支持固定间距及有限列数网格；
   单次提交、可撤销、no-op 不增加 revision，无 eval。
3. **全部节点与模板**：9 kind render，组件文字同步，内嵌图片校验，connection 端点引用，
   required/optional roles、重复/空角色校验。支持 render 一次输出多页。
4. **SVG import**：新工程初始化或已有工程追加页面，事务审计及 undo；EAS 语义恢复，
   普通图形 opaque 保留，稳定顶层 ID，依赖无法独立拆分时保留整体并警告。
5. **export-manifest / export-assets**：CSV/JSON 清单；node/template/page 选择器；
   SVG/PNG/BMP、尺寸/透明度/背景；资产全部转换成功后才发布完整批次。
6. **events / watch**：读取持久提交增量，JSONL 前台监听，不创建后台服务或新协议。

当前行为、命令样例和具体限制集中在 [stage-4-cli.md](stage-4-cli.md)。

## 可复核证据

- [结构化验收结果](../build/stage4/20260910T060131Z-12a73398/report.json)
- [完整 pytest 日志](../build/stage4/20260910T060131Z-12a73398/tests.log)
- [JUnit 测试记录](../build/stage4/20260910T060131Z-12a73398/tests.xml)
- [整包覆盖率](../build/stage4/20260910T060131Z-12a73398/coverage.json)
- [45 次 CLI 原始命令、输出与退出码](../build/stage4/20260910T060131Z-12a73398/cli-transcript.json)
- [局部上下文](../build/stage4/20260910T060131Z-12a73398/context.json)
- [批量修改语义差异](../build/stage4/20260910T060131Z-12a73398/batch-diff.json)
- [Human caption sync 差异](../build/stage4/20260910T060131Z-12a73398/human-diff.json)
- [事件记录](../build/stage4/20260910T060131Z-12a73398/events.json)
- [Canonical 最终快照](../build/stage4/20260910T060131Z-12a73398/final-model.json)
- [绑定 CSV](../build/stage4/20260910T060131Z-12a73398/bindings/bindings.csv) / [绑定 JSON](../build/stage4/20260910T060131Z-12a73398/bindings/bindings.json)
- [9 kind 初始示例](../examples/stage4/model.json) / [JSON Schema](../schemas/project.schema.json)
- [一键验收脚本](../scripts/verify_stage4.py) / [依赖版本记录](environment-stage-4.txt)

最终主工程（`.eas` commit chain + HEAD + history）在该运行目录的 `project/`。
导入/撤销验证工程在 `imported-project/`。可读快照可使用 `init --from-model` 初始化新目录；
该方式产生新的 init 审计，不复制原 history。恢复原审计工程请保留完整存储目录。

## 完整项目文件

项目 ZIP 及 SHA-256 记录由 `deliverables/latest.json` 指向。
包内包括完整 src、tests、scripts、schemas、examples、docs、README、pyproject 和
阶段 2/3/4 各自最终成功的全部验收产物，包含真实 Canonical 提交链。
`PACKAGE-MANIFEST.json` 逐项记录大小和 SHA-256；打包脚本读取 ZIP 内每个文件再次校验，另做 ZIP CRC 检查。

不打包 `.venv`、缓存、egg-info、被后续运行替代的验收目录及历史 ZIP。
它们不是项目源文件；依赖通过 pyproject 安装，具体本机版本另有记录。
解压后按 README 创建 Python 3.12+ venv 并安装 `.[dev]`，Inkscape CLI 是外部前提。
验收记录中的绝对命令路径是原运行证据；在另一目录复现会生成新的正确路径。

## 已知边界与未完成项

- SVG 支持范围是明确的保守子集；外部资源、复杂 CSS、任意富文本/未知语义格式会明确拒绝。
  共享样式/合成/defs 依赖可能合并为一个 opaque，顶层 XML ID 仍保留可追踪。
- Semantic SVG 不包含整个 Canonical registry；导入时缺失 point tag/datatype、模板 required roles、
  expression 和任意 metadata 会明确警告并创建占位项，不能称为无损工程备份恢复。
- connection 使用自身几何的显式线段，没有设备移动后的自动路由；状态/数值仅为设计态内容。
- 只验证当前 Windows/Python/Inkscape 环境，没有完成跨平台、全新安装或真实 Plant SCADA 绑定。
- 未连接或发布 GitHub；本地项目和完整 ZIP 已具备，无远端依赖。

## 下一步工作（需要用户授权阶段 5）

1. 生成完全合成的 overview/cooling/electrical 三页数据中心工程。
2. 设备至少 20 pumps、20 valves、30 sensors、12 UPS、8 generators；points ≥300，nodes ≥500。
3. 主 Demo 与独立 broken fixtures 覆盖：缺 required ≥3、错误 equipment refs ≥2、错误 point refs ≥2、
   duplicate ID fixture ≥1、out-of-bounds ≥2、invalid geometry fixture ≥1。
4. 编写真正调用 CLI 的 A–G 工作流：查询/上下文、修复 fault、批量布局、模拟 SVG 人工改动、
   分阶段 diff、undo hash 复原、清单与 SVG/PNG/BMP 产物；保存每一步证据。
5. 阶段 5 完成后单独提交验收报告并停止。

阶段 6 再做干净安装、最终完整回归及 ≥85% 覆盖率、500+ nodes/300+ points 的 query/validate
性能 p50/p95、完整 Demo 复跑、最终 README 和交付报告。当前没有遗留必须放回阶段 4 的未完成工作。
