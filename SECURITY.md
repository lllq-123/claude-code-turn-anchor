# Security and privacy

`claude-code-turn-anchor` runs locally and makes no network requests. Its main
risk comes from the data it handles: Claude Code transcripts can contain full
private conversations, file paths, source code, tool inputs, and command output.

## Before using it

- Read the source and run the test suite.
- Run `python3 install.py --dry-run` before changing settings.
- Start with a non-critical session.
- Keep enough free disk space for rotating transcript backups.
- Re-test after a Claude Code upgrade because transcript JSONL is an internal
  format.

## Local data

The hook stores rotating transcript and settings backups under:

```text
~/.claude/turn-anchor-backups/
```

The directory is created with owner-only permissions and new backup files use
mode `0600`. Your existing filesystem permissions and backup software still
apply.

Do not attach real transcripts or backups to a public issue. Build a minimal
fixture with fictional content and replace:

- names and conversation text;
- usernames, home directories, and absolute paths;
- session IDs and timestamps;
- domains, repository names, and tool arguments;
- tokens, credentials, and environment values.

## Recovery

If a transcript needs recovery, stop the corresponding Claude Code process
before replacing the file. Preserve the current file separately, then restore
the selected `.bak` to the original transcript path.

## Reporting a security issue

Please use GitHub's private vulnerability reporting for the repository when
available. Do not include a real transcript, credential, or private backup in
the report.
