# EAFS MCP 适配层

`eafs-mcp` 是 `sfe/0.1` 的本地 stdio MCP 服务。它为原有 29 种普通文件 profile、EAS-HMI 工程及实验性的 Minecraft Java 26.3 世界目录提供能力发现、结构读取、整组预览和提交工具。编辑计划仍是原来的 JSON 对象；MCP 调用直接传 `plan` 对象，不需要先把计划写成文件。

## 启动与连接

在仓库根目录安装含 MCP SDK 的可选依赖：

```sh
python -m pip install -e '.[mcp]'
python -m sfe_files.mcp_server
```

第二条命令启动后等待 MCP host 从 stdin 发送请求，不会输出交互提示。安装后也可使用 `eafs-mcp` 入口。通用 stdio host 配置如下；`command` 要换成所用 Python 环境的绝对路径：

```json
{
  "command": "/absolute/path/to/python",
  "args": ["-m", "sfe_files.mcp_server"],
  "env": {"EAFS_MCP_ROOTS": "/absolute/path/to/workspace"}
}
```

Windows 可使用虚拟环境的 `Scripts/python.exe`，例如 `D:/Codes/eas-hmi/.venv/Scripts/python.exe`；配置中的 `EAFS_MCP_ROOTS` 需指向实际允许操作的目录。`EAFS_MCP_ROOTS` 可包含多个绝对目录，按操作系统路径分隔符分隔：Windows 是 `;`，Unix 是 `:`。未设置时仅允许服务进程的当前工作目录。输入、工程目录和新输出路径都会解析后检查是否位于允许目录内；它是服务的路径边界，不是操作系统沙箱。

本仓库依赖 `mcp>=1.28,<2`，沿用已有 EAS Visual 服务使用的 Python SDK v1 API。MCP 是传输和工具发现层；文件操作语义仍由 `sfe/0.1` 的执行器及各格式 profile 决定。服务只启用 stdio，没有 HTTP 监听。

## 工具

| 工具 | 必需输入 | 返回与作用 |
|---|---|---|
| `file_contract` | 无；可选 `format_name` | 默认列出普通文件与 Minecraft 目录 profile、扩展名或目录类型、操作；指定格式后返回参数示例、地址与限制 |
| `file_status` | `file` | 格式、源字节 SHA-256 或世界目录指纹、结构摘要 |
| `file_inspect` | `file`；可选 `pointer`、`key_column` + `key`、`heading`、`object_id`、`limit` | 与 CLI 相同的格式专用寻址结果 |
| `file_preview` | `file`、`plan`；可选 `limit` | 验证整个计划，返回差异、前后哈希和 `committed:false` |
| `file_apply` | `file`、`plan`、`output` | 只向不存在的新路径写出结果，保留输入文件或世界目录 |
| `eas_contract` | 无 | EAS-HMI 计划 JSON Schema 与工程操作能力 |
| `eas_status` | `project` | 工程 revision、`canonical_hash` 和对象计数 |
| `eas_query` | `project`；可选 `kind`、`page`、`equipment`、`limit` | 查找节点 ID；结果有总数和截断标记 |
| `eas_inspect` | `project`、`object_id` | 节点、设备或点位及直接关系 |
| `eas_context` | `project`、`object_id`；可选 `depth`、`limit` | 有界的局部工程上下文 |
| `eas_preview` | `project`、`plan` | 在项目锁内执行完整 dry run，不更新 HEAD |
| `eas_apply` | `project`、`plan` | 将整组指令作为一次审计事务提交 |
| `eas_undo` | `project`、`expected_revision` | 仅在当前 revision 匹配时撤销一次事务 |

文件工具共用 [`service.py`](../src/sfe_files/service.py)，CLI 和 MCP 因而使用同一套状态读取、对象定位、预览及应用逻辑。EAS 工具直接调用现有 [`Store`](../src/eas_hmi/operations/transaction.py) 与强类型计划解析器。服务实现见 [`mcp_server.py`](../src/sfe_files/mcp_server.py)。

## 调用顺序与结果

普通文件先调用 `file_contract`，再按 `format_name` 读取具体格式的指令集；随后依次调用 `file_status`、`file_inspect`、`file_preview`、`file_apply`。以下是 `file_preview` 的 MCP 参数示例。文件路径和哈希均为示意；实际调用时，`file` 应在允许目录内，`base.sha256` 必须取自该文件当前的 `file_status`：

```json
{
  "file": "/workspace/settings.json",
  "plan": {
    "protocol": "sfe/0.1",
    "format": "json",
    "base": {"sha256": "5bc2c01c09988bd96f742ab7f8228cc29b93d5ed67829cf6153f40c431de6e93"},
    "steps": [{"op": "set", "pointer": "/enabled", "value": true}]
  }
}
```

`file_apply` 在相同参数上增加 `output`。EAS-HMI 使用 `eas_status` 中的 `revision` 与 `canonical_hash`，分别填入 plan 的 `base.revision` 和 `base.semantic_hash`；用 `eas_preview` 核对后再调用 `eas_apply`。每个成功结果都同时提供 MCP `structuredContent` 和 JSON 文本内容。已知业务错误返回 `isError:true`，其结构仍含 `ok:false`、稳定 `code` 和 `message`，例如 `BASE_CONFLICT`、`PATH_NOT_ALLOWED` 或 `OUTPUT_EXISTS`。

服务不提供 EAS 工程初始化工具；先用 `eas-hmi --project ... init` 创建工程。普通文件 plan 的 SHA-256 绑定源文件字节，Minecraft 目录 plan 的 SHA-256 绑定目录文件清单与字节，EAS plan 的哈希绑定工程语义；三者不能混用。原候选 30 种 profile 的操作表见[介绍与贡献指南](EAFS-INTRODUCTION.md)，Minecraft demo 见[专门说明](EAFS-MINECRAFT-JAVA-26-3.md)。

## 验证

运行真实 stdio 会话测试：

```sh
python -m pytest -q tests/test_sfe_mcp.py
```

测试启动子进程、列出 13 个工具，对 CSV/Markdown/SVG 执行格式专用读取，对 JSON/XLSX/SQLite/draw.io 执行读取、预览和新文件提交，并检查源文件不变、预览哈希与输出一致、旧计划拒绝、错误状态、路径限制以及 EAS 工程预览、提交和撤销。

v0.2 修订前的本地工作树中，MCP 会话测试通过，整库 `pytest -q` 为 **755 passed**；安装后的 `eafs-mcp` 入口也已在真实 stdio 会话中完成 `file_status` 调用。v0.2 候选的安装与验证结果另见 [发布报告](EAFS-V0.2-RELEASE.md)。
