# claude-code-turn-anchor

Refresh one short alignment anchor on every Claude Code prompt, then prune its
historical copies before they accumulate in context and consume tokens.

[中文说明](README.md)

> [!WARNING]
> This is an unofficial community experiment and is not affiliated with
> Anthropic. It rewrites local Claude Code session JSONL, which is not a stable
> public API. Run the tests and `--dry-run` first, and do not use your only copy
> of an important session for the first trial. The current release is tested
> with Claude Code 2.1.175.

> [!IMPORTANT]
> **2026-08 correction: this project only fits a "resume-per-turn" runtime.**
> Earlier revisions of this README omitted a key precondition. Read
> [Which runtime shape this fits](#which-runtime-shape-this-fits) before
> installing:
>
> - Pruning only edits the transcript **on disk**. A running Claude Code
>   process keeps its history **in memory and never re-reads the transcript**,
>   so in a long-lived interactive session pruning removes nothing from what
>   the model actually receives — the savings only materialize after the next
>   resume;
> - Worse, disk and memory now disagree. **On the next resume, the rebuilt
>   prompt prefix diverges at the first blanked anchor, and the entire prompt
>   cache is invalidated.** Anchors exist from turn one, so the divergence
>   point sits at the very top of history (measured: 99.6–99.8% of context
>   rewritten at cache-write rates, billed at 1.25×/2×).
>
> A "resume-per-turn" architecture — headless automation that starts a fresh
> `claude -p --resume <session-id>` process for every turn — has neither
> problem: disk is the single source of truth, pruning takes effect
> immediately, and the cache loss is only a small tail slice (measured shared
> prefix between adjacent turns ≈ 99.1%). **Do not enable this in a
> long-lived interactive terminal session.**

## The problem

In a long conversation, a few important signals can sink far back in context:

- the role and relationship already established in the conversation;
- the desired tone and response rhythm;
- the working style that matters for the current phase;
- a small constraint that generic defaults tend to override.

A Claude Code `UserPromptSubmit` hook can return `additionalContext` on every
prompt, placing a short turn-level anchor near the current token window.

Claude Code also saves injected context in the session transcript and replays it
when a session is resumed. If a hook only adds, a session with `N` turns can end
up carrying `N` copies of the same anchor.

This project keeps one fresh copy **in the on-disk transcript**:

```text
user submits a prompt
        │
        ▼
one UserPromptSubmit hook
        │
        ├─ old <TURN_ANCHOR> attachment content → []
        │    (uuid / parentUuid stay intact; disk only)
        │
        └─ return one fresh anchor as additionalContext
                    │
                    ▼
   the on-disk transcript holds one live anchor
```

**When the model actually "sees only one" depends on your runtime shape** —
the precondition earlier revisions failed to state, later established with
real API usage data. See the next section.

## Which runtime shape this fits

Claude Code runs in two typical shapes, and this project behaves very
differently in each:

| | Resume-per-turn (fits ✅) | Long-lived process (does not fit ❌) |
|---|---|---|
| Typical setup | headless automation: a fresh `claude -p --resume <sid>` process per turn | interactive terminal; long-lived `--input-format stream-json` process |
| Where history comes from | rebuilt from the JSONL on disk every turn | process memory, append-only, never re-read from disk |
| When pruning takes effect | **immediately** — the next turn rebuilds from the pruned disk | **never within the session** — the model keeps receiving every historical anchor |
| Token savings | real (formula below) | zero while the process lives |
| Prompt-cache cost | small tail slice only: pruning touches the previous turn's anchor; measured shared prefix ≈ 99.1% | **full cache loss on next resume**: divergence at the top of history; measured 99.6–99.8% rewritten |

Mechanism: a running Claude Code process keeps conversation history in memory
and always builds requests from that copy; the transcript JSONL is an
append-only record, read only when a session is resumed or rebuilt. Rewriting
the disk changes nothing about what the live process sends — but it silently
changes what the *next resume* will read. The two copies now disagree, and on
resume the prompt prefix mismatches from the first blanked anchor onward, so
the cache backend recomputes everything.

A verification trap worth naming: **counting anchors in the JSONL proves
nothing about what the model sees.** "One live anchor on disk" looks great
while the live process still ships all `N` copies in every request. To verify
real effect, look at API usage (`input_tokens` / `cache_read_input_tokens`)
or Claude Code's `/context` — not at the file.

## Why one combined hook

Anthropic documents that matching hooks for the same event run in parallel.
This project therefore does not depend on array order between a separate
pruner and injector. One `turn_anchor.py` process:

1. reads `transcript_path` from the hook payload;
2. finds only `hook_additional_context` attachments carrying its own
   `<TURN_ANCHOR>` marker;
3. changes their `content` to a true empty array, `[]`;
4. returns the current contents of `anchor.md` as fresh `additionalContext`.

It keeps each physical JSONL entry and does not modify `uuid` or `parentUuid`.
It leaves other hooks untouched and ignores the same literal marker when it
appears in normal user or assistant text.

## Token shape

**The formula below only holds in a resume-per-turn runtime** (in a
long-lived process nothing is saved while it runs; see above). For an anchor
of roughly `A` tokens:

- add-only injection can approach `N × A` anchor tokens near turn `N`;
- this project stays near `A`, with one live copy;
- across `N` turns, the rough amount of repeated input avoided is
  `A × N × (N - 1) / 2`.

Prompt caching, compaction, and Claude Code versions affect actual billing.
This estimate describes context volume, not a guaranteed invoice reduction.

The cost of enabling it in a long-lived process is just as real: in one
measured incident, a long session resumed with its early anchors blanked and
`cache_read` fell from the usual two-hundred-thousand-plus tokens to 15,493
(only the system + tools segments still hit), with the rest recomputed at
cache-write rates. After disabling pruning, the same scenario resumed with
the cache fully inherited (`cache_read` 72,197 = the previous turn's
67,906 + 4,291, zero collapse).

## Requirements

- macOS, Linux, or WSL;
- Python 3.10+, standard library only;
- a Claude Code version with hooks;
- native Windows is not supported because the implementation uses
  `fcntl.flock`.

Official references:

- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks)
- [Automate workflows with hooks](https://code.claude.com/docs/en/hooks-guide)

Keep stable project rules in `CLAUDE.md`. Use a turn anchor for a short,
important alignment signal that benefits from being refreshed near every
prompt.

## Install

```bash
git clone https://github.com/lllq-123/claude-code-turn-anchor.git
cd claude-code-turn-anchor

python3 -m unittest discover -s tests -v
python3 install.py --dry-run
python3 install.py
```

The installer shows a plan and asks for confirmation. It:

- copies the runtime to
  `~/.claude/hooks/claude-code-turn-anchor/turn_anchor.py`;
- creates an editable `anchor.md` on first install;
- appends one hook group to `hooks.UserPromptSubmit` in
  `~/.claude/settings.json`;
- preserves every existing hook and setting;
- backs up settings under `~/.claude/turn-anchor-backups/` before editing.

If `CLAUDE_CONFIG_DIR` is set, the installer follows it instead of assuming
`~/.claude`.

For a reviewed non-interactive install:

```bash
python3 install.py --yes
```

Avoid unreviewed `curl | bash` installation. Fully exit and reopen Claude Code
after installation.

## Write your anchor

Edit:

```text
~/.claude/hooks/claude-code-turn-anchor/anchor.md
```

Write only the body. The hook adds `<TURN_ANCHOR>` tags automatically.

Example:

```md
The current conversation has an established role, tone, and set of constraints.
Follow the user's latest intent while preserving that established context.
Keep the response natural; do not turn this reminder into a checklist.
```

Good anchors are usually two to five lines:

- describe the current context as facts rather than a wall of commands;
- keep only signals with a clear purpose;
- leave stable project conventions in `CLAUDE.md`;
- keep the anchor short enough that it does not become a new context burden.

Edits to `anchor.md` take effect on the next prompt without reinstalling.

## Disable or use another file

Set this before starting Claude Code from that environment:

```bash
export CLAUDE_TURN_ANCHOR_DISABLED=1
```

This disables both pruning and injection. To load another file:

```bash
export CLAUDE_TURN_ANCHOR_FILE=/absolute/path/to/my-anchor.md
```

## Backups and recovery

Before every transcript mutation, the hook writes a complete private backup
under:

```text
~/.claude/turn-anchor-backups/
```

It retains the latest three backups per session. These files can contain the
entire private conversation. Never upload them to an issue, cloud drive, or
online JSON viewer.

To restore, fully stop the corresponding Claude Code session, make another copy
of the current JSONL, then copy the selected `.bak` back to the original
transcript path.

## Uninstall

```bash
python3 uninstall.py --dry-run
python3 uninstall.py
```

The uninstaller removes only the exact command registered by this installation.
It preserves other `UserPromptSubmit` handlers and moves installed files into
`~/.claude/turn-anchor-backups/uninstalled-files.*` instead of deleting them.
Existing transcript and settings backups remain.

Restart Claude Code after uninstalling.

## Verify and develop

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile src/turn_anchor.py install.py uninstall.py
```

The tests cover marker scope, `content=[]`, line and parent-chain preservation,
malformed JSONL, idempotence, backup rotation, fail-closed behavior,
idempotent installation, third-party hook preservation, precise uninstall, and
custom anchor preservation across reinstall.

## Known limitations

- **Resume-per-turn runtimes only** (see "Which runtime shape this fits"):
  in a long-lived process pruning never takes effect, and it invalidates the
  entire prompt cache on the next resume.
- This is a careful rewrite of Claude Code's internal transcript structure, not
  an official “remove injected context” API. Re-run tests and start with a
  non-critical session after Claude Code upgrades.
- Current Claude Code skips hook attachments whose `content` is `[]` when it
  rebuilds context. An internal format change may require an update here.
- There is no official ephemeral (one-shot) injection option today; the
  feature request is
  [claude-code#40216](https://github.com/anthropics/claude-code/issues/40216).
  Also note that **`SessionStart` hook `additionalContext` is not written to
  the transcript**
  ([#11906](https://github.com/anthropics/claude-code/issues/11906)) — if your
  injected content never changes within a session, plain `SessionStart`
  injection needs none of this. This project only serves injections whose
  content changes every turn.
- The hook scans the transcript on every prompt and rewrites the same inode when
  an old anchor is found. Very large sessions add disk I/O.
- Rotating backups can use up to roughly three transcript copies per session.
- An anchor is an alignment signal, not an enforcement boundary. Observe and
  adjust its content for your own conversations.

## Privacy and security

- no runtime network requests;
- no telemetry;
- anchors, transcripts, and backups stay local;
- redact people, paths, session IDs, domains, tokens, tool arguments, and
  timestamps before publishing a bug fixture;
- see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
