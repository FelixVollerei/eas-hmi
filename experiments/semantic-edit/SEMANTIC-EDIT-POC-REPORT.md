# SEMANTIC EDIT POC REPORT

实验日期：2026-09-14。基于 EAS 0.1.1 当前仓库及上一轮 `REPORT.md`。本轮仅验证 Pixel-faithful Baseline + Lazy Semantic Decomposition + Local Semantic Edit；没有开发插件、MCP、Harness、适配器或 UI，没有修改 EAS Core。

**结论：核心假设在本轮两个人工辅助定位的局部上成立。可以按需提升局部对象、执行床的空间移动以及嘴部 mock/真实生成式修改，同时让编辑掩膜外的 EAS 最终渲染逐像素保持不变。分割质量、背景修补和生成接缝仍是局部质量问题；这个结果不等于已经完成 Raster → editable SVG。**

## 1. 验收结果

这里的真值为上一轮已经验证的 **64 色 pixel-faithful baseline**。原始未减色位图另存 `input.png`，没有把减色误差算成零。所有主要指标均从 **EAS 导出的 `edited.svg` 经 Inkscape 实际渲染的 `edited.png`** 计算。

|案例|操作|允许编辑像素 / 占整图|未编辑区域 MAE|未编辑区域变化像素|最大绝对差|
|---|---|---:|---:|---:|---:|
|A：床移动|右移 12 px、下移 26 px|9,872 / 2.0933%|0|0 / 461,728（0%）|0|
|B1：嘴部 mock|确定性闭嘴微笑|8,589 / 0.3685%|0|0 / 2,322,411（0%）|0|
|B2：真实生成 patch|闭嘴、温和微笑|8,589 / 0.3685%|0|0 / 2,322,411（0%）|0|

三例还同时满足：

- 原 baseline 文件与外部输入 SHA-256 未变；baseline 对应的 EAS opaque payload SHA-256 在编辑前后未变。
- 按需提取并提升对象后的 `promoted.png` 与 baseline **整图逐像素一致**。
- baseline 重新渲染误差为零；最终 EAS 渲染与独立 NumPy 预期合成结果整图一致。
- 编辑掩膜外紧邻边缘的 1 像素带也为零误差。
- 掩膜外人为翻转一个像素的一个通道的最低位，三个案例的负对照均检测到恰好一个变化像素、最大差 1。
- 保存的 provider patch 重放后，三个案例的最终 **RGBA 像素** 均与首次实验一致。

主实验 27 次子进程调用、重放 27 次，均无非零退出；每例包含 6 次 EAS CLI 操作和 3 次 Inkscape 渲染。新增 9 项针对性测试通过；新代码 Ruff 检查通过。没有重跑 EAS 全部历史测试，也没有据此宣称此前偶发原生崩溃已解决。

## 2. 实际实现的最小 Scene IR

入口为 `scripts/probe_semantic_edit.py`。配置、重放脚本和针对性测试位于 `experiments/semantic-edit/`。

每个案例保存三个状态：`scene-initial.json`（无 semantic object）、`scene-promoted.json`（用户请求触发提升）、`scene.json`（编辑完成）。核心字段为：

```text
scene
  baseline: SVG/PNG 路径、原始输入、SHA-256、尺寸、不可变约定
  semantic_objects[]:
    id / semantic_label / bbox / mask / source_region / extracted
    vector_payload / transform / metadata / 可选 replacement
  residual_scene:
    baseline 引用 / 局部 repair overlay / 修复方法与 donor
  edit_contract:
    预先固定的 editable-mask、哈希、margin、验收目标
  history / output
```

`semantic_label` 是实验侧语义，不是新建的 EAS 家具类型。EAS 继续使用现有 `opaque_svg`：底稿一个节点、对象一个局部节点、修补或替换一个局部节点。对象拥有独立 ID、局部 viewport 和正常的 EAS 位置属性。Scene IR 在外部记录 mask、标签和来源，不要求 EAS Core 理解“床”或“嘴”。

