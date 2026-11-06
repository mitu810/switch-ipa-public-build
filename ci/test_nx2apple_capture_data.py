import struct
import tempfile
import unittest
from pathlib import Path

from nx2apple_capture_data import validate_capture, validate_output, TITLE_ID, MAIN_BUILD_ID


class CaptureDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = {'schema': 1, 'diagnostic_only': True, 'title_id': TITLE_ID,
                        'main_build_id': MAIN_BUILD_ID, 'package_version': 'v1',
                        'capture_directory': f'Documents/games/{TITLE_ID}/glslc_capture'}

    def fixture(self):
        # Fixed FNV-1a hash for this minimal header.
        data = bytearray(0x48)
        struct.pack_into('<I', data, 0, 0x19866891)
        struct.pack_into('<I', data, 0x44, len(data))
        path = self.root / 'b0ac3b8612d1124d.glslc'
        path.write_bytes(data)
        return path

    def test_empty_capture_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_capture(self.request, self.root)

    def test_game_identity_is_required(self):
        self.request['main_build_id'] = '0' * 32
        with self.assertRaises(ValueError):
            validate_capture(self.request, self.root)

    def test_valid_capture_does_not_claim_full_coverage(self):
        self.fixture()
        record = validate_capture(self.request, self.root)
        self.assertEqual(len(record['files']), 1)
        self.assertFalse(record['gameplay_coverage_verified'])

    def test_truncation_is_rejected(self):
        path = self.fixture()
        path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            validate_output(path)

    def test_corruption_is_rejected(self):
        path = self.fixture()
        data = bytearray(path.read_bytes())
        data[20] ^= 1
        path.write_bytes(data)
        with self.assertRaises(ValueError):
            validate_output(path)

    def test_symlink_is_rejected(self):
        path = self.fixture()
        link = self.root / '0000000000000000.glslc'
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            validate_output(link)


if __name__ == '__main__':
    unittest.main()
