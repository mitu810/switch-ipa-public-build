"""Bootstrap an incomplete iPad capture app, or replay its validated outputs.

The normal builder boots an NVN game on the build Mac to collect GLSLC output.
Our cloud Mac aborts in mtl_init because Apple Paravirtual has no Metal 4.
The iPad host contains the same hle_glslc.c capture hook and has a real M4 GPU.
Only the diagnostic bootstrap mode defers that boot. Offline shaders are still
split, translated and checked normally. Missing runtime shaders remain an open
limitation until captures are returned and the normal builder consumes them.
Replay stages real device outputs and invokes the original capture check.
No engine or shader-translation code is changed.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

TITLE_ID = '010036B0034E4000'
MAIN_BUILD_ID = 'fb321c86b35aa9755d6e42baa5e5d540'


def check_build(builder, dirs, pkg_ver, game):
    if game.id.upper() != TITLE_ID:
        raise builder.BuildError('Device capture is restricted to the surveyed Party build')
    with (dirs[pkg_ver] / 'ExeFS/main').open('rb') as source:
        header = source.read(0x60)
    if len(header) != 0x60 or header[:4] != b'NSO0' or header[0x40:0x50].hex() != MAIN_BUILD_ID:
        raise builder.BuildError('Device capture main build does not match the failed cloud survey')


def install_bootstrap_hook(builder, request_path):
    def defer(repo, dirs, pkg_ver, patched, work, env, logs, game):
        check_build(builder, dirs, pkg_ver, game)
        if builder.glslc_wanted(game):
            raise builder.BuildError('A registered capture set must use the normal builder')
        request = {
            'schema': 1, 'diagnostic_only': True, 'title_id': TITLE_ID,
            'main_build_id': MAIN_BUILD_ID, 'package_version': pkg_ver,
            'runtime_capture_complete': False,
            'capture_directory': f'Documents/games/{TITLE_ID}/glslc_capture',
            'reason': 'Cloud Apple Paravirtual GPU lacks Metal 4; capture on physical iPad',
        }
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps(request, indent=2) + '\n')
        builder.PROG.stage('GLSLC capture: deferred to physical iPad')
        builder.say('[RED] DIAGNOSTIC BUILD ONLY: runtime shaders have not been captured; this is not a completed game port')
        builder.PROG.end('capture deferred; offline shader validation remains enabled')
        return None
    builder.capture_glslc = defer
    return defer


def install_replay_hook(builder, request_path, capture_directory):
    """Stage validated device output, then call the original capture validator."""
    from nx2apple_capture_data import validate_capture
    original = builder.capture_glslc

    def replay(repo, dirs, pkg_ver, patched, work, env, logs, game):
        check_build(builder, dirs, pkg_ver, game)
        request = json.loads(request_path.read_text())
        record = validate_capture(request, capture_directory)
        if pkg_ver != record['package_version'] or set(dirs) != {pkg_ver}:
            raise builder.BuildError('Device captures are only valid for the recorded package version')
        names = {item['name'] for item in record['files']}
        missing = (builder.glslc_wanted(game) or set()) - names
        if missing:
            raise builder.BuildError('Device capture lacks required outputs: ' + ', '.join(sorted(missing)))
        destination = dirs[pkg_ver] / 'glslc_capture'
        if destination.exists():
            # Never mix a previous capture with this device run or discard it.
            raise builder.BuildError('Capture destination already exists; use a fresh extraction')
        destination.mkdir()
        for item in record['files']:
            shutil.copy2(capture_directory / item['name'], destination / item['name'])
        builder.say(f'Staged {len(names)} validated device outputs; running the normal capture check')
        return original(repo, dirs, pkg_ver, patched, work, env, logs, game)

    builder.capture_glslc = replay
    return replay


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--bootstrap-device-capture', action='store_true')
    mode.add_argument('--device-captures', type=Path)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    args, rest = parser.parse_known_args(argv)
    sys.path.insert(0, str(args.engine.resolve() / 'scripts'))
    import build_ipa_from_nsp as builder
    if args.bootstrap_device_capture:
        install_bootstrap_hook(builder, args.request.resolve())
    else:
        install_replay_hook(builder, args.request.resolve(), args.device_captures.resolve())
    return builder.cli(rest)


if __name__ == '__main__':
    sys.exit(main())
