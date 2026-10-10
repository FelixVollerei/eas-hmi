# EAFS 第 1 批：字幕、日历、联系人与逐行记录

已实现 SRT、WebVTT、ICS、VCF、JSONL 五个 profile，沿用 `sfe/0.1` 计划封套。全部提供 `status`、`inspect`、`preview`、`apply`；预览展示文本差异和输出哈希，应用要求新输出路径。输入 SHA-256 过期或任何一步失败，都不写出结果。

没有新增格式专用运行时依赖。以下 CLI 命令在仓库根目录执行；先运行 `python -m pip install -e '.[dev]'`，或使用现有 `.venv/Scripts/python.exe`。也可安装 `.[mcp]`，由 `file_contract` 发现指令，并将相同计划作为 JSON 对象传给 `file_preview` / `file_apply`；连接方式见 [MCP 适配层说明](EAFS-MCP.md)。

## 指令一览

| Profile | 查看地址 | 修改指令 | 范围 |
|---|---|---|---|
| SRT | `/cues/2` | `set-text`、`shift-time`、`set-timing` | 带编号的 UTF-8 字幕；整数毫秒 |
| WebVTT | `/cues/2` | `set-text`、`shift-time`、`set-timing` | 保留 cue ID、settings、NOTE、STYLE、REGION |
| ICS | `/events/review-1@example.test` | `set-event-field`、`reschedule` | 单次 VEVENT；重排支持 DATE 或 UTC 时间 |
| VCF | `/contacts/<UID>` 或 `/records/1` | `set-property`、`add-property` | vCard 3.0/4.0；重复属性按名称内序号定位 |
| JSONL | `/records/1` 或 `/by-key/id/P-01/status` | `set-field`、`replace-record`、`append-record`、`delete-record` | 读取任意 JSON 值；修改要求对象记录及唯一字符串键 |

序号从 1 开始，只在计划指定的 base 下有效。UID 和业务键区分大小写；地址里的 `/`、`~` 使用 JSON Pointer 的 `~1`、`~0` 转义。

## SRT 与 WebVTT

```sh
python -m sfe_files.cli status examples/sfe/captions.srt
python -m sfe_files.cli inspect examples/sfe/captions.srt --pointer /cues/2
python -m sfe_files.cli preview examples/sfe/captions.srt examples/sfe/captions.plan.json
python -m sfe_files.cli apply examples/sfe/captions.srt examples/sfe/captions.plan.json --output build/captions-edited.srt

python -m sfe_files.cli inspect examples/sfe/subtitles.vtt --pointer /cues/2
python -m sfe_files.cli preview examples/sfe/subtitles.vtt examples/sfe/subtitles.plan.json
python -m sfe_files.cli apply examples/sfe/subtitles.vtt examples/sfe/subtitles.plan.json --output build/subtitles-edited.vtt
```

样例计划修改第 2 条字幕，并把所有字幕延后 250 毫秒。可用指令：

```json
[
  {"op":"set-text","cue":2,"text":"The pump is ready for commissioning."},
  {"op":"shift-time","delta_ms":250},
  {"op":"set-timing","cue":3,"start_ms":7250,"end_ms":9500}
]
```

`shift-time` 可增加 `"cues":[1,2]` 限定条目；省略即全部。`cue` 指文件中的第几条字幕，不是 SRT 原始编号或 VTT identifier。查看结果会同时给出原始标识。

