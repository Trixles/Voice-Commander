"""
core/listener.py
================
Audio capture loop and state machine.

Speech recognition happens behind the seam in core/recognizer.py: the
entry point (voice_commander.py) passes a recognizer_factory, and this
loop consumes RecognizerEvents -- it never touches an engine API. Heavy
engine state (e.g. the Vosk model) is owned by the entry point and
captured in the factory; mic restarts recreate only the pw-record
subprocess and a fresh recognizer from the factory.

State machine:
  SLEEPING   -> LISTENING    wake word heard (acknowledged immediately off a
                             Vosk partial; or at finalize as a fallback)
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
from datetime import datetime

from core.context import Context, State
from core.log_buffer import LOG_BUFFER
from core.notify import notify as _notify
from core.recognizer import SAMPLE_RATE
from core.run import get_default_source
from core.wake import WakeWordDetector
import core.commands as commands
import core.overrides as overrides


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
NOTIFY_DURATION_MS = 2000  # standard duration for all non-transient notifications

# Confirmation notification body -- lists all valid confirm/cancel phrases.
_CONFIRM_BODY = (
    "Say 'confirm', 'yes', or 'do it' to proceed.\n"
    "Say 'cancel', 'never mind', or 'abort' to cancel."
)

# -- Mic change polling -------------------------------------------------------
CHECK_MIC_EVERY = 50

# -- Hallucination filter -----------------------------------------------------
# Vosk hallucinates short stopwords from background noise. Any of these as the
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
    """Hallucination filter for Vosk transcriptions, applied before matching.

    Currently a single rule: strip a leading 'the ', which some mics with
    onboard AGC (notably the ReSpeaker XVF3800) hallucinate from background
    noise. Consequence: command phrases cannot start with 'the' -- they
    will be silently stripped before matching.

    User-configurable mishearing rewrites live in core/overrides.py and are
    applied separately by the listener loop after this function runs. The
    split is intentional: hallucination filters are plumbing for artifacts
    the user never said; overrides are 'Vosk heard X, user meant Y'.
    """
    return text.removeprefix("the ")


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
                          fresh recognizer while heavy engine state (e.g. the
                          Vosk model) stays alive inside the factory's closure.
    state_queue   -- if provided, push State values whenever context.state changes
    command_queue -- if provided, check each loop for 'toggle_open_mic' / 'quit'
    """
    def set_state(new_state):
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
        LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  {title}")
        print(f"[listener] -> CONFIRMING ({context.pending_confirm.get('name')})")

    rec  = recognizer_factory()
    proc = _open_pw_record(source)
    audio_queue = _start_reader(proc)

    print(f"[listener] Listening on: {source}")

    command_window_start: float = 0.0
    command_window: int = commands.get_command_window()
    confirm_start: float = 0.0
    pre_confirm_state: State = State.SLEEPING  # state to restore on cancel/timeout
    mic_check_counter: int = 0

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
                print("[listener] Command window expired, back to sleep.")
                _notify_general("No match", timeout_ms=NOTIFY_DURATION_MS, gui_env=gui_env)
                LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  No match (window expired)")
                set_state(State.SLEEPING)

        elif context.state == State.CONFIRMING:
            if now - confirm_start > CONFIRM_WINDOW:
                print(f"[listener] Confirmation window expired, restoring {pre_confirm_state.value}.")
                _notify("Cancelled", "Confirmation timed out.", gui_env=gui_env)
                LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Confirmation timed out")
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
                            LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Open mic disabled")
                        elif context.state == State.SLEEPING:
                            set_state(State.OPEN_MIC)
                            _notify_general("Open mic enabled", "Say 'close mic' to return to normal.", gui_env=gui_env)
                            print("[listener] Open mic toggled on via tray.")
                            LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Open mic enabled")
                    elif cmd == "quit":
                        print("[listener] Quit command received, stopping.")
                        proc.kill()
                        return
            except queue.Empty:
                pass

        event = rec.feed(data)
        if event is None:
            # Backend has nothing to report for this chunk (e.g. a batch
            # engine still buffering). Vosk never does this; Whisper will.
            continue

        if event.kind == "partial":
            # Mid-utterance: the engine has no final text yet, but partials
            # stream live. Two jobs here, both so feedback/timing track
            # *speech* rather than the end-of-utterance endpoint (~1.5s of
            # silence for Vosk):
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
                    command_window = commands.get_command_window()
                    print("[listener] Wake word detected (partial).")
                    LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Wake word detected")
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

        # Hallucination filter: drop bare-stopword results from background noise.
        if text in _HALLUCINATION_STOPWORDS:
            continue

        text = _normalize(text)
        text = overrides.apply_overrides(text, commands.get_overrides())
        print(f"[listener] Heard ({context.state.value}): {text}")
        LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  [{context.state.value}]  {text}")

        # -- State machine ----------------------------------------------------

        if context.state == State.SLEEPING:
            if not detector.check(text):
                continue

            print("[listener] Wake word detected.")
            LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Wake word detected")
            _notify_general("Listening...", timeout_ms=command_window * 1000, gui_env=gui_env)
            if commands.try_match(text, gui_env, context):
                if context.pending_confirm:
                    enter_confirming(State.SLEEPING, now)
                else:
                    print("[listener] Command matched in wake utterance, back to sleep.")
                    set_state(State.SLEEPING)
            else:
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
                print("[listener] Wake-only utterance, still listening.")
                command_window_start = now
                continue

            if _is_open_mic_command(text):
                set_state(State.OPEN_MIC)
                _notify_general("Open mic enabled", "Say 'close mic' to return to normal.", gui_env=gui_env)
                print("[listener] -> OPEN_MIC")
                LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Open mic enabled")
                continue

            if commands.try_match(text, gui_env, context):
                if context.pending_confirm:
                    enter_confirming(State.LISTENING, now)
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
                print("[listener] No match, back to sleep.")
                _notify_general("No match", timeout_ms=NOTIFY_DURATION_MS, gui_env=gui_env)
                LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  No match")
                set_state(State.SLEEPING)

        elif context.state == State.CONFIRMING:
            if _is_cancel(text):
                print(f"[listener] Cancelled, restoring {pre_confirm_state.value}.")
                _notify("Cancelled", gui_env=gui_env)
                LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Cancelled")
                context.pending_confirm = None
                context.pending_args    = None
                set_state(pre_confirm_state)
            elif _is_confirm(text):
                print("[listener] Confirmed -- executing.")
                LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Confirmed")
                commands.dispatch_confirmed(gui_env, context)
                set_state(State.SLEEPING)
            else:
                print("[listener] Waiting for confirm/cancel...")

        elif context.state == State.OPEN_MIC:
            if _is_close_mic_command(text):
                set_state(State.SLEEPING)
                _notify_general("Open mic disabled", "Waiting for wake word.", gui_env=gui_env)
                print("[listener] -> SLEEPING")
                LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Open mic disabled")
                continue

            if commands.try_match(text, gui_env, context):
                if context.pending_confirm:
                    enter_confirming(State.OPEN_MIC, now)
                else:
                    print("[listener] Command matched in open mic, staying in OPEN_MIC.")
                    set_state(State.OPEN_MIC)
