#!/usr/bin/env python3
"""say: speak text aloud with the Kokoro-82M voice model (local, cross-platform text-to-speech).

  say "The test is finished."            speak with the current voice (waits until done)
  echo "some text" | say                 speak text from stdin
  say -v bella "Hello"                   one-off voice
  say --set-voice michael                change the saved voice (add text to hear it)
  say --voices                           list every voice; the current one is marked
  say --bg "Starting now."               return immediately, speak in the background
  say --stop                             stop speaking and drop anything queued
  say -o out.wav "text"                  write a WAV file instead of playing
  say --status                           show the saved voice, speed and volume

Voices can be named in full (af_bella) or by name alone (bella). Speech starts while later
sentences are still being generated. On Linux with pactl, a newly appearing microphone
recording stream stops playback. Other platforms support playback without microphone detection.
"""
import argparse
import shutil
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = HERE / "models" / "kokoro-v1.0.onnx"
VOICES = HERE / "models" / "voices-v1.0.bin"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "say" / "config.json"
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "say"
LOCK = STATE / "queue.lock"
PIDFILE = STATE / "speaking.pid"
STOPFILE = STATE / "stop"
TICKETS = STATE / "tickets"
SAMPLE_RATE = 24000
DEFAULTS = {"voice": "af_heart", "speed": 1.0, "volume": 1.0}

# voice prefix -> (description, espeak language used for pronunciation)
GROUPS = {
    "af": ("American English, female", "en-us"), "am": ("American English, male", "en-us"),
    "bf": ("British English, female", "en-gb"), "bm": ("British English, male", "en-gb"),
    "ef": ("Spanish, female", "es"), "em": ("Spanish, male", "es"),
    "ff": ("French, female", "fr-fr"),
    "hf": ("Hindi, female", "hi"), "hm": ("Hindi, male", "hi"),
    "if": ("Italian, female", "it"), "im": ("Italian, male", "it"),
    "jf": ("Japanese, female", "ja"), "jm": ("Japanese, male", "ja"),
    "pf": ("Portuguese, female", "pt-br"), "pm": ("Portuguese, male", "pt-br"),
    "zf": ("Chinese, female", "cmn"), "zm": ("Chinese, male", "cmn"),
}
PREFERENCE = ["af", "am", "bf", "bm"]  # when a bare name exists in several languages


def queue_lock(handle, unlock=False):
    """Use OS file locks; the open handle releases the lock on process exit."""
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        if unlock:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX)


def pid_alive(pid):
    try:
        number = int(pid)
        if number <= 0:
            return False
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, number)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            finally:
                kernel.CloseHandle(handle)
        os.kill(number, 0)
        return True
    except PermissionError:
        return True
    except (ValueError, OSError):
        return False


class PortablePlayer:
    """Pipe-compatible blocking PCM sink using PortAudio (sounddevice)."""
    def __init__(self, volume):
        import sounddevice as sd
        self.stream = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16")
        self.stream.start()
        self.volume = volume
        self.stdin = self
        self.closed = False

    def write(self, data):
        import numpy as np
        if self.closed:
            raise BrokenPipeError("Playback stopped")
        if self.volume != 1.0:
            data = (np.frombuffer(data, dtype="<i2").astype("float32") * self.volume).astype("<i2").tobytes()
        self.stream.write(data)

    def flush(self):
        pass

    def close(self):
        if not self.closed:
            self.closed = True
            self.stream.stop()
            self.stream.close()

    def poll(self):
        return 0 if self.closed else None

    def kill(self):
        if not self.closed:
            self.closed = True
            self.stream.abort()
            self.stream.close()

    def wait(self):
        self.close()
        return 0


