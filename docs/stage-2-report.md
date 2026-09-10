# 阶段 2 验收报告 — 优先验证难点

日期：2026-09-10。项目：`D:\Codes\eas-hmi`。

## 结论与范围

**阶段 2 的高风险路径实测通过，提交用户验收，本次停止。**
实测覆盖确定性渲染、no-op 往返、几何/文字/可见性同步、opaque 保留、
真实 Inkscape 保存/变换，以及 SVG/PNG/BMP 导出。
本结论不代表阶段 3 闭环、阶段 4 全功能或整个 MVP 已完成。

小样本为完全合成的 **1 页面、8 个节点、0 个点位**，包含 group、shape、text、opaque_svg，
两个引用相同原始艺术文件的opaque实例、父子结构、隐藏对象和零尺寸对象。
500节点/300点位及设备数量要求留到阶段5，不将本样本计为大型Demo。

## 最终实测结果

| 项目 | 结果 |
|---|---|
| 一键验证脚本 | 退出码0 |
| pytest | **93 passed，0 failures，0 errors，0 skipped** |
| pytest耗时 | 31.427秒，包含真实Inkscape集成测试 |
| SVG/导出模块行覆盖率 | **89.38%** |
| 整包行覆盖率 | **77.33%**；未达到最终85%门槛，阶段6继续验收 |
| 同模型重复render | byte-identical |
| 未修改sync | canonical hash、HEAD、operations、events均不变，无新revision |
| 真实Inkscape保存 | 不产生semantic changes |
| 真实Inkscape move/rotate/scale | 成功投影，只有目标节点变化，重渲染图像一致或在明确的抗锯齿误差上限内 |
| 组合编辑与重渲染 | 本次探针最大像素通道差 **0/255** |
| PNG | 240×180，RGBA，alpha范围0..255，实际Pillow解码成功 |
| BMP | 240×180，RGB，透明部分合成到#f1e2d3，实际解码成功 |
| SVG资产 | 自包含引用；独立栅格化成功；按实际绘制边界裁剪 |
| pip check | 退出码0，No broken requirements found. |
| Ruff所选错误/导入检查 | 通过（E4/E7/E9/F/I） |
| Ruff格式检查 | 12个目标文件已格式化，退出码0 |
| GUI行为 | screenshot/OCR/mouse/keyboard GUI actions全部0 |

GUI计数的依据是可审查的验证脚本与测试：仅调用Python API与不带GUI选项的Inkscape子进程，
没有电脑控制、截图、OCR或鼠标键盘自动化代码。像素差是确定性文件测试，不用于Agent工程理解。

覆盖率统计未排除未完成模块。中间阶段脚本使用 `--cov-fail-under=0` 记录整包实测值，
不是降低最终门槛；pyproject.toml中保留85%的阶段6要求。

## 六组退出条件

| 探针 | 验证内容 | 结论 |
|---|---|---|
| 确定性/no-op | 连续输出bytes；零尺寸/opaque；存储hash/head/history/events | 通过 |
| 几何投影 | x/y、frame宽高、translate/scale/rotate/matrix、父子组合、简单矩形内部属性 | 通过 |
| 文字/可见性 | 单行text、简单tspan、文字位置、display/visibility/style、父隐藏不改子自身标记 | 通过 |
| Opaque保留 | gradient/clipPath/mask/path/use/CSS、本地引用、两实例ID隔离、内部艺术修改 | 通过 |
| 图形导出 | 透明PNG、背景BMP、指定非等比例尺寸、真实边界、旋转父组、后端缺失/失败不损坏已有文件 | 通过 |
| 拒绝与一致性 | stale/project/page、重复/丢失ID、重组、未知编辑、skew/reflection/singular、外部/断裂引用、失败无部分写入 | 通过 |

Opaque两个实例的页面裁剪像素完全一致。原始透明图形与页面背景分别合成后的比较，
允许最多2/255的通道差，处理独立alpha合成的量化差异；没有用大范围模糊容差掩盖内容丢失。
原始模型payload在render/no-op期间保持原文，不因ID命名空间处理而被重写。

## 实测发现与修正

1. **嵌套svg上的transform在本机后端未按预期生效。**
   最终改为 tagged g承载变换，内层svg承载坐标和尺寸；模型仍使用约定的TRS矩阵。
   添加真实栅格化对比，避免只通过数值单测却画错位置。
2. **Inkscape会在保存时补充XML ID和version。**
   生成确定性ID及版本属性，已通过真实保存no-op；仅忽略明确的非渲染编辑器元数据。
3. **父组包围盒未计入嵌套viewport位移。**
   导出派生视图中将系统生成的frame展开为等价translate/scale组，再查询真实边界。
   添加40×20艺术块裁剪和旋转父组测试，修正空白PNG与错误裁剪。
