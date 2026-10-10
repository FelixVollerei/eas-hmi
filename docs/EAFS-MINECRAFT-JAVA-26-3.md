# Minecraft Java 26.3 世界目录：EAFS 实验 demo

本 demo 把一个**已关闭的 Java 26.3 世界目录**交给现有 `sfe-files` 和 `eafs-mcp` 文件工具。目标目录须包含 `level.dat` 和 `dimensions/minecraft/overworld/region/`。它复用 `sfe/0.1` 的 `contract → status → inspect → preview → apply` 流程，`apply` 输出一个新的世界目录，保留原存档。

## 指令范围

- 地址：`/blocks/minecraft:overworld/<x>/<y>/<z>`，三个坐标均为十进制整数。当前只处理已经生成的主世界区块。
- 指令：保留要求 `from` 精确匹配的 `replace-block`；新增 `fill-box`（整盒填充）、`clear-box`（整盒设为空气）、`replace-box`（盒内按旧方块筛选）、`paint-runs`（调色板 + 沿 X 正方向的直线段）。盒子的 `min`、`max` 均包含端点，坐标顺序是 `[x,y,z]`。指令按计划顺序生效，后续指令可覆盖前一步结果。
- `replace-block` 仍限原有六种无属性实心方块。批量目标限运行时 `contract` 的 `build_blocks` 清单；它现含宅邸所需的木材、草地、苔藓、石瓦、彩色材料，以及明确列出的石瓦楼梯、半砖和树叶状态。带属性的目标写成规范化的 `minecraft:block[property=value,...]`，只接受清单中完整匹配的状态。`inspect.block_state` 返回存档中显式保存的状态，`inspect.block` 保留方块名；游戏保存后可能把默认状态压缩为只有方块名，不能凭此字段反推默认属性。`replace-box.from` 也须在清单内；不匹配的方块跳过。批量覆盖可移除已有方块属性，但命中方块实体就整次失败。
- 每份计划最多 500,000 个目标位置。相同区块在一份计划内只解码、编码一次；`changed_blocks` 统计实际状态转换次数，重叠编辑可能对同一位置计数多次。`preview.changes` 最多返回请求的 `limit` 个样本，完整结果由候选目录哈希核对。
- 限制：只解析内联 zlib 压缩的 Anvil 区块；版本不符、旧版区块、未生成区块、缺失 section、其他压缩格式及不支持的编辑都会返回错误，不输出新世界。不会编辑实体、群系、结构、地图物品或新建区块。批量编辑会从变动区块移除高度图和光照缓存标签；游戏载入后的重建效果仍需实机核对，尤其是透光材料和露天清除。
- 前置条件：`base.sha256` 是世界目录全部相对文件路径与文件字节的确定性指纹。源存档发生任何文件变化后，需要重新 `status`、`inspect` 并生成计划。预览和应用必须针对同一静止的源存档。

## 从 PCL 世界运行

先在 PCL 中关闭该世界，再把**整个世界文件夹**复制一份到工作目录。下面的 `$world` 必须指向那份副本，且 `level.dat` 直接位于该目录下。选一个 `inspect` 确认为 `minecraft:stone` 的坐标；示例中的 `(0,-40,0)` 仅说明参数形状。

```powershell
$world = 'D:/path/to/copied-world'
$output = 'D:/path/to/edited-world'
$python = '.venv/Scripts/python.exe'
& $python -m sfe_files.cli contract
& $python -m sfe_files.cli status $world
& $python -m sfe_files.cli inspect $world --pointer '/blocks/minecraft:overworld/0/-40/0'
```

把 `status.sha256` 填入 `base.sha256`，把坐标和 `from` 改为刚才检查得到的值。计划示例：

```json
{
  "protocol": "sfe/0.1",
  "format": "minecraft-java-world",
  "base": {"sha256": "COPY_SHA256_FROM_STATUS"},
  "steps": [
    {"op": "replace-block", "dimension": "minecraft:overworld",
     "x": 0, "y": -40, "z": 0,
     "from": "minecraft:stone", "to": "minecraft:diamond_block"}
  ]
}
```

保存为 `build/minecraft.plan.json` 后执行：

```powershell
& $python -m sfe_files.cli preview $world build/minecraft.plan.json
& $python -m sfe_files.cli apply $world build/minecraft.plan.json --output $output
& $python -m sfe_files.cli inspect $output --pointer '/blocks/minecraft:overworld/0/-40/0'
```

`preview` 返回语义变化、涉及的 region 文件以及候选世界的 `after_sha256`；`apply` 成功后，输出世界的 `status.sha256` 应与之相同。把输出目录作为单独世界放入 PCL 对应版本的存档位置，再用 Java 26.3 打开。可在启用命令的世界运行 `/execute if block X Y Z minecraft:diamond_block`；条件符合时游戏反馈“测试通过”。打开和保存世界会正常更新存档字节，因此游戏后的目录指纹不再与预览指纹相同。此步是游戏兼容性验证，不能由 NBT/Anvil 单元测试代替。

