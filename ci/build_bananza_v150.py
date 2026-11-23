"""Build the one existing Bananza title against the official NX2Apple v1.5.0 engine."""
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

import nx2apple_bananza as baseline
import nx2apple_build as build
from nx2apple_normalize import normalize_dump

TAG = 'nx2apple-bananza-v1.5.0-20261123'
NX_COMMIT = 'f82be32dd41cbb167b66d8a85cedab947415f81b'
ENGINE_PIN = '99224562186a8fdb7e97afb872319a21941ec317'
CHAIN = 'b-df1800562702'
TITLE = baseline.TITLE
VERSION = baseline.VERSION
BASE_ROMFS = '1cdc8582e5fad3cfae47e4ff64556ffb21fd6a60abdb64414e91138f7a104981'
AOC = '73f31100dd860f6821726d53446d552167ff119d8292654ca9b65182f0e99cfd'


def run_logged(args, log):
    baseline.logged(args, log, release_tag=TAG)


def data_record(path, role):
    return {'destination': role, 'size': path.stat().st_size, 'sha256': baseline.sha(path)}


def prepare_input():
    inputs = build.DATA / 'inputs'
    manifest = build.restore_files(baseline.INPUT_TAG, 'manifest.json', inputs, {'game.7z'})
    assert manifest['title_id'] == TITLE and manifest['version'] == VERSION
    archive = inputs / 'game.7z'
    extracted = inputs / 'extracted'
    extracted.mkdir()
    run_logged(['7zz', 'x', archive, '-o' + str(extracted), '-y'], build.DATA / 'archive-extract.log')
    archive.unlink()
    dump = extracted / 'Donkey Kong Bananza'
    baseline.validate_exefs(dump / 'exefs')
    update = dump / 'updates' / str(VERSION)
    update.mkdir(parents=True)
    for name in ('exefs', 'romfs'):
        shutil.move(dump / name, update / name)
    path = build.download(baseline.INPUT_TAG, 'obsolete-product100.json', build.DATA / 'normalization')
    assert baseline.sha(path) == baseline.NORMALIZATION_SHA256
    report = normalize_dump(update / 'romfs', build.DATA / 'obsolete-base-files', json.loads(path.read_text()))
    print('Verified base-file normalization:', report['quarantined_obsolete_files'], 'files', flush=True)
    return dump, report


def main():
    assert build.capture(['gh', 'api', 'repos/' + build.REPO, '--jq', '.private']).strip() == 'true'
    assert build.capture(['git', '-C', build.NX, 'rev-parse', 'HEAD']).strip() == NX_COMMIT
    assert (build.NX / '.upstream-ref').read_text().strip() == ENGINE_PIN
    renderer = build.ENGINE / 'dm/gfx/nvn/nvn_mtl.m'
    assert 'arena_take(' in renderer.read_text()
    assert f'"{CHAIN[2:]}"' in (renderer.parent / 'dmsh_chain.h').read_text()
    build.DATA.mkdir(exist_ok=True)
    build.release(TAG)
    try:
        dump, normalization = prepare_input()
        work = build.DATA / 'game-build'
        out = build.DATA / 'frameworks'
        run_logged([sys.executable, build.ENGINE / 'scripts/build_ipa_from_nsp.py', dump,
                    '--title-id', TITLE, '--work-dir', work, '--shell-framework', out,
                    '--romfs', 'separate', '--gpu', 'none', '--language', 'zh-Hans',
                    '--progress', 'lines'], build.DATA / 'game-build.log')
        framework = out / f'DmGame{TITLE}.framework'
        game = json.loads((framework / 'game.manifest.json').read_text())
        assert game['title_id'] == TITLE and game['package_version'] == f'v{VERSION}'
        assert game['chain'] == CHAIN and game['loader'] == 'FH' and game['gfx_api'] == 'nvn'
        executable = framework / game['framework_executable']
        binary_sha = baseline.sha(executable)
        library = json.loads((out / '.msl-cache' / f'{TITLE}.json').read_text())
        assert library['chain'] == CHAIN and not library['multi_program']
        shader = Path(library['lib_path'])
        shader_asset = build.DATA / f'{CHAIN}.img'
        if shader.is_file():
            assert shader.name == shader_asset.name
            shutil.move(shader, shader_asset)
        else:
            assert shader.is_dir() and shader.name == CHAIN
            build.run([sys.executable, build.ENGINE / 'dm/tools/romfs_image.py', shader, shader_asset])
        archive = build.DATA / 'bananza-framework.tar.gz'
        with tarfile.open(archive, 'w:gz', compresslevel=1) as saved:
            saved.add(framework, arcname=framework.name)
        romfs = data_record(work / f'extract/v{VERSION}/RomFS.img', 'romfs.img')
        aoc = data_record(work / 'aoc/1.img', 'aoc/1.img')
        matches_device_data = romfs['sha256'] == BASE_ROMFS and aoc['sha256'] == AOC
        if not matches_device_data:
            print('Installed data requires a compatibility check before deployment.', flush=True)
        result = {
            'schema': 1, 'title_id': TITLE, 'version': VERSION, 'game': game,
            'nx2apple_commit': NX_COMMIT, 'engine_pin': ENGINE_PIN,
            'engine_commit': build.capture(['git', '-C', build.ENGINE, 'rev-parse', 'HEAD']).strip(),
            'run_id': os.environ['GITHUB_RUN_ID'], 'framework_executable_sha256': binary_sha,
            'files': [build.upload_file(TAG, archive), build.upload_file(TAG, shader_asset)],
            'game_data': [romfs, aoc], 'matches_device_data': matches_device_data,
            'input_normalization': normalization,
        }
        manifest = build.DATA / 'output-manifest.json'
        manifest.write_text(json.dumps(result, indent=2) + '\n')
        build.run(['gh', 'release', 'upload', TAG, manifest, '--repo', build.REPO, '--clobber'])
        print('New engine, framework, and matching shader image built and published privately.', flush=True)
    finally:
        build.diagnostics(TAG)


if __name__ == '__main__':
    main()
