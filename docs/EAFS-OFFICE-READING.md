# EAFS 第 2 批：开放办公与阅读文件指令集

本批新增 ODT、ODS、ODP、HTML、EPUB 五个可运行 profile。它们沿用 `sfe/0.1`：先 `status` 取得源文件 SHA-256，用 `inspect` 确定地址，再以绑定该哈希的 JSON plan 执行 `preview` 与 `apply --output`。`apply` 写新文件；旧 base 和整组计划中任一步失败时不写输出。

在仓库根目录运行 `python -m sfe_files.cli contract` 可取得每个操作的完整参数示例及限制。下列固定样例可直接执行；先把 `preview` 换为 `apply` 并加 `--output build/文件名` 即可保存结果。MCP 的 `file_contract(format_name)` 提供相同的能力发现，`file_preview` / `file_apply` 直接接收计划 JSON 对象；连接方式见 [MCP 适配层说明](EAFS-MCP.md)。

| 格式 | 读取示例 | 样例计划 | 修改词汇 |
|---|---|---|---|
| ODT | `inspect examples/sfe/notes.odt --pointer /paragraphs/2` | [notes.plan.json](../examples/sfe/notes.plan.json) | `set-paragraph`、`set-table-cell`、`append-paragraph` |
| ODS | `inspect examples/sfe/costs.ods --pointer /sheets/Costs/B3` | [costs.plan.json](../examples/sfe/costs.plan.json) | `set-cell`、`set-formula` |
| ODP | `inspect examples/sfe/briefing.odp --pointer /slides/1/objects/status` | [briefing.plan.json](../examples/sfe/briefing.plan.json) | `set-text`、`move`、`resize` |
| HTML | `inspect examples/sfe/page.html --pointer /elements/status` | [page.plan.json](../examples/sfe/page.plan.json) | `set-text`、`set-attribute`、`remove-attribute`、`append-element` |
| EPUB | `inspect examples/sfe/handbook.epub --pointer /chapters/review/elements/status` | [handbook.plan.json](../examples/sfe/handbook.plan.json) | `set-metadata`、`set-text`、`set-modified` |

命令中的 `inspect` 前缀均为 `python -m sfe_files.cli`。例如：

```sh
python -m sfe_files.cli status examples/sfe/costs.ods
python -m sfe_files.cli inspect examples/sfe/costs.ods --pointer /sheets/Costs/B3
python -m sfe_files.cli preview examples/sfe/costs.ods examples/sfe/costs.plan.json
python -m sfe_files.cli apply examples/sfe/costs.ods examples/sfe/costs.plan.json --output build/costs-edited.ods
```

## 样例实际修改

- ODT：第 2 个直属段落改为 `Approved for staging.`，`Actions` 表格第 2 行第 2 列改为 `Complete`，末尾追加段落。
- ODS：把重复行中的 B3 改为 `75`，A4 改为 `现场调试`，C5 改用 `of:=SUM([.B2:.B4])`。重复行/单元格只在目标周围拆分，公式缓存清除，结果须由表格引擎重算。
- ODP：把第 1 页 `status` 对象文字改为 `Approved for staging`，位置设为 `(2.5,5)` 厘米，尺寸设为 `21×2.5` 厘米。
- HTML：把 `status` 元素文字和 class 改为已就绪状态、删去 title，并在 `actions` 容器中追加带 ID 的段落。修改点外的源文字保持原样。
- EPUB：修改书名、`review` 章节的 `status` 元素文字，并显式更新 `dcterms:modified` UTC 时间；阅读顺序、导航、媒体和样式资源保留。

ODF 和 EPUB 在 ZIP 包内按 XML 结构编辑。预览提供语义差异、变化部件列表和预计输出哈希；未改 ZIP 成员的解压后内容保持逐字节一致。HTML 的 `preview` 返回源文本差异。固定样例已通过 ODF 1.3 Relax NG（ODT/ODS/ODP 的源文件及编辑结果）与 EPUBCheck 5.4.0（EPUB 源文件及编辑结果，零错误、零警告）；这些检查没有覆盖排版效果。

## 当前支持边界

- ODT 处理正文直属段落和普通表格；复杂内联对象、受保护或合并表格目标拒绝编辑。替换混合样式文字会合并原有字样式，变化出现在语义预览中。
- ODS 只编辑已有的普通单元格，支持受限重复行列与 OpenFormula；不计算公式，不重排引用，图表/透视缓存可能需要外部刷新。
- ODP 处理有唯一名称、明确几何的独立文本框/常用形状；分组、变换、母版和备注不作为编辑目标。
- HTML 要求 UTF-8、显式闭合非空元素、唯一 ID；静态文件操作不执行脚本或求 CSS 布局。`.htm` 是同一 profile 的扩展名别名。
- EPUB 限未加密、未签名、可重排的 EPUB 3 单包及普通 XHTML 叶文本。暂不增删章节或更改导航、manifest、图片、链接和样式。

标准依据：[OASIS OpenDocument 1.3](https://docs.oasis-open.org/office/OpenDocument/v1.3/os/part2-packages/OpenDocument-v1.3-os-part2-packages.html)、[HTML Standard](https://html.spec.whatwg.org/multipage/)、[EPUB 3.3](https://www.w3.org/TR/epub-33/)。本地 profile 的通过结果不构成通用软件兼容性认证。
