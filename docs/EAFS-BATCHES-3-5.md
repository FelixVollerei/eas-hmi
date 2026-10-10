# EAFS 批次 3—5：十种文件格式指令集

批次 3—5 新增 draw.io、GeoJSON、GPX、KML、SQLite、Parquet、Arrow IPC、DXF、KiCad 原理图和 MusicXML，统一使用 `sfe/0.1` 计划封套。先运行 `status` 取得源文件 SHA-256，再用 `inspect` 选择目标，提交绑定该哈希的 JSON plan。`preview` 在内存验证整组指令并给出语义差异；`apply --output` 只写新路径。旧 base 或任一步失败时不写输出。

`python -m sfe_files.cli contract` 返回全部操作的参数示例和限制。下表中的 `inspect` 前缀均为 `python -m sfe_files.cli`，每个样例的 plan 可以直接运行 `preview 源文件 plan文件`。MCP 的 `file_contract(format_name)` 提供相同的能力发现，`file_preview` / `file_apply` 直接接收计划 JSON 对象；连接方式见 [MCP 适配层说明](EAFS-MCP.md)。

| 批次 | 格式与样例 | 查看地址 | 修改指令 |
|---|---|---|---|
| 3 | [draw.io](../examples/sfe/network.drawio) · [plan](../examples/sfe/network.plan.json) | `/pages/2/cells/pump` | `set-label`、`move`、`resize`、`connect` |
| 3 | [GeoJSON](../examples/sfe/sites.geojson) · [plan](../examples/sfe/sites.plan.json) | `/by-id/pump` 或 `/features/1` | `set-property`、`set-point` |
| 3 | [GPX](../examples/sfe/walk.gpx) · [plan](../examples/sfe/walk.plan.json) | `/routes/1/points/2` | `set-name`、`set-point`、`set-time` |
| 3 | [KML](../examples/sfe/plant.kml) · [plan](../examples/sfe/plant.plan.json) | `/placemarks/pump` | `set-name`、`set-description`、`set-point` |
| 4 | [SQLite](../examples/sfe/equipment.sqlite) · [plan](../examples/sfe/equipment.plan.json) | `/tables/equipment/rows/P-01` | `set-cell`、`append-row`、`delete-row` |
| 4 | [Parquet](../examples/sfe/inventory.parquet) · [plan](../examples/sfe/inventory.plan.json) | `/by-key/id/P-01` | `set-field`、`append-row` |
| 4 | [Arrow IPC](../examples/sfe/telemetry.arrow) · [plan](../examples/sfe/telemetry.plan.json) | `/by-key/id/P-01` | `set-field`、`append-row` |
| 5 | [DXF](../examples/sfe/layout.dxf) · [plan](../examples/sfe/layout.plan.json) | `/entities/32` | `set-text`、`translate`、`set-layer` |
| 5 | [KiCad 原理图](../examples/sfe/control.kicad_sch) · [plan](../examples/sfe/control.plan.json) | `/symbols/e2b62867-7d5d-4a92-9064-d715d42a6657` | `set-property`、`move-symbol` |
| 5 | [MusicXML](../examples/sfe/melody.musicxml) · [plan](../examples/sfe/melody.plan.json) | `/parts/P1/measures/1/voices/1/notes/3` | `set-pitch`、`set-duration`、`set-dynamics` |

例如，对压缩 draw.io 页面改标签、位置、尺寸和连线：

```sh
python -m sfe_files.cli status examples/sfe/network.drawio
python -m sfe_files.cli inspect examples/sfe/network.drawio --pointer /pages/2/cells/pump
python -m sfe_files.cli preview examples/sfe/network.drawio examples/sfe/network.plan.json
python -m sfe_files.cli apply examples/sfe/network.drawio examples/sfe/network.plan.json --output build/network-edited.drawio
```

`python scripts/demo_sfe.py` 运行全部三十个 profile，保存每次真实 CLI 调用、编辑结果及 `results.json`。历史本地实跑完成 210 次 CLI 调用、199 项检查，全部通过；该原始运行位于未提交的 `build/` 目录，不作为公开仓库证据。v0.2 候选的可复验命令与结果见 [发布报告](EAFS-V0.2-RELEASE.md)。演示独立读取这些新格式的输出值，并检查源文件不变、预览哈希与输出一致、旧计划拒绝、整组失败不落盘。

2026-09-27 整库 `pytest -q` 结果为 **754 passed**。GPX、KML、MusicXML 固定样例的源文件与编辑结果通过相应 XML Schema，共 6/6 个文档通过。

## 支持范围和验证边界

- **draw.io**：支持普通 `mxCell` 的未压缩和 URL 转义 raw-DEFLATE 页面。按 cell ID 编辑普通节点和边；复杂 group、相对几何及路由视觉效果不处理。
- **GeoJSON**：至多 10000 个 Feature；唯一字符串 ID 或 base 下 1 起始索引。点位编辑限 Point，存在 bbox 时拒绝，以免留下过期边界。
- **GPX / KML**：GPX 保留路线及轨迹分段，修改现有点属性；KML 按唯一 Placemark ID 处理直接 Point。固定样例的源文件和编辑结果以相应 XML Schema 校验通过。复杂视图、样式网络和外部资源不改。
- **SQLite**：使用 SQLite 引擎在内存快照上执行参数化编辑，输出新库并做 `quick_check`。WAL 模式主文件被拒绝，因为其 SHA-256 无法覆盖 WAL 中尚未并入的提交；可先由 SQLite 生成一致性快照。写操作要求单列主键。
- **Parquet / Arrow IPC**：通过 PyArrow 解析并重写已有 schema 的普通标量列，按唯一字符串业务键修改或追加。保留 schema metadata、Parquet 原有 row group 长度及 Arrow file variant 原有 record batch 长度；追加产生一组/批。编码和压缩字节不保证保持原样，Arrow stream variant 拒绝。
- **DXF**：通过 ezdxf 处理 ASCII DXF 的 modelspace LINE、TEXT、LWPOLYLINE，按 handle 定位。未处理块内容、DWG 或 CAD 视觉排版；每次同计划写出固定字节，便于预览哈希核对。
- **KiCad `.kicad_sch`**：按已放置元件 UUID 修改现有 Value、Footprint、Datasheet 或元件及属性位置。解析并保留未改 S-expression 源跨度。移动元件不移动连线端点，当前环境无 `kicad-cli`，尚未执行 KiCad ERC 或原生加载验证；用于工程前应补做两项检查。
- **MusicXML**：限 4.0 `score-partwise` 的普通单声部小节，支持音高、时值及 note playback dynamics。整组计划完成后核对小节时值和节拍，固定样例的源文件与编辑结果通过 MusicXML 4.0 XSD。未做乐谱渲染或音频合成。

标准与实现依据：[draw.io XML](https://www.drawio.com/docs/manual/advanced/diagram-source-edit/)、[GeoJSON RFC 7946](https://www.rfc-editor.org/rfc/rfc7946)、[GPX 1.1](https://www.topografix.com/gpx/1/1/)、[OGC KML](https://www.ogc.org/standards/kml/)、[SQLite 文件格式](https://www.sqlite.org/fileformat.html)、[Parquet](https://parquet.apache.org/docs/file-format/)、[Arrow IPC](https://arrow.apache.org/docs/format/Columnar.html)、[Autodesk DXF](https://help.autodesk.com/view/OARX/2023/ENU/?guid=GUID-235B22E0-A567-4CF6-92D3-38A2306D73F3)、[KiCad 原理图](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/)、[MusicXML 4.0](https://www.w3.org/2021/06/musicxml40/)。
