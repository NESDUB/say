# say — local Kokoro speech

Speak text locally using the Kokoro-82M ONNX model. The project includes one-step installation scripts that create a private Python environment, install dependencies, download and verify the model and voice archive, and create user-level commands.

Linux uses PipeWire when available; macOS, Windows, and other Linux audio setups use PortAudio via `sounddevice`. Queue locking and live-process checks work through platform-specific implementations. Python 3.10+ is required; Python 3.12 is recommended on new installations. The original speech features and saved settings are retained.

## Install

Download or clone the repository, then run the installer from its folder as your **normal user**.

### Linux / macOS

```sh
sh install.sh
```

The bootstrap installs Python if it is missing and a supported package manager is available. The installer supports apt (Debian/Ubuntu), dnf (Fedora/RHEL), pacman (Arch), zypper (openSUSE), and Homebrew (macOS). It invokes sudo only when Linux system packages are missing. On macOS, Homebrew must be installed first if native audio dependencies are missing. On other Linux distributions, install the named native libraries using your package manager and run `python3 install.py --skip-system-deps`.

Default install: `~/.local/share/say`; commands: `~/.local/bin/say` and `~/.local/bin/kokoro-say`. If the command directory is absent from PATH, the installer prints the line to add to your shell profile. On macOS, `kokoro-say` avoids confusion with Apple's built-in `say` command.

### Windows

Run in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The bootstrap uses an existing Python installation, or installs Python 3.12 with winget when available. Otherwise install Python from python.org and rerun it. Default install: `%LOCALAPPDATA%\kokoro-say`; launchers are in its `bin` folder, which the installer adds to your user PATH. Open a new terminal; Windows may need a sign-out/in to refresh the environment. Until then, invoke the printed full path to `say.cmd`.

### What setup does

1. Check/install platform audio libraries and Python venv support.
2. Create an isolated `venv` inside the install directory; install `requirements.txt`.
3. Download `kokoro-v1.0.onnx` (~326 MB) and `voices-v1.0.bin` (~28 MB) from the pinned official [Kokoro model-files-v1.0 release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0).
4. Verify SHA-256 before atomically replacing model files. Failed/corrupted downloads leave existing models intact. Valid existing models are reused on reruns.
5. Back up the previous application source when upgrading, install the application, verify imports and the voice archive, and write launchers.

Downloads require internet access; speech runs offline after installation. The model/voice archive and virtual environment are intentionally not committed to GitHub. ONNX Runtime must offer a compatible package for your OS, architecture, and Python version; arbitrary operating systems or obsolete hardware are not guaranteed. Linux ARM64 and x86-64 are intended targets alongside modern Windows/macOS. Windows and macOS setup paths have not been exercised on this Linux machine.

## Use

```sh
say 'The task is finished.'
printf '%s\n' 'Read from stdin.' | say
say --bg 'Working in the background.'
say -v bella -r 1.15 --volume 0.7 'Hello.'
say --voices
say --status
say --set-voice michael
say --set-speed 1.1
say --set-volume 0.8
say --stop
say -o message.wav 'Export audio without playback.'
say --help
```

Examples use POSIX shells. On Windows, use `say.cmd 'Hello'`, or `"Read from stdin" | say.cmd` in PowerShell. `kokoro-say` accepts the same options on all platforms.

| Option | Function |
| --- | --- |
| `-v`, `--voice NAME` | One-time voice; full identifiers such as `af_heart` avoid ambiguous names. |
| `--set-voice NAME` | Persist the default voice. |
| `--voices`, `--status` | List installed voices / report saved settings. |
| `-r`, `--speed X` | One-time speed, clamped to 0.5–2.0. |
| `--set-speed X` | Persist the default speed. |
| `--volume X`, `--set-volume X` | One-time / persisted playback volume, clamped to 0–1. |
| `--bg` | Detached queued playback; returns before speech is complete, hides child errors. |
| `--stop` | Stop active playback and cancel already queued requests. |
| `--no-interrupt` | Disable Linux microphone waiting/interruption. |
| `--raw` | Skip Markdown/emoji cleanup. |
| `-o`, `--output FILE` | Export 24 kHz mono WAV instead of playing. |
| `--verbose` | Report time until first audio during foreground playback. |

