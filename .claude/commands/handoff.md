---
description: Update HANDOFF.md to reflect current session state. Run before /clear, /compact, or ending a session.
---

Update `HANDOFF.md` so it accurately reflects the current state of the
repo and this session's work. Follow the protocol documented in the
"How to update this handoff" section of `HANDOFF.md` itself. Specifically:

1. **Rewrite the Current state section from scratch.** Two questions
   only: what's in-flight (uncommitted in the working tree), and
   what's committed-but-not-pushed? If everything is committed and
   pushed and there's nothing in flight, the section can be a single
   line: "Working tree clean."

2. **Retire stale content elsewhere before adding new content.**
   Prune resolved future-work notes, completed next-priorities,
   anything that's no longer accurate. Growth fills freed space,
   never stacks. The hard ceiling is 200 lines; soft target 150.

3. **Do NOT touch or re-add an invariant index in `HANDOFF.md`.**
   The mirror was dropped end of s19 — `ARCHITECTURE.md` is the single
   source of truth and its `^### ` headers ARE the index. If a new
   invariant emerged this session, it belongs in `ARCHITECTURE.md`
   only (and should already be there per CLAUDE.md's
   update-immediately rule — if it isn't, add it there now, not here).

4. **Update the session number and date in the top heading.** It
   should read `# HANDOFF — End of session N (YYYY-MM-DD)`. Bump N
   by one from whatever's there now.

5. **Write the file directly — no diff, no approval step.** Standing
   autonomy rule: HANDOFF.md updates never wait on review. Summarize
   what changed in a few bullets after saving.

Do not propose any commits afterward unless I ask — `HANDOFF.md` is
gitignored and just needs to exist on disk.

If you're unsure whether something belongs in HANDOFF vs.
ARCHITECTURE vs. nowhere, ask. Don't guess.
