import json
import tempfile
import unittest
import struct
from pathlib import Path
from types import SimpleNamespace

import nx2apple_capture as capture


class CaptureFixture:
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


class CaptureGuardTests(CaptureFixture, unittest.TestCase):
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


class CaptureReplayTests(CaptureFixture, unittest.TestCase):
    def prepare_replay(self):
        # Generate the real request through the bootstrap guard, then provide a
        # structurally valid capture. No fabricated file is used outside tests.
        self.invoke()
        directory = self.root / 'device'
        directory.mkdir()
        data = bytearray(0x48)
        struct.pack_into('<I', data, 0, 0x19866891)
        struct.pack_into('<I', data, 0x44, len(data))
        self.name = 'b0ac3b8612d1124d.glslc'
        (directory / self.name).write_bytes(data)
        self.normal_calls = []

        def normal(repo, dirs, pkg_ver, *rest):
            self.normal_calls.append((dirs[pkg_ver] / 'glslc_capture' / self.name).read_bytes())
            return 'normal capture result'

        self.builder.capture_glslc = normal
        self.hook = capture.install_replay_hook(self.builder, self.request, directory)

    def test_replay_calls_original_validator_with_real_files(self):
        self.prepare_replay()
        self.assertEqual(self.invoke(), 'normal capture result')
        self.assertEqual(len(self.normal_calls), 1)
        self.assertEqual(len(self.normal_calls[0]), 0x48)

    def test_replay_requires_matching_version(self):
        self.prepare_replay()
        record = json.loads(self.request.read_text())
        record['package_version'] = 'v999'
        self.request.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertEqual(self.normal_calls, [])

    def test_replay_preserves_existing_capture(self):
        self.prepare_replay()
        existing = self.root / 'glslc_capture'
        existing.mkdir()
        (existing / 'sentinel').write_text('preserve')
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertEqual((existing / 'sentinel').read_text(), 'preserve')
        self.assertEqual(self.normal_calls, [])

    def test_replay_requires_all_registered_outputs(self):
        self.prepare_replay()
        self.builder.glslc_wanted = lambda _: {'0000000000000000.glslc'}
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertFalse((self.root / 'glslc_capture').exists())
        self.assertEqual(self.normal_calls, [])


if __name__ == '__main__':
    unittest.main()
