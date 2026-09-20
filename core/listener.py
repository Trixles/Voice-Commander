"""
core/listener.py
================
Audio capture loop and state machine.

Speech recognition happens behind the seam in core/recognizer.py: the
entry point (voice_commander.py) passes a recognizer_factory, and this
loop consumes RecognizerEvents -- it never touches an engine API. Heavy
engine state (e.g. the whisper-server wiring) is owned by the entry point
and captured in the factory; mic restarts recreate only the pw-record
subprocess and a fresh recognizer from the factory.

State machine:
  SLEEPING   -> LISTENING    wake word heard (acknowledged immediately off a
                             streaming partial; or at finalize as a fallback)
  LISTENING  -> SLEEPING     command matched OR command window expired
  LISTENING  -> CONFIRMING   dangerous command matched, awaiting "confirm"
  CONFIRMING -> prev state   cancelled (voice or timeout); -> SLEEPING on confirm
  SLEEPING   -> OPEN_MIC     wake word + open mic phrase heard
  OPEN_MIC   -> SLEEPING     close mic phrase heard
  (LISTENING and OPEN_MIC both process commands; only entry condition differs)

On cancel or timeout, CONFIRMING restores the state that was active before
the confirmation was triggered (SLEEPING, LISTENING, or OPEN_MIC).

Open mic and confirm/cancel are built-in and not user-configurable.
"""

import queue
import subprocess
import threading
import time

from core.context import Context, State
from core.log_buffer import log
from core.notify import notify as _notify
from core.recognizer import SAMPLE_RATE
from core.run import get_default_source
from core.wake import BAKED_IN_WAKE_WORDS, WakeWordDetector
from core.actions import apps
import core.commands as commands
import core.media_control as media_control
import core.overrides as overrides


# States that make up the transient "wake window": the app is awake and
# actively listening for (or confirming) a command. OPEN_MIC is deliberately
# NOT here -- auto-pause is scoped to the transient wake only, so a false wake
# briefly pauses music, but toggling always-on open mic never touches media.
_WAKE_WINDOW_STATES = frozenset({State.LISTENING, State.CONFIRMING})


def _auto_pause_on_wake(context: Context, gui_env: dict) -> None:
    """Entering the wake window from SLEEPING: pause whatever is playing and
    remember it. Opens a fresh media-touched window (reset the flag) so a
    command in THIS window can veto the resume."""
    # Consume the wake gate's snapshot FIRST (clear even when the toggle is
    # off, so a stale snapshot can never leak into a later window). A wake
    # that went through the gate already paid for the playerctl query; reuse
    # it rather than shelling out a second time.
    snapshot = context.pending_media_snapshot
    context.update(pending_media_snapshot=None)
    if not commands.get_auto_pause_media():
        return
    context.update(media_touched=False)
    if snapshot is not None:
        playing = [name for (name, status, _url, _title) in snapshot
                   if status == "Playing"]
    else:
        playing = media_control.list_playing_players(gui_env)
    if playing:
        media_control.pause_players(playing, gui_env)
        context.update(auto_paused_players=list(playing))
        print(f"[listener] Auto-paused media on wake: {playing}")
    else:
        context.update(auto_paused_players=[])


def _auto_resume_after_wake(context: Context, gui_env: dict) -> None:
    """Leaving the wake window: resume exactly the players we paused, unless a
    command touched media this window (media_touched) -- in which case the user
    is managing playback and we leave it alone. We always resume what WE paused
    regardless of the toggle's current state: the toggle gates whether we START
    pausing, not whether we honor an in-flight pause."""
    paused = context.auto_paused_players
    if not paused:
        return
    # Clear first so a mid-resume exception can't strand a stale snapshot that
    # would double-resume on the next window.
    context.update(auto_paused_players=[])
    # Predicate 1 (intent): a command deliberately set playback this window
    # (pause/play, or a knowingly-autoplaying action like celery_man). Honor it.
    if context.media_touched:
        print(f"[listener] Auto-resume suppressed (media touched this window): {paused}")
        return
    # Predicate 2 (reality): don't stack. If media we DIDN'T pause is playing
    # now -- e.g. a URL command opened an autoplaying page -- resuming the
    # pre-wake audio on top of it would double up. Ask what's actually playing
    # rather than guessing from the command; anything not in our snapshot is new.
    now_playing = media_control.list_playing_players(gui_env)
    new_media = [p for p in now_playing if p not in set(paused)]
    if new_media:
        print(f"[listener] Auto-resume suppressed (new media playing: {new_media})")
        return
    media_control.resume_players(paused, gui_env)
    print(f"[listener] Auto-resumed media: {paused}")


