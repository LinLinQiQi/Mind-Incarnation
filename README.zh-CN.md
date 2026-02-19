# Mind Incarnation（MI）

[English](README.md) | [中文](README.zh-CN.md)

Mind Incarnation（MI）是一个“心智层（mind layer）”：它位于执行型 agent（V1：Codex CLI）**之上**，目标是用更少的用户负担完成更多事情：

- 注入最小的价值观/偏好上下文（“light injection”）
- 读取底层 agent 的原始输出（完整 transcript），并决定下一步怎么做
- 在可能的情况下，基于「价值观 + 证据 + 记忆」替用户自动回答底层 agent 的问题
- 持久化 EvidenceLog，避免上下文丢失，并支持 MI 自评是否形成闭环完成

状态：V1（草案），基于 batch 的 Hands 自动推进器（默认：Codex CLI）。

## 核心原则

- MI 是操控者，不是执行者：只负责控制输入与读取输出；**不**代理/拦截/门禁底层工具或命令。
- 不强制协议：MI 不应把底层 agent 绑进僵硬的 step-by-step 报告协议里。
- 降低用户负担：默认自动推进；只有在无法安全继续或价值观不明确时才问用户。
- 默认透明：始终记录原始 transcript 与 EvidenceLog，方便审计“发生了什么/为什么这么做”。
- 个人化且可调：价值观通过 prompt 描述并被结构化；学习（learned）是可回滚的。

## 文档

- V1 规范（事实来源）：`docs/mi-v1-spec.md`
- Workflows + host adapters（实验性；包含 OpenClaw 的 Skills-only 目标）：详见 `docs/mi-v1-spec.md`

## 环境要求

- Python 3.10+
- 默认 providers：已安装并完成鉴权的 Codex CLI
- 可选：通过 `mi config` 配置替代的 Mind/Hands providers（OpenAI 兼容 API、Anthropic、其他 agent CLI）

## 安装

可编辑安装（推荐用于开发）：

```bash
pip install -e .
```

安装后可直接使用 `mi` 命令（也可以继续用 `python -m mi`）。

```bash
mi version
```

## 开发

运行单元测试：

```bash
make check
```

最佳努力的文档漂移检查（默认只警告；设置 `MI_DOCCHECK_STRICT=1` 可在有警告时失败）：

```bash
make doccheck
```

注：CI 会以严格模式运行 doccheck。

如果没有 `make`：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## 快速开始

初始化 providers 配置（默认写入 `~/.mind-incarnation/config.json`）：

```bash
mi config init
mi config path
mi config show
mi config validate
mi config examples
mi config template mind.openai_compatible
mi config apply-template mind.openai_compatible
mi config rollback
```

可选：把 Mind 切换到 OpenAI 兼容 API（OpenAI/DeepSeek/Qwen/GLM 等）

编辑 `~/.mind-incarnation/config.json`：

```json
{
  "mind": {
    "provider": "openai_compatible",
    "openai_compatible": {
      "base_url": "https://api.openai.com/v1",
      "model": "<model>",
      "api_key_env": "OPENAI_API_KEY"
    }
  }
}
```

可选：用其他 agent CLI 作为 Hands（wrapper）

MI 可以通过 `hands.provider=cli` 包装大多数 agent CLI。你需要提供 *你本机安装的工具* 的启动命令与参数（不同版本的 flags 可能不同）。

示例：Claude Code（headless + stream-json；请按你本机版本调整 flags/args）

编辑 `~/.mind-incarnation/config.json`：

```json
{
  "hands": {
    "provider": "cli",
    "cli": {
      "prompt_mode": "arg",
      "exec": ["claude", "-p", "{prompt}", "--output-format", "stream-json"],
      "resume": ["claude", "-p", "{prompt}", "--output-format", "stream-json", "--resume", "{thread_id}"],
      "thread_id_regex": "\"(?:session_id|sessionId)\"\\s*:\\s*\"([A-Za-z0-9_-]+)\""
    }
  }
}
```

说明：