残余场景采用 **不可变底稿 + 局部覆盖层** 表达。没有把原图永久挖掉；提升时对象在原位置覆盖同样的像素，移动时启用原区域修补层，再平移对象。底稿中被遮盖的信息一直保留。这是局部合成表示，不是已经把全图拆成互不相交的理想语义图层。

每次仅对被选中的局部 masked pixels 生成矢量路径；**没有重新矢量化整张 edited PNG**，没有嵌入位图。床对象含 1,895 个矩形子路径，床原位置修补含 2,754 个；嘴部源对象含 703 个，真实生成后的局部替换含 6,474 个。底稿仍然保留原有大量矩形，不追求这一阶段的结构压缩。

## 3. Case A：床的结构移动

输入使用上一轮云溪户型图，655×720。床轮廓相对明确，选定 bbox 为 `[229,416,330,484)`，多边形顶点完整保存在配置和 Scene IR。掩膜面积 6,433 像素。

定位方式是 **Agent 查看图像后的人工辅助多边形**；没有调用自动分割模型，也没有独立分割真值。因此不报告 segmentation IoU，也不声称达到干净抠图质量。

操作链：提取床的原始 RGB 与二值 alpha → 转为局部纯矢量对象 → 从原图指定地板 donor `[251,489,319,517)` 周期复制纹理修补原位置 → 通过真实 CLI `set residual-repair-001 visible true` 启用修补 → `move semantic-object-001 --dx 12 --dy 26` → EAS 导出和渲染。

editable mask 在操作前定义为 **原对象及 2 px 修补 margin ∪ 平移后的目标 mask**。源和目标可以重叠：先修复源，再在目标放置原像素，避免重叠部分误删或覆盖顺序错误。床的位置从 bbox 左上角 `(229,416)` 移至 `(241,442)`。本轮没有实现 swap；用户要求的最少一种空间操作 move 已验证。

|检查|结果|证据与限制|
|---|---|---|
|对象提取并独立操作|通过|`object-extracted.png`、`object.svg`、CLI 操作记录|
|床自身外观 MAE / 最大差 / 变化率|0 / 0 / 0%|比较源 mask 像素与平移后目标 mask 像素；不是与整块 bbox 比较|
|新位置放置|通过|目标 mask 中与预期像素完全一致|
|腾空原区域写入修补|通过|源修补区域减去目标区域共 3,439 px，与修补估计完全一致|
|真实隐藏地板恢复|未证明|不存在被床遮挡部分的真实地板图；对应质量误差为 null|
|边缘与接缝|部分通过|床可辨认且移动正确；多边形带有少量边缘地板，纹理复制可能出现重复和接缝|

局部查看 `detail.png` 可以看到床已移动、卧室文字未变化、床头柜等未被重新生成。提取 mask 不是经过严格标注的干净实例掩膜，不能从“床内像素 MAE 0”推出分割准确。背景修补是当前空间编辑质量的主要瓶颈；“与修补估计 MAE 0”仅证明程序按计划写入，绝不是修复质量满分。

## 4. Case B：嘴部局部修改

输入使用上一轮 Kasuga 少女插画，1110×2100。嘴部基础 mask 为 7,281 px；其 3 px 方形扩张 margin 后 editable mask 为 8,589 px。同一固定 mask 用于 mock 和真实生成版，没有按生成结果扩大允许区域。

上下文裁剪 bbox 为 `[280,290,760,670)`，即 **480×380**，包含嘴、眼睛、脸缘和少量衣领，用于维持局部风格与位置。提交模型的只有这一裁剪图；未提交全图。真正回写的区域仅占全图 0.3685%，其余上下文只供参考。

### B1：确定性 mock

mock 用肤色填充旧嘴部并画一条闭嘴弧线，另外故意给整个返回上下文施加 `[+2,-1,+1]` 色偏作为越界污染对照。它没有调用模型，不代表生成式能力。

返回上下文在嘴部 mask 之外有 100% 像素改变；程序在回写时只采纳固定 support 内的像素，最终全图未编辑区域变化为 0。mock 的下巴出现了重复轮廓线，**局部美观性失败**，原始结果保留，没有抹去或替换成更好看的结果。其价值是验证管线隔离与负对照。

