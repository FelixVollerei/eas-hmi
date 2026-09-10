# 最终验收复现说明

前提：Windows、Python 3.12+、已安装 Inkscape CLI。当前验证机使用 Inkscape 1.4.2。
解压完整项目 ZIP，在项目根目录执行以下命令。安装需要可访问 Python 包索引。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe scripts\verify_stage6.py
```

如 Inkscape 不在默认路径，设置：

```powershell
$env:EAS_HMI_INKSCAPE = "C:\Program Files\Inkscape\bin\inkscape.com"
```

脚本使用新 UUID 目录，不清理或复用旧工程。每个执行步骤的 command/cwd/exit_code/log 都写入 steps.json。
失败立即停止，不更新 latest.json。脚本中的断言属于验收逻辑，请勿使用 `python -O`。

## 安装隔离与重复性

1. 复制源代码、tests、scripts、examples、schema、docs、pyproject 至独立 checkout，记录逐文件 SHA-256。
2. 通过 `python -m venv` 创建新的 editable 环境；不使用 system site-packages，移除继承的 PYTHONPATH/PYTHONHOME/VIRTUAL_ENV。
3. 在复制的项目目录执行 `pip install -e ".[dev]"`；pip check、全部命令 help、全量 pytest、A–G Demo。
4. 使用 pip wheel 构建实际 `.whl`；创建第二个新 venv，仅安装 wheel 及其运行依赖。
5. 使用 `python -I` 记录模块路径：editable 必须来自复制源码，wheel 必须来自新 venv 的 site-packages；user site 必须禁用。
6. wheel 环境再次运行完整 A–G；两个结果的 source statistics、final revision 和 canonical hash 必须相同。

第二个环境不安装 dev extras，验证 CLI 与 Demo 的运行依赖是否完整。
Inkscape 是外部命令行前提，不计入 Python wheel 内的依赖。

## Performance

性能在 wheel 环境、A–G 完成后的 672-node/390-point 工程上测量，保留 8 条真实审计提交。
每个样本启动新的安装版 CLI 进程，时间包括 Python 启动、工程/历史读取、操作和 JSON 输出捕获。
交替执行 query 与 strict validate，各先做 3 次预热，再各记录 20 次正式样本。

- query：cooling + equipment_card + equipment-type=pump，返回完整 20 个 card 的 JSON。
- validate：strict，必须返回 ok=true 且 issues=[]。
- p50：中位数；p95：nearest-rank，20 个样本取排序后第 19 个。
- 门槛：每种命令 p95 ≤1000 ms，使用阶段 1 确定的暂定验收阈值。
- 每次核对退出码与结果；测前后 HEAD、operations/events 字节 hash 和 status 均不变。

原始样本包含命令、耗时、退出码、stderr、stdout 字节数，保留在 performance.json。
测量环境的 OS、CPU 标识/逻辑核心数、Python 可执行路径也记录在其中。
这是有明确规模和环境的本机测量，不代表任意机器或无限长历史下的延迟保证。

单独测量已有 Demo：

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_project.py --project <Demo项目目录> --output .\build\performance.json
```

## 产物

`build/stage6/latest.json` 指向成功运行目录，内容包括：

- report.json、steps.json、logs/、source-hashes.json、requirements-tested.txt
- tests.xml、coverage.json、coverage.xml
- editable-demo/、wheel-demo/ 的完整工程、最终快照、CLI transcript、A–G、错误样本和 SVG/PNG/BMP
- dist/ 下的安装 wheel 与报告中的 SHA-256
- performance.json 的原始测量与判定

venv 和 checkout 副本分别在 `build/environments/` 与 `build/clean-checkouts/`；不装入交付 ZIP。
完整项目包包括原始源文件和上述实际验收结果，可按同样命令再次生成隔离环境。
