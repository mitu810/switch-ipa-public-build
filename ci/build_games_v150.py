"""Build individual existing game frameworks using the stock v1.5.0 engine."""
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

import nx2apple_build as build
import nx2apple_zelda as legacy_zelda
from nx2apple_capture import MAIN_BUILD_ID
from nx2apple_capture_data import validate_capture

NX_COMMIT = 'f82be32dd41cbb167b66d8a85cedab947415f81b'
ENGINE_PIN = '99224562186a8fdb7e97afb872319a21941ec317'
CHAIN = 'b-df1800562702'
GAMES = {
    'odyssey': ('0100000000010000', 'v393216', '1.4.1', 'rom-odyssey-1.4.1'),
    'party': ('010036B0034E4000', 'v131072', '1.1.0', 'rom-party-1.1.0'),
    'zelda': ('01007EF00011E000', 'v1114112', '1.9.0', 'rom-zelda-botw'),
}


def restore_game(mode, title, source_tag, inputs):
    if mode == 'zelda':
        legacy_zelda.restore_inputs(inputs)
    else:
        manifest = build.restore_files(source_tag, 'manifest.json', inputs)
        assert manifest['title_id'] == title
        assert {f['name'] for f in manifest['files']} <= {'base.nsp', 'base.nsz', 'update.nsp', 'update.nsz'}


def command_for(mode, title):
    builder = build.ENGINE / 'scripts/build_ipa_from_nsp.py'
    if mode == 'odyssey':
        return [sys.executable, builder], []
    override = build.DATA / f'{mode}.env'
    if mode == 'zelda':
        override.write_text('DM_PSO_BUNDLE=off\n')
        return [sys.executable, build.ROOT / 'ci/run_zelda_pipeline.py', builder], ['--env-file', override]

    # Reuse the same validated iPad shader captures that built the installed Party.
    folder = build.DATA / 'device-inputs'
    captured = build.restore_files('party-device-captures-1.1.0', 'manifest.json', folder,
        {'device-captures.tar.gz', 'device-capture-request.json', 'capture-manifest.json'})
    assert captured['title_id'] == title
    with tarfile.open(folder / 'device-captures.tar.gz') as archive:
        archive.extractall(folder, filter='data')
    request = folder / 'device-capture-request.json'
    captures = folder / 'glslc_capture'
    metadata = json.loads(request.read_text())
    saved = json.loads((folder / 'capture-manifest.json').read_text())
    assert validate_capture(metadata, captures) == saved
    assert saved['main_build_id'] == MAIN_BUILD_ID and saved['package_version'] == 'v1'
    # The new extractor reports the CNMT update version. The replay tool still
    # checks the exact main module build ID before using any captured shaders.
    request = folder / 'v131072-capture-request.json'
    request.write_text(json.dumps({**metadata, 'package_version': 'v131072'}) + '\n')
    override.write_text(f'DM_GLSLC_CAPTURE=games/{title}/glslc_capture\nDM_NVN_COUNT=1\n')
    return ([sys.executable, build.ROOT / 'ci/nx2apple_capture.py', '--device-captures', captures,
            '--engine', build.ENGINE, '--request', request],
            ['--env-file', override, '--app-name', 'Super Mario Party (experimental)'])


def stage_shader(folder, title):
    meta = json.loads((folder / '.msl-cache' / f'{title}.json').read_text())
    assert meta['chain'] == CHAIN and meta['gfx_api'] == 'nvn' and not meta['multi_program']
    library = Path(meta['lib_path'])
    target = build.DATA / f'{CHAIN}.img'
    if library.is_file():
        assert library.name == target.name
        shutil.move(library, target)
    else:
        assert library.is_dir() and library.name == CHAIN
        build.run([sys.executable, build.ENGINE / 'dm/tools/romfs_image.py', library, target])
    sys.path.insert(0, str(build.ENGINE / 'dm/tools'))
    import shader_lib
    records = list(shader_lib.view(target).iterdir())
    present = {path.name for path in records}
    pulling = {p.name[:-7] + '_bp.metallib' for p in records if p.name.endswith('_b.json')
               and isinstance(json.loads(p.read_text()).get('pull'), dict)}
    assert pulling and pulling <= present
    stamp = next(p for p in records if p.name == 'inputs.tsv').read_text()
    assert f'game\t{title}' in stamp
    return target, len(pulling)


