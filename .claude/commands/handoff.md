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

3. **Verify the invariant index in `HANDOFF.md` matches the `###`
   headers in `ARCHITECTURE.md`.** If a new invariant was added this
   session, sync it into the index. If you can't tell, grep
   `^### ` in `ARCHITECTURE.md` and compare.

4. **Update the session number and date in the top heading.** It
   should read `# HANDOFF — End of session N (YYYY-MM-DD)`. Bump N
   by one from whatever's there now.

5. **Show me the diff before saving.** I want to see what's
   changing.

After I approve the diff, save the file. Do not propose any commits
afterward unless I ask — `HANDOFF.md` is gitignored and just needs
to exist on disk.

If you're unsure whether something belongs in HANDOFF vs.
ARCHITECTURE vs. nowhere, ask. Don't guess.
