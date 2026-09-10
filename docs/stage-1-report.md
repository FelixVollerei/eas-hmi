# 阶段 1 验收报告 — 建立验收矩阵

日期：2026-09-10。项目：`D:\Codes\eas-hmi`。

## 结论

**阶段 1 的文档与环境准备退出条件已满足，提交用户验收。**
本结论仅涵盖需求映射和设计基线，不代表功能验收或 MVP 完成。
按用户要求，本次在此结束，不自动进入阶段 2。

## 本阶段交付物

| 文件 | 内容 |
|---|---|
| docs/requirements-original.txt | 用户原始需求的原样副本 |
| docs/implementation-plan.md | 原六阶段、各阶段退出条件及逐阶段停止规则 |
| docs/acceptance-matrix.md | 原始30章节、Functional16项、非功能条件、测试族、错误fixtures和Demo A–G的验证映射 |
| docs/design-contract.md | 模块、ID、坐标、同步、opaque、事务、hash、查询、导出及阶段2探针约定 |
| docs/stage-1-evidence.json | 实际环境与映射检查结果，未运行项明确标记 |
| docs/environment-stage-1.txt | 独立环境实际pip freeze版本快照 |
| pyproject.toml / .gitignore | Python包与依赖配置、生成物忽略规则 |
| README.md | 当前阶段状态、环境准备方法和实验边界 |

## 实际检查

| 检查 | 结果 | 判断 |
|---|---|---|
| Python版本 | 3.12.7 | 满足>=3.12 |
| 独立环境 | 项目目录下.venv | 已创建；未向Anaconda全局安装依赖 |
| pip install -e '.[dev]' | 退出码0，editable wheel构建/安装成功 | 包安装检查通过；不代表CLI可运行 |
| pip check | 退出码0，No broken requirements found. | 依赖一致性通过 |
| R01–R30映射编号 | 30/30存在，缺失列表为空 | 覆盖检查通过；内容已按原需求人工复核 |
| F01–F16映射编号 | 16/16存在，缺失列表为空 | 覆盖检查通过；不是16项功能实测通过 |
| Inkscape | 先前只读环境检查：1.4.2 | 可执行版本已确认，尚未做栅格化/保存兼容性验证 |
| 原需求副本SHA256 | 0d471d5f7b3435508ac88c734a29f7a4e8d366a9b6f2c9a18d5cab6dd5be4070 | 可追溯 |

核心依赖实际安装版本：Pydantic 2.13.5、Typer 0.27.2、lxml 6.1.3、Pillow 12.3.0、
filelock 3.32.6、pytest 9.1.1、pytest-cov 7.1.0、ruff 0.16.6。
全部传递依赖见environment-stage-1.txt。

## 设计决策摘要

1. 本地Canonical Model为唯一工程事实来源；工程变更走事务。
2. 采用父级局部坐标和固定固有内容尺寸，明确旋转中心/缩放继承规则。
3. 旧revision SVG与不支持的人编辑整体拒绝；不自动猜测合并或部分同步。
4. Opaque保留原始内容及引用依赖，不能可靠拆分则提升保留边界并warning。
5. 缺required binding默认WARNING、strict可升级ERROR；严重错误留独立fixtures。
6. 不可变提交+原子HEAD，历史/事件可恢复；undo恢复语义同时保留审计。
7. PNG保留alpha、BMP声明背景；后端及真实导出结果在阶段2验证。
8. 覆盖率目标>=85%；性能报告包含CLI启动/加载开销。

## 偏差与未验收内容

收到“分阶段完成后退出”的新指令之前，已经写入13个Python源文件草稿，涉及model、
geometry、validation、query、operations及errors。这些超出了纯文档准备范围。
收到指令后已停止扩展，保留为未验收草稿，后续必须按阶段验证，不计入阶段2/3/4完成度。

- cli.py尚不存在，当前`eas-hmi`入口不能正常运行。
- tests目录尚不存在，pytest未运行，覆盖率未测量；不能报告测试通过。
- SVG importer/renderer/synchronizer与exporter尚未实现。
- 没有生成synthetic工程、500节点/300点位、错误fixtures或Demo产物。
- 事务、恢复、query等草稿的行为与设计一致性尚未验证，可能需要修改。
- CairoSVG未安装，最终栅格化后端尚未确定。
- GitHub连接未验证，未创建/推送远程仓库，未执行远端发布。
- 没有使用电脑/浏览器GUI动作；没有把现有SCADA工程作为示例或修改它。

## 下一阶段入口

用户确认后进入**阶段2：优先验证难点**，只针对少量合成节点验证：
确定性render与no-op往返、矩阵和文本/可见性同步、opaque引用保留、PNG/BMP真实导出、
stale/unsupported编辑拒绝。届时提交专门的实测报告并再次停止。
