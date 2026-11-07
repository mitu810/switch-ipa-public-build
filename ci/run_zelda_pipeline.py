"""Run the pinned BOTW pipeline, freeing verified CI inputs after extraction."""
import json
import os
import runpy
import sys
from pathlib import Path


def discard_extracted_inputs(directory, aoc):
    expected = Path.cwd() / 'private-build/inputs'
    if os.environ.get('GITHUB_ACTIONS') != 'true' or directory != expected or directory.is_symlink():
        raise ValueError('Input cleanup is restricted to disposable CI input copies')
    proof = json.loads((directory.parent / 'verified-inputs.json').read_text())
    names = {'base.nsp', 'update.nsp', 'dlc1.nsp', 'dlc2.nsp'}
    if proof.get('title_id') != '01007EF00011E000' or {f['name'] for f in proof['files']} != names:
        raise ValueError('Wrong verified BOTW input set')
    if aoc != directory / 'build/aoc' or not all((aoc / n).is_file() for n in ('1.img', '2.img')):
        raise ValueError('DLC extraction has not completed')
    # Validate every candidate before removing any. These copies were already
    # hashed by restore_inputs; the source release assets are retained.
    for item in proof['files']:
        path = directory / item['name']
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item['size'] or not item.get('sha256'):
            raise ValueError('Verified input copy changed')
    for name in sorted(names):
        (directory / name).unlink()
    print('Released verified CI NSP copies after all game and DLC extraction', flush=True)


def main():
    engine_script = Path(sys.argv[1]).resolve()
    arguments = sys.argv[2:]
    directory = Path(arguments[0]).resolve()
    namespace = runpy.run_path(str(engine_script), run_name='ci_zelda_pipeline')
    cli = namespace['cli']
    globals_ = cli.__globals__
    original = globals_['extract_aoc']

    def extracted(*args, **kwargs):
        aoc = original(*args, **kwargs)
        discard_extracted_inputs(directory, aoc)
        return aoc

    # This pinned pipeline calls extract_aoc only after all merged RomFS images,
    # ExeFS modules and HTML have been extracted. Later BOTW stages use those
    # extracted files, not the NSPs (BOTW has no secondary program).
    globals_['extract_aoc'] = extracted
    raise SystemExit(cli(arguments))


if __name__ == '__main__':
    main()
