---
name: handoff
description: Inspect or retry a context handoff in Codex or Claude Code, or generate one manually from the current local transcript.
---

The `cc-autohandoff` hooks generate the handoff with **gpt-6-luna** in Codex or **claude-haiku-5-5** in Claude Code, not the current working model. Do not write or replace its summary yourself and do not call compact, fork, or create a continuation chat. Never substitute another writer model.

The hook notification gives the session directory and a retry command. To retry a failed writer, run that command once. To inspect state use `python <plugin-root>/scripts/handoff.py status --session-dir <directory>`. To generate manually use `python <plugin-root>/scripts/handoff.py --platform codex generate --transcript <absolute-jsonl-path> --workspace <absolute-workspace>`; use `--platform claude` in Claude Code (macOS/Linux: `python3` if needed). Resolve the plugin root from this skill's location; it is two levels above the `handoff` directory. Quote absolute paths containing spaces. Status and retry restore the platform from the saved session metadata.

Once ready, give the user clickable absolute links to `HANDOFF.md` and `START-NEXT-SESSION.txt` from the reported directory. They open the fresh working chat manually and paste the startup text. If the writer fails, report the failure and retry path; never present raw source as a completed handoff. Stop after delivery.
