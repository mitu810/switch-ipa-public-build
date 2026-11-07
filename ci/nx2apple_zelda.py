"""Build BOTW with complete Metal 4 libraries and assemble the three-game shell."""
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
from pathlib import Path

import nx2apple_build as b

TITLE = '01007EF00011E000'
TAG = 'nx2apple-zelda-library-1.9.0'
INPUT_TAG = 'rom-zelda-botw'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(8 * 1024 * 1024):
            h.update(block)
    return h.hexdigest()


def restore_inputs(directory):
    """Legacy NSP assets have authoritative SHA256 per part, without a whole-file manifest."""
    cache = directory / '.download'
    m = json.loads(b.download(INPUT_TAG, 'nx2apple-input-manifest.json', cache).read_text())
    if m.get('schema') != 1 or m.get('title_id') != TITLE:
        raise ValueError('Wrong BOTW input identity')
    wanted = {'base.nsp': ['zelda_botw.part_a' + c for c in 'abcdefgh'],
              'update.nsp': ['update.nsp'], 'dlc1.nsp': ['dlc1.nsp'], 'dlc2.nsp': ['dlc2.nsp']}
    if {f['name'] for f in m['files']} != set(wanted) or len(m['files']) != 4:
        raise ValueError('Incomplete BOTW inputs')
    for item in m['files']:
        if [p['name'] for p in item['parts']] != wanted[item['name']]:
            raise ValueError('Wrong part ordering')
        target = directory / item['name']
        with target.open('wb') as out:
            for part in item['parts']:
                if not re.fullmatch('[0-9a-f]{64}', part['sha256']) or part['size'] <= 0:
                    raise ValueError('Missing authoritative part digest')
                source = b.download(INPUT_TAG, part['name'], cache)
                if source.stat().st_size != part['size'] or sha(source) != part['sha256']:
                    raise ValueError('BOTW input checksum mismatch: ' + part['name'])
                with source.open('rb') as stream:
                    shutil.copyfileobj(stream, out, 8 * 1024 * 1024)
                source.unlink()
        if target.stat().st_size != item['size']:
            raise ValueError('BOTW assembled size mismatch')
        item['sha256'] = sha(target)
        print('Verified private input:', item['name'], flush=True)
    (b.DATA / 'verified-inputs.json').write_text(json.dumps(m, indent=2) + '\n')
    return m


def pairs(path):
    if not path.is_file():
        return set()
    rows = {tuple(line.split()) for line in path.read_text().splitlines() if line.strip() and not line.startswith('#')}
    if any(len(row) != 2 or any(not re.fullmatch('[0-9a-f]{16}', key) for key in row) for row in rows):
        raise ValueError('Malformed shader pairs')
    return rows


def audit(framework):
    sys.path.insert(0, str(b.ENGINE / 'dm/tools'))
    import shader_lib
    m = json.loads((framework / 'game.manifest.json').read_text())
    images = list((framework / 'msl').glob('b-*.img'))
    if len(images) != 1 or images[0].stem != m['chain']:
        raise ValueError('Wrong shader library chain')
    library = shader_lib.view(images[0])
    members = list(library.iterdir())
    names = {f.name for f in members}
    meta = {f.name[:-7]: json.loads(f.read_text()) for f in members if f.name.endswith('_b.json')}
    required = {key + '_bp.metallib' for key, value in meta.items() if isinstance(value.get('pull'), dict)}
    if not required or required - names:
        raise ValueError('Packaged pulling libraries are incomplete')
    table = pairs(framework / 'dm/shader_pairs.tsv') | pairs(framework / 'dm/pso_pairs.tsv')
    if not table or any(key + '_b.metallib' not in names for row in table for key in row):
        raise ValueError('Pair table shader coverage is incomplete')
    if any(meta[vs]['stage'] != 'vs' or meta[ps]['stage'] != 'ps' for vs, ps in table):
        raise ValueError('Wrong shader stages in a pair')
    if list(framework.rglob('*.mtl4a')):
        raise ValueError('Do not package hardware-specific archives')
    return {'title_id': m['title_id'], 'chain': m['chain'], 'pairs': len(table),
            'required_pulling_libraries': len(required), 'missing_pulling_libraries': 0,
            'executable_sha256': sha(framework / m['framework_executable'])}


