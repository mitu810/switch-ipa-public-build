"""Build user-supplied Bananza and retain the three installed self-hosted games."""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import nx2apple_build as b

TITLE = '040074A01BF12000'
VERSION = 458752
TAG = 'nx2apple-bananza-library-v458752'
INPUT_TAG = 'rom-bananza-v458752'
NORMALIZATION_SHA256 = '9cb2d12dc23983683b742614774507bb4ce378611d29cec13c0a50cba268c10f'
OLD_COMMIT = '994ede741c0dae6e6ccd1d06a3f177c4f3b9ac61'
OLD_GAMES = [
    ('0100000000010000', 'nx2apple-odyssey-1.4.1-metal4'),
    ('010036B0034E4000', 'nx2apple-party-1.1.0-experimental'),
    ('01007EF00011E000', 'nx2apple-zelda-library-1.9.0'),
]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(8 * 1024 ** 2):
            h.update(block)
    return h.hexdigest()


def validate_exefs(folder):
    d = (folder / 'main.npdm').read_bytes()
    if d[:4] != b'META':
        raise ValueError('Invalid NPDM')
    off, size = struct.unpack_from('<II', d, 0x70)
    if size < 0x18 or d[off:off + 4] != b'ACI0' or struct.unpack_from('<Q', d, off + 0x10)[0] != int(TITLE, 16):
        raise ValueError('Wrong program ID')
    ids = {'main': 'e25c90c1c136638885659fd55bf8a03b', 'sdk': '18265daea2105b31f25346adfbbd09b5'}
    for module, expected in ids.items():
        with (folder / module).open('rb') as source:
            d = source.read(0x60)
        if d[:4] != b'NSO0' or d[0x40:0x50].hex() != expected:
            raise ValueError('Unrecognized Bananza update: ' + module)