def _wake_is_celery_echo(text: str, detector: WakeWordDetector,
                         context: Context, gui_env: dict) -> bool:
    """True -> this wake is the Celery Man video saying "computer"; stay
    asleep. The clip's audio uses the baked-in wake word beyond its launch
    phrase, so without a gate every viewing fires a spurious wake window
    (and auto-pause interrupts the video mid-line).

    Same philosophy as the command echo guard in core/actions/apps.py: ask
    reality (is the video actually Playing right now?), never a timer, and
    fail OPEN -- a broken probe must never make VC deaf. Gates ONLY wakes
    carried exclusively by baked-in wake words; a custom word ("hey dude")
    always wakes, without even paying for the probe. A Paused video makes
    no sound, so that "computer" was the user -- wake normally.

    Side channel: when the wake PROCEEDS and the probe ran, the snapshot is
    stashed on context for _auto_pause_on_wake to consume -- one playerctl
    query per wake, shared, not two. The stash is cleared on every other
    path so nothing stale survives."""
    context.update(pending_media_snapshot=None)
    if any(w not in BAKED_IN_WAKE_WORDS for w in detector.matched(text)):
        return False
    rows = media_control.metadata_snapshot(gui_env)
    if rows is None:  # probe failed -> fail open
        return False
    if any(apps._row_is_celery_echo(name, status, url, title)
           for (name, status, url, title) in rows):
        return True
    context.update(pending_media_snapshot=rows)
    return False


def _notify_general(*args, **kwargs) -> None:
    """Desktop notification for GENERAL feedback (no-match, mic toggles,
    "Listening...", etc.). Suppressed when the user turns notifications off on
    the Options tab. The shutdown/restart/logout CONFIRMATION prompts call
    ``_notify`` directly so they ALWAYS fire regardless of this toggle (user
    protection -- never let someone confirm a destructive action blind)."""
    if commands.notifications_enabled():
        _notify(*args, **kwargs)


# -- Built-in phrase sets -----------------------------------------------------
# Open/close mic phrase defaults live in core/commands.py alongside the other
# shipped defaults (see DEFAULT_OPEN_MIC_PHRASES / DEFAULT_CLOSE_MIC_PHRASES).
# Confirm/cancel are hardcoded here -- not user-configurable.
CONFIRM_PHRASES   = {"confirm", "yes", "do it"}
CANCEL_PHRASES    = {"cancel", "never mind", "abort"}

CONFIRM_WINDOW = 5   # seconds to say "confirm" before the pending command expires

# Confirmation notification body -- lists all valid confirm/cancel phrases.
_CONFIRM_BODY = (
    "Say 'confirm', 'yes', or 'do it' to proceed.\n"
    "Say 'cancel', 'never mind', or 'abort' to cancel."
)

# -- Mic change polling -------------------------------------------------------
CHECK_MIC_EVERY = 50

# -- Hallucination filter -----------------------------------------------------
# Speech engines hallucinate short stopwords from background noise. Any of these as the
# *entire* recognized text is dropped before logging or matching. Multi-word
# results that happen to start or contain these words are unaffected.
_HALLUCINATION_STOPWORDS = {"the", "a", "an", "uh", "um", "huh", "oh", "and", "i", "to"}


# -- Helpers ------------------------------------------------------------------

def _open_pw_record(source: str) -> subprocess.Popen:
    # Not routed through core.run: pw-record is a long-running streaming
    # process whose stdout we read from a thread and which we .kill() on
    # mic toggle. Neither run_bg nor run_capture models this lifecycle.
    return subprocess.Popen(
        ["pw-record", f"--target={source}", "--format=s16", f"--rate={SAMPLE_RATE}", "--channels=1", "-"],
        stdout=subprocess.PIPE,
    )