def previous(tag, title):
    dest = b.DATA / title
    m = b.restore_files(tag, 'output-manifest.json', dest, {'framework.tar.gz'})
    if m['nx2apple_commit'] != b.capture(['git', '-C', b.NX, 'rev-parse', 'HEAD']).strip():
        raise ValueError('Framework from another NX2Apple revision')
    with tarfile.open(dest / 'framework.tar.gz') as source:
        source.extractall(dest / 'frameworks', filter='data')
    return dest / 'frameworks' / f'DmGame{title}.framework', m


def main():
    if b.capture(['gh', 'api', 'repos/' + b.REPO, '--jq', '.private']).strip() != 'true':
        raise ValueError('Output repository must be private')
    b.DATA.mkdir(parents=True, exist_ok=True)
    b.release(TAG)
    try:
        patch = b.ROOT / 'ci/patches/hid-forced-vibration.patch'
        b.run(['git', '-C', b.ENGINE, 'apply', '--check', patch])
        b.run(['git', '-C', b.ENGINE, 'apply', patch])
        tests = b.ENGINE / 'dm/host/tests'
        b.run(['make', '-C', tests, 'GAME=01007EF00011E000', 'build/t_hid', 'build/t_haptics'])
        b.run([tests / 'build/t_hid'], env=dict(os.environ, TMPDIR=str(tests / 'build')))
        b.run([tests / 'build/t_haptics'])
        inputs = b.DATA / 'inputs'
        inputs.mkdir()
        source_manifest = restore_inputs(inputs)
        keys = b.download(INPUT_TAG, 'prod.keys', b.DATA / 'keys')
        work, fwdir = inputs / 'build', inputs / 'frameworks'
        env_file = b.DATA / 'zelda.env'
        env_file.write_text('DM_PSO_BUNDLE=off\n')
        b.logged([sys.executable, b.ENGINE / 'scripts/build_ipa_from_nsp.py', inputs, '-k', keys,
                  '--title-id', TITLE, '--work-dir', work, '--shell-framework', fwdir,
                  '--romfs', 'separate', '--gpu', 'none', '--language', 'zh-Hans', '--progress', 'lines',
                  '--env-file', env_file], b.DATA / 'game-build.log', dict(os.environ, IOS_PULL='1'))
        zelda = fwdir / f'DmGame{TITLE}.framework'
        zm = json.loads((zelda / 'game.manifest.json').read_text())
        if zm['title_id'] != TITLE or zm['display_version'] != '1.9.0':
            raise ValueError('Wrong BOTW version')
        # The iOS framework packer only copies registered profile tables. Include
        # supplemental split output as well, as the Mac packer already does.
        extra = work / 'shader/pso_pairs.tsv'
        target = zelda / 'dm/pso_pairs.tsv'
        if extra.is_file() and not pairs(target):
            shutil.copy2(extra, target)
        settings = {s['env']: s for s in json.loads((zelda / 'dm/launcher.json').read_text())['settings']}
        for key in ('DM_PSO_MTL4', 'DM_PSO_PREBUILD'):
            if settings[key]['default'] != '1':
                raise ValueError('BOTW must default to Metal 4 and prebuild')
        zelda_audit = audit(zelda)
        zelda_archive = b.DATA / 'framework.tar.gz'
        with tarfile.open(zelda_archive, 'w:gz') as t:
            t.add(zelda, arcname=zelda.name)
        aoc = work / 'aoc'
        if not all((aoc / n).is_file() for n in ('1.img', '2.img')):
            raise ValueError('Both BOTW DLC images are required')
        aoc_archive = b.DATA / 'aoc.tar.gz'
        with tarfile.open(aoc_archive, 'w:gz') as t:
            t.add(aoc, arcname='aoc')
        game_data = {'title_id': TITLE, 'version': '1.9.0', 'romfs_identity': zm['romfs_identity'],
                     'legacy_local_romfs_identity': source_manifest['local_romfs_identity'],
                     'aoc': [{'name': p.name, 'size': p.stat().st_size, 'sha256': sha(p)}
                             for p in sorted(aoc.glob('*.img'))]}
        from romfs_image import same_identity
        game_data['local_romfs_reusable'] = same_identity(zm['romfs_identity'], source_manifest['local_romfs_identity'])
        (b.DATA / 'game-data-manifest.json').write_text(json.dumps(game_data, indent=2) + '\n')
        odyssey, om = previous('nx2apple-odyssey-1.4.1-metal4', '0100000000010000')
        pair_source = b.download('nx2apple-odyssey-1.4.1-metal4', 'pso-pairs-source.json', b.DATA / 'odyssey-pairs')
        pair_file = b.download('nx2apple-odyssey-1.4.1-metal4', 'pso_pairs.tsv', pair_source.parent)
        proof = json.loads(pair_source.read_text())
        if str(proof['run_id']) != str(om['run_id']) or sha(pair_file) != proof['pairs_sha256']:
            raise ValueError('Wrong Odyssey supplemental pairs')
        shutil.copy2(pair_file, odyssey / 'dm/pso_pairs.tsv')
        party, pm = previous('nx2apple-party-1.1.0-experimental', '010036B0034E4000')
        if pm.get('host_patch_sha256') != sha(patch):
            raise ValueError('Party framework lacks the tested HID fix')
        frameworks = [odyssey, party, zelda]
        audits = [audit(fw) for fw in frameworks]
        if audits[0]['pairs'] != 32864:
            raise ValueError('Odyssey supplemental coverage lost')
        audit_file = b.DATA / 'metal4-library-audit.json'
        audit_file.write_text(json.dumps(audits, indent=2) + '\n')
        shell = b.DATA / 'shell'
        env = dict(os.environ, SHELL_DIR=str(shell), DM_SHELL_FRAMEWORKS='\n'.join(map(str, frameworks)),
                   DM_SHELL_PACKAGE=str(b.NX / 'frontend-ios'), DM_BUNDLE_ID='org.nxnative.shell', FONT_PY=sys.executable)
        b.logged(['bash', b.ENGINE / 'dm/ios/tools/build_shell.sh', '--build'], b.DATA / 'shell-build.log', env)
        app = shell / 'dd/Build/Products/Release-iphoneos/DmShell.app'
        b.run(['xattr', '-cr', app])
        b.run(['codesign', '--force', '--sign', '-', '--entitlements', shell / 'Gen/DmShell.entitlements', app / 'DmShell'])
        payload = b.DATA / 'stage/Payload'
        payload.mkdir(parents=True)
        b.run(['ditto', app, payload / 'DmShell.app'])
        ipa = b.DATA / 'NX2Apple.ipa'
        b.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', payload, ipa])
        outputs = [zelda_archive, aoc_archive, audit_file, b.DATA / 'game-data-manifest.json', b.DATA / 'verified-inputs.json', ipa]
        if not game_data['local_romfs_reusable']:
            images = list((work / 'extract').rglob('RomFS.img'))
            if len(images) != 1:
                raise ValueError('Expected one replacement BOTW image')
            outputs.append(images[0])
        result = {'schema': 1, 'mode': 'zelda-library', 'nx2apple_commit': b.capture(['git', '-C', b.NX, 'rev-parse', 'HEAD']).strip(),
                  'engine_pin': (b.NX / '.upstream-ref').read_text().strip(), 'bundle_id': 'org.nxnative.shell',
                  'run_id': os.environ.get('GITHUB_RUN_ID'), 'metal4_audits': audits,
                  'game_data': game_data, 'reused_runs': {'odyssey': om['run_id'], 'party': pm['run_id']},
                  'host_patch_sha256': sha(patch),
                  'device_validation': 'deferred_by_user', 'files': []}
        for path in outputs:
            result['files'].append(b.upload_file(TAG, path))
        manifest = b.DATA / 'output-manifest.json'
        manifest.write_text(json.dumps(result, indent=2) + '\n')
        b.run(['gh', 'release', 'upload', TAG, manifest, '--repo', b.REPO, '--clobber'])
        print('BUILD_COMPLETE:', TAG, flush=True)
    finally:
        b.diagnostics(TAG)


if __name__ == '__main__':
    main()