4. **零尺寸嵌套svg仍被后端绘制。**
   对零尺寸frame明确隐藏；无修改往返保持模型visible原值，歧义编辑明确拒绝。
5. **直接改变带描边矩形内部宽高可能改变重渲染描边粗细。**
   这类内部编辑明确拒绝；通过frame或tagged g的整体resize仍受支持，避免错误投影。

## 已选择的后端与依赖

- Python 3.12.7，专属`.venv`。
- 栅格化与绘制边界：**Inkscape 1.4.2 (f4327f4, 2025-05-13)**。
- PNG读取/验证、BMP背景合成：Pillow。
- 新增tinycss2 1.5.1（及webencodings）处理CSS token与引用，避免粗略字符串替换CSS。
- 无需CairoSVG；后端路径可通过EAS_HMI_INKSCAPE配置，缺失时结构化报错。
- 完整依赖快照：[environment-stage-2.txt](environment-stage-2.txt)。

## 产物与复现

最终运行目录：
`D:\Codes\eas-hmi\build\stage2\20260910T052255Z-5856e629`

| 产物 | 链接 |
|---|---|
| 机器可读验收结果 | [report.json](../build/stage2/20260910T052255Z-5856e629/report.json) |
| 测试日志 | [tests.log](../build/stage2/20260910T052255Z-5856e629/tests.log) |
| JUnit结果 | [tests.xml](../build/stage2/20260910T052255Z-5856e629/tests.xml) |
| 完整coverage | [coverage.json](../build/stage2/20260910T052255Z-5856e629/coverage.json) |
| 原始semantic SVG | [semantic.svg](../build/stage2/20260910T052255Z-5856e629/semantic.svg) |
| 模拟人工编辑 | [human-edited.svg](../build/stage2/20260910T052255Z-5856e629/human-edited.svg) |
| 重渲染 | [candidate-render.svg](../build/stage2/20260910T052255Z-5856e629/candidate-render.svg) |
| 语义diff | [semantic-diff.json](../build/stage2/20260910T052255Z-5856e629/semantic-diff.json) |
| 拒绝诊断 | [rejections.json](../build/stage2/20260910T052255Z-5856e629/rejections.json) |
| 真实Inkscape动作记录 | [inkscape-actions.json](../build/stage2/20260910T052255Z-5856e629/inkscape-actions.json) |
| 页面PNG | [page.png](../build/stage2/20260910T052255Z-5856e629/page.png) |
| 独立SVG资产 | [opaque-asset.svg](../build/stage2/20260910T052255Z-5856e629/opaque-asset.svg) |
| 透明PNG资产 | [opaque-asset.png](../build/stage2/20260910T052255Z-5856e629/opaque-asset.png) |
| BMP资产 | [opaque-asset.bmp](../build/stage2/20260910T052255Z-5856e629/opaque-asset.bmp) |

在项目目录运行：

```powershell
.\.venv\Scripts\python.exe scripts\verify_stage2.py
```

脚本生成新的run目录，不覆盖之前验收证据；build/stage2/latest.json指向最新成功运行。
输入位于examples/stage2/model.json和opaque-source.svg。

## 明确限制与待后续工作

- 仅阶段2的group/shape/text/opaque_svg适配器已验收，其余5种kind在阶段4实现。
- 完整SVG importer尚未实现；这里验证的是自包含opaque样本与自身renderer输出。
- 仅支持有限CSS子集；at-rules、伪类/函数/属性/namespace选择器、外部资源和断裂引用明确拒绝，不静默删除。
- 富文本、多行文字、重组、新增/删除语义节点、skew/reflection、零尺寸编辑等明确拒绝。
- 有描边或语义子节点的矩形内部几何编辑拒绝；frame尺寸或tagged g整体变换可用。
- 资产SVG是派生导出，带asset标记，不作为semantic sync输入。
- 实际Inkscape证据来自CLI save/transform/export，不等同于全面验证任意GUI编辑路径。
- 当前没有cli.py，最终eas-hmi命令仍不可运行；阶段2通过verify_stage2.py复现。
- Store仅用于no-op和失败无写入探针，完整事务/崩溃恢复/成功提交/undo验收留阶段3。
- candidate-model.json仅为候选结果展示，不是一个成功提交的新工程。
- 未生成manifest、完整DC项目或A–G Demo；没有操作Plant SCADA或GitHub远端。

## 下一阶段

收到用户继续指令后进入阶段3：打通最小CLI与事务闭环，验证成功提交、history/diff/undo、
revision冲突及持久化故障恢复。本次停在阶段2，不自动继续。