def logged(args, log, release_tag=TAG):
    with log.open('wb') as output:
        process = subprocess.Popen([str(a) for a in args], stdout=output, stderr=subprocess.STDOUT,
            env=dict(os.environ, IOS_PULL='1'))
        last = 0
        while process.poll() is None:
            if time.monotonic() - last >= 120:
                free = shutil.disk_usage(b.DATA).free
                progress = b.DATA / 'build-progress.json'
                progress.write_text(json.dumps({'run_id': os.environ['GITHUB_RUN_ID'], 'step': log.stem,
                    'time': time.time(), 'free_bytes': free, 'log_bytes': log.stat().st_size}, indent=2))
                tail = b.DATA / 'build-progress-tail.log'
                with log.open('rb') as source:
                    source.seek(max(0, log.stat().st_size - 32768))
                    tail.write_bytes(source.read())
                try:
                    subprocess.run(['gh', 'release', 'upload', release_tag, progress, tail, '--repo', b.REPO, '--clobber'],
                        check=False, timeout=45, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except subprocess.TimeoutExpired:
                    pass
                print(f'{log.stem}: {log.stat().st_size} private log bytes; {free / 2**30:.1f} GiB free', flush=True)
                if free < 3 * 1024 ** 3:
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise RuntimeError('Stopped before disk exhaustion')
                last = time.monotonic()
            time.sleep(10)
        if process.returncode:
            raise RuntimeError(f'{log.stem} failed ({process.returncode}); private diagnostics contain the log')


def existing_frameworks():
    # The installed three-game package also contains Odyssey's supplemental PSO
    # table. Restore that exact package rather than earlier per-game archives.
    tag = 'nx2apple-zelda-library-1.9.0'
    dest = b.DATA / 'existing-library'
    m = b.restore_files(tag, 'output-manifest.json', dest, {'NX2Apple.ipa'})
    if m['nx2apple_commit'] != OLD_COMMIT:
        raise ValueError('Unexpected revision for an existing game')
    frameworks, records = [], []
    with zipfile.ZipFile(dest / 'NX2Apple.ipa') as source:
        for title, _ in OLD_GAMES:
            name = f'DmGame{title}.framework'
            prefix = 'Payload/DmShell.app/Frameworks/' + name + '/'
            framework = dest / 'frameworks' / name
            for item in source.infolist():
                if not item.filename.startswith(prefix) or item.is_dir():
                    continue
                relative = Path(item.filename[len(prefix):])
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('Unsafe framework resource path')
                target = framework / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as stream, target.open('wb') as output:
                    shutil.copyfileobj(stream, output, 8 * 1024 ** 2)
            manifest = json.loads((framework / 'game.manifest.json').read_text())
            if manifest['title_id'] != title or manifest['loader'] != 'FH':
                raise ValueError('Existing framework is not self-contained')
            executable = framework / manifest['framework_executable']
            executable.chmod(0o755)
            frameworks.append(framework)
            records.append({'title_id': title, 'source_tag': tag, 'manifest': manifest,
                'executable_sha256': sha(executable),
                'pso_pairs_sha256': sha(framework / 'dm/pso_pairs.tsv') if (framework / 'dm/pso_pairs.tsv').is_file() else None})
    (dest / 'NX2Apple.ipa').unlink()
    return frameworks, records


def main():
    if b.capture(['gh', 'api', 'repos/' + b.REPO, '--jq', '.private']).strip() != 'true':
        raise ValueError('Game input and output repository must remain private')
    b.DATA.mkdir(exist_ok=True)
    b.release(TAG)
    try:
        b.wait_for_inputs(INPUT_TAG)
        inputs = b.DATA / 'inputs'
        manifest = b.restore_files(INPUT_TAG, 'manifest.json', inputs, {'game.7z'})
        if manifest['title_id'] != TITLE or manifest['version'] != VERSION:
            raise ValueError('Wrong input identity')
        archive = inputs / 'game.7z'
        extract = inputs / 'extracted'
        extract.mkdir()
        logged(['7zz', 'x', archive, '-o' + str(extract), '-y'], b.DATA / 'archive-extract.log')
        archive.unlink()
        dump = extract / 'Donkey Kong Bananza'
        validate_exefs(dump / 'exefs')
        update = dump / 'updates' / str(VERSION)
        update.mkdir(parents=True)
        for name in ['exefs', 'romfs']:
            shutil.move(str(dump / name), update / name)
        normalization_file = b.download(INPUT_TAG, 'obsolete-product100.json', b.DATA / 'normalization')
        if sha(normalization_file) != NORMALIZATION_SHA256:
            raise ValueError('Unexpected normalization manifest digest')
        from nx2apple_normalize import normalize_dump
        normalization = normalize_dump(update / 'romfs', b.DATA / 'obsolete-base-files',
            json.loads(normalization_file.read_text()))
        print('Verified and quarantined obsolete base files:', normalization['quarantined_obsolete_files'], flush=True)
        work, fwdir = b.DATA / 'game-build', b.DATA / 'frameworks'
        logged([sys.executable, b.ENGINE / 'scripts/build_ipa_from_nsp.py', dump,
            '--title-id', TITLE, '--work-dir', work, '--shell-framework', fwdir,
            '--romfs', 'separate', '--gpu', 'none', '--language', 'zh-Hans', '--progress', 'lines'],
            b.DATA / 'game-build.log')
        framework = fwdir / f'DmGame{TITLE}.framework'
        game = json.loads((framework / 'game.manifest.json').read_text())
        if game['title_id'] != TITLE or game['loader'] != 'FH' or game['package_version'] != 'v458752':
            raise ValueError('Wrong new game framework')
        archive = b.DATA / 'framework.tar.gz'
        with tarfile.open(archive, 'w:gz', compresslevel=1) as output:
            output.add(framework, arcname=framework.name)
        outputs = [b.upload_file(TAG, archive)]
        archive.unlink()
        frameworks, preserved = existing_frameworks()
        frameworks.append(framework)
        shell = b.DATA / 'shell'
        env = dict(os.environ, SHELL_DIR=str(shell), DM_ENGINE_DIR=str(b.ENGINE),
            DM_SHELL_FRAMEWORKS='\n'.join(str(f) for f in frameworks), DM_BUNDLE_ID='org.nxnative.shell',
            DM_APP_VERSION='1.4.0')
        with (b.DATA / 'shell-build.log').open('w') as output:
            b.run(['bash', b.NX / 'frontend-ios/Shell/build_shell.sh', '--build'], env=env,
                stdout=output, stderr=subprocess.STDOUT)
        app = shell / 'dd/Build/Products/Release-iphoneos/DmShell.app'
        # Shader resources are external data in current NX2Apple unsigned exports.
        # Keep every executable and profile unchanged; side-loading stays below ZIP32 limits.
        external = []
        for fw in frameworks:
            staged = app / 'Frameworks' / fw.name
            m = json.loads((staged / 'game.manifest.json').read_text())
            expected = next((x['executable_sha256'] for x in preserved if x['title_id'] == m['title_id']),
                sha(fw / m['framework_executable']))
            if sha(staged / m['framework_executable']) != expected:
                raise ValueError('An embedded executable changed')
            msl = staged / 'msl'
            files = [p for p in msl.rglob('*') if p.is_file()]
            if files:
                (staged / 'external-msl.json').write_text(json.dumps({'schema': 1,
                    'files': len(files), 'bytes': sum(p.stat().st_size for p in files)}))
                external.append({'title_id': m['title_id'], 'source_framework': fw.name,
                    'files': [{'path': str(p.relative_to(msl)), 'size': p.stat().st_size,
                               'sha256': sha(p)} for p in files]})
                shutil.rmtree(msl)
        # Preserve the Increased Memory Limit request for signers that support it.
        b.run(['xattr', '-cr', app])
        b.run(['codesign', '--force', '--sign', '-', '--entitlements', shell / 'Gen/DmShell.entitlements', app / 'DmShell'])
        payload = b.DATA / 'package/Payload'
        payload.mkdir(parents=True)
        shutil.move(str(app), payload / app.name)
        ipa = b.DATA / 'NX2Apple.ipa'
        with zipfile.ZipFile(ipa, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=False) as z:
            for path in sorted((b.DATA / 'package').rglob('*')):
                if path.is_file():
                    z.write(path, path.relative_to(b.DATA / 'package'))
        with zipfile.ZipFile(ipa) as z:
            if z.testzip() is not None:
                raise ValueError('IPA ZIP verification failed')
            titles = {json.loads(z.read(n))['title_id'] for n in z.namelist() if n.endswith('/game.manifest.json')}
            if titles != {TITLE, *(x[0] for x in OLD_GAMES)}:
                raise ValueError('The IPA does not contain all four games')
        outputs.append(b.upload_file(TAG, ipa))
        data = [{'source': str(p.relative_to(work)), 'destination': 'romfs.img', 'size': p.stat().st_size,
                 'sha256': sha(p)} for p in [work / 'extract/v458752/RomFS.img']]
        for p in sorted((work / 'aoc').glob('*.img')):
            data.append({'source': str(p.relative_to(work)), 'destination': 'aoc/' + p.name,
                'size': p.stat().st_size, 'sha256': sha(p)})
        result = {'schema': 1, 'title_id': TITLE, 'version': VERSION, 'game': game,
            'nx2apple_commit': b.capture(['git', '-C', b.NX, 'rev-parse', 'HEAD']).strip(),
            'engine_commit': b.capture(['git', '-C', b.ENGINE, 'rev-parse', 'HEAD']).strip(),
            'run_id': os.environ['GITHUB_RUN_ID'], 'files': outputs, 'game_data': data,
            'preserved_games': preserved, 'external_shaders': external, 'input_normalization': normalization,
            'all_four_games_verified': True}
        path = b.DATA / 'output-manifest.json'
        path.write_text(json.dumps(result, indent=2) + '\n')
        b.run(['gh', 'release', 'upload', TAG, path, '--repo', b.REPO, '--clobber'])
        print('Four-game shared IPA built; all outputs remain in the private repository.', flush=True)
    finally:
        b.diagnostics(TAG)


if __name__ == '__main__':
    main()
