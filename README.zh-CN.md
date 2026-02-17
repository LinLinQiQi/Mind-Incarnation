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

示例：Claude Code（请按你本机版本调整 flags/args）

编辑 `~/.mind-incarnation/config.json`：

```json
{
  "hands": {
    "provider": "cli",
    "cli": {
      "prompt_mode": "arg",
      "exec": ["claude", "...", "{prompt}", "..."],
      "resume": ["claude", "...", "{thread_id}", "...", "{prompt}", "..."],
      "thread_id_regex": "\"session_id\"\\s*:\\s*\"([A-Za-z0-9_-]+)\""
    }
  }
}
```

说明：

- 支持占位符：`{project_root}`、`{prompt}`、`{thread_id}`（仅 resume）。
- 如果 CLI 能输出 JSON 事件（例如 “stream-json”），MI 会尽力解析，以提升证据提取、session id 识别与“最后一条消息”识别的可靠性。

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
mi run --cd /path/to/your/project --show "完成 X，并用最小检查验证。"
```

关于 `--cd`（项目根目录）的说明：

- `--cd` 可省略：
  - 在 git repo 内部：MI 默认会使用 git toplevel（仓库根目录），除非当前目录曾经被你作为一个独立的 MI project root 使用过（例如 monorepo 子项目）。
  - 不在 git repo 内：MI 会优先使用 `@last`（如果已记录），否则使用当前目录。
- 你也可以设置环境变量 `$MI_PROJECT_ROOT`，从任意目录运行 MI 命令而无需反复写 `--cd`。
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
mi last --cd /path/to/your/project
mi last --cd /path/to/your/project --redact
```

查看项目级状态（overlay + 存储路径解析）：

```bash
mi project show --cd /path/to/your/project
mi project show --cd /path/to/your/project --json
mi project show --cd /path/to/your/project --redact
```

项目选择快捷方式（`@last/@pinned/@alias`）：

```bash
mi project use --cd /path/to/your/project
mi project pin --cd /path/to/your/project
mi project unpin
mi project alias add repo1 --cd /path/to/your/project
mi project alias list

mi run --cd @repo1 --show "完成 X，并用最小检查验证。"
```

说明：为了兼容旧版本，`--json` 输出里保留了一些 legacy 字段名（例如 `codex_last_message`、`next_codex_input`），它们实际指的是 Hands。

查看 EvidenceLog / 展示原始 transcript：

```bash
mi evidence tail --cd /path/to/your/project -n 20
mi transcript show --cd /path/to/your/project -n 200
mi transcript show --cd /path/to/your/project -n 200 --redact
```

可选：归档旧 transcript（gzip + stub；默认 dry-run）：

```bash
mi gc transcripts --cd /path/to/your/project
mi gc transcripts --cd /path/to/your/project --apply
```

偏好收紧（可回滚；严格 Thought DB 模式）：

```bash
# 手动应用一条已记录的建议（当 auto-learn 关闭，或你想手动控制时）：
mi claim apply-suggested <suggestion_id> --cd /path/to/your/project

# 查看/回滚“规范化”的偏好 Claim：
mi claim list --cd /path/to/your/project --scope effective
mi claim retract <claim_id> --cd /path/to/your/project --scope project
```

说明：`learned_changes` 建议都会记录到 EvidenceLog（`kind=learn_suggested`）。如果 `violation_response.auto_learn=true`（默认），MI 也会把它们落盘为 Thought DB 的 preference Claim（`applied_claim_ids`）。如果为 false，可用 `mi claim apply-suggested ...` 之后再应用。

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
mi claim show <claim_id> --cd /path/to/your/project
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