def _start_reader(proc: subprocess.Popen) -> queue.Queue:
    """
    Read pw-record stdout in a daemon thread, pushing 4000-byte chunks into a
    queue. When pw-record dies the thread pushes None as a sentinel.
    PipeWire keeps the pipe open after device removal, so the main loop uses
    queue.get(timeout=...) to detect stalls instead of relying on EOF.
    """
    q = queue.Queue()
    def _reader():
        while True:
            data = proc.stdout.read(4000)
            q.put(data if data else None)
            if not data:
                break
    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    return q


def _confirm_title(cmd: dict) -> str:
    """
    Build the title for a confirmation-required notification.
    Uses display_name if present, otherwise prettifies the action key.
    e.g. action="shutdown" -> "Shutdown command received."
    """
    display = cmd.get("display_name")
    if not display:
        action = cmd.get("action", cmd.get("name", "Command"))
        display = action.replace("_", " ").title()
    return f"{display} command received."


# -- Mic readiness check ------------------------------------------------------

def wait_for_mic_ready(source: str, timeout: int = 10) -> bool:
    """
    Check that pw-record can open the source and produce data.
    A mic that opens and streams data is considered ready, even if silent.
    Silence != broken; we don't require audio above threshold here.
    """
    print(f"[listener] Checking mic '{source}'...")
    proc = _open_pw_record(source)
    start = time.time()
    try:
        while time.time() - start < timeout:
            data = proc.stdout.read(4000)
            if not data:
                break
            print(f"[listener] Mic is live.")
            proc.kill()
            return True
        print(f"[listener] Mic produced no data within {timeout}s.")
        proc.kill()
        return False
    except Exception as e:
        print(f"[listener] Error during mic check: {e}")
        try:
            proc.kill()
        except Exception:
            pass
        return False


# -- Built-in phrase checks ---------------------------------------------------

def _normalize(text: str) -> str:
    """Hallucination filter for transcriptions, applied before matching.

    Currently a single rule: strip a leading 'the ', which some mics with
    onboard AGC (notably the ReSpeaker XVF3800) hallucinate from background
    noise. Consequence: command phrases cannot start with 'the' -- they
    will be silently stripped before matching.

    User-configurable mishearing rewrites live in core/overrides.py and are
    applied separately by the listener loop after this function runs. The
    split is intentional: hallucination filters are plumbing for artifacts
    the user never said; overrides are 'the engine heard X, user meant Y'.
    """
    # Leading "and ": per-segment chains deliver "...and open kate..." as
    # its own segment; the connective is chain glue, not command text.
    return text.removeprefix("and ").removeprefix("the ")


def _is_open_mic_command(text: str) -> bool:
    return any(phrase in text for phrase in commands.get_open_mic_phrases())


def _is_close_mic_command(text: str) -> bool:
    return any(phrase in text for phrase in commands.get_close_mic_phrases())


def _is_confirm(text: str) -> bool:
    return any(phrase in text for phrase in CONFIRM_PHRASES)


def _is_cancel(text: str) -> bool:
    return any(phrase in text for phrase in CANCEL_PHRASES)


# -- Core listener loop -------------------------------------------------------

