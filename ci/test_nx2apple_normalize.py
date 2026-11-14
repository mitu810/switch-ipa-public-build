"""Reject unexpected, modified or unsafe files before changing the dump copy."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from nx2apple_normalize import normalize_dump


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.romfs, self.quarantine = root / 'romfs', root / 'quarantine'
        self.romfs.mkdir()
        self.manifest = {'schema': 1, 'title_id': '040074A01BF12000', 'version': 458752,
            'files': [], 'original_romfs_entries': 44, 'normalized_romfs_entries': 2}
        for index in range(42):
            name = f'table-{index}.Product.100.bin'
            data = f'old-{index}'.encode()
            (self.romfs / name).write_bytes(data)
            self.manifest['files'].append({'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        (self.romfs / 'current.Product.200.bin').write_bytes(b'current')

    def test_verified_old_files_are_retained_outside_the_build_tree(self):
        result = normalize_dump(self.romfs, self.quarantine, self.manifest)
        self.assertEqual(result['quarantined_obsolete_files'], 42)
        self.assertEqual((self.romfs / 'current.Product.200.bin').read_bytes(), b'current')
        self.assertEqual(len(list(self.quarantine.iterdir())), 42)

    def test_changed_input_rejects_the_entire_operation_before_any_move(self):
        (self.romfs / self.manifest['files'][-1]['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'differs'):
            normalize_dump(self.romfs, self.quarantine, self.manifest)
        self.assertEqual(len(list(self.romfs.iterdir())), 43)
        self.assertFalse(self.quarantine.exists())

    def test_path_traversal_is_rejected(self):
        self.manifest['files'][0]['path'] = '../outside.Product.100.bin'
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            normalize_dump(self.romfs, self.quarantine, self.manifest)
        self.assertFalse(self.quarantine.exists())

    def test_unexpected_extra_old_file_is_rejected(self):
        (self.romfs / 'unknown.Product.100.bin').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'contents differ'):
            normalize_dump(self.romfs, self.quarantine, self.manifest)
        self.assertFalse(self.quarantine.exists())


if __name__ == '__main__':
    unittest.main()
