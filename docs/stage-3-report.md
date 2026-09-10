# 阶段 3 验收报告 — 打通最小闭环

日期：2026-09-10。项目：`D:\Codes\eas-hmi`。

## 结论

**阶段 3 的最小CLI与事务闭环实测通过，提交用户验收，本次停止。**

Model → CLI edit → validation → commit → history/diff → render → human sync → undo已贯通。
本次没有进入阶段4；完整batch/context/importer、其余node kind、manifest、完整资产选择器仍未验收。

## 实测结果

| 项目 | 结果 |
|---|---|
| 全量pytest | **144 passed，0 failures，0 errors，0 skipped** |
| 新增阶段3测试 | 51项；前阶段93项同时回归通过 |
| pytest耗时 | 42.248秒，包含真实Inkscape回归和真实写进程中断/并发 |
| 实际安装的CLI | `.venv\Scripts\eas-hmi.exe`，help及最小命令可运行 |
| 一键验收CLI调用 | **34次**，包括预期错误退出码的核对，全部满足断言 |
| 当前整包行覆盖率 | **88.77%** |
| transaction/history/CLI行覆盖率 | **95.70%** |
| 初始/最终revision | 0 → 5 |
| 审计提交数 | 6条，包含初始化；no-op/preview/拒绝操作未增加记录 |
| 样本规模 | 完全合成：1页面、8节点、2点位、1设备、1模板 |
| 最终strict validate | 通过，补齐required fault binding |
| Undo | 几何编辑撤销及human sync撤销都恢复原语义hash，保留新增审计 |
| 损坏投影恢复 | 截断operations并删除events后，由下一次CLI读取精确重建原字节 |
| 真实进程崩溃探针 | 7个注入点全部通过 |
| 并发 | 同expected revision只有一个成功；无expected revision时多个写入无丢失更新 |
| pip check / 所选Ruff检查 | 通过 |
| GUI/OCR/鼠标键盘 | 全部0 |

当前整包覆盖率高于85%，但完整MVP尚未完成；后续新增代码会改变分母，阶段6仍须重新验收。
中间阶段脚本沿用 `--cov-fail-under=0` 收集整包覆盖率；未排除未完成模块，pyproject最终门槛保持85%。

## 实际CLI闭环

1. 从Canonical fixture初始化工程，读取status和缺fault查询，strict validate准确报告缺项。
2. `bind BOX_A fault P07_FLT`通过revision检查提交，strict validate通过，保存binding diff。
3. `move BOX_A --dx 40 --dy 10`提交并自动render，inspect确认位置，保存move diff。
4. 旧expected revision与负宽度操作被拒绝，状态/历史未变化。
5. `undo`恢复move之前的semantic hash，revision继续增长。
6. render后原文件no-op sync不提交；测试脚本模拟SVG中的平移与文字编辑。
7. dry-run输出完整候选差异而不修改工程；实际sync提交human事务，只有两个目标节点变化。
8. 旧SVG再次同步被拒绝；undo恢复human edit之前的hash。
9. 仅损坏可重建的JSONL投影，下一次status恢复；最终history有6条、strict validate和render通过。

Human edit前后撤销hash均为：
`5eeb7327ac7462bc2bf1980c9f807095646b28befc08740deb7b38436571d7c3`

## 事务与恢复的证据

| 真实进程终止点 | 重开后行为 |
|---|---|
| 写commit之前 | revision仍为0，无修改 |
| 写commit之后、HEAD之前 | 孤立commit不被采用，revision仍为0 |
| 写HEAD之前 | revision仍为0 |
| HEAD临时文件已写、尚未原子替换 | 临时文件不生效，revision仍为0 |
| HEAD替换之后 | revision为1，恢复缺少的history/events |
| operations写入之后 | revision为1，补齐events和投影标记 |
| events写入之后 | revision为1，完成投影标记，重开不重复事件 |

测试使用独立Python写进程，在指定点执行 `os._exit(73)`，随后新Store以1秒锁超时重新打开。
因此验证了实际进程退出后的OS锁释放和恢复，而非只在同一进程捕获一个异常。
另外测试了schema错误、非法引用、重复ID、操作中途异常、非有限metadata、非法actor、
不允许修改project identity/revision、同值no-op、日志缺失/截断/改写、权威提交损坏等情况。

