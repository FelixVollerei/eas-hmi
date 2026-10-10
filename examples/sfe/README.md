# 直接运行三十种 EAFS 文件编辑指令集

在仓库根目录安装：`python -m pip install -e '.[dev]'`。以下命令也可用 `.venv/Scripts/python.exe -m ...` 执行。

一次运行全部三十种格式、保存输出和每次真实 CLI 调用记录：

```sh
python scripts/demo_sfe.py
```

每次在新的 `build/sfe-trial-<时间>/` 目录生成编辑结果、`REPORT.md` 和 `results.json`。演示还会验证旧计划冲突、EAS 整组失败回滚及撤销恢复；源文件不需要屏幕操作。

## JSON

```sh
python -m sfe_files.cli status examples/sfe/settings.json
python -m sfe_files.cli inspect examples/sfe/settings.json --pointer /alarms
python -m sfe_files.cli preview examples/sfe/settings.json examples/sfe/settings.plan.json
python -m sfe_files.cli apply examples/sfe/settings.json examples/sfe/settings.plan.json --output build/sfe-settings-edited.json
```

计划执行 `set /enabled`、`append /alarms`、`add /reviewed_by`。源文件保留。

## CSV

```sh
python -m sfe_files.cli status examples/sfe/assets.csv
python -m sfe_files.cli inspect examples/sfe/assets.csv --key-column id --key P-01
python -m sfe_files.cli preview examples/sfe/assets.csv examples/sfe/assets.plan.json
python -m sfe_files.cli apply examples/sfe/assets.csv examples/sfe/assets.plan.json --output build/sfe-assets-edited.csv
```

计划按 `id` 唯一定位 `P-01` 修改 `status`，并添加完整新行。

## Markdown

```sh
python -m sfe_files.cli status examples/sfe/handover.md
python -m sfe_files.cli inspect examples/sfe/handover.md --heading Findings
python -m sfe_files.cli preview examples/sfe/handover.md examples/sfe/handover.plan.json
python -m sfe_files.cli apply examples/sfe/handover.md examples/sfe/handover.plan.json --output build/sfe-handover-edited.md
```

计划替换 `Findings` 章节正文，并追加 `Next action` 章节。

## EAS-HMI

```sh
python -m eas_hmi.cli --project build/sfe-eas init --from-model examples/stage4/model.json
python -m eas_hmi.cli --project build/sfe-eas status
python -m eas_hmi.cli --project build/sfe-eas context CARD_A
python -m eas_hmi.cli --project build/sfe-eas apply-plan examples/sfe/eas.plan.json --dry-run
python -m eas_hmi.cli --project build/sfe-eas apply-plan examples/sfe/eas.plan.json
python -m eas_hmi.cli --project build/sfe-eas diff --json
```

计划把 `CARD_A` 移动 10×5 并标记 `metadata.reviewed=true`，作为一个 revision 提交。这个例子的 base 只适用于新初始化的演示工程。

## YAML：部署与自动化配置

```sh
python -m sfe_files.cli status examples/sfe/deployment.yaml
python -m sfe_files.cli inspect examples/sfe/deployment.yaml --pointer /spec/replicas
python -m sfe_files.cli preview examples/sfe/deployment.yaml examples/sfe/deployment.plan.json
python -m sfe_files.cli apply examples/sfe/deployment.yaml examples/sfe/deployment.plan.json --output build/sfe-deployment-edited.yaml
```

计划把 `replicas` 从 1 改为 3，追加端口 `8443`，添加 `metadata.labels.reviewed`。原有中文注释与引号保留；YAML 缩进可能规范化。也接受 `.yml`。

```json
{"op":"set","pointer":"/spec/replicas","value":3}
```

## TOML：项目与服务配置

```sh
python -m sfe_files.cli status examples/sfe/service.toml
python -m sfe_files.cli inspect examples/sfe/service.toml --pointer /server/port
python -m sfe_files.cli inspect examples/sfe/service.toml --pointer /commissioned
python -m sfe_files.cli preview examples/sfe/service.toml examples/sfe/service.plan.json
python -m sfe_files.cli apply examples/sfe/service.toml examples/sfe/service.plan.json --output build/sfe-service-edited.toml
```

计划把端口改为 `8081`，追加 `backup`，新增 `enabled=true`。原有 `commissioned` 日期仍是 TOML date，inspect 会返回类型标记，端口行的说明注释保留。计划值不接受 `null`。

```json
{"op":"append","pointer":"/server/peers","value":"backup"}
```

## SVG：按对象 ID 编辑矢量图

