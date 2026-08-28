# claude-code-turn-anchor

给 Claude Code 每轮刷新一条短锚点，并及时清掉历史副本：帮助长对话保持角色、语气和当前约束，同时避免相同提示在上下文里反复累积、占用 token。

[English README](README.en.md)

> [!WARNING]
> 这是非官方社区实验，不隶属于 Anthropic。它会改写 Claude Code 的本地会话
> JSONL；该格式不是稳定的公开 API。请先跑测试和 `--dry-run`，不要拿唯一一份
> 重要会话做第一次实验。当前版本在 Claude Code 2.1.175 上验证。

> [!IMPORTANT]
> **2026-08 重要修正：本方案只适合「每轮 resume」的运行架构。**
> 早期版本的这份 README 没有写清一个关键前提，先读
> [适用架构](#适用架构先确认你的运行方式) 再决定装不装：
>
> - 清理动作只改**磁盘上的** transcript；**运行中的 Claude Code 进程把历史
>   放在内存里，从不回头重读磁盘**。所以在常驻交互式会话里，清理不会减少
>   任何发给模型的内容——省 token 的效果要等到下一次 resume 才兑现；
> - 更重要的是，磁盘和内存从此不一致：**下一次 resume 从磁盘重建历史时，
>   prompt 前缀从第一条被清空的锚点起全部失配，整份 prompt cache 报废**。
>   锚点从第一轮就存在，所以失配点几乎在历史最开头（实测 resume 后
>   99.6–99.8% 的上下文被迫全量重写，按缓存写入 1.25×/2× 计费）。
>
> 在「每轮都是 `claude -p --resume <session-id>` 起新进程」的 headless /
> 自动化架构下没有这个问题：磁盘就是唯一真相，清理立即生效，缓存损耗只有
> 尾部一小段（实测相邻两轮共同前缀约 99.1%）。**常驻交互式终端会话请不要
> 启用本项目。**

## 它解决什么问题

长对话里，一些本来很重要、但只写在很前面的信号会逐渐沉底，例如：

- 当前对话已经建立的角色与关系；
- 希望保持的表达节奏和语气；
- 这一阶段最重要的工作方式；
- 容易被通用默认行为覆盖的少量约束。

Claude Code 的 `UserPromptSubmit` hook 可以在每次用户发送消息时返回
`additionalContext`，把一条短提示放回当前 token 窗口。这适合做
**turn-level anchor（每轮锚点）**。

但官方文档也说明：注入的内容会保存进 session transcript，恢复会话时会重新
播放。若每轮只加不清，聊了 `N` 轮以后，历史里就可能躺着 `N` 份相同锚点。

本项目只在**磁盘 transcript** 里保留当前轮需要的一份：

```text
用户发送 prompt
      │
      ▼
同一个 UserPromptSubmit hook
      │
      ├─ 旧 <TURN_ANCHOR> attachment 的 content → []
      │    （保留 uuid / parentUuid，只改磁盘）
      │
      └─ 返回一份新 anchor 作为 additionalContext
               │
               ▼
   磁盘 transcript 里只剩最新一份锚点
```

**模型什么时候才真的"只看到一份"，取决于你的运行架构**——这是本项目
早期文档没写清、后来用真实 API 账单实测补上的关键前提，见下一节。

## 适用架构：先确认你的运行方式

Claude Code 有两种典型的运行形态，本项目在两边的行为完全不同：

| | 每轮 resume（适用 ✅） | 常驻进程（不适用 ❌） |
|---|---|---|
| 典型场景 | headless 自动化：每轮 `claude -p --resume <sid>` 起新进程，答完即退 | 交互式终端会话；`--input-format stream-json` 长驻进程 |
| 历史从哪来 | 每轮从磁盘 JSONL 重建 | 进程内存，只增不改，从不重读磁盘 |
| 清理何时生效 | **立即**——下一轮重建就用清理后的磁盘 | **本会话内永不生效**——模型每轮照样收到全部历史锚点 |
| 省 token | 真省（见下节公式） | 活进程期间 0 |
| prompt cache 代价 | 只烧尾部一小段：清理只动上一轮的锚点，实测相邻两轮共同前缀 ≈ 99.1% | **下次 resume 时整份报废**：失配点在历史最开头，实测 99.6–99.8% 全量重写 |

机制解释：运行中的 Claude Code 进程把对话历史保存在内存里，发请求永远用
内存那份；transcript JSONL 是**只往后追加的落盘记录**，只有 resume /
重启重建上下文时才会被读。改写磁盘对活进程发出的请求没有任何影响——
但它悄悄改掉了「下次 resume 会读到的历史」。两份从此不一致，resume 一读，
prompt 前缀从第一条被清空的锚点起对不上，缓存服务端只能全量重算。

一个容易踩的验证陷阱：**数磁盘文件里的锚点数量，证明不了模型看到了什么。**
磁盘上"有内容的锚点=1"很好看，但活进程发出去的请求里可能还带着全部 N 份。
想验证真实效果，看 API 返回的 usage（`input_tokens` / `cache_read_input_tokens`）
或 Claude Code 的 `/context`，别看文件。

## 为什么合并成一个 hook

Anthropic 的 hook 指南说明，同一事件下匹配的 hooks 会并行运行。因此本项目
不依赖“先注册清理脚本、再注册注入脚本”这样的数组顺序，而是由一个
`turn_anchor.py` 在一次调用里完成：

1. 读取本轮 hook payload 中的 `transcript_path`；
2. 只寻找 `hook_additional_context` 中自己的 `<TURN_ANCHOR>`；
3. 把旧 attachment 的 `content` 改成真正的空数组 `[]`；
4. 返回刚从 `anchor.md` 读到的新锚点。

它不会删除 JSONL 物理行，也不会改 `uuid` / `parentUuid`，因此对话链仍然
完整；不会清理其他 hook 的上下文，也不会碰普通 user / assistant 正文里恰好
出现的 `<TURN_ANCHOR>` 字样。

## 能省多少 token

**以下公式只在「每轮 resume」架构下成立**（常驻进程里活进程期间省不到，
见上节）。假设锚点长度约为 `A` tokens：

- 只注入不清理：第 `N` 轮附近可能带着约 `N × A` tokens 的历史锚点；
- 本项目：稳定在约 `A` tokens，只留当前一份；
- 按整个 `N` 轮会话粗略累计，少处理的重复输入约为
  `A × N × (N - 1) / 2`。

实际账单还会受 prompt caching、compaction 和 Claude Code 版本影响，这个
公式只描述上下文体积，不等于最终计费数字。

反过来，如果在常驻进程里启用，代价是实打实的：某次真实事故里，一个长会话
在 resume 时因为历史开头的锚点被清空过，`cache_read` 从正常的二十多万 token
跌到 15,493（只剩 system + tools 段命中），十几万 token 按缓存写入价全量
重算；关闭清理后同场景实测 resume 完整继承缓存（`cache_read` 72,197 =
上一轮 67,906 + 4,291，零崩塌）。

## 系统要求

- macOS、Linux 或 WSL；
- Python 3.10+，只使用标准库；
- 支持 hooks 的 Claude Code；
- 当前实现使用 `fcntl.flock`，不直接支持原生 Windows。

相关官方文档：

- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks)
- [Automate workflows with hooks](https://code.claude.com/docs/en/hooks-guide)

官方建议把长期不变的项目规则写在 `CLAUDE.md`；turn anchor 更适合放
**短、重要、容易在长对话里沉底**的动态对齐信号。

## 安装

先 clone 仓库并运行测试：

```bash
git clone https://github.com/lllq-123/claude-code-turn-anchor.git
cd claude-code-turn-anchor

python3 -m unittest discover -s tests -v
python3 install.py --dry-run
python3 install.py
```

安装器会先展示计划并询问确认。它会：

- 把运行脚本复制到
  `~/.claude/hooks/claude-code-turn-anchor/turn_anchor.py`；
- 第一次安装时创建用户可编辑的
  `~/.claude/hooks/claude-code-turn-anchor/anchor.md`；
- 在 `~/.claude/settings.json` 的 `UserPromptSubmit` 数组末尾追加自己的
  hook group；
- 完整保留已有 hooks 和其他设置；
- 修改 settings 前，在 `~/.claude/turn-anchor-backups/` 留一份私有备份。

如果设置了 `CLAUDE_CONFIG_DIR`，安装器会跟随该目录，而不是固定使用
`~/.claude`。

非交互安装：

```bash
python3 install.py --yes
```

不建议使用未经检查的 `curl | bash`。安装完成后，请完全退出并重新打开
Claude Code。

## 写自己的 anchor

编辑安装目录中的：

```text
~/.claude/hooks/claude-code-turn-anchor/anchor.md
```

文件里只写锚点正文，不要自己添加 `<TURN_ANCHOR>` 标签；脚本会自动包裹。

一个短锚点通常只需要 2–5 行：

```md
The current conversation has an established role, tone, and set of constraints.
Follow the user's latest intent while preserving that established context.
Keep the response natural; do not turn this reminder into a checklist.
```

建议：

- 写事实和当前语境，少写大段命令式规则；
- 每条都有明确用途，删除“可能有用”的泛泛提醒；
- 保持短小，让锚点本身不要成为新的上下文负担；
- 长期稳定的项目规范仍放 `CLAUDE.md`；
- 改完 `anchor.md` 后无需重装，下一次 prompt 即生效。

## 临时关闭与自定义路径

从该环境启动 Claude Code 前设置：

```bash
export CLAUDE_TURN_ANCHOR_DISABLED=1
```

这会同时停止旧锚点清理和新锚点注入。

也可以让 hook 读取另一份 anchor：

```bash
export CLAUDE_TURN_ANCHOR_FILE=/absolute/path/to/my-anchor.md
```

## 备份与恢复

每次真正改写 transcript 前，hook 会先把原文件完整备份到：

```text
~/.claude/turn-anchor-backups/
```

每个 session 轮转保留最近 3 份。备份可能包含完整私人对话，请勿上传到
Issue、网盘或在线 JSON viewer。

需要恢复时：

1. 完全退出对应 Claude Code 会话，确认它不再写 transcript；
2. 给当前 JSONL 再留一份副本；
3. 从 `turn-anchor-backups` 选择对应 session 的 `.bak`；
4. 复制回原 transcript 路径后再恢复会话。

## 卸载

```bash
python3 uninstall.py --dry-run
python3 uninstall.py
```

卸载器只移除**精确指向本安装目录**的 hook command，不会动其他
`UserPromptSubmit` handlers。安装文件不会直接删除，而会移进
`~/.claude/turn-anchor-backups/uninstalled-files.*`；已有 transcript 和
settings 备份继续保留。

卸载后重新打开 Claude Code。

## 验证与开发

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile src/turn_anchor.py install.py uninstall.py
```

测试覆盖：

- 只清自己的 marker，不碰正文和其他 hook；
- `content=[]`、物理行数、`uuid/parentUuid` 链保持；
- malformed JSONL 行原样保留；
- 幂等与三份备份轮转；
- 清理失败时不继续注入；
- 安装幂等、保留第三方 hooks、精确卸载；
- 用户自定义 `anchor.md` 在重装时不被覆盖。

## 已知限制

- **只适用于每轮 resume 架构**（见「适用架构」节）：常驻进程内清理不生效，
  且会在下一次 resume 时打掉整份 prompt cache。
- 这是对 Claude Code 内部 transcript 结构的谨慎改写，不是官方提供的
  “删除已注入上下文”API；Claude Code 升级后应重新跑测试并先用非重要会话
  验证。
- 当前 Claude Code 在重建上下文时会跳过 `content=[]` 的 hook attachment；
  若以后内部格式改变，本项目也需要跟着更新。
- 官方目前没有 ephemeral（一次性）注入选项，相关请求挂在
  [claude-code#40216](https://github.com/anthropics/claude-code/issues/40216)。
  另一个方向：**`SessionStart` hook 的 `additionalContext` 不会写进
  transcript**（[#11906](https://github.com/anthropics/claude-code/issues/11906)）——
  如果你的注入内容整个会话都不变，直接用 `SessionStart` 注入即可，
  不需要本项目；本项目只服务「每轮内容会变」的注入。
- 每轮需要扫描 transcript，并在命中旧锚点时原 inode 重写文件；超大的长会话
  会产生额外磁盘 I/O。
- 轮转备份最多占用约 3 份 transcript 的空间，长会话请关注磁盘余量。
- 锚点是一个对齐信号，不是强制执行器；内容是否合适仍需要你自己观察和调整。

## 隐私与安全

- 运行时不发起网络请求；
- 不收集遥测；
- 所有 anchor、transcript 和备份都留在本机；
- 发布 bug fixture 前，请替换人物、路径、session ID、域名、token、工具参数
  和时间戳；
- 详见 [SECURITY.md](SECURITY.md)。

## License

[MIT](LICENSE)
