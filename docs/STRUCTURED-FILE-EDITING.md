# EAFS 结构化文件编辑：方法与接口草案

状态：**可运行的 `sfe/0.1` 实验接口**。EAFS 是本方案的暂用名称；协议标识保持 `sfe/0.1`，已有计划继续兼容。这是一份供讨论的提案，不是已经被外部机构采纳的标准。原 v0.2 候选包含 EAS-HMI 与二十九种普通文件 profile，覆盖 JSON、CSV、Markdown、YAML、TOML、SVG、Office、ODF、HTML、EPUB、图示、位置、列式数据和专业对象等三十种可编辑对象。当前开发分支另提供 [Minecraft Java 26.3 世界目录实验 demo](EAFS-MINECRAFT-JAVA-26-3.md)。

## 方法

Agent 直接读取文件的结构信息，定位稳定的对象，然后提交一组声明式编辑指令。执行器解析原格式，在内存中应用整组指令，验证结果，展示差异，最后写出。每一步都返回机器可读结果。图形软件仍可用于人工检查复杂视觉质量，但不参与这里的编辑链。

```text
源文件 / EAS 工程
  → status（格式、版本或哈希、结构摘要）
  → inspect（按对象 ID、JSON Pointer、CSV 键、Markdown 标题读取）
  → plan（纯 JSON 指令，固定 base）
  → preview（全部指令在内存执行、校验、展示语义或文本差异）
  → apply（一次提交或写入新文件）
  → status / inspect（核对结果）
```

核心约束：**定位要明确，修改要受限，旧状态要可核对，失败不产生半套结果**。这比模仿鼠标和屏幕坐标更适合批量、审计和跨 Agent 复现。它也不能自动解决格式本身缺乏语义 ID、渲染质量或隐藏内容恢复的问题。

## `sfe/0.1` 公共接口

原候选三十个 profile 与当前 Minecraft 目录 demo 都提供 `status`、`inspect`、`preview`、`apply` 的同构工作流。Plan 是 JSON 对象：

```json
{
  "protocol": "sfe/0.1",
  "format": "json",
  "base": {"sha256": "源文件字节的 SHA-256"},
  "steps": [{"op": "set", "pointer": "/enabled", "value": true}]
}
```

| 字段 / 行为 | 通用约定 | EAS-HMI profile | 普通文件 profile |
|---|---|---|---|
| `format` | 标识解析器与指令词汇 | `eas-hmi` | `json`、`csv`、`markdown`、`yaml`、`toml`、`svg`、`xlsx`、`docx`、`pptx`、`srt`、`vtt`、`ics`、`vcf`、`jsonl`、`odt`、`ods`、`odp`、`html`、`epub`、`drawio`、`geojson`、`gpx`、`kml`、`sqlite`、`parquet`、`arrow`、`dxf`、`kicad_sch`、`musicxml` |
| `base` | 防止对过期内容应用编辑 | `revision` + `semantic_hash` | 原文件字节 `sha256` |
| `steps` | 顺序执行，整组校验 | 一次 EAS 事务 | 在内存生成一个输出文件 |
| `preview` | 不发布编辑结果 | 语义字段 diff、校验结果、预计 revision | 文本 diff；Office/ODF/EPUB 使用语义 diff + ZIP 部件变化；输出哈希 |
| `apply` | 拒绝旧 base | 更新 EAS HEAD、写审计记录、可 undo | 必须指定新输出路径，保留源文件 |
| 失败 | 返回 `ok:false` 和稳定错误码 | 现有 EAS 退出码 2/3/4 | profile 错误退出 2，I/O 错误退出 4 |

`sfe/0.1` 规定工作流与 plan 封套；**不要求所有文件共享同一种寻址和编辑语义**。文件字节 SHA、Minecraft 目录指纹与 EAS 语义哈希不可互换。实现可以通过 CLI、API 或 MCP 暴露同一组操作，传输层不改变 plan 内容。EAS-HMI、二十九种普通文件 profile 及 Minecraft 目录 demo 已接入本地 [stdio MCP 适配层](EAFS-MCP.md)；现有 EAS Visual MCP 是独立组件。CLI 从磁盘读取计划文件，MCP 的 `file_preview/file_apply` 与 `eas_preview/eas_apply` 将同一 JSON 对象作为 `plan` 参数传入。MCP 提供 5 个 `file_*` 和 8 个 `eas_*` 工具；运行时能力表仍是每种格式参数与限制的依据。

## 已实现的指令集

首批选择 JSON、CSV、Markdown：它们分别覆盖配置/数据交换、表格记录、日常文档，都是文本格式，容易在不同 Agent 主机复用 CLI；同时三者的定位难点不同，适合检验“公共封套 + 格式专用指令”的设计。EAS-HMI 则验证有版本、引用和事务的工程文件。