```sh
python -m sfe_files.cli status examples/sfe/schematic.svg
python -m sfe_files.cli inspect examples/sfe/schematic.svg --id pump
python -m sfe_files.cli preview examples/sfe/schematic.svg examples/sfe/schematic.plan.json
python -m sfe_files.cli apply examples/sfe/schematic.svg examples/sfe/schematic.plan.json --output build/sfe-schematic-edited.svg
```

计划把 `pump` 在父坐标系中平移 `(24,12)`，把 `pump-body` 改为绿色，把 `pump-label` 改为 `Pump ready`。`tank` 与 `pipe` 的 XML 子树保持相同。这里只编辑 SVG 文件，运行这些命令无需图形软件。

```json
[
  {"op":"translate","id":"pump","dx":24,"dy":12},
  {"op":"set-attribute","id":"pump-body","name":"fill","value":"#16a34a"},
  {"op":"set-text","id":"pump-label","text":"Pump ready"}
]
```

把上面数组放入计划的 `steps` 字段，完整封套见 [schematic.plan.json](schematic.plan.json)。SVG 的 CSS 可以覆盖属性，因此本 profile 不宣称验证所有视觉效果。

## XLSX：Excel 工作簿

```sh
python -m sfe_files.cli status examples/sfe/budget.xlsx
python -m sfe_files.cli inspect examples/sfe/budget.xlsx --pointer /sheets/Budget/A1:C8
python -m sfe_files.cli preview examples/sfe/budget.xlsx examples/sfe/budget.plan.json
python -m sfe_files.cli apply examples/sfe/budget.xlsx examples/sfe/budget.plan.json --output build/sfe-budget-edited.xlsx
```

```json
[
  {"op":"set-cell","sheet":"Budget","cell":"B5","value":1800},
  {"op":"set-formula","sheet":"Budget","cell":"C7","formula":"=SUM(C5:C6)"}
]
```

按工作表名和 A1 地址定位；`set-cell` 接受字符串、数字、布尔值和清空用的 `null`。保留原有单元格样式。写公式后不会在 CLI 内计算结果；编辑会清除公式缓存并请求 Excel 等引擎重新计算。图表等派生缓存需要应用程序刷新。普通表格数据行也可编辑；共享/数组公式范围、表头、合计行、计算列等复杂目标暂不编辑。完整示例还把 A5 改为 `HMI controls`。

## DOCX：Word 文档

```sh
python -m sfe_files.cli status examples/sfe/meeting.docx
python -m sfe_files.cli inspect examples/sfe/meeting.docx --pointer /paragraphs/2
python -m sfe_files.cli inspect examples/sfe/meeting.docx --pointer /tables/1
python -m sfe_files.cli preview examples/sfe/meeting.docx examples/sfe/meeting.plan.json
python -m sfe_files.cli apply examples/sfe/meeting.docx examples/sfe/meeting.plan.json --output build/sfe-meeting-edited.docx
```

```json
[
  {"op":"set-paragraph","paragraph":2,"text":"Approved for staging."},
  {"op":"set-table-cell","table":1,"row":2,"column":3,"text":"Complete"},
  {"op":"append-paragraph","text":"Follow-up: verify alarms."}
]
```

正文直属段落、表格、行和列从 1 编号；表格内部段落不占用正文段落号。文字替换保留段落属性与首个 run 样式，原有混合字样式会合并。含域、修订、图片、超链接等复杂内容的目标被拒绝，其他部件保留。

## PPTX：PowerPoint 演示文稿

```sh
python -m sfe_files.cli status examples/sfe/project.pptx
python -m sfe_files.cli inspect examples/sfe/project.pptx --pointer /slides/1
python -m sfe_files.cli inspect examples/sfe/project.pptx --pointer /slides/1/shapes/2
python -m sfe_files.cli preview examples/sfe/project.pptx examples/sfe/project.plan.json
python -m sfe_files.cli apply examples/sfe/project.pptx examples/sfe/project.plan.json --output build/sfe-project-edited.pptx
```

```json
[
  {"op":"set-text","slide":1,"shape_id":2,"text":"Status: Ready for review"},
  {"op":"move","slide":1,"shape_id":2,"x":914400,"y":2286000},
  {"op":"resize","slide":1,"shape_id":2,"width":8229600,"height":1371600}
]
```

幻灯片从 1 编号，`shape_id` 是文件中对象 ID。`move` 设置绝对位置；单位为 EMU，914400 EMU = 1 英寸。支持明确位置的独立文字框、图形和图片的几何编辑；复杂分组和复杂文字目标暂不修改。

以上 JSON 数组用于说明 `steps`；实际运行使用绑定源文件 SHA-256 的完整 `.plan.json`。Office 预览返回语义差异和变化的 ZIP 部件列表。未修改部件的解压后字节保持一致，源文件始终保留。分页、文字溢出和公式计算需要独立检查。

