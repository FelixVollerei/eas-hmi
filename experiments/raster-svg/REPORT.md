> Historical experiment. Its local build/deliverables assets are not bundled in Git. For the installable release and public reproducible demos, see [EAS Visual](../../README.md).

# Editable Agentic Scene：位图 → EAS → SVG 还原验证

日期：2026-09-14。范围：视觉还原实验；未实现插件、语义分组或编辑。

**结论：原始分辨率下，减色位图可以经 EAS 以纯矢量几何零像素误差还原。四张外部样本的 32 色和 64 色版本均验证一致。但本次精简平滑曲线方案未通过暂定保真门槛，不能宣称已得到适合语义编辑的 SVG。**

## 怎么查看

- `png/*-pixel-faithful.png`：实际 EAS SVG 经 Inkscape 渲染的原尺寸 PNG。
- `png/*-smooth.png`：同一输入的平滑描摹对照。
- `previews/*-comparison.png`：原图、64 色输入、像素保真版、平滑版四格对照。
- `previews/*-detail.png`：固定局部坐标，最近邻放大，检查文字、五官、发丝。
- `svg/`：对应纯矢量 SVG；`sources/`：原始下载、白底归一化输入、来源；`measurements/`：全部 30 个变体结果。

## 64 色像素保真版

MAE 为 RGB 平均绝对误差（0–255，越低越好）。原图误差只来自减色；还原误差是相对减色输入。MB 按 1,000,000 字节。

|样本|原始尺寸|对原图 MAE|还原 MAE|SVG MB|矢量矩形子路径数|已记录处理秒数|
|---|---|---:|---:|---:|---:|---:|
|楼盘户型 A|655×720|1.7717|0.0000|1.559|97,876|4.63|
|楼盘户型 B|622×933|0.9800|0.0000|1.067|66,378|4.37|
|少女插画 A|1110×2100|0.0209|0.0000|1.151|67,468|4.70|
|少女插画 B|1000×2100|0.0519|0.0000|1.043|61,655|4.61|

处理秒数 = 矢量化 + CLI 导入/导出/严格验证 + 两次 PNG 渲染，不包含下载、减色、指标计算、人工查看或 Agent 推理。本次有并行实验，不能据此作正式性能基准。Token 和 Agent 步数没有可靠仪表记录，未估算。

## 平滑描摹版与局部误差

使用 VTracer 0.6.15，spline、stacked、speckle=4、color_precision=8、layer_difference=16、path_precision=3；另保留 polygon、少量更保守参数和无平滑轮廓实验，未挑掉失败结果。

|样本|对减色输入 MAE|误差≤8 像素占比|边缘 F1（容差 1 px）|主体对原图 MAE|细节对原图 MAE|
|---|---:|---:|---:|---:|---:|
|楼盘户型 A|4.682|80.30%|0.9305|9.048|8.202|
|楼盘户型 B|3.431|88.19%|0.8966|13.846|10.967|
|少女插画 A|3.561|95.20%|0.9750|5.297|7.704|
|少女插画 B|4.283|93.82%|0.9756|7.361|8.955|

主体掩膜统一取“原图相对边框中位背景色的最大通道差 >12”，用于减少空白背景稀释误差，不是语义分割。具体裁剪坐标、掩膜像素数和像素版主体误差均见 summary.json。边缘取相邻像素最大通道差 >24；F1 采用 1 像素容差，并非 OCR 或人脸识别分数。

暂定门槛在看外部样本结果前设为：对减色输入 MAE≤2、误差≤8 像素≥97%、边缘 F1≥0.97、path≤20,000、SVG≤5MB；平滑版未全部满足。它是实验筛选条件，未经用户确认，不等于审美或生产验收标准。

## 像素保真版究竟证明了什么

1. 只读取位图，先 MEDIANCUT 减色，无抖动、不缩小分辨率。插画原 PNG 的透明区域合成到白底；保留下载原件。
2. 将同色横向像素段纵向合并为矩形，以闭合矢量子路径表达；相同颜色汇总到 path 中，设置 crispEdges。没有 image、foreignObject、base64 位图或外部图像引用。
3. 将整张矢量图作为一组，经真实 EAS CLI import-svg、render、validate --strict，再用 Inkscape 1.4.2 按原尺寸渲染。
4. 这等价于保存像素网格的矢量几何，不是优雅的贝塞尔曲线重建。64 个颜色 path 背后仍有数万个矩形，不能把 path 数少当作结构简单；放大后仍呈像素阶梯。
5. 当前 EAS 接收为一个 opaque_svg 节点；内部均为矢量，但还没有家具、墙体或脸部语义。分组绕开了现有 importer 对大量顶层对象逐个复制 SVG 的成本。
6. Agent 编写、选择、调用并检查算法；没有让视觉模型逐笔重画，没有读取样本对应的原始 SVG，没有调用生成模型补画或修图。

