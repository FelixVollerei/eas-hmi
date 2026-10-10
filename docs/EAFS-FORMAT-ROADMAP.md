# EAFS 格式扩展路线图

日期：2026-09-27。EAFS 是当前方案的暂用名称，执行协议保持 `sfe/0.1`。以下顺序是按现有实现的复用程度、日常用途与验证成本作出的工程判断，不代表正式标准认证或交付时间承诺。

## v0.2 发布候选重点

本候选固定已实现的三十个 profile，不新增格式或改变 `sfe/0.1` 计划语义。发布前优先完成排他输出、可从 Git 还原的固定样例、干净 wheel 演示、EAFS CI，以及可公开复验的报告。后续格式扩展应另立范围与验收。

## 已实现：30 个 profile

| 已完成批次 | 格式 | 数量 |
|---|---|---:|
| 工程与基础数据 | EAS-HMI、JSON、CSV、Markdown | 4 |
| 配置与矢量图 | YAML、TOML、SVG | 3 |
| 常用办公 | XLSX、DOCX、PPTX | 3 |
| 路线图第 1 批：日常小文件 | SRT、WebVTT、ICS、VCF、JSONL | 5 |
| 路线图第 2 批：办公与阅读 | ODT、ODS、ODP、HTML、EPUB | 5 |
| 路线图第 3 批：图示与位置 | draw.io、GeoJSON、GPX、KML | 4 |
| 路线图第 4 批：数据文件 | SQLite、Parquet、Arrow IPC | 3 |
| 路线图第 5 批：专业对象 | DXF、KiCad 原理图、MusicXML | 3 |
| 合计 | 统一查看、预览、计划和应用工作流 | **30** |

这里按编辑 profile 计数，`.yaml/.yml` 等别名只算一种；EAS-HMI 是工程模型 profile。每个 profile 覆盖已声明的操作子集，支持列表不等于完整实现所有文件特性。

## 路线图第 3—5 批：已实现，共 10 个 profile

这些格式的首个明确操作子集均已实现，表中的地址和指令可在 CLI 的 `contract` 或 MCP 的 `file_contract(format_name)` 中发现。完整样例和限制见 [第 3—5 批指令集](EAFS-BATCHES-3-5.md)。

| 已完成批次 | 格式 | 数量 | 当前查看与修改范围 | 后续难点 |
|---|---|---:|---|---|
| 3：图示与位置 | draw.io、GeoJSON、GPX、KML | 4 | 按 cell/Feature/点/Placemark 地址编辑普通目标 | 复杂布局与地理拓扑 |
| 4：数据文件 | SQLite、Parquet、Arrow IPC | 3 | 引擎读取；按主键或业务键更新、追加；写出新文件 | WAL 快照、大表和复杂列 |
| 5：专业对象 | DXF、KiCad 原理图、MusicXML | 3 | 2D 实体文字/位置；元件属性/坐标；音高、时值和力度 | CAD 布局、KiCad ERC 与复杂乐谱 |
| 合计 | 新增 10 个 profile，累计 30 个 | **10** | | |

### 批次 1：已完成的日常文件扩展

- **SRT / WebVTT**：`inspect --pointer /cues/1`、`set-text`、`shift-time`、`set-timing`。保留原编号/ID、设置、样式和备注。
- **ICS**：`inspect --pointer /events/<UID>`、`set-event-field`、`reschedule`。限定单次事件，重排支持日期与 UTC；不发送邀请或同步日程。
- **VCF**：`inspect --pointer /contacts/<UID>`、`set-property`、`add-property`。支持 3.0/4.0、多值属性、组织组件；保留照片与未知字段。
- **JSONL**：`inspect --pointer /by-key/id/P-01`、`set-field`、`replace-record`、`append-record`、`delete-record`。修改按唯一字符串业务键定位，未改行保留。

本轮验证：338 项相关测试、105 次真实 CLI 调用、91 项演示检查通过。完整示例、参数与规范来源见 [日常文件指令集](EAFS-DAILY-FILES.md)。

### 批次 2：已完成的办公与阅读扩展

