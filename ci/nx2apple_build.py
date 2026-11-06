"""Build game frameworks and a shared NX2Apple IPA using private release assets."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path.cwd()
NX = ROOT / 'nx2apple'
ENGINE = NX / 'engine'
DATA = ROOT / 'private-build'
REPO = os.environ['PRIVATE_REPO']
MODE = os.environ['BUILD_GAME']
CONFIG = {
    'odyssey': ('0100000000010000', 'rom-odyssey-1.4.1', 'nx2apple-odyssey-1.4.1'),
    'party': ('010036B0034E4000', 'rom-party-1.1.0', 'nx2apple-party-1.1.0-experimental'),
}
CHUNK = 1024 ** 3

def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)

def capture(args):
    return subprocess.check_output([str(a) for a in args], text=True)

def download(tag, name, directory):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise ValueError('Unsafe asset name')
    directory.mkdir(parents=True, exist_ok=True)
    run(['gh', 'release', 'download', tag, '--repo', REPO, '--pattern', name, '--dir', directory, '--clobber'])
    return directory / name

def restore_files(tag, manifest_name, directory, wanted=None):
    cache = directory / '.download'
    manifest = json.loads(download(tag, manifest_name, cache).read_text())
    if manifest['schema'] != 1:
        raise ValueError('Unsupported manifest schema')
    for item in manifest['files']:
        name = item['name']
        if wanted is not None and name not in wanted:
            continue
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
            raise ValueError('Unsafe output name')
        hasher = hashlib.sha256()
        with (directory / name).open('wb') as output:
            for part in item['parts']:
                src = download(tag, part['name'], cache)
                part_hash = hashlib.sha256()
                with src.open('rb') as stream:
                    while block := stream.read(8 * 1024 * 1024):
                        part_hash.update(block)
                        hasher.update(block)
                        output.write(block)
                if src.stat().st_size != part['size'] or part_hash.hexdigest() != part['sha256']:
                    raise ValueError('Part checksum mismatch: ' + part['name'])
                src.unlink()
        if (directory / name).stat().st_size != item['size'] or hasher.hexdigest() != item['sha256']:
            raise ValueError('File checksum mismatch: ' + name)
        print('Verified input:', name, item['size'], flush=True)
    return manifest

def logged(args, log, env=None):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as output:
        process = subprocess.Popen([str(a) for a in args], stdout=output, stderr=subprocess.STDOUT, env=env)
        started = time.monotonic()
        while process.poll() is None:
            time.sleep(30)
            size = log.stat().st_size
            with log.open('rb') as source:
                source.seek(max(0, size - 8192))
                tail = source.read().decode(errors='replace')
            progress = re.findall(r'compiling: (\d+)%, ([\d,]+)/([\d,]+) shaders', tail)
            suffix = f'; shaders {progress[-1][1]}/{progress[-1][2]} ({progress[-1][0]}%)' if progress else ''
            print(f'{log.stem}: {int(time.monotonic()-started)} seconds; private log {size} bytes{suffix}', flush=True)
        if process.returncode:
            raise RuntimeError(f'{log.stem} failed ({process.returncode}); see private diagnostic archive')

def release(tag):
    r = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPO], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode:
        run(['gh', 'release', 'create', tag, '--repo', REPO, '--title', tag,
             '--notes', 'Private NX2Apple build from Actions run ' + os.environ.get('GITHUB_RUN_ID', '')])

def upload_file(tag, path, prefix=None):
    prefix = prefix or path.name
    item = {'name': path.name, 'size': path.stat().st_size, 'parts': []}
    whole = hashlib.sha256()
    with path.open('rb') as source:
        index = 0
        while source.tell() < item['size']:
            name = f'{prefix}.part_{index:02d}'
            part = DATA / name
            digest = hashlib.sha256()
            with part.open('wb') as output:
                left = CHUNK
                while left and (block := source.read(min(left, 8 * 1024 * 1024))):
                    output.write(block)
                    digest.update(block)
                    whole.update(block)
                    left -= len(block)
            run(['gh', 'release', 'upload', tag, part, '--repo', REPO, '--clobber'])
            item['parts'].append({'name': name, 'size': part.stat().st_size, 'sha256': digest.hexdigest()})
            part.unlink()
            index += 1
    item['sha256'] = whole.hexdigest()
    return item

def diagnostics(tag):
    archive = DATA / 'diagnostics.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for path in DATA.rglob('*'):
            if path.is_file() and (path.suffix == '.log' or path.name == 'survey.md'):
                tar.add(path, arcname=path.relative_to(DATA))
    run(['gh', 'release', 'upload', tag, archive, '--repo', REPO, '--clobber'])

def wait_for_inputs(tag):
    deadline = time.monotonic() + 3600
    while True:
        result = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPO, '--json', 'assets',
                                 '--jq', '[.assets[].name] | index("manifest.json") != null'],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if result.returncode == 0 and result.stdout.strip() == 'true':
            return
        if time.monotonic() > deadline:
            raise TimeoutError('Private input upload has not completed')
        print('Waiting for the verified private input manifest...', flush=True)
        time.sleep(30)

def build():
    if MODE not in (*CONFIG, 'party-capture', 'combined'):
        raise ValueError('Unknown game')
    device_capture = MODE == 'party-capture'
    game_mode = 'party' if device_capture else MODE
    if capture(['gh', 'api', 'repos/' + REPO, '--jq', '.private']).strip() != 'true':
        raise RuntimeError('Assets destination is not private')
    DATA.mkdir(parents=True, exist_ok=True)
    if game_mode in CONFIG:
        wait_for_inputs(CONFIG[game_mode][1])
    tag = 'nx2apple-party-device-capture' if device_capture else CONFIG[game_mode][2] if game_mode in CONFIG else 'nx2apple-mario-library'
    release(tag)
    try:
        frameworks = []
        outputs = []
        if MODE == 'combined':
            for key, (tid, _, source_tag) in CONFIG.items():
                dest = DATA / key
                m = restore_files(source_tag, 'output-manifest.json', dest, {'framework.tar.gz'})
                if m['nx2apple_commit'] != capture(['git', '-C', NX, 'rev-parse', 'HEAD']).strip():
                    raise ValueError('Frameworks were built with a different NX2Apple revision')
                with tarfile.open(dest / 'framework.tar.gz') as tar:
                    tar.extractall(dest / 'frameworks', filter='data')
                frameworks.append(dest / 'frameworks' / f'DmGame{tid}.framework')
        else:
            tid, source_tag, _ = CONFIG[game_mode]
            inputs = DATA / 'inputs'
            m = restore_files(source_tag, 'manifest.json', inputs)
            if m['title_id'] != tid or {f['name'] for f in m['files']} - {'base.nsp', 'base.nsz', 'update.nsp', 'update.nsz'}:
                raise ValueError('Wrong game inputs')
            keys = download('rom-zelda-botw', 'prod.keys', DATA / 'keys')
            work = inputs / 'build'
            fwdir = inputs / 'frameworks'
            entry = [sys.executable, ENGINE / 'scripts/build_ipa_from_nsp.py']
            extra = []
            if device_capture:
                request = DATA / 'device-capture-request.json'
                entry = [sys.executable, ROOT / 'ci/nx2apple_capture.py', '--bootstrap-device-capture',
                         '--engine', ENGINE, '--request', request]
                env_file = DATA / 'device-capture.env'
                env_file.write_text(f'DM_GLSLC_CAPTURE=games/{tid}/glslc_capture\nDM_NVN_COUNT=1\n')
                extra = ['--env-file', env_file, '--app-name', 'Super Mario Party (diagnostic capture)']
            logged([*entry, inputs, '-k', keys,
                    '--title-id', tid, '--work-dir', work, '--shell-framework', fwdir,
                    '--romfs', 'separate', '--gpu', 'none', '--language', 'zh-Hans', '--progress', 'lines', *extra],
                   DATA / 'game-build.log')
            framework = fwdir / f'DmGame{tid}.framework'
            if not (framework / 'game.manifest.json').is_file():
                raise RuntimeError('Game framework missing')
            frameworks.append(framework)
            archive = DATA / 'framework.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                tar.add(framework, arcname=framework.name)
            outputs.append(archive)
            images = list((work / 'extract').rglob('RomFS.img'))
            if len(images) != 1:
                raise RuntimeError(f'Expected one game RomFS, found {len(images)}')
            outputs.append(images[0])
            if device_capture:
                # Preserve the expensive offline shader work for the full build
                # after the iPad returns the missing runtime compiler outputs.
                cache_archive = DATA / 'shader-cache.tar.gz'
                with tarfile.open(cache_archive, 'w:gz', compresslevel=1) as tar:
                    tar.add(work / 'shader', arcname='shader')
                outputs.extend([cache_archive, request])
                old = DATA / 'odyssey'
                previous = restore_files(CONFIG['odyssey'][2], 'output-manifest.json', old, {'framework.tar.gz'})
                if previous['nx2apple_commit'] != capture(['git', '-C', NX, 'rev-parse', 'HEAD']).strip():
                    raise ValueError('Odyssey framework revision differs from the diagnostic shell')
                with tarfile.open(old / 'framework.tar.gz') as tar:
                    tar.extractall(old / 'frameworks', filter='data')
                frameworks.insert(0, old / 'frameworks/DmGame0100000000010000.framework')
        shell = DATA / 'shell'
        env = dict(os.environ, SHELL_DIR=str(shell), DM_SHELL_FRAMEWORKS='\n'.join(map(str, frameworks)),
                   DM_SHELL_PACKAGE=str(NX / 'frontend-ios'), DM_BUNDLE_ID='org.nxnative.shell',
                   FONT_PY=sys.executable)
        if MODE != 'combined' and (DATA / 'inputs/build/profile').exists():
            env['DM_GAMES_DIR'] = str(DATA / 'inputs/build/profile')
        logged(['bash', ENGINE / 'dm/ios/tools/build_shell.sh', '--build'], DATA / 'shell-build.log', env)
        app = shell / 'dd/Build/Products/Release-iphoneos/DmShell.app'
        run(['xattr', '-cr', app])
        run(['codesign', '--force', '--sign', '-', '--entitlements', shell / 'Gen/DmShell.entitlements', app / 'DmShell'])
        payload = DATA / 'stage/Payload'
        payload.mkdir(parents=True, exist_ok=True)
        run(['ditto', app, payload / 'DmShell.app'])
        ipa = DATA / 'NX2Apple.ipa'
        run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', payload, ipa])
        outputs.append(ipa)
        result = {'schema': 1, 'mode': MODE, 'nx2apple_commit': capture(['git', '-C', NX, 'rev-parse', 'HEAD']).strip(),
                  'engine_pin': (NX / '.upstream-ref').read_text().strip(), 'bundle_id': 'org.nxnative.shell',
                  'run_id': os.environ.get('GITHUB_RUN_ID'), 'files': []}
        if device_capture:
            result['capture_request'] = json.loads(request.read_text())
        for path in outputs:
            result['files'].append(upload_file(tag, path))
        manifest = DATA / 'output-manifest.json'
        manifest.write_text(json.dumps(result, indent=2) + '\n')
        run(['gh', 'release', 'upload', tag, manifest, '--repo', REPO, '--clobber'])
        print('BUILD_COMPLETE:', tag, flush=True)
    finally:
        diagnostics(tag)

if __name__ == '__main__':
    build()