- 支持占位符：`{project_root}`、`{prompt}`、`{thread_id}`（仅 resume）。
- `-p` 表示 headless/非交互执行（wrapper 推荐使用）。
- 如果 CLI 能输出 JSON（例如 `--output-format stream-json` 或 `json`），MI 会尽力解析，以提升证据提取、session id 识别与“最后一条消息”识别的可靠性。
- 如果你希望 MI 在多次 `mi run` 之间复用上一轮的 Claude Code session，请设置 `hands.continue_across_runs=true`。

设置全局价值观/偏好（canonical: Thought DB）：

```bash
mi values set --text "我的偏好：尽量少问；默认行为不变重构；没有测试就停下来；非必要不联网/不安装依赖/不 push。"
mi init --values "..."  # 兼容旧命令
mi values show
```

说明：

- `mi init` / `mi values set` 会在 `~/.mind-incarnation/global/evidence.jsonl` 追加一个全局 EvidenceLog `values_set` 事件（稳定的 `event_id` 证据来源）。
- 同时会写入一条 raw values 的 preference Claim（标签 `values:raw`，用于审计）。当编译成功（即未使用 `--no-compile`）时，还会写入一条全局 Summary 节点（标签 `values:summary`，方便人类查看）。
- 除非设置 `--no-compile` 或 `--no-values-claims`，还会把价值观衍生为全局 Thought DB 的 preference/goal Claim（带 `values:base` 标签），供 `mi run` 的 `decide_next` 作为 canonical values 使用。

操作性默认设置（canonical: Thought DB）：

```bash
mi settings show --cd /path/to/your/project
mi settings set --ask-when-uncertain ask --refactor-intent behavior_preserving
mi settings set --scope project --cd /path/to/your/project --ask-when-uncertain proceed
```

在 Hands 之上运行 MI（默认将 transcript + evidence 写入 `~/.mind-incarnation/projects/<id>/`；默认 Hands=Codex）：

```bash
# 引号可选；支持多词 task（示例用英文更直观）：
mi run --cd /path/to/your/project Do X then verify with minimal checks
mi run --cd /path/to/your/project "完成 X，并用最小检查验证。"
```

说明：

- `mi run` 默认会打印 live 流，便于你看到全过程：
  - `[mi]` MI 的阶段/决策日志
  - `[mi->hands]` MI 发给 Hands 的完整 prompt（light injection + batch_input）
  - `[hands]` Hands 的渲染输出（用 `--hands-raw` 可展示原始捕获流）
- 脚本/CI 用 `--quiet`；不想展示 MI->Hands prompt 用 `--no-mi-prompt`；需要安全展示用 `--redact`。

日常状态 + 统一查看入口（降低命令面、减少心智负担）：

```bash
mi status --cd /path/to/your/project
mi status --cd /path/to/your/project --json

# 通过 id（ev_/cl_/nd_/wf_/ed_）或 transcript 路径查看：
mi show ev_<id> --cd /path/to/your/project --json
mi show ev_<id> --global --json
mi show cl_<id> --cd /path/to/your/project --json
mi show wf_<id> --cd /path/to/your/project --json
mi show /path/to/transcript.jsonl -n 200

# 便捷 pseudo-ref（委托给已有命令，保持行为一致）：
mi show last --cd /path/to/your/project --json
mi show project --cd /path/to/your/project --json
mi show hands --cd /path/to/your/project -n 200
mi show mind --cd /path/to/your/project -n 200

# 列表入口（分别是 claim/node/edge/workflow list 的 alias）：
mi ls claims --cd /path/to/your/project
mi ls nodes --cd /path/to/your/project
mi ls workflows --cd /path/to/your/project

# 编辑 workflow（`mi workflow edit` 的 alias）：
mi edit wf_<id> --cd /path/to/your/project --request "..."
```

可选：在 run 结束时跑一次 WhyTrace（opt-in；会写入 `kind=why_trace`，并可能 materialize `depends_on` 边）：

```bash
mi run --cd /path/to/your/project --why "完成 X，并用最小检查验证。"
```