### B2：真实生成式 patch

实际调用一次内置 `image_gen` 编辑工具，目标是把张嘴改为闭嘴温和微笑。原始 prompt、输入 crop SHA-256、返回文件和 provider record 均已保存。工具没有在返回接口提供精确模型 ID 或 seed，本报告不猜测这些信息。

模型没有遵守请求的 480×380 输出尺寸，实际返回 **1409×1116**。脚本默认拒绝尺寸不符的 patch；本次明确传入 `--allow-patch-resize`，仅对该 patch 用 LANCZOS 归一化到 480×380，处理已记录。原始模型返回文件始终保留。

尺寸归一化后的返回 patch 在原始嘴部 mask 之外：MAE **4.8864**、变化像素 **98.8368%**、最大通道差 **136**。这包含模型改动与尺寸归一化影响，不能解释为模型原生逐像素编辑质量。事实说明，不能依赖 prompt 保证周边不变。

程序只在嘴部核心 mask 使用完整生成像素，在事先声明的 3 px margin 按固定权重衰减；support 外 alpha 严格为零。随后只将这个局部合成结果编码为矢量替换层，开启该节点、隐藏旧嘴部语义节点，再通过 EAS 渲染。底稿未改变。

人工视觉检查：闭嘴微笑目标满足；眼睛、鼻子、头发等最终未编辑部分保持原样。主要线条和配色接近原风格，但嘴周产生轻微肤色/明暗差异，下巴附近仍可见小的接合不平顺。判定 **指令满足、局部质量部分通过、未编辑区域保真通过**。只测一次、一个角色，尚不能声称生成式编辑质量稳定。

## 5. 指标没有被隐藏或重定义

- editable mask 来自配置中的原对象、目标位置、修补范围与 margin，在 EAS 编辑前写盘并记录 SHA-256；没有从实际 diff 反推或扩张 mask。
- POC 额外拒绝大于全图 10% 的 edit support，防止将整图标成“可编辑”来得到零误差；这不是改变用户指标，也不是新的产品限制。
- untouched MAE、非零变化率、max absolute difference 直接比较最终文件与 baseline。没有先把越界差异裁掉再计算指标。
- `diff.png` 是全图最大通道绝对差的 ×8 显示热图；只有 `untouched-diff.png` 在显示时过滤编辑区。数值计算使用未放大的真实差值。
- `metrics.json` 同时保留 `baseline_vs_input` 与 `untouched_vs_original_input`。例如床图相对未减色原图仍有误差，这是上一阶段减色造成的，未声称原始彩色图从未变化。
- edit-region MAE 只说明发生了修改，不是越低越好，也不是 Edit Success 分数。`Instruction Following`、`Edited Region Quality`、`Untouched Region Fidelity` 分开记录。

## 6. 已证明 / 部分证明 / 未证明 / 已知失败

**已证明：**在这两个辅助定位的区域，按需提取和提升不会破坏底稿；单个床对象可以通过 EAS 结构移动；mock 和真实模型返回都能被限制为局部回写；三例 EAS 最终渲染的未编辑区域严格 pixel-stable；最小 Scene IR、操作日志、掩膜、像素证据和保存 patch 的确定性重放完整。

**部分证明：**模型能在这个角色上生成可接受的闭嘴微笑；床源区域可以用简单地板复制形成可用背景估计；现有 `opaque_svg` 足以承载这种局部节点。它们都不等于跨图片自动化、优雅语义结构或稳定画质。

**未证明：**自动定位/分割、真实被遮挡背景恢复、swap、多对象连续编辑与重叠冲突、缩放/旋转/亚像素位移、不同 renderer、任意分辨率、生成模型再次调用的稳定性、批量性能、Agent token 或步骤效率。没有建立复杂综合 benchmark，也没有猜测 token 数。

**已知失败和偏差：**mock 下巴重复线条；模型输出尺寸不符；归一化后的生成上下文发生大量额外像素改动；生成版存在细微颜色与边界差异；床 mask 带少量背景且纹理修补不保证自然。Ruff 初检发现两项编码规范问题及一次 import 排序问题，均已修正；没有未处理的执行失败。初次结果和最终脚本重放证据都保留。

