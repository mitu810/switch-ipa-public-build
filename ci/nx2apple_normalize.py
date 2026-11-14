"""Quarantine manifest-verified obsolete base files from a patched dump copy."""
import hashlib
from pathlib import Path, PurePosixPath


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(8 * 1024 ** 2):
            result.update(block)
    return result.hexdigest()


def normalize_dump(romfs, quarantine, manifest):
    if manifest.get('schema') != 1 or manifest.get('title_id') != '040074A01BF12000' or manifest.get('version') != 458752:
        raise ValueError('Unexpected dump normalization identity')
    files = manifest['files']
    if len(files) != 42:
        raise ValueError('Expected exactly the 42 obsolete base files')
    expected = set()
    for item in files:
        relative = PurePosixPath(item['path'])
        if relative.is_absolute() or '..' in relative.parts or 'Product.100' not in relative.name or str(relative) in expected:
            raise ValueError('Unsafe or duplicate obsolete-file path')
        expected.add(str(relative))
        source = romfs / relative
        if source.is_symlink() or not source.is_file() or source.stat().st_size != item['size'] or sha(source) != item['sha256']:
            raise ValueError('Obsolete input file differs from the verified archive: ' + str(relative))
        if (quarantine / relative).exists():
            raise ValueError('Quarantine destination already exists')
    observed = {str(p.relative_to(romfs)) for p in romfs.rglob('*') if p.is_file() and 'Product.100' in p.name}
    if observed != expected or sum(1 for _ in romfs.rglob('*')) + 1 != manifest['original_romfs_entries']:
        raise ValueError('The dump contents differ from the normalization manifest')
    # All identities are checked before the first move. The original compressed
    # game is never modified, and the isolated bytes remain in the quarantine.
    for item in files:
        destination = quarantine / item['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        (romfs / item['path']).rename(destination)
    count = sum(1 for _ in romfs.rglob('*')) + 1
    if count != manifest['normalized_romfs_entries']:
        raise ValueError('Normalized entry count differs from the known update')
    return {'title_id': manifest['title_id'], 'version': manifest['version'],
        'quarantined_obsolete_files': len(files), 'normalized_romfs_entries': count,
        'all_obsolete_files_sha256_verified': True, 'original_archive_modified': False}
