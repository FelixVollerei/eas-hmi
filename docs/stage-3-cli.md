# 阶段 3 CLI 与存储契约

阶段3提供最小可靠闭环；batch/align/distribute/context、完整import、清单、其余node kind等仍在阶段4。
所有命令在独立项目内执行，不操作现有AVEVA工程。

## 复现本阶段

在 `D:\Codes\eas-hmi` 运行：

```powershell
.\.venv\Scripts\python.exe scripts\verify_stage3.py
```

脚本先运行全部阶段2/3测试，再调用真实安装的 `.venv\Scripts\eas-hmi.exe` 完成闭环。
每次生成新 `build/stage3/<run-id>/`，不覆盖之前证据；最新成功运行见 `build/stage3/latest.json`。

## 手工 CLI 示例

```powershell
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 init --from-model .\examples\stage3\model.json
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 status --json
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 query --equipment PUMP_07 --missing-binding fault --json
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 --expected-revision 0 bind BOX_A fault P07_FLT
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 validate --strict --json
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 --auto-render move BOX_A --dx 40 --dy 10
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 inspect BOX_A --json
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 diff --json
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 undo
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 render --output .\build\manual-stage3\build\semantic.svg
```

人编辑该SVG后显式预览/同步：

```powershell
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 sync-from-svg .\build\manual-stage3\build\semantic.svg --dry-run --json
.\.venv\Scripts\eas-hmi.exe --project .\build\manual-stage3 sync-from-svg .\build\manual-stage3\build\semantic.svg --json
```

SVG支持范围与限制沿用阶段2；不支持的修改整体拒绝。旧revision的SVG也拒绝，需重新render。

## 命令约定

全局选项放子命令前：`--project`、`--actor agent|human|system`、`--expected-revision N`、`--auto-render`、`--json`。
读取命令也接受子命令后的 `--json`。除help和未指定JSON的diff外，结果默认已经是JSON。

| 命令 | 阶段3范围 |
|---|---|
| init | 空白单页或 `--from-model` 验证过的Canonical JSON fixture；不覆盖已有项目 |
| status | revision、canonical_hash、page/node/point计数 |
| inspect | node/equipment/point结构化属性与引用 |
| query | kind/page/equipment/missing-binding有限AND过滤 |
| set | 单节点受支持属性；metadata.key/style.key；无eval |
| move / resize | 单节点几何编辑 |
| bind / unbind | 单节点绑定；验证point引用、模板；同值重复bind不重排绑定或丢metadata |
| validate | `--strict`；`--file`可检查独立非法JSON fixture，输出issue列表 |
| history | 最近操作，`--limit`默认10，上限1000 |
| diff | 最近事务，或 `--revision N`；JSON/语义字段文本 |
| undo | 撤销最近已提交事务并追加新审计记录 |
| render | 当前工程指定页，单页可省略page；原子写SVG派生产物 |
| sync-from-svg | 默认human actor；`--dry-run`只预览，无新revision；实际同步为单一事务 |

默认actor：init为system，sync为human，其他编辑为agent；用户显式actor覆盖默认。
相同语义状态的命令为no-op，不增加revision/history/events。
`undo`针对最近事务，包括undo本身；连续两次undo会撤销上次undo，不是浏览多个原始编辑的撤销栈。
初始化不撤销，schema迁移和多步撤销栈不在本阶段范围内。

## 返回状态

| exit code | 含义 |
|---|---|
| 0 | 成功，包括no-op；若后续派生render失败，JSON明确committed=true、render_failed=true及warnings |
| 2 | 参数、schema、领域校验、不支持的编辑、对象不存在等；未通过校验的工程修改不提交 |
| 3 | revision冲突（CLI expected revision或旧SVG） |
| 4 | I/O错误、锁超时、权威存储损坏 |

预期错误输出machine-readable code/message/details。参数语法错误也输出JSON USAGE_ERROR。
I/O异常不能一概视作未提交；异常退出/进程中断后先用status/history核对当前revision再决定重试。

## 提交点与恢复

- `.eas/commits/<uuid>.json`为不可变记录，含完整模型、操作和事件。
- `HEAD.json`为唯一提交指针，原子替换是提交点。新的HEAD带提交文件SHA256；子提交保存父文件SHA256。
- 无HEAD引用的新提交或临时文件不生效，不会被恢复程序自动采用。
- operations/events JSONL仅是投影。revision.json带两个投影的SHA256；缺失、截断、被改写或标记损坏时，下一次CLI访问从提交链重建。
- 恢复不增加revision，不重复操作或事件，且在同一个进程锁内完成。
- 权威HEAD/提交记录损坏时明确STORE_CORRUPT，不从可能过时的投影猜测恢复工程。
- 提交前失败无修改；HEAD已更新后投影写失败返回committed=true与恢复提示。
- 自动render使用带revision的文件名，先提交工程再生成派生文件；不能谎称渲染失败导致已提交工程回滚。
- render不能覆盖HEAD、.eas、history或锁文件。
- 进程锁串行化跨进程读写；expected_revision防止基于过期观察覆盖另一位编辑者的结果。
- 验证范围为进程中断、文件损坏和并发，不宣称完成断电/硬盘故障的容错认证。

## Diff与hash

- hash保留完整工程语义，仅排除revision；默认字段先完成类型校验，初始化/序列化前后hash一致。
- 自由metadata中的NaN/Infinity/非JSON对象在转换前拒绝，不能被悄悄写成null。
- diff明确区分缺字段与值null，metadata键中的点会转义；页面/registry顺序变化也能定位。
- history里的node_order/registry_order是审计快照的辅助字段，不是新增的Canonical schema字段。
- undo恢复完整模型（包括opaque payload），revision与审计历史继续增长。