`eafs-mcp` 的 `file_contract("minecraft-java-world")`、`file_status`、`file_inspect`、`file_preview`、`file_apply` 使用同一个目录路径与 JSON plan；`file_apply.output` 也必须是尚不存在的新目录。MCP 主机还须将该路径纳入 `EAFS_MCP_ROOTS`。

## 万方块建筑计划

以下步骤先建 32×32×10 的石英体，再挖空内部、替换地面，并批量铺设玻璃。将坐标改到自己的世界中已生成且含相应 section 的区域，并从 `status` 取得 `base.sha256`；执行仍使用上面的 `preview` 和 `apply` 命令。

```json
{
  "protocol": "sfe/0.1", "format": "minecraft-java-world",
  "base": {"sha256": "COPY_SHA256_FROM_STATUS"},
  "steps": [
    {"op": "fill-box", "dimension": "minecraft:overworld", "min": [0, 100, 0], "max": [31, 109, 31], "block": "minecraft:quartz_block"},
    {"op": "clear-box", "dimension": "minecraft:overworld", "min": [2, 102, 2], "max": [29, 108, 29]},
    {"op": "replace-box", "dimension": "minecraft:overworld", "min": [0, 100, 0], "max": [31, 100, 31], "from": "minecraft:quartz_block", "to": "minecraft:white_concrete"},
    {"op": "paint-runs", "dimension": "minecraft:overworld", "palette": {"g": "minecraft:glass"}, "runs": [[2, 104, 0, 28, "g"], [2, 105, 0, 28, "g"]]}
  ]
}
```

`paint-runs.runs` 每行是 `[起点 x, y, z, 长度, 调色板键]`，从起点沿 X 正方向连续写入。多个短 run 可描述立面、窗带、装饰线和稀疏设计。真实 PCL 世界的 16,928 次状态转换计划和离线验证见 ``MC-EAFS-002` 证据` (donor historical record; see standalone VALIDATION.md)。

### 300 × 300 中式宅邸

运行 [`scripts/build_minecraft_mansion.py`](../scripts/build_minecraft_mansion.py) 可从该存档的实时目录指纹与高度图生成完整 EAFS 计划。场地是主世界 `x=-560..-261、z=-240..59`；先按高度图清理高出 Y=63 的方块，再铺地、建围墙、南门、仪门、正殿、后寝、东西厢房、书房、客舍、偏院、庭院与园池。方块目标数包括清地和重叠铺设，因此多于最终转换数。原存档不被更改；输出为独立世界。

```powershell
$world = 'D:/.minecraft/versions/26.3-Forge_66.0.5/saves/EAFS_MapEditing'
$python = '.venv/Scripts/python.exe'
& $python scripts/build_minecraft_mansion.py $world
& $python -m sfe_files.cli preview $world build/ChineseMansion_300x300.plan.json --limit 20
& $python -m sfe_files.cli apply $world build/ChineseMansion_300x300.plan.json --output build/ChineseMansion_300x300
```

生成器同时写出 `build/ChineseMansion_300x300.svg` 俯视图。若源存档有变化，应重新运行生成器以更新 `base.sha256` 与高度图；目标目录已存在时应选一个新的输出路径。该方案要求整块 300 × 300 范围内的区块已完整生成，且施工时世界关闭。离线实测和游戏内验证边界见 ``MC-EAFS-003` 证据` (donor historical record; see standalone VALIDATION.md)。

该实例已直接放入对应版本的 `saves/ChineseMansion_300x300`，在 Java 26.3 Forge 中成功加载、保存和重载，并在世界列表命名为“中式宅邸 300x300”。游戏回写后的可携带副本是 `build/ChineseMansion_300x300_Validated.zip`；原 `build/ChineseMansion_300x300` 仍是与 EAFS `preview.after_sha256` 精确对应的未经游戏回写结果。完整数值和实景截图见上述证据。

### 离线语义建筑沙箱

前述 300 × 300 生成器直接输出坐标指令，存档能正确执行，但计划没有说明某个方块属于哪座建筑、哪面墙或哪层屋顶，也无法在写入前按建筑角色审阅。新的 [`courtyard.scene.json`](../examples/minecraft/courtyard.scene.json) 是独立于存档的可编辑设计源文件，作为较小的 64 × 64 院落教学样例；它**不是**前述宅邸的建筑质量修订版。