输入和输出保留 BOM、换行和未改块；VTT 的标题、备注、样式、区域和 cue settings 保留。允许时间重叠，但起始时间必须非负、结束晚于开始；VTT 起始时间还必须按文件顺序非递减。含内嵌时间戳的 VTT cue 暂不改时间，以免外层时间与逐字显示时间脱节。文本不允许空白分隔行，VTT 文本不允许保留字 `-->`。不新增或删除 cue，也不验证字幕与视频是否同步。[WebVTT 规范](https://www.w3.org/TR/webvtt1/)

## ICS 日历文件

```sh
python -m sfe_files.cli status examples/sfe/schedule.ics
python -m sfe_files.cli inspect examples/sfe/schedule.ics --pointer /events/review-1@example.test
python -m sfe_files.cli preview examples/sfe/schedule.ics examples/sfe/schedule.plan.json
python -m sfe_files.cli apply examples/sfe/schedule.ics examples/sfe/schedule.plan.json --output build/schedule-edited.ics
```

```json
[
  {"op":"set-event-field","uid":"review-1@example.test","field":"LOCATION","value":"Room B"},
  {"op":"reschedule","uid":"review-1@example.test","start":"2026-10-01T03:00:00Z","end":"2026-10-01T04:00:00Z"}
]
```

可修改 `SUMMARY`、`DESCRIPTION`、`LOCATION`；值自动处理逗号、分号、反斜杠与换行转义。日期事件用 `YYYY-MM-DD`，UTC 事件用 `YYYY-MM-DDTHH:MM:SSZ`，需保持原类型。结束日期或时间按 iCalendar 规则为排他边界。[RFC 5545](https://www.rfc-editor.org/info/rfc5545/)

循环事件、重复 UID、不明确的字段和复杂目标会被拒绝。TZID 和浮动时间事件可改普通文字字段，暂不重排时间；DURATION 事件也暂不重排。保留 VTIMEZONE、VALARM、未知属性及原始折行。相对提醒跟随事件的新时间，绝对提醒保持原时点。`DTSTAMP`、`SEQUENCE`、`LAST-MODIFIED` 保留，确保预览和应用得到相同结果；这里只编辑本地文件，不发送邀请或同步日程。

## VCF 联系人文件

```sh
python -m sfe_files.cli status examples/sfe/contacts.vcf
python -m sfe_files.cli inspect examples/sfe/contacts.vcf --pointer /records/1
python -m sfe_files.cli inspect examples/sfe/contacts.vcf --pointer /contacts/urn:uuid:5b30ba4a-8bc5-4a90-a37e-96c34dbaeff1
python -m sfe_files.cli preview examples/sfe/contacts.vcf examples/sfe/contacts.plan.json
python -m sfe_files.cli apply examples/sfe/contacts.vcf examples/sfe/contacts.plan.json --output build/contacts-edited.vcf
```

```json
[
  {"op":"set-property","uid":"urn:uuid:5b30ba4a-8bc5-4a90-a37e-96c34dbaeff1","name":"EMAIL","index":2,"value":"li.support@example.com"},
  {"op":"set-property","uid":"urn:uuid:5b30ba4a-8bc5-4a90-a37e-96c34dbaeff1","name":"ORG","index":1,"value":["EAS","Field Service"]},
  {"op":"add-property","uid":"urn:uuid:5b30ba4a-8bc5-4a90-a37e-96c34dbaeff1","name":"TEL","params":";VALUE=uri;TYPE=work","value":"tel:+66-2-555-0100"}
]
```

支持 FN、TITLE、ROLE、NOTE、EMAIL、TEL、URL、ORG；ORG 使用组织层级的字符串数组。`index` 是该属性名内从 1 开始的出现序号，包含不同 group。替换保留原 group 和参数；新增可指定 `params` 与 `group`。同名多个邮箱、电话不会被整组覆盖。

vCard 4.0 TEL 默认为文本，明确 `VALUE=uri` 才按 URI 处理；3.0 TEL 按电话号码文本处理。URI 不使用 TEXT 反斜杠转义。[RFC 6350 §6.4.1](https://www.rfc-editor.org/rfc/rfc6350#section-6.4.1)

不修改 N、ADR、PHOTO、未知字段和 REV。无 UID 联系人可以按记录号查看，编辑需要 UID；重复 UID 被拒绝。修改的内容行按 75 个 UTF-8 字节折行，不拆字符，未改内容行保留原字节。2.1 与旧字符编码暂不支持。

## JSONL 逐行记录

```sh
python -m sfe_files.cli status examples/sfe/records.jsonl
python -m sfe_files.cli inspect examples/sfe/records.jsonl --pointer /by-key/id/P-01/status
python -m sfe_files.cli preview examples/sfe/records.jsonl examples/sfe/records.plan.json
python -m sfe_files.cli apply examples/sfe/records.jsonl examples/sfe/records.plan.json --output build/records-edited.jsonl
```

```json
[
  {"op":"set-field","key_column":"id","key":"P-01","pointer":"/status","value":"ready"},
  {"op":"append-record","key_column":"id","record":{"id":"T-03","status":"inspection"}}
]
```

`set-field` 修改已存在的 JSON Pointer；整条替换用 `replace-record`，删除用 `delete-record`。所有记录必须是对象，指定业务键必须为唯一字符串；修改业务键后也要保持唯一。未改行保留原始空白和换行，改动行写为紧凑 JSON。追加到没有末尾换行的文件时会补必要分隔符。

`.ndjson` 是同一 profile 的扩展名别名。输入可读取数字、数组、null 等任意 JSON 值，但这类记录不满足按业务键修改的要求。拒绝 BOM、空白行、重复对象键、NaN 和 Infinity；空文件视为空数据集。当前整文件在内存验证，不支持压缩或流式超大文件。[JSON Lines 格式说明](https://jsonlines.org/)

## 完整计划与演示

以上数组只是 `steps` 示例，执行时请使用旁边的固定 `.plan.json`。自己的计划应先读取 `status.sha256`：

```json
{
  "protocol":"sfe/0.1",
  "format":"jsonl",
  "base":{"sha256":"源文件字节哈希"},
  "steps":[{"op":"set-field","key_column":"id","key":"P-01","pointer":"/status","value":"ready"}]
}
```

运行 `python scripts/demo_sfe.py` 可一次执行现在的 30 个 profile，并在新的 `build/sfe-trial-<时间>/` 目录保存结果、逐条 CLI 日志和报告。既定第 1—5 批已全部实现明确子集，见 [格式路线图](EAFS-FORMAT-ROADMAP.md)。
