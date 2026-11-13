# beom-oss-analyzer

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | **简体中文**

**完整分析开源项目，只把需要的功能移植到自己的仓库，并验证其行为与原项目一致的工具包**

![version](https://img.shields.io/badge/version-0.3.0-blue) ![python](https://img.shields.io/badge/python-3.11%2B-blue) ![license](https://img.shields.io/badge/license-use%20only%2C%20no%20modification-lightgrey)

与其“粗略读一遍别人写的功能再照着做”，不如对整个代码库进行分析并为每条结论附上证据（文件:行号），
再通过程序确认移植后的结果与原项目行为一致。本工具包同时提供技能、MCP 工具和 CLI，让 Claude Code 等 AI 代理能够按流程使用；
对于不应由 AI 独自决定的事项，未经人工批准就无法完成。

## 目录

- [它能做什么](#它能做什么)
- [核心概念](#核心概念)
- [环境要求](#环境要求)
- [安装](#安装)
- [桌面应用与自动运行](#桌面应用与自动运行)
- [快速开始 1: 全量分析仓库](#快速开始-1-全量分析仓库)
- [快速开始 2: 移植功能并验证](#快速开始-2-移植功能并验证)
- [快速开始 3: 按需求构建](#快速开始-3-按需求构建)
- [修改原项目后使用 (定制)](#修改原项目后使用-定制)
- [用例文件格式](#用例文件格式)
- [命令一览](#命令一览)
- [数据与存储位置](#数据与存储位置)
- [配置](#配置)
- [安全原则](#安全原则)
- [已知限制](#已知限制)
- [许可](#许可)

## 它能做什么

| 想做的事 | 工具包的做法 |
|---|---|
| **完整掌握功能** | 对所有文件分类，把所有函数拆分为审查单元，并为每条结论附上证据（文件:行号）。无法确定的内容会留在“未确认列表”中。 |
| **真实的 API 列表** | 按各框架的规则查找 HTTP·CLI 入口点，并展开代码中拼接而成的 URL，生成真实的路径列表。 |
| **修改前确认影响** | 展示要修改的函数会影响哪些地方（调用方·实现·入口点·测试），以及在不触碰核心代码的前提下可以插入的扩展点。 |
| **跟进原项目更新** | 比较两个版本，找出与我们的修改发生冲突的地方（函数删除·重命名·签名变更）。 |
| **只挑选功能进行移植** | 从一个入口点切出所需的代码·状态·配置·外部依赖，并在台账中逐项记录移植到了哪里。也可以用其他语言重新实现。 |
| **验证移植结果** | 向原项目和自己的代码输入相同的数据并比较结果（HTTP·CLI·函数级别）。有意修改的行为用期望值验证，原项目中不存在的新行为也用期望值验证。 |
| **构建想要的功能** | 无需参考原项目，从“帮我做一个这样的功能”开始，先让要求和验收标准获得批准再构建，然后验证是否符合标准、面对异常输入能否稳定运行。 |
| **阻止 AI 走捷径** | 允许差异·省略功能·缩减测试·期望值在人工批准之前都无法完成。验证后代码发生变化，或在工具包之外修改记录，都会被检测出来。 |

## 核心概念

**证据等级** — 以等级记录结论的确定程度。

| 等级 | 含义 | 获取方式 |
|---|---|---|
| L0 | 阅读代码并通过结构图确认 | 默认 |
| L1 | 通过语言服务器确认调用·引用 | `lsp` |
| L2 | 确认值从哪里流向哪里 | `dataflow` |
| L3 | 确认实际被执行 | 覆盖率报告、差分验证 |

**台账** — 分析·移植状态累积在每个项目独立的 SQLite 台账（`~/.oss-analyzer/projects/<id>/ledger.db`）中。
即使会话切换，或多个代理同时工作，也会延续同一份记录。

**移植完成条件（7 道门禁）** — 全部满足后，`port-finish` 才会给出完成判定。

1. 所有待移植项均已处理（移植 / 替代 / 省略）
2. 移植位置至今仍保持原样（精确到行内容）
3. 差分验证通过 — 最后保存的全部用例，**以当前代码为准**（验证后代码发生变化则失效）
4. 用例执行到了所有待移植函数（原项目覆盖率，不可行时用自己代码的覆盖率）
5. 需要人工确认的项目均已批准（允许差异·省略·用例变更·期望值；批准后内容发生变化则失效）
6. 记录未在工具包之外被修改（台账哈希链·黄金基准哈希）
7. 没有需要确认许可的文件

所有移植命令的响应都会附带 `next_step`（下一步要做的事）。

## 环境要求

| 类别 | 内容 |
|---|---|
| 必需 | Python 3.11+、git |
| 必需引擎 | 结构图引擎、入口点规则引擎 — 由 `setup` 安装（校验和验证） |
| 可选 | 语言服务器（L1）、数据流引擎（L2，JDK 21+）、覆盖率工具（L3）、容器引擎（隔离运行他人的代码） |
| 语言服务器（L1） | Rust · Java · Kotlin · TypeScript/JavaScript · Python · Go · C/C++ · C# · Dart · Ruby（其他语言最高到 L0，文件·符号分类与语言无关） |
| 入口点规则 | Axum · Spring · Express · FastAPI/Flask · Gin · clap · Next.js（没有规则的框架会自动标记为未确认） |

引擎名称·许可·安装位置见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 安装

```bash
# 1. 安装工具包 — 在任意目录都可以使用 oss-analyzer 命令
pipx install git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git
# （或）uv tool install git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git

# 2. 状态检查 — 引擎·语言服务器·技能·MCP 注册
oss-analyzer doctor

# 3. 安装引擎 — 先只查看计划，再只安装选中的项
oss-analyzer setup --languages python typescript
oss-analyzer setup --apply codebase-memory-mcp ast-grep lsp:python lsp:typescript coverage
```

- 不带任何选项运行 `setup` 时，**只显示计划**，不做任何更改。只安装 `--apply` 中列出的项。
- 要隔离运行他人的代码，需要容器引擎。在 Windows 上，`setup --apply sandbox` 会在 WSL 发行版中
  安装容器引擎（无需桌面应用）。如果已有 `docker`，则直接使用它。
- JDK·Node.js·Go·Rust 等语言运行时需要自行安装。

### AI 代理(Claude Code) 集成

二选一即可。

```bash
# A. 通过 setup 注册技能 + MCP
oss-analyzer setup --apply skill mcp-register
oss-analyzer setup --apply skill mcp-register --scope project --project-dir <项目>   # 仅用于单个项目

# B. Claude Code 插件（需先按上面第 1 步安装工具包）
claude plugin marketplace add ParkBeomsoo/beom-oss-analyzer
claude plugin install beom-oss-analyzer@beom-oss-analyzer
```

- 技能会在安装的副本中记录版本，工具包更新后，`doctor` 会以 `outdated` 提示。
- 如果已自行创建了同名技能，或存在指向其他安装的 MCP 注册，不会覆盖，而是给出提示。
- 也可以不用 MCP，只用 CLI。批准·完成判定等规则在 CLI 中同样生效。

### 更新与卸载

```bash
pipx upgrade beom-oss-analyzer
oss-analyzer setup --update                      # 仅将已安装的技能更新到新版本（不会新安装）
oss-analyzer setup --remove skill mcp-register   # 仅撤销本工具包安装·注册的内容
pipx uninstall beom-oss-analyzer
```

## 桌面应用与自动运行

**Windows** (PowerShell) — 安装应用，并询问是否创建开始菜单和桌面快捷方式:

```powershell
irm https://raw.githubusercontent.com/ParkBeomsoo/beom-oss-analyzer/main/install.ps1 | iex
```

**macOS · Linux**: `curl -fsSL https://raw.githubusercontent.com/ParkBeomsoo/beom-oss-analyzer/main/install.sh | sh`

手动安装:

```bash
pipx install "beom-oss-analyzer[app] @ git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git"
beom-oss-analyzer                    # 打开应用
oss-analyzer app --shortcut          # 创建开始菜单(搜索) + 桌面快捷方式，--remove-shortcut 删除
```

首次打开应用时也会询问是否创建快捷方式。Linux 上会创建 `.desktop` 条目。

**在首页选择模式**

| 模式 | 适用场景 | 阶段 |
|---|---|---|
| **按需求** | 帮我做这样的功能 (没有可参照的原项目) | 需求 → 验收标准 → 计划 → 实现 → 验证 → 交接 |
| **基于开源** | 把某个仓库的功能搬到我的项目 | 分析 → 范围 → 记录原项目 → 计划 → 实现 → 与原项目对比 → 交接 |
| **策划** | 快速把想法变成策划书 | 几个问题 → 策划书 → 打磨 → *按此策划构建* |
| **自由对话** | 不走本流程，直接使用 Claude Code、Codex | 消息 → 工作 → 消息 … |

**附件** — 任何模式都可附加策划书、文档、链接: md · txt · pdf · 图片，以及 Word · PowerPoint · Excel · 韩文 (HWPX)
(同时提供转换后的文本)。公开网页会保存内容，需要登录的页面 (如 Confluence) 只传链接 — 这时导出为文件再附加最可靠。
代理会先读附件。

开源模式只需要仓库 URL (或路径)。要搬的功能可以用文字写在输入框里，留空则由代理列出仓库功能让你挑选。

**代理 · 模型 · 思考力度** — 在发送按钮旁的一个按钮里选择。

| 代理 | 说明 |
|---|---|
| Claude Code | 每个操作都按工具包的权限规则确认。模型: 默认 · Opus · Sonnet · Haiku (别名始终指向最新)，力度 低 → 最高 |
| Codex | 用沙箱代替逐项询问 (只能写目标文件夹)。模型和力度级别直接使用 Codex 为你的账号保存的列表，新模型会自动出现 |
| Gemini CLI | 实验性，没有力度设置 |

列表中没有的模型名可以直接输入。模型和力度在运行中也能修改 (从下一轮生效)。

**权限** — 选择模式，按类别的细则在 *选项 → 权限*。

| 模式 | 行为 |
|---|---|
| 全部询问 | 每次修改和命令都确认 |
| 自动允许编辑 (默认) | 目标文件夹内的修改和构建/测试直接执行，其他命令和网络需确认 |
| 自动 | 命令也不询问直接执行，只确认网络和文件夹外的文件 — 仅用于可信任务 |

push、发布、文件夹外删除和系统更改始终拒绝。代理传给验证工具的命令 (包括用例文件里的命令) 也走同样的规则。
认证: *我的登录* (订阅) 或 *API 密钥* (读取环境变量中的密钥，按量计费) — 能做的事情相同。

**由人决定的事** — 只有这些会以卡片出现。

- **开始时一次**: 需求、验收标准、计划以及 (有界面时) 设计方向一起出现 — 批准一次后会一直进行到满足所有完成条件。
  每张卡片: *批准*、*修改* (自己改需求、用例或计划步骤后 *保存并批准* — 以用户编写记入账本并转告代理)，
  或在输入框写下要改的地方 (不批准，由代理修改)。
- 代理的 **提问** 和 **权限请求** (`Y` 允许 · `N` 拒绝)。
- 最后的 **交接**: 接受，或发送消息继续 — 给已停止或已完成的运行发送消息即可接着进行。

批准只在应用内、按显示的内容原样记入账本。代理没有可触及的批准途径。

**右侧面板**

| 标签 | 内容 |
|---|---|
| 概况 | 进度、计划步骤、需求、完成条件 |
| 标准 | 约定了什么 — 需求、验收用例 (输入 → 期望)、计划、谁在何时批准 |
| 验证 | 验证了什么 — 按需求列出每个用例的输入 → 期望 → 实际结果，以及每个完成条件的含义 |
| 变更 | 变更文件和 diff、按文件撤销、检查点 (每轮记录目标文件夹，可回到任意时点) |

**轮次** — 代理会话一次即为一轮，运行器会一轮接一轮地进行，直到满足所有完成条件。
默认不限次数，连续两轮没有进展会自动停止。可以在 *选项* 中设置上限，或选择 *最后确认一次*，先构建并验证再查看。

**界面与设计** — 有界面的工作，计划中会附带设计方向 (风格、颜色、字体、参考、要避免的东西)，与标准一起批准。
代理遵循避免常见 "AI 味" 的设计规则。界面用例用真实的浏览器引擎打开页面，检查文字、元素和脚本错误并保存截图 — 可在验证标签中放大查看。

**数据存放位置** — 全部留在本机。见[数据与存储位置](#数据与存储位置)。

不用应用: `oss-analyzer run --mode request --target-root <文件夹> --request "<需求>"` (`--agent codex`、`--resume <运行>`)，在应用中回答。

按[官方指南](https://code.claude.com/docs)安装 Claude Code 并执行 `claude auth login`；Codex 用 `npm i -g @openai/codex` 后执行 `codex login`
(检查: `oss-analyzer agent-status`)。自己在自己的电脑上使用时用订阅登录，为他人提供服务时用 API 密钥
([Anthropic 条款](https://code.claude.com/docs/en/legal-and-compliance))。

## 快速开始 1: 全量分析仓库

**交给 AI 代理时** — 在 Claude Code 中：

> 请全量分析这个仓库 https://github.com/<组织>/<仓库>

技能会按照下面的流程进行，大型仓库会由多个子代理分摊单元并行分析。

**直接使用 CLI 时**

```bash
oss-analyzer open https://github.com/<组织>/<仓库> --ref v1.2.0   # 分析专用 clone，固定提交 → project_id
oss-analyzer inventory <pid>                    # 文件分类·符号·结构图·入口点·分析单元
oss-analyzer features <pid>                     # 真实 URL·CLI 入口点列表

# 分析循环（重复直到没有单元为止）
oss-analyzer next <pid> --agent a1              # 领取下一个单元
oss-analyzer context <pid> <unit_id> --agent a1 --raw
oss-analyzer finding <pid> <unit_id> --agent a1 --json finding.json   # 结论 + 证据
oss-analyzer unknown-add <pid> --agent a1 --json unknown.json        # 无法确定的内容
oss-analyzer complete <pid> <unit_id> --agent a1 --summary "该单元的作用"

oss-analyzer coverage <pid>                     # 文件·符号两个维度的完成率与失败项
oss-analyzer unknowns <pid>                     # 剩余的未确认项
oss-analyzer export <pid>                       # Markdown + JSON 报告
```

`finding.json` 示例：

```json
{"text": "创建订单时先锁定库存，支付失败时释放", "severity": "important",
 "evidence": [{"path": "src/order/service.rs", "start_line": 120, "end_line": 158}],
 "symbol_ids": ["s_1a2b3c"]}
```

## 快速开始 2: 移植功能并验证

示例：将原项目的 `POST /api/v1/orders` 用另一种语言移植到自己的仓库。

```bash
# 1. 查看移植范围 → 创建移植台账
oss-analyzer slice <pid> "POST /api/v1/orders"
oss-analyzer port-create <pid> --name 订单 --targets "POST /api/v1/orders" --language javascript --target-root <我的仓库>

# 2. 保存原项目响应（黄金基准） — 原项目在容器中隔离运行
oss-analyzer port-capture <pid> <port_id> --cases cases.json \
    --source-image <原项目运行镜像> --source-url http://127.0.0.1:18001 --source-cmd "<原项目服务器启动命令>" \
    --coverage-cmd "<生成覆盖率报告的命令>" --coverage-report cov.json --explore 40

# 3. 编写自己的代码 → 逐项记录移植到了哪里
oss-analyzer port-map <pid> <port_id> --json map.json

# 4. 比较验证（包括并发请求）
oss-analyzer port-verify <pid> <port_id> --target-url http://127.0.0.1:18002 --target-cmd "node server.js" --concurrency 8

# 5. 进度与下一步
oss-analyzer port-status <pid> <port_id>

# 6. 人工批准 — 由用户在终端中亲自执行（代理代为执行会被拒绝）
oss-analyzer port-approve <pid> <port_id>

# 7. 完成判定 — 仅在 7 道门禁全部满足时签发
oss-analyzer port-finish <pid> <port_id>
```

`map.json` 示例 — 移植（`ported`）需填写自己代码的 `路径:行号`，替代·省略需要填写理由。

```json
[{"item": "s_1a2b3c", "status": "ported", "target": "src/orders/create.js:12"},
 {"item": "s_4d5e6f", "status": "replaced", "note": "使用现有消息服务代替原项目的队列"},
 {"item": "s_7a8b9c", "status": "omitted", "note": "管理员统计不在本次范围内"}]
```

**修改后再引入移植的功能**

| 情况 | 方法 | 判定 |
|---|---|---|
| 必须与原项目一致 | 只编写用例 | 与原项目响应比较 |
| 有意做成不同（可以确定值） | 在用例中写 `expect` | 用期望值代替原项目进行比较，期望值需人工批准 |
| 原项目中没有的新行为 | `"new": true` + `expect` | 不运行原项目，按期望值判定 |
| 仅格式不同，无法确定值 | `port-accept --case <名称> --reason "<理由>"` | 提议允许差异 → 人工批准 |
| 去掉部分功能 | 在 `port-map` 中设为 `omitted` + 理由 | 人工批准 |

- 批准界面会并排显示原项目响应与期望值（或实际差异）。批准后内容若发生变化，需要重新批准。
- 如果无法对原项目进行覆盖率插桩，可用 `port-verify --target-coverage-cmd ... --target-coverage-report lcov.info` 测量自己代码一侧的执行范围。
- 修改自己的代码导致行号偏移时，用 `port-map --follow` 更新移植位置。

## 快速开始 3: 按需求构建

在应用中选择 **基于需求**，或使用 CLI：

```bash
oss-analyzer make-create --name 订单总额 --request "接收数量并计算总额。每个 2000 韩元，10 个及以上优惠 10%，0 及以下返回 400" --target-root <目录>
oss-analyzer make-req <pid> <port_id> --json reqs.json          # [{"text": "总额 = 数量 x 2000 韩元"}, …] → R1, R2 …
oss-analyzer port-capture <pid> <port_id> --cases cases.json --explore 20   # 每条要求写 expect + req
oss-analyzer port-approve <pid> <port_id>                       # 实现之前批准要求·验收标准
oss-analyzer port-map <pid> <port_id> --json map.json           # 要求 → 实现位置 "路径:行号"
oss-analyzer port-verify <pid> <port_id> --target-url http://127.0.0.1:8080 --target-cmd "<服务器启动命令>"
oss-analyzer port-finish <pid> <port_id>
```

- 每个用例都需要 `req`（所验证的要求）和 `expect`（期望值），并且每条要求至少要有一个用例。
- 输入自动变异用例不看期望值，而是按**不崩溃·不返回 5xx** 判定（异常输入必须以 4xx 拒绝才算通过）。
- 如果已有要放入的仓库，先对其执行 `open`·`inventory`，再通过 `--context <该 project_id>` 传入，即可按现有结构构建。

## 修改原项目后使用 (定制)

```bash
oss-analyzer extpoints <pid>                                  # 先找出不触碰核心即可插入的位置
oss-analyzer impact <pid> pricing.calc_total --verify         # 修改后受影响的入口点·测试·结论
oss-analyzer custom-add <pid> --description "修改折扣计算" --kind modify --targets pricing.calc_total

# 原项目发布新版本时
oss-analyzer open <仓库> --ref v1.3.0 && oss-analyzer inventory <新 pid>
oss-analyzer compare <基准 pid> <新 pid> --save                # 变更之处 + 与我们的修改冲突之处
```

如需将修改后的原项目与原始原项目进行比较验证，请把修改后的仓库设为 `--target-root`，然后按照与 [快速开始 2](#快速开始-2-移植功能并验证) 相同的方法验证。

## 用例文件格式

```json
{
  "defaults": {"headers": {"Content-Type": "application/json"}},
  "cases": [
    {"name": "创建订单", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 2},
     "match": {"id": "[0-9a-f]{32}"}, "ignore": ["createdAt"]},
    {"name": "查询订单", "method": "GET", "path": "/api/v1/orders/{{创建订单.id}}"},
    {"name": "数量为0", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 0}, "compare": "status"},
    {"name": "应用折扣", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 10},
     "expect": {"status": 201, "equals": {"total": 18000}}},
    {"name": "优惠券_新功能", "new": true, "method": "POST", "path": "/api/v1/orders",
     "json": {"sku": "A1", "qty": 1, "coupon": "WELCOME"}, "expect": {"equals": {"discount": 1000}}}
  ]
}
```

| 字段 | 含义 |
|---|---|
| `method`·`path`·`json`·`headers` | HTTP 用例 |
| `argv`·`stdin` | CLI 用例 |
| `call: {source, target}`·`args`·`kwargs` | 函数级别比较（`路径:函数名`，语言不同也可以） |
| `{{用例名称.字段}}` | 使用前面用例的结果值（名称中不能有空格） |
| `ignore` | 不参与比较的路径（`items.*.t`） |
| `match` | 用正则表达式代替值进行校验（id·时间等每次都不同的值） |
| `compare: "status"` | 只比较状态码 |
| `expect` | 用期望值代替原项目进行比较 — `status`·`body`·`equals`（路径: 值，数组写作 `items.0.price`）·`match` |
| `new: true` | 原项目中没有的行为（必须有 `expect`，不在原项目上运行） |
| `screen: {path \| file, mobile}` · `expect: {visible, selectors, no_errors}` | 界面用例 — 打开页面，检查文字、元素和脚本错误，并保存截图 |
| `description` | 用自然语言说明这个用例检查什么 — 代替名称显示 |
| `explore: false`·`concurrent: false` | 排除在输入自动变异·并发请求检查之外 |

表中没有的键在保存时会被拒绝 (像用 `args` 代替 `argv` 这样的错误键若被悄悄忽略，就会用别的输入来验证)。按需求模式下每个用例还需要 `req`。

## 命令一览

| 分类 | 命令 |
|---|---|
| 准备 | `doctor` `setup` `open` `list` `inventory` `classify` `agent-status` |
| 应用·自动运行 | `app` `run` `runs` `data` |
| 分析循环 | `next` `claim` `renew` `context` `finding` `withdraw` `mark` `unknown-add` `unknown-update` `complete` `review` `pending` |
| 精确确认 | `lsp`(L1) `dataflow`(L2) `runtime-ingest` `runtime-run` `runtime-runs`(L3) |
| 结果 | `coverage` `features` `units` `unknowns` `export` `license` |
| 定制 | `extpoints` `impact` `custom-add` `custom-list` `custom-update` `compare` |
| 按需求构建 | `make-create` `make-req`（之后与 port-* 命令通用） |
| 移植 | `slice` `port-create` `port-map` `port-capture` `port-verify` `port-accept` `port-status` `port-approve` `port-finish` |

各命令的选项见 `oss-analyzer <命令> --help`。所有命令都会输出 `{ok, data, warnings, errors}` JSON，
失败时 `errors[0].recovery` 中会给出解决方法。面向代理的详细流程见技能文档（`oss-analyze` 技能的 SKILL.md·CLI.md）。

## 数据与存储位置

和 Claude Code (`~/.claude`)、Codex (`~/.codex`) 一样，所有数据放在主目录下的一个文件夹 (`~/.oss-analyzer`，可用 `OSS_ANALYZER_HOME` 修改)，
内部按性质划分。不发送到任何地方，也不保存机密 — 密钥和令牌只从环境变量读取，并在输出中遮蔽。

| 文件夹 | 内容 | 可否删除 |
|---|---|---|
| `settings.json` | 用户设置 (清理周期、默认值) | 可以 — 恢复默认 |
| `projects/<id>/` | 分析/移植账本 `ledger.db` (SQLite、带模式版本、哈希链)、固定提交的原项目克隆、黄金响应 | **不可以** — 批准与验证记录 |
| `runs/<运行>/` | 一次自动运行 = 一段对话: `run.json` 状态、`events.jsonl` 流程 (只追加)、`inbox/` 提问和批准请求、`shadow.git` 目标文件夹检查点、`agent/` 每轮重新生成的代理设置 | 移到回收站 |
| `trash/` | 已删除的运行，7 天后自动清空 | 可以 |
| `app/` | 快捷方式使用的应用图标 | 不可以 |
| `cache/` · `cbm-cache/` | 代理状态、验证 harness、结构图索引 | 可以 — 会重建 |
| `work/` | 验证和覆盖率用的临时副本，用完即删 | 可以 |
| `bin/` · `engines/` · `node/` | `setup` 安装的分析引擎 | 可以 — 用 `setup` 重装 |

与代理的对话原文保存在各 CLI 原本的位置 (`~/.claude/projects`、`~/.codex/sessions`)，通过会话 id 继续。
目标文件夹里只写入 `.oss/cases.json` (验收用例) — 提交或忽略都可以。

| | Claude Code | Codex | 本工具包 |
|---|---|---|---|
| 对话 | `projects/<文件夹>/<会话>.jsonl` | `sessions/YYYY/MM/DD/*.jsonl` | `runs/<运行>/events.jsonl` (+ 代理自己的对话记录) |
| 设置 | `settings.json` | `config.toml` | `settings.json` |
| 状态 | 文件变更历史 (检查点) | 带版本的 SQLite | 各项目的 SQLite 账本 (模式版本、哈希链) + 每次运行的 git 检查点 |
| 清理 | 默认 30 天后自动删除旧对话 | 保留 | 默认保留；可选在 30/90 天后自动清理已完成或已隐藏的运行；回收站 7 天后清空 |

删除运行不会删除批准和验证结果 — 它们留在项目账本里。

```bash
oss-analyzer data                        # 什么东西在哪里、有多大
oss-analyzer data --trash <运行>          # 把运行移到回收站 (--restore <名称>、--empty-trash)
oss-analyzer data --prune 30 --dry-run   # 30 天未变动的已完成或已隐藏运行
oss-analyzer data --set-cleanup 30       # 启动时自动清理 (0 = 关闭)
```

在应用中: 侧边栏底部的 **存储空间**，运行的 ⋯ 菜单里的 **移到回收站**。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `OSS_ANALYZER_HOME` | `~/.oss-analyzer` | 台账·clone·引擎·备份的位置 |
| `OSS_ANALYZER_CONTAINER` | `auto` | 隔离运行引擎 `auto`·`docker`·`wsl`·`none` |
| `OSS_ANALYZER_WSL_DISTRO` | `oss-sandbox` | 在 Windows 上使用的 WSL 发行版 |
| `OSS_ANALYZER_LEASE_SECONDS` | `3600` | 分析单元的租用时长 |
| `OSS_ANALYZER_UNIT_MAX_LINES` / `_UNIT_MAX_CHARS` | `1500` / `45000` | 分析单元的大小上限 |
| `OSS_ANALYZER_CONTEXT_MAX_CHARS` | `24000` | `context` 单页大小 |
| `OSS_ANALYZER_LSP_TIMEOUT_SECONDS` / `_LSP_IDLE_SECONDS` | `900` / `600` | 语言服务器响应等待·空闲退出 |
| `OSS_ANALYZER_RULEPACKS` | — | 额外的入口点规则目录 |
| `OSS_ANALYZER_CBM` · `_ASTGREP` · `_JOERN` · `_JAVA_HOME` | 自动查找 | 直接指定引擎可执行文件 |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | 安装技能的 Claude Code 配置目录 |

## 安全原则

- **不运行被分析的代码。** 语言服务器的构建脚本·宏执行也会被关闭。需要运行的验证（覆盖率·移植原项目）
  在容器（网络隔离）中运行，或仅对用户声明信任的仓库在临时副本中运行。
- **不读取机密文件**，普通文件中的机密值（密码·密钥·令牌·URL 凭据）在输出时以 `****` 遮盖。
  运行环境变量中的令牌·密钥类也会被剔除后再传递。
- 分析**只在专用 clone 的固定提交**上进行。正在工作的原项目目录中未提交的更改不会被反映。
- 移植的人工批准只通过桌面应用、MCP 确认窗口或用户的终端输入获取 — 不接受自动运行中的代理。

## 已知限制

| 限制 | 应对 |
|---|---|
| 差分验证只能证明实际运行过的输入 | 通过输入自动变异·覆盖率扩大范围，剩余范围记录在证据的 limitations 中。不比较性能 |
| 覆盖率只能证明“被执行过” | 具体是哪个分支·输入，需要用用例另行确认 |
| 基于名称的调用关系只是候选 | 需通过语言服务器（L1）或运行（L3）确认后才算证据 |
| 动态分派·反射·宏生成的代码 | 自动标记为未确认 |
| 数据流引擎首次运行较慢（1 万行 Java 约 15 分钟） | 缓存，可用 `dataflow --build-only` 预先生成 |
| CLI 每次调用都会重新启动语言服务器 | 同一语言在进程间排队。MCP 模式会复用服务器 |
| 随配置值变化的路径 | 按默认值展开并标记为未确认 |
| 由于是本地程序，若有人以相同权限蓄意伪造记录则无法阻止 | 伪造尝试会以门禁失败的形式暴露。最终判定可以在干净的环境中重新运行 |

## 许可

**允许使用，禁止修改** — [LICENSE](LICENSE)（这不是开源许可证）。本译文仅供参考，以韩文 LICENSE 原文为准。

- 任何人都可以在未修改的状态下安装·使用（包括公司业务·商业用途）。使用本工具包产出的成果归用户所有。
- 修改·创作衍生作品·分发修改版·将部分代码纳入其他软件，均需获得作者的书面许可。
- 改进建议请通过本仓库的 Issue·Pull Request 提交。

所使用的库·外部引擎各自遵循其自身的许可 — [THIRD_PARTY.md](THIRD_PARTY.md)。
