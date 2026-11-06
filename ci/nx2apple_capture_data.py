"""Validate and package physical-device GLSLC outputs for a subsequent build.

This checks transport integrity and the diagnostic request's game identity. It
does not assert that the capture includes every shader needed during gameplay.
"""
import argparse
import hashlib
import json
import re
import struct
import tarfile
from pathlib import Path

from nx2apple_capture import TITLE_ID, MAIN_BUILD_ID


def validate_output(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Capture must be a regular file: ' + path.name)
    if not re.fullmatch(r'[0-9a-f]{16}\.glslc', path.name):
        raise ValueError('Invalid capture filename: ' + path.name)
    if not 0x48 <= path.stat().st_size <= 64 * 1024 * 1024:
        raise ValueError('Invalid capture size: ' + path.name)
    data = path.read_bytes()
    if data[:4] != b'\x91\x68\x86\x19' or struct.unpack_from('<I', data, 0x44)[0] != len(data):
        raise ValueError('Truncated or invalid GLSLC output: ' + path.name)
    digest = 0xcbf29ce484222325
    for value in data:
        digest = ((digest ^ value) * 0x100000001b3) & 0xffffffffffffffff
    if path.stem != f'{digest:016x}':
        raise ValueError('Capture content does not match its runtime hash: ' + path.name)
    return {'name': path.name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def validate_capture(request, directory):
    if (request.get('schema') != 1 or request.get('diagnostic_only') is not True
            or request.get('title_id') != TITLE_ID or request.get('main_build_id') != MAIN_BUILD_ID
            or request.get('capture_directory') != f'Documents/games/{TITLE_ID}/glslc_capture'):
        raise ValueError('Capture request does not match the surveyed diagnostic build')
    paths = sorted(directory.iterdir())
    if not paths:
        raise ValueError('No runtime outputs captured; an empty set cannot resume the normal build')
    files = [validate_output(path) for path in paths]
    return {'schema': 1, 'title_id': TITLE_ID, 'main_build_id': MAIN_BUILD_ID,
            'package_version': request['package_version'], 'source': 'physical-device-glslc',
            'gameplay_coverage_verified': False, 'files': files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--captures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    record = validate_capture(json.loads(args.request.read_text()), args.captures)
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / 'device-captures.tar.gz'
    with tarfile.open(archive.with_suffix('.partial'), 'w:gz') as tar:
        for item in record['files']:
            tar.add(args.captures / item['name'], arcname='glslc_capture/' + item['name'])
    archive.with_suffix('.partial').replace(archive)
    (args.output / 'capture-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(f'Validated {len(record["files"])} runtime outputs. Gameplay coverage remains unverified.')


if __name__ == '__main__':
    main()
