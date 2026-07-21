# Voice Commander

> `CLAUDE.md` is committed project instructions for Claude Code sessions
> working on this repo. `HANDOFF.md` (session working memory) stays
> gitignored — see below.

## Read order at session start

1. **`HANDOFF.md`** (gitignored, working memory). Always read first.
   Contains: environment, working rules, invariant index, current
   state, next priorities. Single source of truth for "where are we
   right now."

2. **`ARCHITECTURE.md`** (committed, durable invariants — the single
   source of truth for the invariant list). Do **not** read in full at
   session start. Read selectively when about to change code in a
   documented area. Procedure:
   - `grep -n "^### " ARCHITECTURE.md` to list the invariants and see
     if any touches the code area you're about to change.
   - If yes, read just that section via targeted `Read` with
     offset/limit.
   - Never read the whole file.

3. If a change touches an invariant, update `ARCHITECTURE.md`
   **immediately** (not at end-of-session). (HANDOFF no longer mirrors
   the invariant list — the `### ` headers here are the index.)

## End of session

Update `HANDOFF.md` per the protocol documented inside it:
retire-first, ≤200 lines, Current state rewrites from scratch.
`HANDOFF.md` is gitignored — update it **before** proposing any
commits so the in-memory model of "what just happened" stays current.

If `HANDOFF.md` or `ARCHITECTURE.md` is missing (fresh project or
accidentally deleted), ask Tyler before creating one from scratch.

## During the session: incremental handoff updates

CC sessions can be interrupted at any moment by `/clear`, `/compact`,
a closed terminal, a crash, or running out of tokens. None of these
give you a chance to write `HANDOFF.md` first. The discipline is
to keep `HANDOFF.md` always-current, not just end-of-session-current.

**Update `HANDOFF.md`'s Current state immediately after:**

- Any commit (Current state should reflect what's now committed vs.
  in-flight vs. uncommitted in the working tree).
- Any architectural decision worth preserving (the kind of thing
  you'd be sad to lose if a new session had to re-derive it).
- Any new invariant being added to `ARCHITECTURE.md` (sync the
  index in `HANDOFF.md` in the same edit — see read order step 3).
- A failed approach being abandoned, if the reason it failed is
  non-obvious. Future-you will retry it otherwise.
- The end of any task that fits the "one task per session" frame,
  even if Tyler wants to keep working on something else next.

**Do not update for:**

- Every tool call, every file read, every minor edit. That's noise.
- Mid-thought exploration where the conclusion isn't settled yet.
- Anything already captured by `git log` (don't duplicate commit
  messages into HANDOFF — just note "committed" and move on).

The goal: if Tyler `/clear`s right now, a fresh session reading
`HANDOFF.md` should be able to pick up without asking "wait, what
just happened?"

## When in doubt about clearing

If Tyler says "I'm about to `/clear`" or signals a context reset,
offer to update `HANDOFF.md` first if it isn't already current.
Don't assume — just ask: "Want me to update HANDOFF first?"

## Project quick reference

- Deploy: `./install.sh` copies repo source to
  `~/.local/share/voice-commander/app/`. **Never `cp` directly** —
  always go through `install.sh`.
- Service: `systemctl --user restart voice-commander.service`
- Logs: `journalctl --user -u voice-commander.service -f`
- Default config emit: `python -m core.commands --emit-defaults`
  (used by `install.sh`; single source of truth for defaults).
- Placer script lives in `vc-window-placer/`, deployed to
  `~/.local/share/kwin/scripts/vc-window-placer/`.

## Project-specific rules

- **Edit the repo; Claude runs `install.sh` to deploy — but ONLY
  after an explicit go-ahead from Tyler in that conversation.** Never
  deploy unprompted, never assume a past yes carries forward. Never
  write directly to `~/.local/share/voice-commander/app/` or
  `~/.local/share/kwin/scripts/vc-window-placer/` — all deploys go
  through `install.sh`. After deploying, byte-verify deployed files
  against the repo. Reading deployed code is fine but flag it in
  case of repo divergence.
- **Tagged releases get a source tarball backup at
  `~/Development/Backups/Voice-Commander/`.** Build with
  `git archive --format=tar.gz --prefix=voice-commander-<version>/
  -o /tmp/voice-commander-<version>.tar.gz <tag>`, then `cp` to the
  backup dir. GitHub Release attachment is primary; local is
  recovery.
- **Tyler's tests can shift state mid-investigation.** Ask whether
  config / aliases / `commands.json` changed across attempts before
  forming a hypothesis about why behavior changed.
- **`core/aliases.py` must stay Qt-free** so `python -m
  core.commands --emit-defaults` works in `install.sh` without
  PySide6 installed.
- Search past chats (via the conversation_search tool, if available)
  before re-deriving design decisions. If past chats contradict
  `HANDOFF.md`, ask Tyler.
