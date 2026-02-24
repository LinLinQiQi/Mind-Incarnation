# Mind Incarnation (MI)

[English](README.md) | [中文](README.zh-CN.md)

Mind Incarnation（MI）是一个“价值观驱动”的 mind layer，位于执行类 agent 之上（V1 的 Hands：Codex CLI），目标是减少用户负担：

- 注入最小的价值观/偏好上下文（light injection）
- 读取底层 agent 的原始输出（完整 transcript）并决定下一步
- 能用“价值观 + 证据 + 记忆”自动回答 agent 的问题
- 持久化 EvidenceLog，避免上下文丢失并支持事后自评是否完成

状态：V1（草稿），在 Hands 之上的 batch autopilot。

## 核心原则

- 控制者而非执行者：MI 只控制输入、读取输出，不代理/拦截工具与命令。
- 低用户负担：默认自动推进；只有无法安全继续时才问用户。
- 透明：始终保留 raw transcripts + EvidenceLog 便于审计。
- 个性化可调：价值观用自然语言描述；学习可回滚。

## 文档

- 行为规范（唯一真相）：`docs/mi-v1-spec.md`
- CLI 指南（可操作参考）：`docs/cli.zh-CN.md`
- Thought DB 设计说明：`docs/mi-thought-db.md`
- Internals（贡献者）：`docs/internals.md`

## 环境要求

- Python 3.10+
- 默认 Hands：已安装并完成登录的 Codex CLI
- 可选：通过 `mi config` 配置其他 Mind/Hands provider

## 安装

```bash
pip install -e .
mi version
```

## 快速开始（60 秒）

```bash
# 1) 写入 values（global）：
mi init --values "我偏好行为不变的重构；没测试就停下来问；尽量避免联网/安装依赖。"

# 2) 跑在你的 repo 上（path-first shorthand 会设置 project root）：
mi /path/to/your/project run "完成 X，并用最小检查验证。"

# 3) 查看最近一轮 batch bundle：
mi /path/to/your/project last --json

# 4) 查看底层 Hands transcript：
mi /path/to/your/project hands -n 200
```

完整命令参考（workflows/host adapters、Thought DB、GC、memory index 等）见 `docs/cli.zh-CN.md`。

## 开发

```bash
make check
make doccheck
```

## 存储

默认写入 `~/.mind-incarnation/`（transcripts、EvidenceLog、Thought DB、indexes）。精确目录结构以 `docs/mi-v1-spec.md` 为准。

## 协议

MIT，见 `LICENSE`。

