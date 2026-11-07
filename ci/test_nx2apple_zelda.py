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
import run_zelda_pipeline as pipeline


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


class DisposableInputCleanup(unittest.TestCase):
    def fixture(self, root):
        directory = root / 'private-build/inputs'
        aoc = directory / 'build/aoc'
        aoc.mkdir(parents=True)
        for name in ('1.img', '2.img'):
            (aoc / name).write_bytes(b'extracted DLC')
        names = ('base.nsp', 'update.nsp', 'dlc1.nsp', 'dlc2.nsp')
        for name in names:
            (directory / name).write_bytes(b'verified input')
        proof = {'title_id': z.TITLE, 'files': [
            {'name': n, 'size': 14, 'sha256': hashlib.sha256(b'verified input').hexdigest()} for n in names]}
        (directory.parent / 'verified-inputs.json').write_text(json.dumps(proof))
        return directory, aoc, names

    def test_only_disposable_sources_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory, aoc, names = self.fixture(root)
            with patch.object(Path, 'cwd', return_value=root), patch.dict(os.environ, GITHUB_ACTIONS='true'):
                pipeline.discard_extracted_inputs(directory, aoc)
            self.assertFalse(any((directory / n).exists() for n in names))
            self.assertTrue((aoc / '1.img').is_file())
            self.assertTrue((directory.parent / 'verified-inputs.json').is_file())

    def test_non_ci_cleanup_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory, aoc, names = self.fixture(root)
            with patch.object(Path, 'cwd', return_value=root), patch.dict(os.environ, GITHUB_ACTIONS='false'):
                with self.assertRaisesRegex(ValueError, 'restricted'):
                    pipeline.discard_extracted_inputs(directory, aoc)
            self.assertTrue(all((directory / n).is_file() for n in names))

    def test_incomplete_extraction_preserves_all_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory, aoc, names = self.fixture(root)
            (aoc / '2.img').unlink()
            with patch.object(Path, 'cwd', return_value=root), patch.dict(os.environ, GITHUB_ACTIONS='true'):
                with self.assertRaisesRegex(ValueError, 'not completed'):
                    pipeline.discard_extracted_inputs(directory, aoc)
            self.assertTrue(all((directory / n).is_file() for n in names))

    def test_changed_input_preserves_all_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory, aoc, names = self.fixture(root)
            (directory / 'update.nsp').write_bytes(b'changed')
            with patch.object(Path, 'cwd', return_value=root), patch.dict(os.environ, GITHUB_ACTIONS='true'):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    pipeline.discard_extracted_inputs(directory, aoc)
            self.assertTrue(all((directory / n).is_file() for n in names))


if __name__ == '__main__':
    unittest.main()