def start_player(volume):
    if sys.platform.startswith("linux") and shutil.which("pw-cat"):
        return subprocess.Popen(
            ["pw-cat", "--playback", "--raw", "--format", "s16", "--rate", str(SAMPLE_RATE),
             "--channels", "1", "--volume", str(volume), "--media-role", "Notification",
             "-P", '{ media.name = "say" application.name = "say" }', "-"],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return PortablePlayer(volume)


def die(msg, code=2):
    print(f"say: {msg}", file=sys.stderr)
    sys.exit(code)


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")


def voice_names():
    import numpy as np

    with np.load(VOICES) as v:
        return sorted(v.files)


def resolve_voice(name, names):
    n = name.strip().lower().replace(" ", "_").replace("-", "_")
    if n in names:
        return n
    matches = [v for v in names if v.split("_", 1)[1] == n]
    if not matches:
        matches = [v for v in names if v.split("_", 1)[1].startswith(n)]
    if not matches:
        die(f"no voice called '{name}'. Run `say --voices` to see them all.")
    matches.sort(key=lambda v: (PREFERENCE.index(v[:2]) if v[:2] in PREFERENCE else 99, v))
    return matches[0]


def list_voices(current):
    names = voice_names()
    by = {}
    for v in names:
        by.setdefault(v[:2], []).append(v)
    for prefix in sorted(by, key=lambda p: (PREFERENCE.index(p) if p in PREFERENCE else 99, p)):
        print(f"{GROUPS.get(prefix, (prefix,))[0]}:")
        for v in by[prefix]:
            mark = "  <- current" if v == current else ""
            print(f"  {v.split('_', 1)[1]:12s} ({v}){mark}")
    print(f"\n{len(names)} voices. Change with: say --set-voice NAME")


# ------------------------------------------------------------------ text preparation

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F\U0000200D]")


def _end_sentence(m):
    """Headings and list items become sentences of their own."""
    body = m.group(1)
    return body if body[-1] in ".!?:;," else body + "."


