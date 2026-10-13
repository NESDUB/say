import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

installer = load('say_installer', 'install.py')
say = load('portable_say', 'say.py')


class ModelInstallationTests(unittest.TestCase):
    def test_valid_cache_avoids_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cache = root / 'cache'; cache.mkdir()
            (cache / 'model.bin').write_bytes(b'valid model')
            target = root / 'installed/model.bin'
            with patch.dict(installer.MODELS, {'model.bin': hashlib.sha256(b'valid model').hexdigest()}), patch.object(installer.urllib.request, 'urlopen') as network:
                installer.download_model('model.bin', target, cache)
                self.assertEqual(target.read_bytes(), b'valid model')
                installer.download_model('model.bin', target)
                network.assert_not_called()

    def test_corrupt_download_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'model.bin'; target.write_bytes(b'old file')
            with patch.dict(installer.MODELS, {'model.bin': hashlib.sha256(b'expected').hexdigest()}), patch.object(installer.urllib.request, 'urlopen', return_value=io.BytesIO(b'corrupted')):
                with self.assertRaises(ValueError):
                    installer.download_model('model.bin', target)
            self.assertEqual(target.read_bytes(), b'old file')
            self.assertEqual(list(Path(tmp).glob('*.part')), [])

    def test_successful_download_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'model.bin'
            with patch.dict(installer.MODELS, {'model.bin': hashlib.sha256(b'expected').hexdigest()}), patch.object(installer.urllib.request, 'urlopen', return_value=io.BytesIO(b'expected')):
                installer.download_model('model.bin', target)
            self.assertEqual(target.read_bytes(), b'expected')

    @unittest.skipIf(os.name == 'nt', 'POSIX shell quoting check')
    def test_launcher_handles_spaces_and_literal_shell_text(self):
        with tempfile.TemporaryDirectory(prefix='say install ') as tmp:
            root = Path(tmp); app = root / 'application with spaces'; app.mkdir()
            (app / 'say.py').write_text('import sys,json; print(json.dumps(sys.argv[1:]))')
            installer.write_launchers(app, root / 'bin', Path(sys.executable))
            result = subprocess.check_output([str(root / 'bin/say'), 'a b', '$(do-not-execute)', '`literal`'], text=True)
            import json
            self.assertEqual(json.loads(result), ['a b', '$(do-not-execute)', '`literal`'])


class SpeechBehaviorTests(unittest.TestCase):
    def test_cleanup_keeps_link_labels_skips_code(self):
        self.assertEqual(say.clean('# Hello\n[World](https://example.com)\n```python\nsecret()\n```'), 'Hello. World')

    def test_voice_selection(self):
        names = ['af_heart','hf_alpha','jf_alpha','am_michael']
        self.assertEqual(say.resolve_voice('Michael', names), 'am_michael')
        self.assertEqual(say.resolve_voice('jf-alpha', names), 'jf_alpha')

    def test_queue_discards_dead_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            tickets = Path(tmp); (tickets/'001').write_text('42'); current=tickets/'002';current.write_text(str(os.getpid()))
            with patch.object(say,'TICKETS',tickets), patch.object(say,'pid_alive',return_value=False):
                self.assertTrue(say.my_turn(current))
                self.assertFalse((tickets/'001').exists())

    def test_queue_waits_for_live_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            tickets=Path(tmp);(tickets/'001').write_text('42');current=tickets/'002';current.write_text('43')
            with patch.object(say,'TICKETS',tickets), patch.object(say,'pid_alive',return_value=True):
                self.assertFalse(say.my_turn(current))

    def test_pid_detection(self):
        self.assertTrue(say.pid_alive(str(os.getpid())))
        self.assertFalse(say.pid_alive('not-a-pid'))
        self.assertFalse(say.pid_alive('0'))

    def test_background_output_is_rejected(self):
        result=subprocess.run([sys.executable,str(ROOT/'say.py'),'--bg','-o','no.wav','hello'],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('--bg cannot be combined',result.stderr)


if __name__ == '__main__':
    unittest.main()