第二批选择 YAML、TOML、SVG：YAML 覆盖部署和自动化配置；TOML 覆盖项目与服务配置，并检验注释和日期类型保留；SVG 把操作扩展到原生矢量图，以元素 ID 定位图形、标签及属性。它们沿用相同的 plan 封套和旧状态检查。

| Profile | 查看地址 | 修改指令 | 约束 |
|---|---|---|---|
| EAS-HMI | 节点 ID、查询筛选、局部上下文 | `move`、`set`、`resize`、`bind`、`unbind`、`align`、`distribute` | plan 的 revision 和 semantic hash 必须匹配；一次事务 |
| JSON | [JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901) | `add`、`set`、`remove`、`append` | `set`/`remove` 要求地址存在；`add` 可加入对象字段或插入数组；不支持完整 JSON Patch |
| CSV | 列头；`key_column` + `key` 唯一定位一行 | `set-cell`、`append-row`、`delete-row` | UTF-8、逗号分隔、唯一列名；键查找必须恰好命中一行；所有单元格按字符串处理 |
| Markdown | ATX 标题大纲；唯一标题定位章节 | `replace-section`、`append-section` | 跳过 fenced code 中的伪标题；同名标题拒绝；仅支持 ATX 标题定位 |
| YAML | JSON Pointer；`.yaml` / `.yml` | `add`、`set`、`remove`、`append` | YAML 1.2 单文档、字符串键、JSON 值；保留常见注释和引号；拒绝别名等共享引用 |
| TOML | JSON Pointer；日期值以类型标记读取 | `add`、`set`、`remove`、`append` | 根为表；保留未改日期和常见注释；拒绝 null；支持数组和表 |
| SVG | `--id` 唯一元素 ID；无 ID 时返回摘要 | `set-attribute`、`remove-attribute`、`set-text`、`translate` | UTF-8；ID 稳定；文本替换限无子元素文本节点；使用 SVG 属性变换 |
| XLSX | `/sheets/Budget/B5` 或 `/sheets/Budget/A1:C8` | `set-cell`、`set-formula` | 保留单元格样式；清除公式缓存并要求重新计算；执行器不计算公式 |
| DOCX | `/paragraphs/1`、`/tables/1`，正文直属对象从 1 编号 | `set-paragraph`、`set-table-cell`、`append-paragraph` | 保留段落和首个 run 样式；替换可合并原混合字样式；复杂目标拒绝 |
| PPTX | `/slides/1`、`/slides/1/shapes/2`，末位为 shape ID | `set-text`、`move`、`resize` | 独立对象；位置和尺寸用 EMU；文本仅支持明确的普通文字子集 |
| SRT | `/cues/2`，文件内字幕序号 | `set-text`、`shift-time`、`set-timing` | UTF-8；整数毫秒；保留原编号与换行 |
| WebVTT | `/cues/2`，返回原 cue ID 和设置 | `set-text`、`shift-time`、`set-timing` | 保留 NOTE/STYLE/REGION；起始时间有序；内嵌时间戳不重排 |
| ICS | `/events/<UID>` | `set-event-field`、`reschedule` | 单次事件；重排限日期与 UTC 时间；保留时区、提醒与元数据 |
| VCF | `/contacts/<UID>`、`/records/1` | `set-property`、`add-property` | vCard 3.0/4.0；属性名称内序号；保留照片与未知字段 |
| JSONL | `/records/1`、`/by-key/id/P-01/status` | `set-field`、`replace-record`、`append-record`、`delete-record` | 修改要求唯一字符串键；未改行保留；也接受 `.ndjson` |
| ODT | `/paragraphs/2`、`/tables/Actions` | `set-paragraph`、`set-table-cell`、`append-paragraph` | 正文直属段落和普通表格；复杂内联目标拒绝 |
| ODS | `/sheets/Costs/B3` | `set-cell`、`set-formula` | 已有单元格；局部拆分普通重复行列；不计算公式 |
| ODP | `/slides/1/objects/status` | `set-text`、`move`、`resize` | 唯一对象名；独立、明确几何的普通对象；厘米单位 |
| HTML | `/elements/status`，唯一 ID | `set-text`、`set-attribute`、`remove-attribute`、`append-element` | 静态 UTF-8、显式闭合标签；不执行脚本 |
| EPUB | `/chapters/review/elements/status` | `set-metadata`、`set-text`、`set-modified` | 未加密可重排 EPUB 3；保留书脊、导航与资源 |
| draw.io | `/pages/2/cells/pump` | `set-label`、`move`、`resize`、`connect` | 普通 mxCell，支持压缩页 |
| GeoJSON | `/by-id/pump`、`/features/1` | `set-property`、`set-point` | FeatureCollection；点位限 Point |
| GPX | `/routes/1/points/2` 等 | `set-name`、`set-point`、`set-time` | GPX 1.1；保留轨迹分段 |
| KML | `/placemarks/pump` | `set-name`、`set-description`、`set-point` | KML 2.2；直接 Point |
| SQLite | `/tables/equipment/rows/P-01` | `set-cell`、`append-row`、`delete-row` | 单列主键；拒绝 WAL 模式主文件 |
| Parquet | `/by-key/id/P-01` | `set-field`、`append-row` | 普通标量列；按唯一字符串键 |
| Arrow IPC | `/by-key/id/P-01` | `set-field`、`append-row` | file variant；保留批次边界 |
| DXF | `/entities/32` | `set-text`、`translate`、`set-layer` | modelspace 普通 2D 实体 |
| KiCad 原理图 | `/symbols/<UUID>` | `set-property`、`move-symbol` | 已放置元件；ERC 尚待原生工具验证 |
| MusicXML | `/parts/P1/measures/1/voices/1/notes/3` | `set-pitch`、`set-duration`、`set-dynamics` | 普通单声部小节；时值校验 |

