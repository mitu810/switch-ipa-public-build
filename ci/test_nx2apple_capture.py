import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import nx2apple_capture as capture


class CaptureGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'ExeFS').mkdir()
        header = bytearray(0x60)
        header[:4] = b'NSO0'
        header[0x40:0x50] = bytes.fromhex(capture.MAIN_BUILD_ID)
        (self.root / 'ExeFS/main').write_bytes(header)
        self.request = self.root / 'request.json'
        self.builder = SimpleNamespace(BuildError=ValueError, glslc_wanted=lambda _: set(),
            PROG=SimpleNamespace(stage=lambda _: None, end=lambda _: None), say=lambda _: None)
        self.hook = capture.install_bootstrap_hook(self.builder, self.request)

    def invoke(self, title=capture.TITLE_ID):
        return self.hook(None, {'v1': self.root}, 'v1', None, None, {}, None,
                         SimpleNamespace(id=title))

    def test_matching_build_is_marked_incomplete(self):
        self.assertIsNone(self.invoke())
        record = json.loads(self.request.read_text())
        self.assertTrue(record['diagnostic_only'])
        self.assertFalse(record['runtime_capture_complete'])

    def test_other_game_cannot_defer_capture(self):
        with self.assertRaises(ValueError):
            self.invoke('0100000000010000')
        self.assertFalse(self.request.exists())

    def test_changed_main_cannot_defer_capture(self):
        data = bytearray((self.root / 'ExeFS/main').read_bytes())
        data[0x40] ^= 1
        (self.root / 'ExeFS/main').write_bytes(data)
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertFalse(self.request.exists())

    def test_known_capture_requirements_are_not_skipped(self):
        self.builder.glslc_wanted = lambda _: {'required.glslc'}
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertFalse(self.request.exists())


if __name__ == '__main__':
    unittest.main()
