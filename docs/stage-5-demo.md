# 阶段 5 合成数据中心 Demo 使用说明

所有设备、点位、图形都是生成器原创的合成数据；不读取真实 AVEVA 工程。
1920×1080 三页：overview、cooling、electrical。

## 生成与运行

在项目目录 `D:\Codes\eas-hmi` 的 PowerShell 运行：

```powershell
.\.venv\Scripts\python.exe scripts\generate_demo.py
.\.venv\Scripts\python.exe scripts\demo_agent_workflow.py
```

第一条命令生成 `examples/datacenter/model.json`、规模统计及 broken fixtures。
第二条命令在新的 `build/demo/<run-id>/` 中独立生成输入并调用安装版 `eas-hmi`。
不依赖上一次 Demo 的工程状态。可以用 `--output <新目录>` 指定输出，已有目录会拒绝覆盖。

完整阶段验收使用：

```powershell
.\.venv\Scripts\python.exe scripts\verify_stage5.py
```

该命令先运行完整 pytest 与 ≥85% 整包覆盖率门槛，再从头运行 A–G。
成功后写 `build/stage5/latest.json`；失败不会把失败目录标记为 latest。
脚本中的断言属于验收逻辑，请使用普通 Python 运行，不使用 `-O` 禁用断言。

## 规模与初始状态

| 类型 | 设备数 | 每设备点位角色 | 点位数 |
|---|---:|---|---:|
| pump | 20 | run / fault / local / remote / command / speed | 120 |
| valve | 20 | status / fault / command / position | 80 |
| sensor | 30 | value / alarm / quality | 90 |
| UPS | 12 | status / fault / load / battery / voltage | 60 |
| generator | 8 | run / fault / command / fuel / power | 40 |

合计 90 台设备、390 点位、5 模板。节点 672：overview 35、cooling 493、electrical 144。
设备 card 之外，还有 7 个无 equipment_ref 的概览卡，所以 equipment_card 节点总数为 97。
另含 95 opaque 图标、180 status_indicator、90 data_slot、79 connection，覆盖全部 9 kind。
所有点位 tag 以 `SYN.DC.` 开头；灰色状态标签和 `--` 数值仅表示设计态，不表示设备实测状态。

主工程默认校验只有 3 条 WARNING：`CHWP_CARD_03 / CHWP_CARD_07 / CHWP_CARD_14`
缺少 card 级 required fault binding。其 fault indicator 子节点已经引用 fault point，
用于演示 card 模板约束与子节点绑定相互独立；Task B 补齐的是 card 级关联。
其余 card 的 required roles 齐全；没有主工程越界或非法引用。

## A–G 与证据目录

| 步骤 | 动作及断言 | 证据 |
|---|---|---|
| A | query 得到 cooling 20 个 pump cards 与缺 fault 名单；inspect/context 得到 CHWP_07 的 6 个直接关联节点、6 个点位及相邻连接 | A/answers.json、A/context.json |
| B | 根据每台设备 inspect 返回的 point.role=fault 选唯一点位，经 bind 修复 3 个 card；strict validate 0 issues | B/diffs.json |
| C | batch set width=164；distribute 10 列、gap=20；核对所有 20 个 card 的坐标/宽高 | C/diffs.json、C/arrangement.json |
| D | 三页 render/no-op sync；脚本在 cooling SVG 平移 CHWP_CARD_07 (+12,+8) 并更改 TITLE_COOLING；dry-run 后以 human 事务同步 | D/before/、D/human-edited.svg、D/diff.json |
| E | 保留 B/C/D 各次语义 diff，同时从 CLI 获取可读版本 | E/semantic-diffs.json、E/B-*.txt、E/C-*.txt、E/D-1.txt |
| F | undo D；与 D 前 canonical hash 相等，revision 继续增加；B/C 修改保留 | F/undo.json |
| G | 三页 semantic SVG；CSV/JSON 660 条 binding；CHWP_CARD_07 的 SVG/PNG/BMP；三页 PNG 预览 | G/exports.json 及其引用文件 |

Task C 的 grid 在 `SECTION_PUMP` 的局部坐标中布局，第一列 x=40，第一行 y=0，
每列步长 184（164+20）、每行步长 114（94+20）。父节点缩放影响显示，子节点局部数据不被重写。
连接仍采用已有显式线几何，布局命令不自动布线。

Task D 是脚本模拟 Inkscape 类 XML 变换，不是通过 GUI 操作 Inkscape。
实际 Inkscape CLI 在 SVG/PNG/BMP 导出中使用；其保存/矩阵同步兼容性已有阶段 2–4 测试。

## 错误样本

`examples/datacenter/broken/index.json` 记录每个样本的预期 issue 数量和 CLI 退出码。
这些独立样本以 required binding 已齐全的基线生成，避免被主 Demo 的 3 个 WARNING 混淆。

| 文件 | 预期 |
|---|---|
| invalid-equipment.json | 2 × INVALID_EQUIPMENT_REFERENCE，ERROR |
| invalid-point.json | 2 × INVALID_POINT_REFERENCE，ERROR |
| duplicate-id.json | 1 × DUPLICATE_ID，ERROR |
| out-of-bounds.json | 2 × OUT_OF_BOUNDS，默认 WARNING；测试也验证 ERROR 策略 |
| invalid-geometry.json | 1 × SCHEMA_VALIDATION，负 width |

工作流对全部文件调用 `validate --file`，逐项核对；4 个 ERROR 样本还用 `init --from-model`
验证无法创建已提交工程。另对主工程尝试非法 batch width 和 stale revision，检查 HEAD 字节、
revision、canonical hash 均不变。所有输入文件在运行结束时重新核对 SHA-256。

## 恢复与交付

每次运行包含 `project/`（完整 commit store 与 history）、`final-model.json`（便于查阅/重新 init）、
`cli-transcript.json`（全部命令、stdout/stderr、退出码与耗时）、`progress.json`、`report.json`。
final-model 是 Task F 撤销人工改动后的状态，已完成 B 的绑定修复和 C 的布局。
用 snapshot 重新 init 会新建审计；需保留原审计时复制完整 project 目录。

完整项目包：

```powershell
.\.venv\Scripts\python.exe scripts\package_project.py --stage 5
```

包含源码、示例、全部测试、文档、schema、阶段 2–5 最终成功的验收目录；
不包含虚拟环境、缓存和被取代的运行。包内每个文件有 SHA-256，外部另提供 ZIP 的 SHA-256。

本阶段不进行真实客户/Plant SCADA 集成，不发布 GitHub。
阶段 6 再进行干净安装、正式 query/validate 性能统计与最终交付验收。