关于 `--cd`（项目根目录）的说明：

- 大多数“项目级”命令都支持 `--cd <project_root>` 来指定要操作的项目。
- 你也可以用 `mi -C <project_root> <cmd> ...` 为本次命令设置默认项目根目录（argparse：`-C/--cd` 需要写在子命令之前）。如果同时提供了 `-C/--cd` 和子命令自己的 `--cd`，则以子命令 `--cd` 为准。
- 你可以通过 `mi --here <cmd> ...` 强制把 project root 设为当前工作目录（即使在 git repo 内也不会自动跳到 repo root；全局参数，必须写在子命令之前）。适用于 monorepo 子目录；当你提供 `--cd/-C` 时该开关会被忽略。
- `--cd` 可省略：
  - 在 git repo 内部：MI 默认会使用 git toplevel（仓库根目录），除非当前目录曾经被你作为一个独立的 MI project root 使用过（例如 monorepo 子项目）。
  - 不在 git repo 内：MI 会优先使用 `@pinned`（如果已记录），否则使用 `@last`（如果已记录），否则使用当前目录。
- 你也可以设置环境变量 `$MI_CD`（可以是路径或 `@last/@pinned/@alias`），从任意目录运行 MI 命令而无需反复写 `--cd`/`-C`。
- 也可以用选择 token：
  - `--cd @last` / `--cd @pinned` / `--cd @<alias>`
  - 通过 `mi project use`、`mi project pin/unpin`、`mi project alias add/rm/list` 管理
- `runtime.project_selection.auto_update_last` 用于控制项目级命令是否自动更新 `@last`（默认：true）。

可选：跨多次运行恢复/重置 Hands 会话（best-effort）：

```bash
mi run --cd /path/to/your/project --continue-hands "继续上次的工作。"
mi run --cd /path/to/your/project --reset-hands "重新开始一个新会话。"
```

查看最近一次 batch（MI 发给 Hands 的输入、最后输出、证据与路径指针；以及 MI 的 decide_next 决策、mind transcript 指针，和相关的 `learn_suggested` 建议 id）：

```bash
mi show last --cd /path/to/your/project
mi show last --cd /path/to/your/project --redact
```

注：`mi show last --json`（即 `mi last --json` 的 alias）在存在 WhyTrace（例如通过 `mi run --why`）时，会包含 `why_trace` / `why_traces`；当 MI 需要 quarantine 损坏的 state 文件时，会包含 `state_corrupt_recent`；在 MI 检测到并尝试打破“重复卡住”的循环时，会包含 `loop_guard` 和 `loop_break` 字段。你也可以通过 `MI_STATE_WARNINGS_STDERR=1`（强制打印）/ `0`（静默）控制底层 state 告警的 stderr 输出。

查看项目级状态（overlay + 存储路径解析）：

```bash
mi show project --cd /path/to/your/project
mi show project --cd /path/to/your/project --json
mi show project --cd /path/to/your/project --redact
```

查看 MI 将如何解析 project root（只读；不会更新 `@last`）：

```bash
mi project status
mi project status --json
mi --here project status --json
```

项目选择快捷方式（`@last/@pinned/@alias`）：

```bash
mi project use --cd /path/to/your/project
mi project pin --cd /path/to/your/project
mi project unpin
mi project alias add repo1 --cd /path/to/your/project
mi project alias list

mi run --cd @repo1 "完成 X，并用最小检查验证。"
```

查看 EvidenceLog / 展示原始 transcript：

```bash
mi evidence tail --cd /path/to/your/project -n 20
mi show <event_id> --cd /path/to/your/project
mi show <event_id> --cd /path/to/your/project --redact
mi show hands --cd /path/to/your/project -n 200
mi show hands --cd /path/to/your/project -n 200 --redact
mi show mind --cd /path/to/your/project -n 200
```

可选：归档旧 transcript（gzip + stub；默认 dry-run）：

```bash
mi gc transcripts --cd /path/to/your/project
mi gc transcripts --cd /path/to/your/project --apply
```

