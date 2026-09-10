# 阶段 1 设计约定

这是设计基线，不代表功能已经实现或验证。原始要求见 requirements-original.txt。

## 模块边界与数据

- model：Project/Page/Node/Equipment/Point/Binding/Template、规范序列化与 hash。
- validation：schema、引用、层级、几何、模板规则。
- query：本地索引、有限 AND 查询、inspect、局部 context。
- operations：有限编辑、事务、history、semantic diff、undo。
- svg：导入、确定性渲染、显式同步；不得直接写工程存储。
- export：清单与资产输出，不修改模型。cli 仅做参数/输出和组合调用。
- Node ID 全项目唯一；其他实体 ID 各 registry 内唯一。ID 非空、无空白、稳定。
- XML ID 通过确定性编码生成，与工程 ID 分离。
- 先保留 list 中所有条目并校验，再建索引，避免字典覆盖掩盖重复 ID。
- schema 拒绝未声明字段、NaN、Infinity、负宽高。
- Binding 明确 role/point_ref/expression/metadata，expression 只存储不执行。
- Template 声明 required/optional roles，不默认给所有设备赋予相同角色。

## 坐标与层级

- 模型单位为 SVG user unit，标准输出明确 width/height/viewBox 并对应 px。
- x/y 在父节点固有内容坐标内，顶层在页面坐标内。
- content_width/content_height 为固定固有尺寸且大于零；width/height 为父级坐标中的未旋转尺寸，允许零。
- 变换顺序：translate(x,y) rotate(rotation) scale(width/content_width,height/content_height)。
- 旋转中心为局部原点；其他中心通过矩阵换算。子节点继承父级变换，组 resize 缩放其内容与子节点。
- 越界检查使用组合变换后四角包围盒；opaque 工程框与绘制边界分别处理。
- align/distribute 要求同页、同父级、未旋转；其他情况明确拒绝。
- 分布使用稳定排序，支持 x/y 固定 gap；可用有限 columns 参数完成网格布局。
- locked 对象拒绝普通编辑；解锁也须审计事务。

## SVG 与同步

- 稳定 data-eas-id/kind/equipment/binding 引用和必要同步属性；不嵌入整份模型 JSON。
- 文档记录 project/page 身份、渲染基准 revision。排序、浮点格式、属性和命名空间稳定，目标 byte-identical。
- renderer 不修改模型；未修改 sync 不改变 semantic hash/revision/history。
- sync 必须匹配 project/page/revision。旧 SVG 默认拒绝，不自动合并。
- 支持 tagged translation、x/y、resize、rotation、text、visibility；常规 translate/scale/rotate 和可安全分解 matrix。
- 从渲染基线与实际 SVG 共同解析，不能只信 data-eas 中旧几何数据。
- 剪切、反射、奇异矩阵、无法可靠解析的重组、ID 重复/丢失、新增/删除语义对象默认拒绝。
- 零尺寸的未修改输出也必须 no-op 往返；人为引入不可分解零缩放明确拒绝。
- 人类文字修改覆盖简单 text 和常见 tspan，复杂富文本/路径文字不支持时明确报告。
- visibility 处理 display/visibility/style 及祖先继承，避免误写所有子节点的自身可见性。
- 不支持的编辑输出 `UNSUPPORTED HUMAN EDIT` 和 JSON code/node/attribute，整次 sync 不提交。
- opaque 内部艺术修改可保存为 payload，不强制投影为结构化路径。

## Opaque 与导入保真

- 保存原始 fragment 或自包含安全引用，不重建未知 path。
- 保留 defs/gradient/clipPath/mask/use/href/样式/命名空间及必要 Inkscape metadata。
- 复制时处理内部 ID 冲突和引用依赖；不能可靠拆分则提升保留边界并 warning。
- 普通 SVG 为重要顶层对象生成稳定 ID；不可拆分依赖保留整组并解释例外。
- 未知图形可保守使用 viewport 工程框，导出按需调用后端的确定性边界查询。
- 外部资源不得静默丢失，无法安全解析/打包明确警告或失败；源文件不改。
- 禁止 XML 实体展开、网络解析及 SVG 主动内容执行。
- 独立 semantic SVG 缺 registry 时不能凭空恢复 tag/type；允许带 unresolved 标记的 placeholder 和 warning，不能宣称完整工程恢复。
- 导入经初始化或编辑事务，失败不能留下半个有效工程。

## 校验、事务、历史与撤销

- 工程写命令执行 load/revision check/candidate/validate/commit/history/optional render。
- ERROR 整体回滚。缺 required binding 默认 WARNING，strict validate 可升级 ERROR。
- 越界默认 WARNING，可配置 ERROR；重复 ID、非法引用、非法几何均 ERROR。
- 主 Demo 的 3 个缺失 binding 可逐步修复，其他严重错误放独立 broken fixtures。
- batch 为一个事务，任一对象失败全体回滚。
- 进程锁串行本地访问/恢复；expected_revision 防止旧状态编辑。
- 不可变 `.eas/commits/<uuid>.json` 包含模型与审计，原子替换 `HEAD.json` 为唯一提交点。
- HEAD 所指模型为唯一事实来源，不再维护竞争的可编辑 project.json。
- operations.jsonl/events.jsonl 是提交链的可恢复视图，不是第二个工程事实来源。
- 提交点前失败无工程改变；提交点后投影失败明确报告已提交且待恢复，不能谎称 rollback。
- render/export 是派生产物写入，不改变工程 revision；失败不损坏原有输出，不伪造成功。
- history 字段包含 transaction_id/timestamp/actor/command/affected_ids/before/after/validation/revision_before/revision_after。
- diff 按实体及字段，binding 按 role 比较，不能返回 raw XML/JSON 行差异。
- undo 恢复最近事务前语义状态，追加审计并递增 revision；初始化不可撤销。
- semantic hash 仅排除 revision，包含工程 metadata 与 opaque payload。