因此，“经 EAS 保存/渲染高保真几何”已获得正向证据；“精简平滑且便于语义微调的 SVG”仍待验证。建议保留精确保真底稿，下一轮仅在选定局部建立语义组和可编辑表示，并量化局部编辑对未编辑区域的影响；不据此跳到完整插件开发。

## 完整性与局限

本轮共 30 个变体，150 次已记录子进程命令，非零退出 0 次。30 个变体经 EAS 的渲染与各自直接 SVG 渲染完全一致。已知历史原生崩溃风险没有因此得到修复或排除。没有重跑整个 EAS 测试套件。

仅验证本机 Inkscape、原始尺寸、白底输入。尚未验证其它渲染器、任意缩放、复杂照片、透明编辑、OCR 文字编辑或跨图泛化。4 个外部样本很小，两个插画同作者同角色，不代表所有二次元画风。额外 HMI 输入是先前工程的 PNG，只用于管线标定；它没有替代外部样本。

最初下载的 Blue Archive Arona 图片实际为黑底，未擅自抠图改成白底；不纳入这次四张验收样本，也不放入交付包。两张白底插画使用下列 Kasuga 原始 PNG，即使来源页面另有 SVG，本实验也未读取它。

## 来源与归属

- [floorplan-yunxi](https://yong.fang.anjuke.com/loupan/511747.html)：安居客 云溪紫郡户型图；仅本地技术验证。原始下载 SHA-256：`89ac4a0d1474d7b16890ed7f094df578d19f8ecfd96d3d4b6950295a46624047`。
- [floorplan-phoenix](https://zhoukou.newhouse.fang.com/loupan/2525198769.htm)：房天下 浩创凤凰居 D1 户型图；仅本地技术验证。原始下载 SHA-256：`c95f1e70f082acb3c81de2cb02d960032af461e2fcd3431b8ad788ffe8a27c3f`。
- [anime-wikipe](https://commons.wikimedia.org/wiki/File:Wikipe-tan_full_length.png)：Kasuga, Wikipe-tan full length; CC BY-SA 3.0 https://creativecommons.org/licenses/by-sa/3.0/; normalization: white alpha composite。原始下载 SHA-256：`5dbe4625375f49fb40a90c71834608c87d6eb75047be4a3a29d55ea29c9ff522`。
- [anime-sailor](https://commons.wikimedia.org/wiki/File:Wikipe-tan_sailor_fuku.png)：Kasuga, Wikipe-tan sailor fuku; CC BY-SA 3.0 https://creativecommons.org/licenses/by-sa/3.0/; white alpha composite; derivatives under same license。原始下载 SHA-256：`d74d5f893ad432401ae4d1d68ed0e009f47a01ebffdcbb165cd0ddb6c53575a0`。

Kasuga 插画选用 CC BY-SA 3.0，相关白底化、减色、矢量化及对照衍生图沿用该许可，修改如上所述。楼盘资料仅用于此次用户请求的技术比较，无额外公开发布许可声明；未上传到公开 GitHub。

## 复现

在 EAS 仓库及其已安装的 Python 环境中运行，依赖版本见 measurements/*.json：

```powershell
.\.venv\Scripts\python.exe scripts/probe_raster_svg.py --input INPUT.png --output NEW_OUTPUT_DIRECTORY --colors 32 64 --modes exact-runs
.\.venv\Scripts\python.exe scripts/probe_raster_svg.py --input INPUT.png --output ANOTHER_NEW_DIRECTORY --colors 64 --modes polygon spline
```

交付包含新增实验脚本及结果；不包含 venv、缓存、历史工程副本或整个项目仓库。核心 EAS 源码未修改。

## 发送状态

按用户最新指令改为在本聊天展示及提供文件。QQ 空白、微信停在登录入口的发送尝试均未发送任何消息或文件；已取消 QQ/微信投递。