Foreground calls wait for playback. Concurrent speech calls queue by timestamp; each call loads its own model. Streaming synthesis starts playback while later sentence groups are generated. Long input is chunked into sentence groups. Stdin is read to EOF before processing; it is not a continuous dictation stream.

Default voice: `af_heart`; speed/volume: `1.0`. Saved configuration follows `$XDG_CONFIG_HOME/say/config.json` or `~/.config/say/config.json`; queue state follows `$XDG_STATE_HOME/say` or `~/.local/state/say`. These fallbacks also work on Windows. Installation upgrades do not overwrite user preferences.

Markdown cleanup removes fenced code blocks and table rows, speaks link labels and image alt text, removes emphasis/heading/list markers and many emoji, and normalizes whitespace. `--raw` retains input formatting but does not make the voice model spell every character literally.

## Microphone awareness

On Linux with `pactl` and a PulseAudio-compatible recording-stream API (including PipeWire's compatibility service), speech waits up to about 20 seconds for existing recording streams, then stops if a new recording stream appears. Meter streams from pavucontrol/Plasma are ignored. This is not universal microphone detection: macOS/Windows and Linux without `pactl` have playback but no automatic microphone interruption. Speech does not resume automatically after an interruption.

## Export and background limitations

WAV export bypasses playback volume, microphone monitoring, and the playback queue. Output directories must exist. `--bg` with `--output` is explicitly rejected instead of silently playing audio. Background errors are hidden; repeat a failing call in foreground for diagnostics. `--verbose` is not forwarded to the background child.

Exit codes: 0 normal/control completion, 1 synthesis/setup/runtime failures, 2 invalid arguments/voice/empty input, 3 microphone interruption, 4 playback canceled. A PipeWire subprocess failure is not always propagated by this inherited playback implementation; exit 0 alone does not prove sound was audible.

## Advanced installation / offline upgrades

```sh
python3 install.py --prefix "$HOME/apps/kokoro-say" --bin-dir "$HOME/bin"
python3 install.py --model-cache /path/to/downloaded/models
python3 install.py --skip-system-deps --skip-python-deps
```

`--skip-python-deps` requires an existing working environment and is for offline upgrades, not fresh installs. Models are still hash-verified. If playback needs the PortAudio fallback, install `sounddevice` in the environment; the full installer does this automatically. Use the same prefix/bin-dir on subsequent upgrades. Keep the clone for future upgrades or rerun setup from a new download.

## Troubleshooting and validation

- `say --status` checks saved settings; `say --voices` checks the voice archive.
- `say -o /tmp/check.wav 'Speech check.'` isolates synthesis from audio-device problems (choose a Windows path on Windows).
- If PipeWire playback is unavailable, ensure PortAudio can find the default output device. macOS may require system permissions for audio access.
- If pip cannot install ONNX Runtime, use a compatible 64-bit Python version (3.12 recommended).
- For Linux microphone issues, inspect `pactl -f json list source-outputs`.
- Run `python3 -m unittest discover -s tests -v` for installer integrity and core behavior tests.

Local validation covers cached model installation, corrupt-download preservation, launcher paths containing spaces, Markdown cleanup, voice selection, queue behavior, configuration/help, and real WAV synthesis. Fresh internet downloads, audible playback, Windows execution, and macOS execution have not been verified in this restricted Linux session.

## GitHub

`publish-private.sh` creates a private repository named `say` under the active GitHub CLI account, or pushes to an already configured origin only after confirming it is private. On Windows, use the equivalent GitHub CLI command directly:

```sh
gh repo create say --private --source=. --remote=origin --push
```

The code depends on the upstream [kokoro-onnx project](https://github.com/thewh1teagle/kokoro-onnx). The downloaded model files remain governed by their upstream terms; they are downloaded during setup rather than bundled in this source repository.
