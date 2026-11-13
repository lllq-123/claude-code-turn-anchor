# claude-code-turn-anchor

给 Claude Code 长会话周期性追加一条短锚点，帮助模型找回角色、语气和当前约束。

**当前版本默认第 1、16、31…个 prompt 注入一次；只追加，永不改写 transcript。**

[English](README.en.md)

> [!IMPORTANT]
> **2026-09-02 破坏性更正：旧版“每轮注入，再清空历史 attachment”的实现已删除。**
>
> Claude Fable 5.1 的 preserved thinking 会把 thinking block 绑定到生成它时的精确前缀。
> 如果从 user 与 assistant thinking 之间删掉旧 anchor，再 resume 并重放后续 thinking，
> 不仅会打掉 prompt cache；启用强制绑定的账号还可能直接收到 400。
>
> 如果你装过旧版，请重新运行安装器。新版会覆盖运行脚本并保留你自己的 `anchor.md`。

## 原理

Claude Code 的 `UserPromptSubmit` hook 可以返回 `additionalContext`。本项目用独立于
transcript 的 session 计数器控制频率：

```text
第 1 个 prompt   → 追加 TURN_ANCHOR
第 2–15 个       → 静默
第 16 个         → 再追加一份
第 17–30 个      → 静默
……
```

状态保存在 `~/.claude/turn-anchor-state/`，不会进入模型输入。运行脚本不会读取、扫描、
清空或重写 session JSONL。

这符合 Fable 5.1 的核心要求：**conversation history 保持 append-only**。

## 为什么不是每轮注入

`additionalContext` 会作为 hook attachment 留在历史里。每轮都加会让相同提醒不断累积；
每轮加完再删则会修改历史前缀。默认每 15 个 prompt 一次是在“信号仍在最近窗口”与
“上下文不过度膨胀”之间的保守折中，可通过环境变量调整。

## 哪些历史操作安全

- ✅ **整轮尾部撤回 / rewind**：从真实 user 轮次组首，把该组、attachment、thinking、
  assistant 和更晚轮次一起截掉，回到曾经存在过的旧前缀。普通输入、心跳、工具返回
  都是 user；连续真实 user 算一组，跨元数据 / replay 也不能只截后半。旧 cache entry
  有机会复用，但 TTL、缓存断点及 resume 请求重建仍影响命中，不能保证。
  真实 user 上的 `isMeta=true` 仍计作 user；元数据记录指 system / attachment 等顶层类型。
- ⚠️ **只改当前最后一条 assistant 的可见尾巴**：后面还没有新 thinking 时，只从改点
  产生尾部分叉。
- ❌ **改历史中段并保留后续 thinking**：后续 thinking 绑定在旧前缀上，不兼容 Fable 5.1。
- ❌ **清空旧 hook attachment 再 resume**：这正是旧版已经删除的危险形状。

整组工具返回也可作为撤回起点，即使保留前缀含对应 `tool_use`：某些 Claude Code
版本会补“中断 / 结果未知”的返回，但不保证所有版本行为相同。删记录不会撤销
已经执行的工具动作，重试前先核实结果。assistant / thinking / attachment / 请求快照
内部的 `role=user` 不算真实入口。手工修复可用 [JSONL 小剪刀](https://github.com/lllq-123/claude-output-guard#用-jsonl-小剪刀修复)
先预览；默认剪尾会删除剪口后的全部内容，不保留后续 thinking。

官方说明：

- [Preserved thinking prefix checks](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking/prefix-checks)
- [Prompting Claude Fable 5.1 — Keep the conversation history append-only](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1)
- [Claude Code prompt caching](https://code.claude.com/docs/en/prompt-caching)

## 安装

要求：Python 3.10+、Claude Code。

```bash
git clone https://github.com/lllq-123/claude-code-turn-anchor.git
cd claude-code-turn-anchor
python3 install.py --dry-run
python3 install.py
```

安装器会：

- 安装 `~/.claude/hooks/claude-code-turn-anchor/turn_anchor.py`；
- 首次安装时创建可编辑的 `anchor.md`，重装时保留已有内容；
- 在 `settings.json` 的 `hooks.UserPromptSubmit` 末尾追加独立 hook group；
- 修改 settings 前保存私有备份。

安装或升级后重启 Claude Code。

## 写自己的 anchor

编辑：

```text
~/.claude/hooks/claude-code-turn-anchor/anchor.md
```

只写正文，不要手写 `<TURN_ANCHOR>` 标签。建议两到五行，只放最近窗口需要周期性
看见的节奏或边界；长期稳定规则仍应写在 `CLAUDE.md`。

## 配置

改变间隔（例如每 10 个 prompt）：

```bash
export CLAUDE_TURN_ANCHOR_INTERVAL=10
```

临时关闭：

```bash
export CLAUDE_TURN_ANCHOR_DISABLED=1
```

使用另一份 anchor：

```bash
export CLAUDE_TURN_ANCHOR_FILE=/absolute/path/to/my-anchor.md
```

环境变量必须在启动 Claude Code 的进程环境中生效。

## 从旧版迁移

```bash
cd claude-code-turn-anchor
git pull
python3 install.py --yes
```

重装会覆盖旧运行脚本、保留自定义 `anchor.md`。旧 transcript 里已经存在的空 attachment
不要为了“清爽”继续做物理手术；让它们随正常 rewind / compact 生命周期退出即可。

## 卸载

```bash
python3 uninstall.py --dry-run
python3 uninstall.py
```

卸载器只移除本项目注册的 handler，并把安装文件移入私有备份目录，不碰其他 hooks。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile src/turn_anchor.py install.py uninstall.py
```

测试会确认：第 1/16 个 prompt 注入、不同 session 独立计数、重启后计数延续、禁用开关
静默，以及带 `transcript_path` 的 payload 也不会让 JSONL 改动一个字节。

## 边界

- 计数单位是 Claude Code 触发的 `UserPromptSubmit`，不是模型内部的 tool call。
- session id 缺失或状态目录不可写时，hook fail closed：本轮不注入。
- anchor 是对齐信号，不是安全边界或强制规则。
- 所有 anchor 与计数状态都留在本机；脚本不联网。

## 相关：每轮临时提醒

本项目采用周期性追加，已经写入的 anchor 随正常历史保留。若需要内容每轮更新、且不进入后续模型上下文的提醒，参见：

- [Claude Code ephemeral reminders](https://github.com/lllq-123/claude-code-ephemeral-reminders)：Claude Code 的请求副本注入研究，需要对应版本的 binary 改动。
- [Codex ephemeral context](https://github.com/lllq-123/codex-ephemeral-context)：Codex app-server 的源码补丁，提供当轮 request-only developer tail。

它们是需要单独部署的方案，不是安装本 hook 后自动启用的模式。“临时”描述上下文生命周期，不是日志或服务端安全擦除。

## License

MIT