def clean(text):
    """Turn chat/markdown text into something that reads naturally aloud."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)             # code blocks: skip
    text = re.sub(r"^\s*\|.*\|\s*$", " ", text, flags=re.M)        # table rows: skip
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)          # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)           # links -> their text
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)                       # inline code
    text = re.sub(r"^\s{0,3}#{1,6}\s*(.+?)\s*$", _end_sentence, text, flags=re.M)      # headings
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$", _end_sentence, text, flags=re.M)  # list items
    text = re.sub(r"(\*\*|__|\*|_)(?=\S)(.+?)(?<=\S)\1", r"\2", text)  # bold / italic
    text = _EMOJI.sub("", text)
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)                  # no space before punctuation
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def chunks(text, first_max=140, rest_max=260):
    """Split into sentence groups. The first group is short so speech starts quickly."""
    sentences = [s for s in re.split(r"(?<=[.!?;:])\s+", text) if s.strip()]
    out, cur = [], ""
    for s in sentences:
        while len(s) > 420:                                         # very long sentence: cut at a comma/space
            cut = max(s.rfind(", ", 0, 400), s.rfind(" ", 0, 400))
            cut = cut if cut > 0 else 400
            sentences_part, s = s[:cut + 1], s[cut + 1:]
            if cur:
                out.append(cur)
                cur = ""
            out.append(sentences_part.strip())
        limit = first_max if not out else rest_max
        if cur and len(cur) + 1 + len(s) > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


# ------------------------------------------------------------------ microphone awareness

_IGNORED_RECORDERS = ("peak detect", "plasma-pa", "pavucontrol")


def recorders():
    """Indices of apps currently recording from a microphone (ignoring volume meters)."""
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", "source-outputs"],
                             capture_output=True, text=True, timeout=3).stdout
        found = set()
        for o in json.loads(out or "[]"):
            p = o.get("properties", {})
            label = " ".join(str(p.get(k, "")) for k in ("media.name", "application.name", "application.id")).lower()
            if not any(x in label for x in _IGNORED_RECORDERS):
                found.add(o.get("index"))
        return found
    except (OSError, ValueError, subprocess.SubprocessError):
        return set()


# ------------------------------------------------------------------ speaking

class Speaker:
    def __init__(self, voice, speed, volume, interrupt, verbose):
        self.voice, self.speed, self.volume = voice, speed, volume
        self.interrupt, self.verbose = interrupt, verbose
        self.player = None
        self.stop = threading.Event()
        self.reason = None

    def log(self, msg):
        if self.verbose:
            print(f"say: {msg}", file=sys.stderr)

    def _engine(self):
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        opts.log_severity_level = 3
        session = ort.InferenceSession(str(MODEL), opts, providers=["CPUExecutionProvider"])
        return Kokoro.from_session(session, str(VOICES))

    def _synthesize(self, parts, out):
        try:
            engine = self._engine()
            lang = GROUPS.get(self.voice[:2], ("", "en-us"))[1]
            for part in parts:
                if self.stop.is_set():
                    break
                samples, _ = engine.create(part, voice=self.voice, speed=self.speed, lang=lang)
                out.put(samples)
        except Exception as e:  # surfaced by the caller
            out.put(e)
        out.put(None)

    def halt(self, reason):
        if not self.stop.is_set():
            self.reason = reason
            self.stop.set()
        if self.player and self.player.poll() is None:
            self.player.kill()

    def _watch_mic(self, baseline):
        while not self.stop.is_set():
            if recorders() - baseline:
                self.halt("mic")
                return
            self.stop.wait(0.3)

    def speak(self, parts, wav_path=None):
        import numpy as np

        t0 = time.time()
        audio = queue.Queue()
        threading.Thread(target=self._synthesize, args=(parts, audio), daemon=True).start()

        if wav_path:
            collected = []
            while (item := audio.get()) is not None:
                if isinstance(item, Exception):
                    die(f"synthesis failed: {item}", 1)
                collected.append(item)
            import soundfile as sf

            sf.write(wav_path, np.concatenate(collected) if collected else np.zeros(1, "float32"), SAMPLE_RATE)
            return 0

        baseline = set()
        if self.interrupt:
            deadline = time.time() + 20          # someone is dictating: let them finish first
            while recorders() and time.time() < deadline and not self.stop.is_set():
                time.sleep(0.3)
            baseline = recorders()
            threading.Thread(target=self._watch_mic, args=(baseline,), daemon=True).start()

        first = True
        while not self.stop.is_set():
            item = audio.get()
            if item is None:
                break
            if isinstance(item, Exception):
                self.halt("error")
                die(f"synthesis failed: {item}", 1)
            if first:
                self.log(f"first audio after {time.time() - t0:.2f}s")
                try:
                    self.player = start_player(self.volume)
                except Exception as exc:
                    self.halt("error")
                    die(f"cannot start audio playback: {exc}", 1)
                first = False
            pcm = (np.clip(item, -1.0, 1.0) * 32767).astype("<i2").tobytes()
            try:
                self.player.stdin.write(pcm)
                self.player.stdin.flush()
            except (BrokenPipeError, OSError):
                break
        if self.player:
            try:
                if not self.stop.is_set():
                    # a short tail of silence so the last word is not clipped
                    self.player.stdin.write(b"\0" * int(SAMPLE_RATE * 0.25) * 2)
                self.player.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            self.player.wait()
        self.stop.set()
        return {"mic": 3, "stopped": 4}.get(self.reason, 0)


def take_ticket():
    """Reserve a place in line; returns the ticket path. Lower numbers speak first."""
    TICKETS.mkdir(parents=True, exist_ok=True)
    ticket = TICKETS / f"{time.time_ns():020d}"
    ticket.write_text("")
    return ticket


def my_turn(ticket):
    """True when no older live ticket is waiting."""
    for other in sorted(TICKETS.iterdir()):
        if other.name >= ticket.name:
            return True
        try:
            pid = other.read_text().strip()
            alive = bool(pid) and pid_alive(pid)
            if not alive and (pid or time.time() - other.stat().st_mtime > 10):
                other.unlink(missing_ok=True)       # owner died, or never showed up
                continue
        except OSError:
            continue
        return False
    return True


def stop_all():
    STATE.mkdir(parents=True, exist_ok=True)
    STOPFILE.touch()
    try:
        os.kill(int(PIDFILE.read_text()), signal.SIGTERM)
    except (OSError, ValueError):
        pass


def main():
    ap = argparse.ArgumentParser(prog="say", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="*", help="text to speak (or pipe it on stdin)")
    ap.add_argument("-v", "--voice", help="voice for this one utterance")
    ap.add_argument("--set-voice", metavar="NAME", help="save NAME as the voice to use from now on")
    ap.add_argument("--voices", action="store_true", help="list all voices")
    ap.add_argument("-r", "--speed", type=float, help="speed for this utterance (0.5-2.0)")
    ap.add_argument("--set-speed", type=float, metavar="X", help="save the default speed")
    ap.add_argument("--set-volume", type=float, metavar="X", help="save the default volume (0-1)")
    ap.add_argument("--volume", type=float, help="volume for this utterance (0-1)")
    ap.add_argument("--bg", action="store_true", help="return immediately and speak in the background")
    ap.add_argument("--stop", action="store_true", help="stop speaking and drop queued speech")
    ap.add_argument("--no-interrupt", action="store_true", help="keep speaking even if a microphone goes live")
    ap.add_argument("--raw", action="store_true", help="speak the text exactly, without markdown cleanup")
    ap.add_argument("-o", "--output", metavar="FILE.wav", help="write audio to a file instead of playing it")
    ap.add_argument("--status", action="store_true", help="show the saved settings")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    if a.bg and a.output:
        ap.error("--bg cannot be combined with --output")

    cfg = load_config()
    if a.stop:
        stop_all()
        return 0
    if a.voices:
        list_voices(cfg["voice"])
        return 0
    if not MODEL.exists() or not VOICES.exists():
        die(f"voice model missing in {MODEL.parent}", 1)

    changed = False
    if a.set_voice:
        cfg["voice"] = resolve_voice(a.set_voice, voice_names())
        changed = True
    if a.set_speed is not None:
        cfg["speed"] = min(2.0, max(0.5, a.set_speed))
        changed = True
    if a.set_volume is not None:
        cfg["volume"] = min(1.0, max(0.0, a.set_volume))
        changed = True
    if changed:
        save_config(cfg)
    if a.status or (changed and not a.text and sys.stdin.isatty()):
        group = GROUPS.get(cfg["voice"][:2], ("",))[0]
        print(f"voice: {cfg['voice'].split('_', 1)[1]} ({cfg['voice']}, {group}) | speed: {cfg['speed']} | volume: {cfg['volume']}")
        if not a.text:
            return 0

    text = " ".join(a.text) if a.text else ("" if sys.stdin.isatty() else sys.stdin.read())
    if not a.raw:
        text = clean(text)
    if not text.strip():
        if changed:
            return 0
        die("nothing to say (give text as an argument or on stdin)")

    if a.bg:
        args = []
        if a.voice:
            args += ["-v", a.voice]
        if a.speed is not None:
            args += ["-r", str(a.speed)]
        if a.volume is not None:
            args += ["--volume", str(a.volume)]
        if a.no_interrupt:
            args.append("--no-interrupt")
        ticket = take_ticket()                             # keeps the order of repeated --bg calls
        background_options = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                              if os.name == "nt" else {"start_new_session": True})
        subprocess.Popen([sys.executable, __file__, *args, "--raw", "--", text], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **background_options,
                         env=dict(os.environ, SAY_TICKET=str(ticket)))
        return 0

    voice = resolve_voice(a.voice, voice_names()) if a.voice else cfg["voice"]
    speed = min(2.0, max(0.5, a.speed if a.speed is not None else cfg["speed"]))
    volume = min(1.0, max(0.0, a.volume if a.volume is not None else cfg["volume"]))
    speaker = Speaker(voice, speed, volume, interrupt=not a.no_interrupt and not a.output, verbose=a.verbose)

    if a.output:
        return speaker.speak(chunks(text), wav_path=a.output)

    STATE.mkdir(parents=True, exist_ok=True)
    asked = time.time()
    ticket = Path(os.environ["SAY_TICKET"]) if os.environ.get("SAY_TICKET") else take_ticket()
    try:
        ticket.write_text(str(os.getpid()))
        signal.signal(signal.SIGTERM, lambda *_: speaker.halt("stopped"))
        signal.signal(signal.SIGINT, lambda *_: speaker.halt("stopped"))
        with open(LOCK, "a+") as lock:
            if lock.tell() == 0:
                lock.write(" ")
                lock.flush()
            while True:                                   # one utterance at a time, oldest first
                queue_lock(lock)
                if my_turn(ticket):
                    break
                queue_lock(lock, unlock=True)
                time.sleep(0.05)
            stopped = STOPFILE.exists() and STOPFILE.stat().st_mtime >= min(asked, ticket.stat().st_mtime)
            if stopped or speaker.stop.is_set():
                return 4                                  # a `say --stop` arrived while we were queued
            PIDFILE.write_text(str(os.getpid()))
            return speaker.speak(chunks(text))
    finally:
        ticket.unlink(missing_ok=True)
        PIDFILE.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
