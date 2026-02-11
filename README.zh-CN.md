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

初始化全局价值观/偏好（默认写入 `~/.mind-incarnation/mindspec/base.json`）：

```bash
mi init --values "我的偏好：尽量少问；默认行为不变重构；没有测试就停下来；非必要不联网/不安装依赖/不 push。"
```

在 Hands 之上运行 MI（默认将 transcript + evidence 写入 `~/.mind-incarnation/projects/<id>/`；默认 Hands=Codex）：

```bash
mi run --cd /path/to/your/project --show "完成 X，并用最小检查验证。"
```

可选：跨多次运行恢复/重置 Hands 会话（best-effort）：

```bash
mi run --cd /path/to/your/project --continue-hands "继续上次的工作。"
mi run --cd /path/to/your/project --reset-hands "重新开始一个新会话。"
```

查看最近一次 batch（MI 发给 Hands 的输入、最后输出、证据与路径指针；以及 MI 的 decide_next 决策与 mind transcript 指针）：

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

学习层（learned，可回滚）：

```bash
mi learned list --cd /path/to/your/project
mi learned disable <id> --scope project --cd /path/to/your/project
mi learned apply-suggested <suggestion_id> --cd /path/to/your/project
```

说明：如果 MindSpec base 中 `violation_response.auto_learn=false`，MI 不会自动写入 `learned.jsonl`，而是把建议记录到 EvidenceLog（`kind=learn_suggested`），之后可用 `apply-suggested` 手动应用。

## 你会得到什么

- Hands 原始 transcript：`~/.mind-incarnation/projects/<id>/transcripts/hands/*.jsonl`
- Mind transcripts（MI prompt-pack 调用）：`~/.mind-incarnation/projects/<id>/transcripts/mind/*.jsonl`
- EvidenceLog（追加写入）：`~/.mind-incarnation/projects/<id>/evidence.jsonl`

## V1 的非目标

- 多 agent 路由
- 硬权限控制 / 工具级门禁

## License

MIT，见 `LICENSE`。