def run_listener(
    source: str,
    recognizer_factory,
    detector: WakeWordDetector,
    context: Context,
    gui_env: dict,
    state_queue=None,
    command_queue=None,
) -> None:
    """
    Run the main audio capture + recognition loop on `source`.
    Returns when the mic changes, goes silent, or an unrecoverable error occurs.
    The caller (voice_commander.py) handles restart.

    recognizer_factory -- zero-arg callable returning a fresh seam-conformant
                          recognizer (see core/recognizer.py). Called once per
                          run_listener invocation, so a mic restart gets a
                          fresh recognizer while heavy engine state stays
                          alive inside the factory's closure.
    state_queue   -- if provided, push State values whenever context.state changes
    command_queue -- if provided, check each loop for 'toggle_open_mic' / 'quit'
    """
    def set_state(new_state):
        # Auto-pause hook: the single transition chokepoint, so pause/resume
        # tracks the wake window no matter which of the many wake/sleep paths
        # fired. Guarded to the SLEEPING<->wake-window boundary so it ignores
        # in-window churn (LISTENING<->CONFIRMING) and the OPEN_MIC paths.
        prev_state = context.state
        if prev_state == State.SLEEPING and new_state in _WAKE_WINDOW_STATES:
            _auto_pause_on_wake(context, gui_env)
        elif prev_state in _WAKE_WINDOW_STATES and new_state not in _WAKE_WINDOW_STATES:
            _auto_resume_after_wake(context, gui_env)
        context.state = new_state
        if state_queue is not None:
            state_queue.put(new_state)

    def enter_confirming(origin: State, now: float) -> None:
        """Shared transition into CONFIRMING from SLEEPING / LISTENING /
        OPEN_MIC: remember the state to restore on cancel/timeout, start the
        confirm window, and fire the (always-on) confirm prompt + log line."""
        nonlocal pre_confirm_state, confirm_start
        pre_confirm_state = origin
        confirm_start = now
        set_state(State.CONFIRMING)
        title = _confirm_title(context.pending_confirm)
        _notify(title, _CONFIRM_BODY, timeout_ms=CONFIRM_WINDOW * 1000, gui_env=gui_env)
        log(title)
        print(f"[listener] -> CONFIRMING ({context.pending_confirm.get('name')})")

    rec  = recognizer_factory()
    proc = _open_pw_record(source)
    audio_queue = _start_reader(proc)

    # Batch backends (whisper) deliver each VAD segment of a chain as its
    # OWN final. For those, a successful match must keep LISTENING so the
    # rest of the chain can land -- the inactivity window is the chain's
    # lifetime. Streaming backends (whole chain in one final) keep the
    # classic match -> sleep behavior; the seam default is that shape.
    per_segment = getattr(rec, "per_segment_finals", False)

    print(f"[listener] Listening on: {source}")

    # Transcription-health warning. A dead whisper-server means no wake and
    # no commands with nothing in the UI to say why -- an outage looks like
    # a stone-dead app with no feedback (lived it on 2026-08-17). The
    # recognizer reports failures as "error" events; warn LOUDLY once per
    # outage and re-arm on recovery.
    transcription_down: bool = False

    command_window_start: float = 0.0
    command_window: int = commands.get_command_window()
    confirm_start: float = 0.0
    pre_confirm_state: State = State.SLEEPING  # state to restore on cancel/timeout
    mic_check_counter: int = 0
    matched_since_wake: bool = False  # gates the "No match" toast at expiry

    while True:
        try:
            data = audio_queue.get(timeout=3.0)
        except queue.Empty:
            print("[listener] No audio data within 3s, mic likely dead.")
            if state_queue is not None:
                state_queue.put(State.ERROR)
            proc.kill()
            return

        if data is None:
            print("[listener] pw-record closed unexpectedly, restarting.")
            if state_queue is not None:
                state_queue.put(State.ERROR)
            break

        # -- Timeout checks ---------------------------------------------------
        now = time.time()

        if context.state == State.LISTENING:
            if now - command_window_start > command_window:
                if matched_since_wake:
                    # A per-segment chain that already fired >=1 command just
                    # ran out of follow-ups. That's success, not a miss --
                    # no toast.
                    print("[listener] Chain window closed, back to sleep.")
                    set_state(State.SLEEPING)
                else:
                    print("[listener] Command window expired, back to sleep.")
                    _notify_general("No match", gui_env=gui_env)
                    log("No match (window expired)")
                    set_state(State.SLEEPING)

        elif context.state == State.CONFIRMING:
            if now - confirm_start > CONFIRM_WINDOW:
                print(f"[listener] Confirmation window expired, restoring {pre_confirm_state.value}.")
                _notify("Cancelled", "Confirmation timed out.", gui_env=gui_env)
                log("Confirmation timed out")
                context.pending_confirm = None
                context.pending_args    = None
                set_state(pre_confirm_state)

        # -- Mic change check -------------------------------------------------
        mic_check_counter += 1
        if mic_check_counter >= CHECK_MIC_EVERY:
            mic_check_counter = 0
            new_source = get_default_source()
            if new_source != source:
                print(f"[listener] Source changed to '{new_source}', restarting.")
                if state_queue is not None:
                    state_queue.put(State.ERROR)
                proc.kill()
                return

        # -- Command queue ----------------------------------------------------
        if command_queue is not None:
            try:
                while True:
                    cmd = command_queue.get_nowait()
                    if cmd == "toggle_open_mic":
                        if context.state == State.OPEN_MIC:
                            set_state(State.SLEEPING)
                            _notify_general("Open mic disabled", "Waiting for wake word.", gui_env=gui_env)
                            print("[listener] Open mic toggled off via tray.")
                            log("Open mic disabled")
                        elif context.state == State.SLEEPING:
                            set_state(State.OPEN_MIC)
                            _notify_general("Open mic enabled", "Say 'close mic' to return to normal.", gui_env=gui_env)
                            print("[listener] Open mic toggled on via tray.")
                            log("Open mic enabled")
                    elif cmd == "quit":
                        print("[listener] Quit command received, stopping.")
                        proc.kill()
                        return
            except queue.Empty:
                pass

        event = rec.feed(data)
        if event is None:
            # Backend has nothing to report for this chunk (e.g. a batch
            # engine still buffering).
            continue

        if event.kind == "error":
            # Transcription failed (whisper-server down/unreachable). Warn
            # LOUDLY and ONCE per outage -- bare _notify so the Options-tab
            # toggle can't silence "your app can't hear you". Naming the
            # real cause is the point: an outage must never present as a
            # dead app with a healthy tray icon.
            if not transcription_down:
                transcription_down = True
                print(f"[listener] Transcription unavailable: {event.text}")
                _notify("Transcription unavailable",
                        "whisper-server isn't responding -- check the journal.",
                        gui_env=gui_env)
                log("Transcription unavailable")
            continue

        if event.kind == "speech":
            # Voice activity from a backend with no streaming partials
            # (Whisper). No text to wake on or match yet -- the only job is
            # keeping the inactivity window alive while the user talks,
            # same as a text-bearing partial does below.
            if context.state == State.LISTENING:
                command_window_start = now
            continue

        if event.kind == "partial":
            # Mid-utterance: the engine has no final text yet, but partials
            # stream live. Two jobs here, both so feedback/timing track
            # *speech* rather than the end-of-utterance endpoint:
            #
            #  - SLEEPING: the moment a partial contains the wake word,
            #    acknowledge immediately -- fire "Listening...", light the
            #    tray/LED via set_state(LISTENING), and start the command
            #    window -- instead of waiting for the utterance to finalize.
            #    Once we leave SLEEPING this branch can't re-fire on later
            #    partials of the same utterance, so no guard flag is needed.
            #    The wake-bearing finalized text is then handled by the
            #    LISTENING branch (the matcher tolerates a wake-word prefix;
            #    a wake-ONLY utterance is swallowed there, see is_wake_only).
            #  - LISTENING: keep the command window alive while the user is
            #    still talking, so a long phrase (e.g. a multi-segment chain)
            #    can't fall asleep before it finishes. This makes
            #    command_window an INACTIVITY timer (silence since you stopped
            #    talking), not wall-clock since the wake word.
            #
            # OPEN_MIC has no window; CONFIRMING is unaffected.
            partial = event.text
            if context.state == State.SLEEPING:
                if partial and detector.check(partial):
                    if _wake_is_celery_echo(partial, detector, context, gui_env):
                        print("[listener] Wake suppressed (Celery Man audio).")
                        log("Wake suppressed (Celery Man playing)")
                        continue
                    command_window = commands.get_command_window()
                    matched_since_wake = False
                    print("[listener] Wake word detected (partial).")
                    log("Wake word detected")
                    _notify_general("Listening...", timeout_ms=command_window * 1000, gui_env=gui_env)
                    set_state(State.LISTENING)
                    command_window_start = now
                    print("[listener] -> LISTENING (early, off partial)")
            elif context.state == State.LISTENING:
                if partial:
                    command_window_start = now
            continue

        # Final: end-of-utterance transcription. The seam guarantees
        # stripped lowercase text, so no per-engine cleanup here.
        text = event.text

        if not text:
            continue

        if transcription_down:
            # A final arrived -> whisper answered -> the outage is over.
            # Re-arm so the NEXT outage warns again.
            transcription_down = False
            print("[listener] Transcription restored.")
            log("Transcription restored")

        # Hallucination filter: drop bare-stopword results from background noise.
        if text in _HALLUCINATION_STOPWORDS:
            continue

        text = _normalize(text)
        text = overrides.apply_overrides(text, commands.get_overrides())
        print(f"[listener] Heard ({context.state.value}): {text}")
        log(f"[{context.state.value}]  {text}")

        # -- State machine ----------------------------------------------------

        if context.state == State.SLEEPING:
            if not detector.check(text):
                continue

            if _wake_is_celery_echo(text, detector, context, gui_env):
                print("[listener] Wake suppressed (Celery Man audio).")
                log("Wake suppressed (Celery Man playing)")
                continue

            print("[listener] Wake word detected.")
            log("Wake word detected")
            _notify_general("Listening...", timeout_ms=command_window * 1000, gui_env=gui_env)
            if commands.try_match(text, gui_env, context):
                if context.pending_confirm:
                    enter_confirming(State.SLEEPING, now)
                elif per_segment:
                    # Wake+command in one segment; the chain may continue
                    # after a pause -- keep the window open for it.
                    matched_since_wake = True
                    set_state(State.LISTENING)
                    command_window_start = now
                    command_window = commands.get_command_window()
                    print("[listener] Command matched in wake utterance, window open for chain.")
                else:
                    print("[listener] Command matched in wake utterance, back to sleep.")
                    set_state(State.SLEEPING)
            else:
                matched_since_wake = False
                set_state(State.LISTENING)
                command_window_start = now
                command_window = commands.get_command_window()
                print("[listener] -> LISTENING")

        elif context.state == State.LISTENING:
            # We may have entered LISTENING early off a partial wake. If this
            # finalized utterance is nothing but the wake word ("computer",
            # then silence), swallow it silently and keep listening for the
            # real command -- do NOT toast "No match". Refresh the window so
            # the user gets the full command window from this acknowledgement.
            if detector.is_wake_only(text):
                # The wake word, transcribed -- keep listening for the command.
                print("[listener] Wake-only utterance, still listening.")
                command_window_start = now
                continue

            if _is_open_mic_command(text):
                set_state(State.OPEN_MIC)
                _notify_general("Open mic enabled", "Say 'close mic' to return to normal.", gui_env=gui_env)
                print("[listener] -> OPEN_MIC")
                log("Open mic enabled")
                continue

            if commands.try_match(text, gui_env, context):
                if context.pending_confirm:
                    enter_confirming(State.LISTENING, now)
                elif per_segment:
                    # Per-segment chain: this final was one installment.
                    # Refresh the window and keep LISTENING for the next
                    # segment; expiry (or a total miss) ends the chain.
                    matched_since_wake = True
                    command_window_start = now
                    print("[listener] Command matched, window open for chain.")
                else:
                    print("[listener] Command matched, back to sleep.")
                    set_state(State.SLEEPING)
            else:
                # Total miss -- nothing in the utterance matched (a partial
                # chain that matched >=1 segment returns True above and never
                # reaches here). One-shot: toast once and go straight back to
                # sleep instead of lingering in LISTENING, which would let the
                # command-window-expiry path fire a SECOND "No match". Re-waking
                # is instant now (wake acknowledges off a partial).
                # Show WHAT was heard, same as the per-segment chain toast in
                # commands._notify_nomatch. Without it the user only learns
                # that something failed, not which mishearing to write an
                # override for -- and the two paths read inconsistently.
                print("[listener] No match, back to sleep.")
                _notify_general("No match", f"'{text}'", gui_env=gui_env)
                log(f"No match: '{text}'")
                set_state(State.SLEEPING)

        elif context.state == State.CONFIRMING:
            if _is_cancel(text):
                print(f"[listener] Cancelled, restoring {pre_confirm_state.value}.")
                _notify("Cancelled", gui_env=gui_env)
                log("Cancelled")
                context.pending_confirm = None
                context.pending_args    = None
                set_state(pre_confirm_state)
            elif _is_confirm(text):
                print("[listener] Confirmed -- executing.")
                log("Confirmed")
                commands.dispatch_confirmed(gui_env, context)
                set_state(State.SLEEPING)
            else:
                print("[listener] Waiting for confirm/cancel...")

        elif context.state == State.OPEN_MIC:
            if _is_close_mic_command(text):
                set_state(State.SLEEPING)
                _notify_general("Open mic disabled", "Waiting for wake word.", gui_env=gui_env)
                print("[listener] -> SLEEPING")
                log("Open mic disabled")
                continue

            if commands.try_match(text, gui_env, context):
                if context.pending_confirm:
                    enter_confirming(State.OPEN_MIC, now)
                else:
                    print("[listener] Command matched in open mic, staying in OPEN_MIC.")
                    set_state(State.OPEN_MIC)