投影缺失/损坏可以重建；HEAD或commit损坏明确返回STORE_CORRUPT，不能从投影猜测回退。
校验和用于检测损坏，不构成防御具有文件修改权限者的安全机制。
这些证据覆盖进程中断和文件故障，未验证真实断电或硬件存储故障。

## 本阶段改动

- 新增 `src/eas_hmi/cli.py`：最小命令、JSON错误、退出码、revision/actor、dry-run和可选自动render。
- 完善transaction存储：HEAD/父提交校验和、投影SHA256、恢复幂等性、actor与完整候选校验。
- 提交后的投影/自动render失败明确报告committed=true和warning，不谎报回滚。
- 保护HEAD、.eas、history及锁路径，禁止render把派生SVG写进权威存储。
- Diff区分字段缺失与null，转义含点metadata键，记录顺序变化；undo恢复完整模型和opaque内容。
- 重复bind保留原绑定顺序和metadata，同值操作不产生无意义提交。
- 修正自由metadata的NaN被Pydantic转成null的问题：在转换前验证原始JSON域。
- 修正默认int/float在初始化前后触发假hash冲突的问题：对默认字段启用类型校验。
- 新增51项阶段3测试、真实崩溃写进程helper、合成fixture及一键验收脚本。

## 产物与复现

最终运行目录：
`D:\Codes\eas-hmi\build\stage3\20260910T054259Z-94a05e42`

| 产物 | 链接 |
|---|---|
| 机器可读报告 | [report.json](../build/stage3/20260910T054259Z-94a05e42/report.json) |
| 34次真实CLI命令记录 | [cli-transcript.json](../build/stage3/20260910T054259Z-94a05e42/cli-transcript.json) |
| pytest/coverage日志 | [tests.log](../build/stage3/20260910T054259Z-94a05e42/tests.log) |
| JUnit结果 | [tests.xml](../build/stage3/20260910T054259Z-94a05e42/tests.xml) |
| 整包覆盖率详情 | [coverage.json](../build/stage3/20260910T054259Z-94a05e42/coverage.json) |
| Binding diff | [binding-diff.json](../build/stage3/20260910T054259Z-94a05e42/binding-diff.json) |
| Move diff | [move-diff.json](../build/stage3/20260910T054259Z-94a05e42/move-diff.json) |
| Human diff（JSON） | [human-diff.json](../build/stage3/20260910T054259Z-94a05e42/human-diff.json) |
| Human diff（文本） | [human-diff.txt](../build/stage3/20260910T054259Z-94a05e42/human-diff.txt) |
| 人编辑SVG | [human-edited.svg](../build/stage3/20260910T054259Z-94a05e42/human-edited.svg) |
| 同步后的SVG | [semantic-after-human.svg](../build/stage3/20260910T054259Z-94a05e42/semantic-after-human.svg) |
| 最终render | [final.svg](../build/stage3/20260910T054259Z-94a05e42/final.svg) |
| 工程HEAD | [project/HEAD.json](../build/stage3/20260910T054259Z-94a05e42/project/HEAD.json) |
| 操作历史 | [operations.jsonl](../build/stage3/20260910T054259Z-94a05e42/project/history/operations.jsonl) |
| 事件历史 | [events.jsonl](../build/stage3/20260910T054259Z-94a05e42/project/history/events.jsonl) |

复现命令（项目根目录）：

```powershell
.\.venv\Scripts\python.exe scripts\verify_stage3.py
```

CLI使用示例、命令范围与错误/恢复约定见 [stage-3-cli.md](stage-3-cli.md)。
依赖版本见 [environment-stage-3.txt](environment-stage-3.txt)。本阶段未增加第三方依赖。

## 未进入的范围

- context、完整query过滤、batch/align/distribute CLI、完整SVG导入器、其余node kind、manifest属于阶段4。
- 本阶段仅回归阶段2资产适配器，未把完整资产导出选择器接到CLI。
- schema全面规则、所有fixture组合与大型性能仍在阶段4–6验收；不能因当前覆盖率够高就宣称完成。
- 大型合成DC工程、500+nodes/300+points、完整Task A–G留到阶段5。
- undo只撤销最近事务（包括undo自身）；没有实现多步撤销栈或schema迁移。
- 未引用客户素材，未修改AVEVA工程，未向GitHub远端发布。

收到用户继续指令后再进入阶段4。本次停在阶段3。
