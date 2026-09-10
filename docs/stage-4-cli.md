# 阶段 4 功能与使用约定

项目根目录 `D:\Codes\eas-hmi`。PowerShell 中先设置：

```powershell
$eas = ".\.venv\Scripts\eas-hmi.exe"
$project = ".\build\my-stage4-project"
& $eas --project $project init --from-model .\examples\stage4\model.json
```

全局 `--project / --actor / --expected-revision / --auto-render / --json` 放在命令前。
读取默认 JSON，均接受 `--json`；`watch` 输出 JSONL，`diff` 默认可读文本。
历史/事务/错误退出码沿用 [阶段 3 约定](stage-3-cli.md)。

## 查询与局部上下文

```powershell
& $eas --project $project query --kind equipment_card --equipment-type pump --template PUMP --missing-binding fault --json
& $eas --project $project context CARD_A --depth 2 --limit 30 --json
```

query 的 kind/page/equipment/equipment-type/template/missing-binding 全部按 AND 组合。
context 的 depth 默认为 1，范围 0–3；limit 默认 30，范围 1–200。
以节点、设备或点位为入口，按父子、设备引用和 connection 端点关系广度优先遍历。
节点入口永远优先保留 target；设备/点位入口从其直接引用节点开始。
连接节点是一条图边中的中间对象：CARD_A → LINK → CARD_B 需要 depth=2。
`connections` 是连接对象 ID，`connected_nodes` 是已选连接的可见上下文端点。
每类集合有数量上限；opaque XML、图片数据、任意 metadata/style 从 context 省略，
文本/表达式最多保留 512 字符；裁剪/省略会标记 `truncated` 或 `*_omitted`。
精确完整属性可通过 inspect 获取。context 不嵌入整个项目或源 SVG。

## 批量与布局

```powershell
& $eas --project $project batch move --kind equipment_card --equipment-type pump --dx 40
& $eas --project $project batch set --kind data_slot --property width --value 140
& $eas --project $project align --ids CARD_A,CARD_B --mode top
& $eas --project $project distribute --ids CARD_A,CARD_B --axis x --gap 24
& $eas --project $project distribute --ids CARD_A,CARD_B,SHAPE --columns 2 --gap 20
```

batch 仅支持 move/set，不执行 eval。无过滤器时选择所有节点；空选择、锁定对象、
无效属性、非法结果或 stale revision 拒绝整个事务。选择在事务锁内基于候选模型解析。
一次批量动作最多产生一次提交，no-op 不增 revision。
align 支持 left/center-x/top/center-y，基于所选对象包围范围。
distribute x/y 按该轴原位置再按 ID 排序，维持固定间距；columns 使用 ID 顺序、
最大宽高作为网格单元。布局要求同页面、同 parent 且无旋转，否则明确拒绝。
移动/缩放处于各节点 parent 的局部坐标系。

## 九种节点

group、text、shape、image_asset、opaque_svg、data_slot、status_indicator、equipment_card、connection 均可 render。
shape 支持 rect/ellipse/line；组件有边框或椭圆底图和可同步的单行 text。
设备卡、数值槽、状态灯显示模型的设计态 text，不读取实时点值，也不执行 binding expression。
image_asset 要求内嵌且可解码的 PNG/JPEG data URI；不加载外部图片路径/URL。
connection 的 source_ref/target_ref 用于工程关系，视觉线条从自身局部原点指向 content_width/content_height；
移动设备不会自动布线。可直接移动/旋转/缩放 connection。

所有 kind 支持外层语义 frame 的几何/visibility 同步；text 与三个组件的单行文字可同步。
组件的底图样式、内部位置重排、任意富文本等未实现编辑会被拒绝，不会静默覆盖。
opaque 内部安全 artwork 改动继续沿用阶段 2 的保留路径。

## SVG 导入与多页 render

```powershell
& $eas --project .\build\imported import-svg .\input.svg
& $eas --project $project render --output-dir .\build\all-pages
& $eas --project $project render --page components --output .\build\components.svg
```

新目录 import 创建 revision 0；已有工程追加页面并记录 human 事务，可 undo。
重复页面或节点 ID 拒绝，不覆盖现有对象。源文件只读，保持原字节。
已有已知 registry ID 继续使用目标工程定义，导入占位符不会覆盖真实工程属性。

EAS format=1 的可编辑 semantic SVG 可恢复 9 种节点、父子、几何、文字、视觉样式、
equipment/template/point 引用和 connection 端点。完整 SVG 树通过同步器检查，
无法解释的绘图、style、重排、矩阵等返回明确错误。
导出 asset fragment 和外部自定义 data-eas 格式不作为可编辑 semantic SVG 导入。
SVG 没有承载完整 registry：名称、point tag/datatype、模板 required roles、expression、
任意模型 metadata 不能凭空恢复。缺失注册对象创建带 `unresolved_import` 的占位项并报告 WARNING；
point.tag 为空、datatype 为 unknown，模板仅列出观察到的 optional roles。须另行补充原工程数据。

普通 SVG：可独立拆分的顶层对象成为 opaque nodes；既有 XML ID 可追踪，未命名对象生成确定性 ID。
共享 CSS、根合成属性、跨对象/defs 引用保留为一个 composite opaque 节点；
顶层稳定 XML IDs 记录在 metadata 并显式 WARNING，内部 artwork 不被重建。
支持 numeric/px 画布尺寸与有效 viewBox。外部资源、活动内容、超大输入及阶段 2 不支持的 CSS
明确拒绝；源文件和已有工程不受影响。并非通用 SVG/任意 Inkscape 特性转换器。

render 无 --page 时输出全部页面，每个页面是独立自包含 SVG；相同模型输出确定。
多页模式需 --output-dir，单文件 --output 需明确选择一页。

## 清单、资产与事件

```powershell
& $eas --project $project export-manifest
& $eas --project $project export-assets --template PUMP --width 180 --height 100
& $eas --project $project export-assets --id IMAGE --formats png --width 32 --height 32
& $eas --project $project export-assets --page components --formats svg,png,bmp
& $eas --project $project events --since-revision 0 --json
& $eas --project $project watch --since-revision 0 --once --json
& $eas --project $project watch --timeout 10 --json
```

清单默认 build/bindings.csv 和 build/bindings.json，按 page/node/role 排序，每条实际 binding 一行。
CSV UTF-8 BOM，10 个必需字段齐全；没有 binding 的对象不生成空行，缺项通过 validate 查询。
两个清单文件均从同一模型快照生成，各文件原子替换；文件系统错误返回失败，不承诺两文件联合提交。

资产默认导出所有页面，--id/--template/--page 三选一；template 导出其每个实例。
格式默认为 svg,png,bmp。width/height 须同时提供，范围 1–16384 像素。
节点按真实绘图边界裁剪，PNG 保留 alpha，BMP 默认白色不透明背景（可 --background 修改）。
page 保留页面背景。请求尺寸允许非等比缩放，几何由 viewBox 保留。
节点裁剪和 rasterization 需要 Inkscape CLI。
同一批资产先全部暂存成功，再发布 build/assets/r<revision>-<unique>/，附 manifest.json；
失败转换不会发布不完整的批次。工程 model/history 路径禁止作为导出目标。

events 读取已提交事件，since-revision 为排除式游标，-1 包含 init。
watch 默认从当前 revision 等待新事件；--since-revision 可重放；--once 读取一次退出，
--timeout 为秒（0 持续至 Ctrl+C），--interval 默认 0.5 秒。
watch 是当前进程中的 CLI 轮询，不创建后台服务/自动任务/WebSocket/MCP。