可选：压缩 Thought DB JSONL（先归档再重写；默认 dry-run）：

```bash
mi gc thoughtdb --cd /path/to/your/project
mi gc thoughtdb --cd /path/to/your/project --apply

mi gc thoughtdb --global
mi gc thoughtdb --global --apply
```

偏好收紧（可回滚；严格 Thought DB 模式）：

```bash
# 手动应用一条已记录的建议（当 auto-learn 关闭，或你想手动控制时）：
mi claim apply-suggested <suggestion_id> --cd /path/to/your/project

# 查看/回滚“规范化”的偏好 Claim：
mi claim list --cd /path/to/your/project --scope effective
mi claim retract <claim_id> --cd /path/to/your/project --scope project
```

说明：`learn_suggested` 建议都会记录到 EvidenceLog（`kind=learn_suggested`）。如果 `violation_response.auto_learn=true`（默认），MI 也会把它们落盘为 Thought DB 的 preference Claim（`applied_claim_ids`）。如果为 false，可用 `mi claim apply-suggested ...` 之后再应用。

可选：当 `config.runtime.violation_response.learn_update.enabled=true`（默认）且 `violation_response.auto_learn=true` 时，MI 可能在 `mi run` 结束时执行一次 consolidation（`kind=learn_update`）来减少重复噪声。该步骤会尽量写入少量规范化 patch（claims/edges）并对旧的 learned claim 做 append-only retract（best-effort）。

实验性：偏好预测（preference mining）

- 如果 `config.runtime.preference_mining.auto_mine=true`（默认），MI 会在 `mi run` 过程中根据大模型判断的 checkpoint（包含 run 结束时）调用 `mine_preferences`，并在重复出现时输出 `kind=learn_suggested`（详见 `docs/mi-v1-spec.md`）。

实验性：Thought DB（原子 Claim + Node）

MI 可以维护一个追加写（append-only）的“Thought DB”，把可复用的原子 `Claim`（fact/preference/assumption/goal）沉淀下来，并且 provenance 只引用 **EvidenceLog 的 `event_id`**（便于审计和追溯）。

- 如果 `config.runtime.thought_db.auto_mine=true`（默认），MI 会在 `mi run` 的 checkpoint 边界调用 `mine_claims`，并记录 `kind=claim_mining`。
- 如果 `config.runtime.thought_db.auto_materialize_nodes=true`（默认），MI 也会在 checkpoint 边界把 `Decision` / `Action` / `Summary` 节点落盘（确定性；不增加额外模型调用），并记录 `kind=node_materialized`。
- 记忆索引（memory index）：Thought DB 的 `claim` / `node` 都可以被索引用于文本召回。默认 `cross_project_recall.include_kinds` 是 Thought-DB-first（`snapshot` / `workflow` / `claim` / `node`）。
- Claim/Edge 存储在项目级（以及可选的全局）目录中，可用 CLI 管理：

```bash
mi claim list --cd /path/to/your/project --scope effective
mi claim list --cd /path/to/your/project --scope effective --type preference --tag values:base --contains "tests"
mi claim show <claim_id> --cd /path/to/your/project
mi claim show <claim_id> --cd /path/to/your/project --json --graph --depth 2
mi claim mine --cd /path/to/your/project
mi claim retract <claim_id> --cd /path/to/your/project
mi claim supersede <claim_id> --cd /path/to/your/project --text "..."
mi claim same-as <dup_id> <canonical_id> --cd /path/to/your/project
```

节点（Decision/Action/Summary）：

```bash
mi node list --cd /path/to/your/project --scope effective
mi node create --cd /path/to/your/project --scope project --type decision --title "..." --text "..."
mi node show <node_id> --cd /path/to/your/project
mi node show <node_id> --cd /path/to/your/project --json --graph --depth 2
mi node retract <node_id> --cd /path/to/your/project
```

边（Edge）：

```bash
mi edge create --cd /path/to/your/project --scope project --type depends_on --from <from_id> --to <to_id>
mi edge list --cd /path/to/your/project --scope project
mi edge list --cd /path/to/your/project --scope project --type depends_on --from <event_id>
mi edge show <edge_id> --cd /path/to/your/project
```