- **ODT / ODS / ODP**：普通段落/表格、已有单元格及公式、独立幻灯片对象。ODS 支持受限重复行列的局部拆分；公式结果需外部重算。[OASIS OpenDocument](https://www.oasis-open.org/standard/open-document-format-for-office-applications-opendocument-version-1-3/)
- **HTML**：按唯一 ID 读元素，改叶文本/受限属性，向容器追加元素。处理静态 UTF-8 文件，不执行脚本或计算浏览器运行态。[HTML 标准](https://html.spec.whatwg.org/multipage/)
- **EPUB**：按书脊章节 ID 读取，改元数据、XHTML 叶文本及修改时间；保留阅读顺序、导航和资源引用，限定未加密可重排 EPUB 3。[EPUB 3.3](https://www.w3.org/TR/epub-33/)

可运行命令、计划和支持边界见 [开放办公与阅读指令集](EAFS-OFFICE-READING.md)。

本批完成后整库测试 739 项通过；二十格式演示真实执行 140 次 CLI 调用、131 项结果检查。ODT/ODS/ODP 固定样例的源文件和编辑结果通过 ODF 1.3 Schema 校验，EPUB 固定样例通过 EPUBCheck 5.4.0 且零错误、零警告。

### 批次 3：已完成图示与位置扩展

- **draw.io**：读取普通 XML 和压缩页面，按 cell ID 改标签、几何和连线端点；保留未改页面。[官方源 XML 编辑说明](https://www.drawio.com/docs/manual/advanced/diagram-source-edit/)
- **GeoJSON**：按 Feature ID 或 base 下索引改 properties、Point 坐标；有 bbox 时拒绝点位编辑。[RFC 7946](https://www.rfc-editor.org/info/rfc7946/)
- **GPX**：按航点、路线点和轨迹段地址改名称、坐标、时间；保留原有分段。[GPX 1.1 Schema](https://www.topografix.com/gpx/1/1/)
- **KML**：按唯一 Placemark ID 改名称、说明及直接 Point；保留样式。[OGC KML](https://www.ogc.org/standards/kml/)

### 批次 4：已完成数据文件扩展

- **SQLite**：调用 SQLite 引擎，以参数化操作改已有行、追加/删除行，从内存事务输出新库。WAL 模式主文件被拒绝，须先生成一致性快照。[SQLite 文件格式与事务日志](https://www.sqlite.org/fileformat.html)
- **Parquet**：用 PyArrow 解析标量 schema 和列数据，按字符串业务键修改/追加后重写，保留 schema metadata 和原 row group 长度。[Apache Parquet 文件格式](https://parquet.apache.org/docs/file-format/)
- **Arrow IPC**：支持 file variant，保留 schema metadata 和原 record batch 长度；stream variant 拒绝。[Arrow Columnar / IPC 格式](https://arrow.apache.org/docs/format/Columnar.html)

### 批次 5：已完成专业对象首个子集

- **DXF**：编辑 modelspace 的 LINE、TEXT、LWPOLYLINE，按 handle 改文字、平移和图层；不处理 DWG。[Autodesk DXF 实体编码](https://help.autodesk.com/cloudhelp/2023/ENU/AutoCAD-DXF/files/GUID-A35B8C2A-1885-4A8E-8533-E61D8A423D62.htm)
- **KiCad 原理图 `.kicad_sch`**：按 UUID 改已放置元件的 Value/Footprint/Datasheet 和位置，保留未改源跨度。当前环境没有 `kicad-cli`，ERC 尚未验证；PCB 另立 profile。[KiCad 文件格式文档](https://dev-docs.kicad.org/en/file-formats/)
- **MusicXML**：按声部/小节/音符改音高、时值和 note playback dynamics；整组后验证小节时值。[MusicXML 4.0](https://www.w3.org/2021/06/musicxml40/)

第 3—5 批完成后，整库测试 **754 项通过**；三十格式演示实际执行 **210 次 CLI 调用、199 项检查**，全部通过。GPX、KML、MusicXML 固定样例的源文件与编辑结果以相应 XML Schema 校验，共 **6/6 有效**。KiCad ERC 与原生加载验证尚未执行。

## 每批的交付口径

交付可发现的 `contract`、可执行 adapter、真实样例和固定 base 计划、语义预览、失败不落盘检查、未修改内容保留检查。涉及排版、公式、拓扑或电气关系时，再增加对应渲染器或领域引擎检查，并在报告中写明未完成的验证。

**既定第 1—5 批均已覆盖明确的可编辑子集；后续扩展应优先补齐原生应用互操作与专业领域验证。**