## CLI 与局部查询

- 命令名 eas-hmi，读命令支持 --json；JSON 模式 stdout 仅输出有效 JSON/JSONL。
- --project 指定工程目录，编辑支持 actor（agent/human/system）、expected revision。
- 有限 AND 查询，无 SQL/eval。inspect 覆盖 node/equipment/point 的属性及引用。
- context 默认 depth=1，上限 3；默认对象上限 100，最大 200，明确 truncated。
- context 提供目标、父子、设备、点位、绑定、直接关联和连接对象，不嵌入整工程。
- 空 query 返回空集合；空写选择明确 no-op 或失败，不伪造修改。
- JSON 错误包含 code/message/details；成功 exit 0，校验失败/不支持/冲突为非零。
- 每成功提交产生事件，no-op/失败无伪事件；watch 可选，其最终状态明确报告。

## 导出与实验证据

- build/bindings.csv 和 .json 包含需求中的 10 字段，从真实 registry 引用解析且稳定排序。
- 资产按 node/template/page 输出 SVG/PNG/BMP，明确尺寸及裁剪边界，SVG fragment 自包含依赖。
- PNG 保存 alpha；BMP 默认合成到声明背景色，不静默丢弃透明信息。
- 阶段 2 实测后再固定 Inkscape CLI 或轻量 rasterizer 后端；缺失后端/资源异常明确失败。
- 不宣称已在 Plant SCADA 中完成导入或绑定。
- 三页；20 pumps、20 valves、30 sensors、12 UPS、8 generators；至少 300 points / 500 nodes。
- 预设错误独立计数；最终 0 个 pump 缺 required fault binding。
- Agent Demo screenshot/OCR/mouse/keyboard GUI actions 均为 0，工程理解来自 CLI 结构化信息。
- query/validate 无 LLM/整页 rasterization；对最低规模各测 20 次 CLI p50/p95（含启动/加载），暂定 p95<=1秒为交互目标。
- 纯语义工作流成功证明可行性；量化 GUI 成本减少比例另需对照实验。

## 阶段 2 优先探针

1. 重复 render 字节一致；no-op sync hash/revision/history 不变。
2. translate/rotate/scale/matrix、嵌套父级、直接 x/y、宽高修改。
3. text/tspan、visibility/style；不支持项整次拒绝。
4. gradients/clipPath/mask/use/CSS/内部 ID 冲突的 opaque 保留。
5. PNG/BMP 实际解码、尺寸、透明/背景和边界。
6. stale revision、丢失/重复语义 ID、skew/reflection 被拒绝且模型不变。

## 阶段 2 实测补充（2026-09-10）

- 本机 Inkscape 1.4.2 未按预期应用嵌套 svg 的 transform，因此最终结构为
  **tagged g 承载 transform/visibility，内部 data-eas-part=frame 的 svg 承载 x/y/width/height/viewBox**。
  组合后的矩阵仍等于前述 translate(x,y) rotate(rotation) scale(...)，模型坐标约定不变。
- 对所有生成的元素分配确定性 XML ID；生成的 svg 标记 version=1.1，避免 Inkscape 保存补写产生假差异。
- 零尺寸 frame 明确 display=none，修正本机后端仍绘制零宽视口的问题；未修改零尺寸可无损往返，编辑零尺寸默认拒绝。
- SVG/PNG/BMP 资产的派生视图将生成的 frame 转成等价 translate/scale 的 g，
  规避后端对嵌套 viewport 父组计算错误包围盒的问题。原 semantic SVG 与模型不变。
  资产带 data-eas-artifact=asset，不作为直接 sync 输入。
- 后端固定为本机 Inkscape CLI + Pillow；tinycss2 用于解析、重写和限定 opaque CSS/URL。
- Opaque CSS 当前支持普通 type/class/ID/后代等选择器与本地url引用；明确拒绝 at-rules、伪类/函数/属性/namespace选择器及外部资源。
- 普通单行 text、简单未样式化 tspan 可同步；复杂文字布局明确拒绝。
- 普通矩形内部属性可投影；带描边或语义子节点的矩形内部几何编辑拒绝，避免改变描边粗细或误移子节点。
  这些对象仍可通过语义 frame 尺寸或 tagged g 变换进行整体resize。
- 已验证真实 Inkscape CLI 保存和move/rotate/scale，并用像素差比较几何等价图像。
  这不代表验证了所有交互式 GUI 操作、任意 SVG 特性或完整工业软件兼容性。
- 阶段 2 不实现完整导入器、CLI和全部9种node kind；这些仍按阶段3/4推进。

参考：SVG坐标与viewBox组合遵循 [W3C SVG 2 坐标规范](https://www.w3.org/TR/SVG2/coords.html)；
CSS解析使用 [tinycss2官方API](https://doc.courtbouillon.org/tinycss2/stable/api_reference.html)。
后端行为以本地 `inkscape.com --version`、`--help`、`--action-list` 和实际测试为准。

## 阶段 4 补充

完整节点、局部上下文、批量事务、导入、资产与事件的当前行为见 [stage-4-cli.md](stage-4-cli.md)。
其中：connection 使用显式局部线几何，不自动布线；context 是有上限的投影，精确属性通过 inspect。
SVG import 不是备份恢复：没有写入 SVG 的 registry/表达式/metadata 不被推测，缺失定义显式占位并警告。
完整 Canonical 工程以 `.eas` 提交链、HEAD 和可重建 history 为准；项目 ZIP 包含这些实际验收状态。