旧版 `.xls/.doc/.ppt` 必须先另存为 `.xlsx/.docx/.pptx`，改扩展名不能转换。当前不支持加密、宏、签名和 Strict OOXML。

## 第 1 批扩展：SRT、WebVTT、ICS、VCF、JSONL

五种格式已实现，完整查看/修改命令、逐条 JSON 参数和支持边界见 [日常文件指令集](../../docs/EAFS-DAILY-FILES.md)。可直接预览仓库样例：

```sh
python -m sfe_files.cli preview examples/sfe/captions.srt examples/sfe/captions.plan.json
python -m sfe_files.cli preview examples/sfe/subtitles.vtt examples/sfe/subtitles.plan.json
python -m sfe_files.cli preview examples/sfe/schedule.ics examples/sfe/schedule.plan.json
python -m sfe_files.cli preview examples/sfe/contacts.vcf examples/sfe/contacts.plan.json
python -m sfe_files.cli preview examples/sfe/records.jsonl examples/sfe/records.plan.json
```

把 `preview` 改为 `apply` 并添加 `--output 新文件路径` 即写出结果。`python scripts/demo_sfe.py` 会实际执行全部三十个 profile 的完整流程并保存证据。

## 第 2 批扩展：ODT、ODS、ODP、HTML、EPUB

五种格式已实现，逐条操作、示例和支持边界见 [开放办公与阅读指令集](../../docs/EAFS-OFFICE-READING.md)。固定计划可直接预览：

```sh
python -m sfe_files.cli preview examples/sfe/notes.odt examples/sfe/notes.plan.json
python -m sfe_files.cli preview examples/sfe/costs.ods examples/sfe/costs.plan.json
python -m sfe_files.cli preview examples/sfe/briefing.odp examples/sfe/briefing.plan.json
python -m sfe_files.cli preview examples/sfe/page.html examples/sfe/page.plan.json
python -m sfe_files.cli preview examples/sfe/handbook.epub examples/sfe/handbook.plan.json
```


## 第 3—5 批扩展：图示、位置、数据与专业对象

十种格式的地址、逐条操作和支持边界见 [第 3—5 批指令集](../../docs/EAFS-BATCHES-3-5.md)。固定计划可直接预览：

```sh
python -m sfe_files.cli preview examples/sfe/network.drawio examples/sfe/network.plan.json
python -m sfe_files.cli preview examples/sfe/sites.geojson examples/sfe/sites.plan.json
python -m sfe_files.cli preview examples/sfe/walk.gpx examples/sfe/walk.plan.json
python -m sfe_files.cli preview examples/sfe/plant.kml examples/sfe/plant.plan.json
python -m sfe_files.cli preview examples/sfe/equipment.sqlite examples/sfe/equipment.plan.json
python -m sfe_files.cli preview examples/sfe/inventory.parquet examples/sfe/inventory.plan.json
python -m sfe_files.cli preview examples/sfe/telemetry.arrow examples/sfe/telemetry.plan.json
python -m sfe_files.cli preview examples/sfe/layout.dxf examples/sfe/layout.plan.json
python -m sfe_files.cli preview examples/sfe/control.kicad_sch examples/sfe/control.plan.json
python -m sfe_files.cli preview examples/sfe/melody.musicxml examples/sfe/melody.plan.json
```

`python -m sfe_files.cli contract` 可读取二十九种普通文件及实验性 Minecraft 世界目录的操作清单；各 profile 返回参数示例、寻址方式和限制。EAS-HMI 使用 `agent-contract`。原有三十种固定样例仍由 `scripts/demo_sfe.py` 运行；Minecraft 26.3 世界需按 [单独 demo 指南](../../docs/EAFS-MINECRAFT-JAVA-26-3.md) 使用真实世界副本。计划格式见 [方法和接口草案](../../docs/STRUCTURED-FILE-EDITING.md)，已完成批次统计见 [EAFS 格式路线图](../../docs/EAFS-FORMAT-ROADMAP.md)。

## 通过 MCP 使用同一指令集

安装 `python -m pip install -e '.[mcp]'` 后，以 `eafs-mcp` 启动本地 stdio 服务。普通文件按 `file_contract(format_name)` → `file_status(file)` → `file_inspect(file, ...)` → `file_preview(file, plan)` → `file_apply(file, plan, output)` 调用；EAS-HMI 使用对应的 `eas_*` 工具。把样例 `.plan.json` 的完整 JSON 对象作为 `plan` 参数传入，提交前以当前 `file_status.sha256` 或 EAS `eas_status` 的 revision 与 `canonical_hash` 生成有效 base。`file_apply` 的 `output` 必须是不存在的新路径。工具参数和路径边界见 [EAFS MCP 适配层说明](../../docs/EAFS-MCP.md)。
