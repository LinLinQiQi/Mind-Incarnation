# Mind Incarnation（MI）

[English](README.md) | [中文](README.zh-CN.md)

Mind Incarnation（MI）是一个“心智层（mind layer）”：它位于执行型 agent（V1：Codex CLI）**之上**，目标是用更少的用户负担完成更多事情：

- 注入最小的价值观/偏好上下文（“light injection”）
- 读取底层 agent 的原始输出（完整 transcript），并决定下一步怎么做
- 在可能的情况下，基于「价值观 + 证据 + 记忆」替用户自动回答底层 agent 的问题
- 持久化 EvidenceLog，避免上下文丢失，并支持 MI 自评是否形成闭环完成

状态：V1（草案），基于 batch 的 Codex 自动推进器。

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
- 已安装并完成鉴权的 Codex CLI

## 安装

可编辑安装（推荐用于开发）：

```bash
pip install -e .
```

安装后可直接使用 `mi` 命令（也可以继续用 `python -m mi`）。

```bash
mi version
```

## 快速开始

初始化全局价值观/偏好（默认写入 `~/.mind-incarnation/mindspec/base.json`）：

```bash
mi init --values "我的偏好：尽量少问；默认行为不变重构；没有测试就停下来；非必要不联网/不安装依赖/不 push。"
```

在 Codex 之上运行 MI（默认将 transcript + evidence 写入 `~/.mind-incarnation/projects/<id>/`）：

```bash
mi run --cd /path/to/your/project --show "完成 X，并用最小检查验证。"
```

查看最近一次 batch（MI 发给 Codex 的输入、最后输出、证据与路径指针）：

```bash
mi last --cd /path/to/your/project
```

查看 EvidenceLog / 展示原始 transcript：

```bash
mi evidence tail --cd /path/to/your/project -n 20
mi transcript show --cd /path/to/your/project -n 200
```

## 你会得到什么

- Hands 原始 transcript：`~/.mind-incarnation/projects/<id>/transcripts/hands/*.jsonl`
- Mind transcripts（MI prompt-pack 调用）：`~/.mind-incarnation/projects/<id>/transcripts/mind/*.jsonl`
- EvidenceLog（追加写入）：`~/.mind-incarnation/projects/<id>/evidence.jsonl`

## V1 的非目标

- 多 agent 路由
- 硬权限控制 / 工具级门禁

## License

MIT，见 `LICENSE`。
