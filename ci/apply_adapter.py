"""Add the reviewed build-gated adapter to an otherwise byte-identical embedded main."""
import hashlib
import importlib.util
import json
import struct
import sys
import types
from pathlib import Path


def patch(main, fixups, cuts, patch_source, tools_dir):
    sys.path.insert(0, str(tools_dir))
    spec = importlib.util.spec_from_file_location('bananza_frame_step', patch_source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    hdr = dict(line[2:].split() for line in fixups.read_text().splitlines())
    assert hdr['BUILD_ID'] == 'e25c90c1c136638885659fd55bf8a03b'
    assert 'FPS_STATE_OFF' not in hdr and 'STEP_MAX' not in hdr
    data = bytearray(main.read_bytes()); old = bytes(data)
    state = int(hdr['KEY_PAGE_OFF'], 16) + 0x4000
    assert state == 0x2814000 and state + 0x100 <= int(hdr['PATCH_OFF'], 16)
    assert not any(data[state:state + 0x100])
    start = len(data)
    assert start % 0x4000 == 0
    words, changes = [], []

    def site(at, expected):
        assert data[at:at + len(bytes.fromhex(expected))].hex() == expected

    def put(at, ws, reason):
        raw = struct.pack('<' + 'I' * len(ws), *ws)
        changes.append((at, data[at:at + len(raw)].hex(), raw.hex(), reason))
        data[at:at + len(raw)] = raw

    ctx = types.SimpleNamespace(here=lambda: start + len(words) * 4, emit=words.append,
                                site=site, put=put, state=state, step_max=0)
    module.FRAME_STEP[hdr['BUILD_ID']](ctx)
    assert ctx.step_max == 3 and len(changes) == 1 and changes[0][0] == 0x204a50
    # Exactly one branch in the existing image changes. No old rewrite is moved.
    check = bytearray(data)
    for at, before, after, _ in changes:
        assert check[at:at + len(bytes.fromhex(after))].hex() == after
        check[at:at + len(bytes.fromhex(before))] = bytes.fromhex(before)
    assert bytes(check) == old
    stub = struct.pack('<' + 'I' * len(words), *words)
    data.extend(stub)
    main.write_bytes(data)
    fixups.write_text(fixups.read_text() + f'# FPS_STATE_OFF\t{state:#x}\n# STEP_MAX\t3\n')
    with cuts.open('a') as f:
        for at, before, after, reason in changes: f.write(f'{at:#x}\t{before}\t{after}\t{reason}\n')
    return {'main_build_id': hdr['BUILD_ID'], 'state_offset': state, 'step_max': 3,
            'stub_offset': start, 'stub_length': len(stub), 'changes': changes,
            'original_image_sha256': hashlib.sha256(old).hexdigest(),
            'patched_image_sha256': hashlib.sha256(data).hexdigest(),
            'patch_source_sha256': hashlib.sha256(Path(patch_source).read_bytes()).hexdigest(),
            'all_other_existing_module_bytes_unchanged': True}