场景格式为 `eafs-minecraft-structure/0.1`：`origin` 是世界 X/Z 坐标，`size` 是本地宽/深；`elements` 按顺序列出稳定 `id`、较早定义的 `parent`、建筑 `role`、允许的完整方块状态 `block`、本地 X/Z 与世界 Y 坐标组成的包含端点 `boxes`，以及明确允许覆盖的先前构件 `overrides`。`block: null` 且没有盒子的 `site`、`building`、`courtyard` 构件只用于分组。未声明的重叠、自重叠、越界、无效父级、非法材料、未真正使用的覆盖声明都会拒绝。覆盖后每个坐标只保留一个最终的 `element/role/block`；统计的是最终方块，非操作目标次数。场景最多宽深各 300、最终 500,000 个坐标。

```powershell
$python = '.venv/Scripts/python.exe'
$scene = 'examples/minecraft/courtyard.scene.json'
& $python scripts/minecraft_structure.py $scene stats
& $python scripts/minecraft_structure.py $scene inspect -88 67 -75
& $python scripts/minecraft_structure.py $scene review build/courtyard-review.html
```

`build/courtyard-review.html` 是无需服务器或外部资源的离线平面审阅页；可在最高方块俯视图与逐 Y 层水平剖面间切换、点击坐标查看构件归属，并查看材料和角色统计。设计修改直接编辑场景 JSON，然后重新生成 HTML。这个页面尚不提供完整 3D 透视、材质渲染或建筑美学评分。

`review` 页面和 `compile` 命令都显示场景原始字节的 SHA-256；审批某次设计时应核对两者一致，避免审阅后文件又被编辑。

安装本项目 wheel 后，同一入口也可用 `eafs-minecraft-structure SCENE stats|inspect|review|compile` 调用。场景 JSON 也可用已有 JSON profile 编辑，但普通 JSON 编辑的 `preview` 不会验证建筑场景的不变量；每轮修改后仍需运行本入口的 `stats` 或 `review`。

审阅确定后，选择**已关闭且已生成目标区块**的 26.3 世界目录，将样例 `origin` 调整到目标场地，再编译并用原有 EAFS 工具预览、写入一个新世界目录：

```powershell
$world = 'D:/path/to/closed-world-copy'
& $python scripts/minecraft_structure.py $scene compile $world build/courtyard.plan.json
& $python -m sfe_files.cli preview $world build/courtyard.plan.json --limit 20
& $python -m sfe_files.cli apply $world build/courtyard.plan.json --output build/courtyard-world
```

编译器把最终坐标压成一条 `paint-runs` 指令，并把计划 `base.sha256` 绑定到当时的世界目录。它**只写场景列出的目标坐标**；已有地形、未声明空间和方块实体的兼容性仍由 EAFS `preview` 检查。场景保存建筑语义，Minecraft 存档只保存方块状态：从普通未标注存档反推“墙／地板”等角色目前没有可靠依据；若后来在游戏中手工修改了建筑，应同步更新场景并重新编译、核对。

建筑质量还需要单独的参考与审阅环节。针对下一版大宅邸，应先明确宅邸与宫殿的类型差异，收集平面、立面和屋顶实例，再审阅院落进深、轴线、围合、入口、柱网和屋脊，而不是仅看方块数。[联合国教科文组织对明清宫殿的说明](https://whc.unesco.org/en/list/439)描述了中轴、对称、前朝后寝与院落层级；[故宫博物院的太和殿条目](https://www.dpm.org.cn/show/225724.html)提供了殿宇开间、进深与屋顶类型的具体实例。这些是**参考方法**，本教学样例并非太和殿复原。

用户选定的下一版风格与场地组织要求见[王府宅邸设计简报](MINECRAFT-MANSION-DESIGN-BRIEF.md)。

## 实现与验证边界

目录 profile 位于 [`minecraft_world.py`](../src/sfe_files/minecraft_world.py)，批量计划解析在 [`minecraft_batch_plan.py`](../src/sfe_files/minecraft_batch_plan.py)，区块编码在 [`minecraft_region.py`](../src/sfe_files/minecraft_region.py)；通用服务只负责路由与输出接口。由于世界包含多个文件，当前普通文件的“单文件原始字节 SHA”被扩展为目录指纹，但封套及失败语义保持一致。26.1 起世界维度路径改到 `dimensions/`，因此不能把旧目录布局直接套在 26.3 上；参见 [Mojang 26.1 世界存储变更](https://www.minecraft.net/en-us/article/minecraft-java-edition-26-1)和[26.3 正式发布说明](https://www.minecraft.net/en-us/article/minecraft-java-edition-26-3)。

自动化测试使用人工构造的小型 NBT/Anvil 世界验证接口、候选哈希、源文件保留和失败不发布。真实 26.3 Forge 存档的导入、加载和游戏内方块条件检查结果已记录在本仓库 ``engineering/evidence/MC-EAFS-001/verification.md`` (donor historical record; see standalone VALIDATION.md)。
