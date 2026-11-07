import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('PRIVATE_REPO', 'test/private')
os.environ.setdefault('BUILD_GAME', 'zelda-library')
import nx2apple_zelda as z


class LegacyInputIntegrity(unittest.TestCase):
    def fixture(self, root):
        names = {'base.nsp': ['zelda_botw.part_a' + c for c in 'abcdefgh'],
                 'update.nsp': ['update.nsp'], 'dlc1.nsp': ['dlc1.nsp'], 'dlc2.nsp': ['dlc2.nsp']}
        data = {name: ('fixture ' + name).encode() for group in names.values() for name in group}
        manifest = {'schema': 1, 'title_id': z.TITLE, 'files': [
            {'name': name, 'size': sum(len(data[p]) for p in parts), 'parts': [
                {'name': p, 'size': len(data[p]), 'sha256': hashlib.sha256(data[p]).hexdigest()} for p in parts]}
            for name, parts in names.items()]}
        def download(tag, name, directory):
            directory.mkdir(parents=True, exist_ok=True)
            p = directory / name
            p.write_bytes(json.dumps(manifest).encode() if name == 'nx2apple-input-manifest.json' else data[name])
            return p
        return data, manifest, download

    def run_fixture(self, mutate=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / 'inputs'
            inputs.mkdir()
            data, manifest, download = self.fixture(root)
            if mutate:
                mutate(data, manifest)
            with patch.object(z.b, 'DATA', root), patch.object(z.b, 'download', download):
                result = z.restore_inputs(inputs)
            expected = b''.join(data['zelda_botw.part_a' + c] for c in 'abcdefgh')
            self.assertEqual((inputs / 'base.nsp').read_bytes(), expected)
            self.assertEqual(result['files'][0]['sha256'], hashlib.sha256(expected).hexdigest())

    def test_verified_parts_assemble_in_order(self):
        self.run_fixture()

    def test_corrupt_part_rejected(self):
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.run_fixture(lambda data, m: data.update({'zelda_botw.part_ac': b'corrupt'}))

    def test_reordered_parts_rejected(self):
        with self.assertRaisesRegex(ValueError, 'part ordering'):
            self.run_fixture(lambda data, m: m['files'][0]['parts'].reverse())


if __name__ == '__main__':
    unittest.main()
