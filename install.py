#!/usr/bin/env python3
"""Portable, per-user installation of say and its Kokoro model files."""
import argparse
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import venv

SOURCE = Path(__file__).resolve().parent
MODELS = {
    'kokoro-v1.0.onnx': '7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5',
    'voices-v1.0.bin': 'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d',
}
MODEL_BASE = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/'


def checksum(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def download_model(name, destination, cache=None):
    expected = MODELS[name]
    if destination.exists() and checksum(destination) == expected:
        print(f'Using verified installed {name}.', flush=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=name + '.', suffix='.part', dir=destination.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        cached = Path(cache) / name if cache else None
        if cached and cached.exists() and checksum(cached) == expected:
            print(f'Copying verified cached {name}.', flush=True)
            shutil.copyfile(cached, tmp)
        else:
            print(f'Downloading {name} from the official Kokoro release…', flush=True)
            request = urllib.request.Request(MODEL_BASE + name, headers={'User-Agent': 'say-installer/1.0'})
            with urllib.request.urlopen(request, timeout=60) as response, tmp.open('wb') as f:
                shutil.copyfileobj(response, f)
        if checksum(tmp) != expected:
            raise ValueError(f'Checksum mismatch for {name}; existing model was left untouched')
        tmp.replace(destination)
    finally:
        tmp.unlink(missing_ok=True)


def system_dependencies():
    if sys.platform == 'win32':
        # sounddevice and soundfile wheels provide native libraries on Windows.
        return
    if sys.platform == 'darwin':
        if ctypes.util.find_library('portaudio'):
            return
        if not shutil.which('brew'):
            raise RuntimeError('Install Homebrew (https://brew.sh), then rerun the installer; PortAudio requires it on macOS.')
        subprocess.run(['brew', 'install', 'portaudio', 'libsndfile', 'espeak-ng'], check=True)
        return
    if not sys.platform.startswith('linux'):
        raise RuntimeError('Supported platforms: Linux, macOS, and Windows. This operating system is unsupported.')
    playback = shutil.which('pw-cat') or ctypes.util.find_library('portaudio')
    if playback and ctypes.util.find_library('sndfile') and shutil.which('pactl') and shutil.which('espeak-ng'):
        return
    choices = [
        ('apt-get', ['install','-y','python3-venv','python3-pip','libsndfile1','libportaudio2','espeak-ng','pulseaudio-utils','pipewire-bin']),
        ('dnf', ['install','-y','python3-pip','libsndfile','portaudio','espeak-ng','pulseaudio-utils','pipewire-utils']),
        ('pacman', ['-S','--needed','--noconfirm','python','python-pip','libsndfile','portaudio','espeak-ng','libpulse','pipewire']),
        ('zypper', ['--non-interactive','install','python3-pip','libsndfile1','libportaudio2','espeak-ng','pulseaudio-utils','pipewire-tools']),
    ]
    selected = next(((manager, args) for manager, args in choices if shutil.which(manager)), None)
    if selected is None:
        raise RuntimeError('Install PortAudio, libsndfile, eSpeak NG, Python venv/pip, and optionally pactl/pw-cat with your package manager; rerun with --skip-system-deps.')
    prefix = [] if os.geteuid() == 0 else ['sudo']
    if prefix and not shutil.which('sudo'):
        raise RuntimeError('System dependencies are missing. Install them as root, then rerun with --skip-system-deps.')
    if selected[0] == 'apt-get':
        subprocess.run(prefix + ['apt-get','update'], check=True)
    subprocess.run(prefix + [selected[0], *selected[1]], check=True)


def write_launchers(prefix, bin_dir, python):
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        for name in ('say.cmd', 'kokoro-say.cmd'):
            (bin_dir / name).write_text(f'@echo off\r\n"{python}" "{prefix / "say.py"}" %*\r\n')
    else:
        text = '#!/bin/sh\nexec ' + shlex.quote(str(python)) + ' ' + shlex.quote(str(prefix / 'say.py')) + ' "$@"\n'
        for name in ('say', 'kokoro-say'):
            p = bin_dir / name
            # Replace a launcher file, never follow a symlink into someone else's app.
            if p.is_symlink():
                p.unlink()
            p.write_text(text)
            p.chmod(0o755)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    default_prefix = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'kokoro-say' if os.name == 'nt' else Path.home() / '.local/share/say'
    ap.add_argument('--prefix', type=Path, default=default_prefix, help='Per-user application directory')
    ap.add_argument('--bin-dir', type=Path, help='Launcher directory (default: ~/.local/bin or PREFIX/bin on Windows)')
    ap.add_argument('--model-cache', type=Path, help='Reuse hash-verified local model files')
    ap.add_argument('--skip-system-deps', action='store_true', help='Use already installed system libraries')
    ap.add_argument('--skip-python-deps', action='store_true', help='Use an existing venv; intended for offline upgrades')
    args = ap.parse_args()
    if sys.version_info < (3, 10):
        ap.error('Python 3.10 or newer is required. Python 3.12 is recommended for broad wheel compatibility.')
    if os.name != 'nt' and os.geteuid() == 0 and os.environ.get('SUDO_USER'):
        ap.error('Run the installer as your normal user; it requests sudo only for system packages.')
    prefix = args.prefix.expanduser().resolve()
    bin_dir = (args.bin_dir or (prefix / 'bin' if os.name == 'nt' else Path.home() / '.local/bin')).expanduser().resolve()
    try:
        if not args.skip_system_deps:
            system_dependencies()
        prefix.mkdir(parents=True, exist_ok=True)
        venv_dir = prefix / 'venv'
        python = venv_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if not python.exists():
            if args.skip_python_deps:
                raise RuntimeError('--skip-python-deps requires an existing working venv')
            try:
                venv.EnvBuilder(with_pip=True).create(venv_dir)
            except Exception as exc:
                raise RuntimeError('Cannot create Python venv. Install your distribution\'s python3-venv package and rerun.') from exc
        if not args.skip_python_deps:
            subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', 'pip'], check=True)
            subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(SOURCE / 'requirements.txt')], check=True)
        for name in MODELS:
            download_model(name, prefix / 'models' / name, args.model_cache)
        # Verify basic imports/model archive before replacing the working application.
        subprocess.run([str(python), '-c', 'import numpy, onnxruntime, soundfile, kokoro_onnx'], check=True)
        if not args.skip_python_deps:
            subprocess.run([str(python), '-c', 'import sounddevice'], check=True)
        app = prefix / 'say.py'
        if app.exists() and app.resolve() != (SOURCE / 'say.py').resolve():
            shutil.copy2(app, prefix / 'say.py.before-portable-upgrade')
        if app.resolve() != (SOURCE / 'say.py').resolve():
            shutil.copy2(SOURCE / 'say.py', app)
        subprocess.run([str(python), str(app), '--voices'], check=True)
        write_launchers(prefix, bin_dir, python)
        print(f'\nInstalled say in {prefix}\nLaunchers: {bin_dir}')
        if os.name == 'nt':
            # Include this directory in the current user's persistent PATH.
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                try:
                    old, _ = winreg.QueryValueEx(key, 'Path')
                except FileNotFoundError:
                    old = ''
                if str(bin_dir).lower() not in [p.lower().rstrip('\\') for p in old.split(';')]:
                    winreg.SetValueEx(key, 'Path', 0, winreg.REG_EXPAND_SZ, old.rstrip(';') + (';' if old else '') + str(bin_dir))
            print('Open a new terminal after signing out/in if Windows has not refreshed PATH. Use the full say.cmd path meanwhile.')
        elif str(bin_dir) not in os.environ.get('PATH','').split(os.pathsep):
            print('Add this to your shell profile: export PATH=' + shlex.quote(str(bin_dir)) + ':"$PATH"')
        print('Try: kokoro-say --status  (or say --status if this launcher is first on PATH)')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'say installer: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