## 7. 八个问题的直接回答

|问题|回答|
|1. Lazy Semantic Decomposition 是否实际可行？|是，单对象、辅助定位的局部已实证；未推广为全图自动语义理解。|
|2. 能否不破坏 baseline 提取对象？|能。文件与 payload 哈希不变，提升后整图渲染也完全不变。|
|3. move/swap 能否结构化完成？|move 已用真实 EAS CLI 完成且对象像素零变化；swap 未做，不能一并宣称通过。|
|4. background repair 是多大瓶颈？|是空间编辑画质的主要瓶颈之一。简单地板能复制估计，复杂纹理/遮挡的真实背景没有被恢复或量化验证。|
|5. generative patch 能否限制在小区域？|最终回写可以严格限制；模型本身不保证局部性，必须由程序强制裁剪和合成。|
|6. untouched region 是否真正 pixel-stable？|本机 Inkscape、原始尺寸、此次整数平移与局部替换，三例均三项零误差；尚不扩展到所有 renderer/缩放。|
|7. 是否已经值得进入 Agent Plugin 阶段？|核心方向值得继续，但本轮结束后不建议立即投入插件。局部 mask 与修补/接缝质量仍需小规模外部样本验证；接入口现在不会解决这些问题。|
|8. 下一轮最该攻什么？|固定 pixel-stable 合成机制，集中验证“局部实例 mask + 局部背景修补/边界接合”的质量与失败检测。|

下一轮建议只增加少量独立样本，覆盖简单地板、复杂地板、细线边缘和遮挡；保留人工 mask 作对照，检查同一个对象在不同目标位置的修补接缝。所有失败仍须保持 mask 外零变化。再评估是否需要提供 CLI/MCP/Plugin 入口，不先开发接口层。

## 8. 复现与交付

每个 `cases/` 子目录都有用户要求的 `input.png`、`baseline.png`、`object-mask.png`、`object-extracted.png`、`background-repaired.png`、`edited.png`、`diff.png`、`editable-mask.png`、`untouched-diff.png`、`metrics.json`、`scene.json`。另存 source region、目标/修补/真实变化 mask、提升前后 Scene、SVG、中间 EAS 工程、命令 stdout/stderr、局部放大和 provider 证据。

B 的 `background-repaired.png` 等于 baseline：嘴部背景替换发生在生成/mock patch 内，没有独立地板式修补阶段。该文件不会伪装成单独的生成修补结果。

交付 ZIP 内包含三个案例全部结果、重放证据、9 项测试、新增实验代码、EAS Core 源码快照及依赖版本。重放只使用保存的 provider 返回像素，不需要 API key，不重新调用模型。全图和局部 comparison 自动生成并已检查，PNG 可直接在本聊天查看。

首次三例记录的本地处理时间约 8.13 / 9.25 / 9.79 秒，包含部分 I/O、EAS 操作与指标计算，计时截至 preview 制作前；**不包含人工定位、模型生成、报告、打包或 Agent 推理**。这些是运行记录，不是速度 benchmark。

当前实验实现仅增加 `scripts/`、`experiments/` 文件。`opaque_svg` 没有成为阻塞点，Core 不需要任何改动；不存在需要评估兼容性的 Core 行为变化。没有公开上传本轮图像或代码。

## 9. 样本归属

- 床图：[安居客云溪紫郡户型图](https://yong.fang.anjuke.com/loupan/511747.html)，沿用上一轮下载与 64 色基底，仅用于此次技术比较。
- 插画：[Kasuga，Wikipe-tan full length](https://commons.wikimedia.org/wiki/File:Wikipe-tan_full_length.png)，选用 [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/)。白底化、减色、局部嘴部替换及其衍生对照沿用此许可；不表示原作者认可生成结果。

原始下载和此前量化保真证据参见包内 `PRIOR-RASTER-REPORT.md`；本轮精确输入哈希在各案例 `scene.json`、模型裁剪哈希在 provider record。