路线图中的第 1 批日常文件扩展已完成：字幕条目、事件 UID、联系人属性序号和 JSONL 业务键各自采用明确寻址。新增内容行解析器保留未改折行，修改行按 UTF-8 字节边界折行。逐条示例与限制见 [日常文件指令集](EAFS-DAILY-FILES.md)。

第 2 批办公与阅读扩展也已完成。ODF 与 EPUB 直接编辑指定 ZIP/XML 部件，HTML 按唯一 ID 修改静态源文本；逐条命令、验证和边界见 [开放办公与阅读指令集](EAFS-OFFICE-READING.md)。

第 3—5 批的十种格式也已实现首个可运行子集：图示/位置按对象地址修改，数据文件交由 SQLite 或 PyArrow 解析，专业对象按 handle、UUID 或音符地址操作。操作参数、验证和局限见 [第 3—5 批指令集](EAFS-BATCHES-3-5.md)。

第三批面向日常办公文件。`.xlsx/.docx/.pptx` 使用 [Office Open XML 包结构](https://learn.microsoft.com/en-us/office/open-xml/about-the-open-xml-sdk)，标准定义见 [ECMA-376](https://ecma-international.org/publications-and-standards/standards/ecma-376/)。执行器直接改目标 XML 部件，未修改 ZIP 成员的解压后字节保持一致；整个 ZIP 的压缩字节不保证不变。预览返回 `diff_kind:semantic` 与 `package_parts:{modified,added,removed}`，同时保留应用结果哈希。段落序号和幻灯片序号只在指定 base 下有效。

JSON Pointer 寻址沿用 [RFC 6901](https://www.rfc-editor.org/rfc/rfc6901) 的 `/` 与 `~0`/`~1` 转义。JSON 的四个编辑词是本 profile 的指令，不声称完整实现 [RFC 6902 JSON Patch](https://www.rfc-editor.org/rfc/rfc6902)。CSV 的基础语法参考 [RFC 4180](https://www.rfc-editor.org/rfc/rfc4180)；Markdown 标题和围栏代码块的范围参考 [CommonMark 规范](https://spec.commonmark.org/spec/)，但本实现是受限子集。

YAML 使用 [ruamel.yaml 的 round-trip API](https://yaml.dev/doc/ruamel.yaml/api/)，TOML 使用 [TOML Kit](https://tomlkit.readthedocs.io/en/latest/quickstart/) 保留注释等源信息。SVG 的 `translate` 按 [SVG 变换规则](https://www.w3.org/TR/SVG/coords.html) 在原变换前添加平移，保留原属性变换。`sfe-files contract` 为三个新 profile 返回寻址方式、逐条指令示例和支持边界，供 Agent 直接读取。

## EAS 改造

新增 `eas-hmi agent-contract`，返回可发现的 Plan JSON Schema 和能力表。`eas-hmi apply-plan FILE --dry-run` 在项目锁内执行全计划和现有 EAS 校验，返回语义 diff，不推进 revision。去掉 `--dry-run` 后，同一计划作为**一个事务**提交；任何一步失败，HEAD 不变。Plan 使用当前 `status` 返回的 `revision` 和 `canonical_hash`；两者都必须匹配。原有 `query`、`inspect`、`context`、`diff`、`history`、`undo` 继续可用。

```powershell
python -m eas_hmi.cli --project build/sfe-eas init --from-model examples/stage4/model.json
python -m eas_hmi.cli --project build/sfe-eas status
python -m eas_hmi.cli --project build/sfe-eas inspect CARD_A
python -m eas_hmi.cli --project build/sfe-eas apply-plan examples/sfe/eas.plan.json --dry-run
python -m eas_hmi.cli --project build/sfe-eas apply-plan examples/sfe/eas.plan.json
python -m eas_hmi.cli --project build/sfe-eas diff --json
```

示例 plan 绑定仓库中的原始 `examples/stage4/model.json`，因此初始化一个全新工程后可直接运行。再次应用会因 revision 过期而拒绝。生成自己的 plan 时，先取 `status` 里的 `revision` 和 `canonical_hash` 填入 `base`。

各格式的可直接运行命令和计划位于 [示例说明](../examples/sfe/README.md)。运行 `python scripts/demo_sfe.py` 可取得三十种格式的实际编辑结果和逐条 CLI 记录。后续候选与分批统计见 [EAFS 格式路线图](EAFS-FORMAT-ROADMAP.md)。

## 适合标准化的边界

可固定的部分：`describe/status/inspect/preview/apply` 的含义；版本或哈希前置条件；纯数据操作；机器可读错误；差异和结果哈希；格式能力发现。应留给各格式 profile 的部分：地址类型、可编辑字段、验证规则、序列化和撤销方式。例如 CSV 需要业务键，Markdown 的自然标题不保证唯一，EAS 节点有稳定 ID。

建议按三个层次推广：先让工具作者复用封套和返回值；再给每种格式发布独立、可测试的 profile；最后用实际跨实现互操作测试决定是否提交正式标准提案。现有 MCP 适配层承载 `contract/status/inspect/preview/apply` 等工具；它是传输/发现层，不代替文件格式的编辑语义。本地 stdio 测试不构成跨 host 兼容性证明。当前 [MCP 规范](https://modelcontextprotocol.io/specification/) 有自己的版本和兼容要求。

## 现阶段限制

- JSON 重排为 UTF-8、两空格缩进，可能改变原始空白；重复键与非标准数字被拒绝。
- CSV 保留输入的 UTF-8 BOM 和首行换行风格，但字段引号可能被规范化。未处理方言自动检测和公式语义。
- Markdown 只识别顶层 ATX 标题及围栏代码块；Setext、容器内标题、复杂扩展语法需要独立 parser profile。
- YAML 仅支持单文档、字符串键和 JSON 类型；重复键、锚点、别名、merge、自定义标签、日期与非有限数字明确拒绝。缩进可能规范化，替换整个子树会替换其内部注释。
- TOML 保留现有日期、时间和注释；inspect 的类型标记用于读取，修改计划不提供日期构造器。null 无对应值，嵌套 null 也拒绝。替换整表或数组会重建该子树。
- SVG 按唯一 ID 操作，禁止修改/删除 ID；DTD 和实体被拒绝。CSS 可能覆盖 presentation/transform 属性，接口不宣称验证所有 SVG 视觉语义；文本替换不会展开或删除子节点。
- 普通文件 profile 写新路径，没有内置历史。EAS-HMI 则使用自身审计链与 undo。
- Office 支持常见 Transitional OOXML 子集；旧二进制 `.xls/.doc/.ppt` 需先另存现代格式，仅改扩展名无效。加密、宏和签名包不在当前范围。
- XLSX 不插入行列、不管理工作表和样式、不计算公式；公式缓存失效后由支持的电子表格引擎重新计算。图表等派生缓存需要应用程序刷新。
- DOCX 的正文段落和普通表格可编辑；页眉页脚、图片等部件保留。PPTX 只编辑支持的独立对象，复杂文字和分组需扩展 profile。语义预览不证明分页、溢出或完整排版正确。
- ODT/ODS/ODP 只支持明确的普通结构；ODS 公式缓存被清除但不重新计算。未修改包部件保留，复杂布局、分组对象及跨表引用关系需要独立检查。
- HTML 处理静态 UTF-8 文件和唯一 ID，要求显式闭合非空元素；不执行脚本或读取浏览器运行态。
- EPUB 限未加密、未签名、可重排 EPUB 3；只改已有元数据和普通 XHTML 叶文本，不改导航和书脊，也不验证阅读器排版。
- 批次 3—5 的明确支持范围见 [专门说明](EAFS-BATCHES-3-5.md)：尤其是 SQLite WAL 主文件拒绝、Parquet/Arrow 标量类型子集，以及 KiCad ERC/CAD 排版尚需原生工具验证。
- 本仓库测试证明三十个本地 profile 的行为，不构成其他软件、Agent host 或平台的互操作认证。
