# claude-code-turn-anchor

Append a short alignment anchor to long Claude Code conversations at a sparse,
stable cadence.

**The current release emits on prompts 1, 16, 31, ... by default. It only
appends; it never rewrites the transcript.**

[中文](README.md)

> [!IMPORTANT]
> **Breaking correction, 2026-09-02: the old “inject every turn, then blank
> historical attachments” implementation has been removed.**
>
> Claude Fable 5.1 binds preserved thinking blocks to the exact prefix that
> produced them. Removing an anchor between a user message and the following
> assistant thinking, then resuming and replaying that thinking, invalidates
> prompt caching and may return HTTP 400 where strict binding is enforced.
>
> If you installed an older release, run the installer again. It replaces the
> runtime script while preserving your custom `anchor.md`.

## How it works

A `UserPromptSubmit` hook may return `additionalContext`. This project keeps a
per-session counter outside the transcript:

```text
prompt 1      -> append TURN_ANCHOR
prompts 2–15  -> silent
prompt 16     -> append again
prompts 17–30 -> silent
...
```

State lives under `~/.claude/turn-anchor-state/` and never enters model input.
The runtime does not read, scan, blank, or rewrite session JSONL. Conversation
history remains append-only, as required by Fable 5.1.

## Why not every turn?

Hook `additionalContext` is persisted as an attachment. Add it every turn and
copies accumulate; delete old copies and you mutate history. The default
15-prompt interval is a conservative trade-off between keeping the signal near
the current window and limiting context growth.

## Safe and unsafe history operations

- ✅ **Whole-tail rewind:** remove the affected user, attachments, thinking,
  assistant, and every later turn together. This returns to a prefix that
  existed before, so its old cache entry can still be reused.
- ⚠️ **Edit only the visible tail of the latest assistant message:** if no later
  thinking exists, only that tail branches.
- ❌ **Edit a middle message while replaying later thinking:** later blocks are
  bound to the old prefix and are incompatible with Fable 5.1.
- ❌ **Blank old hook attachments and resume:** this is the retired design.

Official references:

- [Prompting Claude Fable 5.1 — Keep the conversation history append-only](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1)
- [Claude Code prompt caching](https://code.claude.com/docs/en/prompt-caching)

## Install

Requirements: Python 3.10+ and Claude Code.

```bash
git clone https://github.com/lllq-123/claude-code-turn-anchor.git
cd claude-code-turn-anchor
python3 install.py --dry-run
python3 install.py
```

The installer adds an independent `UserPromptSubmit` hook group, installs the
runtime, creates `anchor.md` only when missing, preserves an existing custom
anchor on reinstall, and backs up settings before editing. Restart Claude Code
after installing or upgrading.

## Write your anchor

Edit:

```text
~/.claude/hooks/claude-code-turn-anchor/anchor.md
```

Write the body only; marker tags are added automatically. Keep it to roughly
two to five lines. Stable project rules still belong in `CLAUDE.md`.

## Configuration

Change the interval:

```bash
export CLAUDE_TURN_ANCHOR_INTERVAL=10
```

Temporarily disable the hook:

```bash
export CLAUDE_TURN_ANCHOR_DISABLED=1
```

Use another anchor file:

```bash
export CLAUDE_TURN_ANCHOR_FILE=/absolute/path/to/my-anchor.md
```

These variables must exist in the environment that launches Claude Code.

## Upgrade from the old release

```bash
cd claude-code-turn-anchor
git pull
python3 install.py --yes
```

Reinstalling replaces the unsafe runtime and preserves your `anchor.md`. Do not
perform more transcript surgery merely to remove old empty attachments; let
normal rewind or compaction retire them.

## Uninstall

```bash
python3 uninstall.py --dry-run
python3 uninstall.py
```

Only this project's handler is removed. Installed files are moved to the
private backup directory; unrelated hooks remain untouched.

## Verify

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile src/turn_anchor.py install.py uninstall.py
```

Tests cover prompts 1/16, independent and persistent session counters, the
disable switch, and byte-identical transcripts even when `transcript_path` is
present in the hook payload.

## Boundaries

- The counter advances on `UserPromptSubmit`, not on internal model tool calls.
- Missing session IDs or unwritable state fail closed: no anchor that prompt.
- An anchor is an alignment signal, not an enforcement or security boundary.
- Anchors and counters stay local; the hook makes no network requests.

## License

MIT