根因追踪（WhyTrace）：

```bash
mi why last --cd /path/to/your/project
mi why event <event_id> --cd /path/to/your/project
mi why claim <claim_id> --cd /path/to/your/project
```

## Workflows + Host Adapters（实验性）

Workflow 是可复用流程，可以是**项目级（project-scoped）**或**全局（global）**。MI 会把项目的**有效（effective）**启用 workflow（project + global，且 project 优先）导出到宿主 workspace（派生物）。

在 `mi run` 中：

- 如果某个已启用 workflow 命中任务（`trigger.mode=task_contains`），MI 会把它注入到第一个 batch 的输入里。
- 当 workflow 处于 active 状态时，MI 会在 `ProjectOverlay.workflow_run` 中维护一个 best-effort 的 step 指针（不会强制 step-by-step 汇报）。
- 如果 `config.runtime.workflows.auto_mine=true`（默认），MI 会在 `mi run` 过程中根据大模型判断的 checkpoint（包含 run 结束时）调用 `suggest_workflow`，并在重复出现时固化为 workflow。

创建/编辑 workflow：

```bash
mi workflow create --cd /path/to/your/project --scope project --name "My workflow"
mi workflow create --cd /path/to/your/project --scope global --name "My global workflow"
mi workflow list --cd /path/to/your/project --scope effective
mi workflow show <workflow_id> --cd /path/to/your/project --markdown
mi workflow edit <workflow_id> --cd /path/to/your/project --scope effective --request "把第 2 步改为跑测试"

# 对某个 global workflow 做项目级覆盖（不修改 global 源文件）：
mi workflow disable <workflow_id> --cd /path/to/your/project --scope global --project-override
mi workflow edit <workflow_id> --cd /path/to/your/project --scope global --project-override --request "只修改某个小步骤：把 s2 改为先跑测试"
mi workflow delete <workflow_id> --cd /path/to/your/project --scope global --project-override
```

绑定并同步 OpenClaw workspace（Skills-only 目标）：

```bash
mi host bind openclaw --workspace /path/to/openclaw/workspace --cd /path/to/your/project
mi host sync --cd /path/to/your/project
```

说明：

- MI 会把派生物写到 `/path/to/openclaw/workspace/.mi/generated/openclaw/...`（可随时重新生成）。
- MI 会把生成的 skill 目录以 symlink 方式注册到 `/path/to/openclaw/workspace/skills/<skill_dir>`（best-effort，可回滚）。

## 你会得到什么

- Hands 原始 transcript：`~/.mind-incarnation/projects/<id>/transcripts/hands/*.jsonl`
- Mind transcripts（MI prompt-pack 调用）：`~/.mind-incarnation/projects/<id>/transcripts/mind/*.jsonl`
- EvidenceLog（追加写入；包含 `snapshot` + `cross_project_recall` 等记录）：`~/.mind-incarnation/projects/<id>/evidence.jsonl`
- 全局 EvidenceLog（追加写入；价值观/偏好生命周期事件，例如 `values_set`）：`~/.mind-incarnation/global/evidence.jsonl`
- Thought DB（追加写的 Claim/Edge/Node）：`~/.mind-incarnation/projects/<id>/thoughtdb/{claims,edges,nodes}.jsonl` 以及 `~/.mind-incarnation/thoughtdb/global/{claims,edges,nodes}.jsonl`
- 记忆文本索引（materialized view；可重建；默认 backend=`sqlite_fts`）：`~/.mind-incarnation/indexes/memory.sqlite`

记忆索引维护：

```bash
mi memory index status
mi memory index rebuild
```

进阶：可通过 `$MI_MEMORY_BACKEND` 覆盖记忆 backend（默认 `sqlite_fts`；`in_memory` 为临时/不落盘）。

## V1 的非目标

- 多 agent 路由
- 硬权限控制 / 工具级门禁

## License

MIT，见 `LICENSE`。
