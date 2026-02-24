# MI 命令行指南（V1）

本文是“可操作”的 CLI 参考。行为真相（prompts / schemas / runtime loop）以 `docs/mi-v1-spec.md` 为准。

## 目标

- 常用路径尽量短（减少心智负担）
- 细节不丢，但不要挤在 `README.md` 里

## 项目选择（任意目录运行）

大多数命令都是 project-scoped。MI 会自动解析 `project_root`，但你也可以显式指定：

- 每条命令：`--cd <project_root>`（大多数 project 命令支持）
- 本次调用默认：`mi -C <project_root> <cmd> ...`
- 选择 token：`@last` / `@pinned` / `@<alias>`
- 环境变量：`$MI_CD`（路径或 token）

更短写法（sugar）：

- `mi @pinned status` 等价 `mi -C @pinned status`
- `mi /path/to/repo status` 等价 `mi -C /path/to/repo status`
- 省略子命令时默认是 `status`：
  - `mi` -> `mi status`
  - `mi @pinned` -> `mi -C @pinned status`

管理 token：

```bash
mi project use --cd /path/to/your/project     # 设置 @last
mi project pin --cd /path/to/your/project     # 设置 @pinned
mi project unpin                              # 清除 @pinned
mi project alias add repo1 --cd /path/to/your/project
mi project alias list
```

## 日常工作流（推荐）

最短闭环：

```bash
mi                       # status（默认）
mi run "完成 X，并用最小检查验证。"
mi last --json           # 等价：mi show last --json
mi hands -n 200          # 等价：mi tail hands -n 200
```

核心入口：

- `mi run ...`：在 Hands 之上跑 batch autopilot（默认 Hands：Codex CLI）
- `mi status`：只读状态 + next-step（可复制粘贴）
- `mi show last`：查看最近一轮 batch bundle（前置入口）
- `mi tail ...`：查看 EvidenceLog / transcripts 的“规范入口”

## Values / Settings（首次配置）

初始化并写入 values（global、canonical）：

```bash
mi init --values "我偏好行为不变的重构；没测试就停下来问；尽量避免联网/安装依赖。"
mi values show
```

查看/调整操作偏好（canonical：Thought DB preference claims）：

```bash
mi settings show --cd /path/to/your/project
mi settings set --ask-when-uncertain ask --refactor-intent behavior_preserving
mi settings set --scope project --cd /path/to/your/project --ask-when-uncertain proceed
```

## Run

引号可选，多词 task 直接写即可：

```bash
mi run --cd /path/to/your/project 完成 X 然后最小检查验证
mi run --cd /path/to/your/project "完成 X，并用最小检查验证。"
```

常用 flags：

- `--max-batches N`
- `--continue-hands` / `--reset-hands`
- `--quiet`
- `--hands-raw`
- `--no-mi-prompt`
- `--redact`
- `--why`（run end 生成 WhyTrace）

## Inspect / Tail

通过 id 或 transcript 路径查看：

```bash
mi show ev_<id> --json
mi show ev_<id> --global --json
mi show cl_<id> --json
mi show wf_<id> --json
mi show /path/to/transcript.jsonl -n 200
```

更短写法：

```bash
mi ev_<id> --json         # 等价：mi show ev_<id> --json
mi last --json            # 等价：mi show last --json
mi hands -n 200           # 等价：mi tail hands -n 200
mi mind -n 200 --jsonl    # 等价：mi tail mind -n 200 --jsonl
```

项目级状态（overlay + 路径解析）：

```bash
mi project show --json
mi project status --json     # 只读解析（不会更新 @last）
```

Tail：

```bash
mi tail -n 20
mi tail evidence -n 20 --raw
mi tail evidence --global -n 20 --json
mi tail hands -n 200
mi tail mind -n 200 --jsonl
```

## Thought DB（Claims / Nodes / Edges / Why）

Claims：

```bash
mi claim list --scope effective
mi claim show cl_<id> --json --graph --depth 2
mi claim mine
mi claim retract cl_<id>
mi claim supersede cl_<id> --text "..."
mi claim same-as cl_<dup> cl_<canonical>
```

Nodes：

```bash
mi node list --scope effective
mi node create --type decision --title "..." --text "..."
mi node show nd_<id> --json --graph --depth 2
mi node retract nd_<id>
```

Edges：

```bash
mi edge create --type depends_on --from <from_id> --to <to_id>
mi edge list --type depends_on --from ev_<id>
mi edge show ed_<id> --json
```

WhyTrace：

```bash
mi why last
mi why event ev_<id>
mi why claim cl_<id>
```

## Workflows + Host Adapters（实验性）

Workflows（MI IR）：

```bash
mi workflow create --scope project --name "我的 workflow"
mi workflow list --scope effective
mi workflow show wf_<id> --markdown
mi workflow edit wf_<id> --scope effective --request "把 step2 改成先跑测试"
```

Host 绑定 + 同步（派生物）：

```bash
mi host bind openclaw --workspace /path/to/openclaw/workspace
mi host sync
```

## 维护

归档旧 transcripts（默认 dry-run）：

```bash
mi gc transcripts
mi gc transcripts --apply
```

压缩 Thought DB JSONL（默认 dry-run）：

```bash
mi gc thoughtdb
mi gc thoughtdb --apply
mi gc thoughtdb --global --apply
```

Memory index：

```bash
mi memory index status
mi memory index rebuild
```