def output_data(work, version):
    main = work / 'extract' / version / 'RomFS.img'
    imgs = [('romfs.img', main), *(('aoc/' + p.name, p) for p in sorted((work / 'aoc').glob('*.img')))]
    return [{'destination': name, 'size': p.stat().st_size, 'sha256': legacy_zelda.sha(p)} for name, p in imgs]


def main():
    mode = build.MODE
    title, version, display, source_tag = GAMES[mode]
    tag = f'nx2apple-{mode}-v1.5.0-20261123'
    assert build.capture(['gh', 'api', 'repos/' + build.REPO, '--jq', '.private']).strip() == 'true'
    assert build.capture(['git', '-C', build.NX, 'rev-parse', 'HEAD']).strip() == NX_COMMIT
    assert (build.NX / '.upstream-ref').read_text().strip() == ENGINE_PIN
    assert f'"{CHAIN[2:]}"' in (build.ENGINE / 'dm/gfx/nvn/dmsh_chain.h').read_text()
    build.DATA.mkdir(exist_ok=True)
    build.release(tag)
    try:
        inputs = build.DATA / 'inputs'
        inputs.mkdir()
        restore_game(mode, title, source_tag, inputs)
        keys = build.download('rom-zelda-botw', 'prod.keys', build.DATA / 'keys')
        work, out = inputs / 'build', build.DATA / 'frameworks'
        command, extra = command_for(mode, title)
        legacy_zelda.logged([*command, inputs, '-k', keys, '--title-id', title,
            '--work-dir', work, '--shell-framework', out, '--romfs', 'separate', '--gpu', 'none',
            '--language', 'zh-Hans', '--progress', 'lines', *extra],
            build.DATA / 'game-build.log', dict(os.environ, IOS_PULL='1'), release_tag=tag)

        fw = out / f'DmGame{title}.framework'
        manifest = json.loads((fw / 'game.manifest.json').read_text())
        assert manifest['title_id'] == title and manifest['package_version'] == version
        assert manifest['display_version'] == display and manifest['chain'] == CHAIN
        assert manifest['loader'] == 'FH' and manifest['gfx_api'] == 'nvn'
        binary = fw / manifest['framework_executable']
        shader, pulling_count = stage_shader(out, title)
        archive = build.DATA / 'framework.tar.gz'
        with tarfile.open(archive, 'w:gz', compresslevel=1) as tar:
            tar.add(fw, arcname=fw.name)
        result = {
            'schema': 1, 'mode': mode, 'title_id': title, 'game': manifest,
            'nx2apple_commit': NX_COMMIT, 'engine_pin': ENGINE_PIN,
            'engine_commit': build.capture(['git', '-C', build.ENGINE, 'rev-parse', 'HEAD']).strip(),
            'run_id': os.environ['GITHUB_RUN_ID'], 'framework_executable_sha256': legacy_zelda.sha(binary),
            'game_data': output_data(work, version), 'metal4_pulling_libraries': pulling_count,
            'files': [build.upload_file(tag, archive), build.upload_file(tag, shader)],
        }
        report = build.DATA / 'output-manifest.json'
        report.write_text(json.dumps(result, indent=2) + '\n')
        build.run(['gh', 'release', 'upload', tag, report, '--repo', build.REPO, '--clobber'])
        print('Verified new framework and matching shaders published privately.', flush=True)
    finally:
        build.diagnostics(tag)


if __name__ == '__main__':
    main()
