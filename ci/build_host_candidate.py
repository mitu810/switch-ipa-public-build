"""Rebuild only the pinned Bananza host; keep embedded game modules and tables byte-identical.

Private inputs/outputs stay in the existing private release repository. No shader rebuild.
"""
import datetime
import hashlib
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import nx2apple_build as b

TITLE = '040074A01BF12000'
NAME = 'DmGame' + TITLE
TAG = 'nx2apple-bananza-pool-retire-20261115'
BASE_TAG = 'nx2apple-bananza-library-v458752'
ROOT = Path.cwd()
ENGINE = b.ENGINE


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sections(path):
    data = path.read_bytes()
    assert struct.unpack_from('<I', data)[0] == 0xfeedfacf
    pos, out = 32, {}
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        cmd, size = struct.unpack_from('<II', data, pos)
        if cmd == 0x19:
            for i in range(struct.unpack_from('<I', data, pos + 64)[0]):
                at = pos + 72 + 80 * i
                name = data[at:at + 16].split(b'\0')[0].decode()
                segment = data[at + 16:at + 32].split(b'\0')[0].decode()
                address, length, offset = struct.unpack_from('<QQI', data, at + 32)
                out[segment, name] = data[offset:offset + length]
        pos += size
    return out


def run(args, log=None, **kw):
    if log is None:
        return subprocess.run([str(x) for x in args], check=True, **kw)
    with log.open('w') as f:
        return subprocess.run([str(x) for x in args], stdout=f, stderr=subprocess.STDOUT, check=True, **kw)


def test_fence_logic(before, after, folder):
    # Run the production helper with a deterministic commit between its two reads.
    # Foundation supplies the array; only defer_obj_at and atomic loads are observed.
    def helper(source):
        start = source.index('static void pool_gone_release(')
        at = source.index('{', start)
        depth, end = 1, at + 1
        while depth:
            depth += (source[end] == '{') - (source[end] == '}')
            end += 1
        return source[start:end]
    pre = r'''
#import <Foundation/Foundation.h>
#include <stdatomic.h>
#include <stdio.h>
@protocol MTLBuffer @end
static _Atomic uint64_t g_fence;
static _Atomic int g_sub_on;
static _Atomic uint64_t g_sub_open;
static void (*g_pool_gone_between)(void);
static uint64_t observed;
static int interleave;
static void commit_lands(void) {
    __c11_atomic_fetch_add(&g_fence, 1, __ATOMIC_SEQ_CST);
    __c11_atomic_store(&g_sub_on, 0, __ATOMIC_SEQ_CST);
    __c11_atomic_store(&g_sub_open, 0, __ATOMIC_SEQ_CST);
}
static uint64_t watched_load(void *p, size_t size) {
    uint64_t v = size == 8 ? __c11_atomic_load((_Atomic uint64_t *)p, __ATOMIC_SEQ_CST)
                           : __c11_atomic_load((_Atomic int *)p, __ATOMIC_SEQ_CST);
    if (interleave && p == &g_fence) { interleave = 0; commit_lands(); }
    return v;
}
#undef atomic_load
#define atomic_load(p) watched_load((void *)(p), sizeof(*(p)))
static void defer_obj_at(id object, int unref, uint64_t fence) { observed = fence; }
'''
    post = r'''
int main(void) { @autoreleasepool {
    NSMutableArray *a = [NSMutableArray arrayWithObject:[NSObject new]];
    for (int i = 0; i < 3; i++) {
        __c11_atomic_store(&g_fence, 10, __ATOMIC_SEQ_CST);
        __c11_atomic_store(&g_sub_on, i != 0, __ATOMIC_SEQ_CST);
        __c11_atomic_store(&g_sub_open, i ? 11 : 0, __ATOMIC_SEQ_CST);
        g_pool_gone_between = i == 2 ? commit_lands : NULL;
        interleave = i == 2;
        pool_gone_release(a);
        printf("%d %llu\n", i, (unsigned long long)observed);
    }
} return 0; }
'''
    result = {}
    for label, source in [('baseline', before), ('candidate', after)]:
        body = helper(source)
        if label == 'baseline':
            # The old expression implicitly loads g_fence. Spell out the exact
            # read order verified at installed ARM64 PCs 0x49e30/0x49e3c, so
            # the deterministic hook intercepts that otherwise implicit load.
            needle = 'uint64_t fence = g_fence + (atomic_load(&g_sub_on) ? 1 : 0);'
            assert body.count(needle) == 1
            body = body.replace(needle, 'uint64_t installed_fence = atomic_load(&g_fence);\n    uint64_t fence = installed_fence + (atomic_load(&g_sub_on) ? 1 : 0);')
        p = folder / (label + '.m'); p.write_text(pre + body + post)
        exe = p.with_suffix('')
        run(['xcrun', '--sdk', 'macosx', 'clang', '-fobjc-arc', '-O2', p, '-framework', 'Foundation', '-o', exe])
        result[label] = [int(line.split()[1]) for line in subprocess.check_output([str(exe)], text=True).splitlines()]
    assert result['baseline'] == [10, 11, 10], result
    assert result['candidate'] == [10, 11, 11], result
    result['expected_fences'] = [10, 11, 11]
    result['scope'] = 'Production retirement helper with deterministic commit interleaving; baseline read order matches installed ARM64 instructions; no GPU execution.'
    (folder / 'fence-test.json').write_text(json.dumps(result, indent=2) + '\n')
    print('Production helper test: baseline retires one fence early; candidate passes all three schedules.', flush=True)


def main():
    assert b.capture(['gh', 'api', 'repos/' + b.REPO, '--jq', '.private']).strip() == 'true'
    b.DATA.mkdir(exist_ok=True)
    b.release(TAG)
    try:
        original_manifest = b.restore_files(BASE_TAG, 'output-manifest.json', b.DATA / 'base', {'NX2Apple.ipa'})
        assert original_manifest['nx2apple_commit'] == '229a6e782bf4299890a59b384fe65b8516dde673'
        package = b.DATA / 'base/NX2Apple.ipa'
        original = b.DATA / (NAME + '.framework')
        prefix = 'Payload/DmShell.app/Frameworks/' + original.name + '/'
        with zipfile.ZipFile(package) as z:
            for member in z.infolist():
                if not member.filename.startswith(prefix) or member.is_dir(): continue
                rel = Path(member.filename[len(prefix):])
                assert not rel.is_absolute() and '..' not in rel.parts
                p = original / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(z.read(member))
        manifest = json.loads((original / 'game.manifest.json').read_text())
        assert manifest['title_id'] == TITLE and manifest['loader'] == 'FH' and manifest['chain'] == 'b-a5e7fb6e4fa4'
        source = ENGINE / 'dm/gfx/nvn/nvn_mtl.m'
        before = source.read_text()
        patch = ROOT / 'ci/pool-retire-fix.patch'
        run(['git', '-C', ENGINE, 'apply', '--check', patch])
        run(['git', '-C', ENGINE, 'apply', patch])
        after = source.read_text()
        test_fence_logic(before, after, b.DATA)
        # A cold-path diagnostic confirms pool retirement is reached in the game.
        needle = 'uint64_t fence = open ? open : atomic_load(&g_fence);'
        assert after.count(needle) == 1
        source.write_text(after.replace(needle, needle + '\n    static unsigned notices; if (gone.count && notices++ < 4) MLOG("pool retirement fence fix active: %lu buffers, fence %llu, open %llu", (unsigned long)gone.count, (unsigned long long)fence, (unsigned long long)open);'))

        patched = b.DATA / 'patched'; patched.mkdir()
        old_sections = sections(original / NAME)
        module_sections = {}
        for module in ['main', 'sdk']:
            parts = [('__TEXT', '__dm' + module + 'x0'), ('__DM' + module + 'W', '__img'), ('__TEXT', '__dm' + module + 'x1')]
            hdr = dict(line[2:].split() for line in (original / 'dm' / (module + '.fixups.tsv')).read_text().splitlines())
            assert int(hdr['PATCH_OFF'], 16) >= 0
            data = b''.join(old_sections[p] for p in parts)
            assert len(old_sections[parts[0]]) == (int(hdr['TEXT_END'], 16) + 0x3fff) & ~0x3fff
            assert sum(len(old_sections[p]) for p in parts[:2]) == int(hdr['PATCH_OFF'], 16)
            (patched / (module + '.patched.bin')).write_bytes(data)
            module_sections.update({','.join(p): hashlib.sha256(old_sections[p]).hexdigest() for p in parts})
            for p in (original / 'dm').glob(module + '.*.tsv'): shutil.copy2(p, patched / p.name)

        profile = ENGINE / 'dm/games' / TITLE
        shutil.copy2(original / 'dm/shader_map.tsv', profile / 'shader_map.tsv')
        env = dict(os.environ, GAME=TITLE, DM_BUNDLE_ID='org.nxnative.040074a01bf12000',
                   DM_DISPLAY_NAME='Donkey Kong Bananza', DM_APP_VERSION='1.0', DM_TEAM_ID='0000000000', DM_SCHEME_NAME='DmIOS')
        run([sys.executable, ENGINE / 'dm/games/game.py', 'profile', TITLE, b.DATA / 'rebuilt-profile.tsv'], env=env)
        def rows(p): return dict(l.split('\t', 1) for l in p.read_text().splitlines() if l and not l.startswith('#'))
        assert rows(b.DATA / 'rebuilt-profile.tsv') == rows(original / 'profile.tsv'), 'Host profile constants must match the original framework'

        ios = ENGINE / 'dm/ios'; gen = ios / 'Gen'
        gen.mkdir(exist_ok=False)
        config = subprocess.check_output([sys.executable, ios / 'tools/gen_build.py', ENGINE / 'dm/host', gen], env=env, text=True)
        flags = subprocess.check_output([sys.executable, ios / 'tools/gen_embed.py', patched, gen / 'embed.S', 'main', 'sdk'], text=True).strip()
        (gen / 'dm.xcconfig').write_text(config + '\nOTHER_LDFLAGS = $(inherited) $(DM_FRAMEWORKS) ' + flags + '\n')
        # xcodegen validates the sibling app/container targets too, although only
        # DmShellGame is compiled here. Supply their ordinary staging paths.
        (gen / 'framework.xcconfig').write_text('OTHER_LDFLAGS = $(inherited) ' + flags + '\n')
        (gen / 'Assets.xcassets').mkdir()
        shutil.copytree(original / 'dm', gen / 'dm')
        shutil.copytree(original / 'dm', gen / 'framework/dm')
        shutil.copy2(original / 'profile.tsv', gen / 'framework/profile.tsv')
        manifest['built_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        manifest['local_host_patch'] = 'pool-retire-fence-259f4a55665f'
        (gen / 'framework/game.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        run(['xcodegen', 'generate', '-q', '--spec', 'project-framework.yml'], cwd=ios, env=env)
        out = b.DATA / 'xcode'
        run(['xcodebuild', '-project', 'DmIOS.xcodeproj', '-target', 'DmShellGame', '-configuration', 'Release', '-sdk', 'iphoneos',
             'CODE_SIGNING_ALLOWED=NO', 'CODE_SIGNING_REQUIRED=NO', 'CODE_SIGN_IDENTITY=', 'PRODUCT_NAME=' + NAME,
             'PRODUCT_BUNDLE_IDENTIFIER=org.nxnative.040074a01bf12000.game', 'DM_XCODE_OUTPUT_ROOT=' + str(out),
             'SYMROOT=' + str(out / 'Build/Products'), 'OBJROOT=' + str(out / 'Build/Intermediates.noindex')],
             log=b.DATA / 'host-build.log', cwd=ios, env=env)
        fw = out / 'Build/Products/Release-iphoneos' / original.name
        assert fw.is_dir(), 'Candidate framework missing'
        new_sections = sections(fw / NAME)
        for key, digest in module_sections.items(): assert hashlib.sha256(new_sections[tuple(key.split(','))]).hexdigest() == digest, key
        for old in (original / 'dm').rglob('*'):
            if old.is_file(): assert (fw / 'dm' / old.relative_to(original / 'dm')).read_bytes() == old.read_bytes()
        assert (fw / 'profile.tsv').read_bytes() == (original / 'profile.tsv').read_bytes()
        audit = {'schema': 1, 'title_id': TITLE, 'status': 'host_only_candidate_built',
                 'baseline_executable_sha256': sha(original / NAME), 'candidate_executable_sha256': sha(fw / NAME),
                 'embedded_module_sections_sha256_unchanged': module_sections, 'runtime_profile_unchanged': True,
                 'all_runtime_tables_unchanged': True, 'shader_chain_unchanged': manifest['chain'],
                 'upstream_patch': '259f4a55665f7402bbede5bae98bebee36dee1dd', 'run_id': os.environ['GITHUB_RUN_ID'],
                 'fence_test': json.loads((b.DATA / 'fence-test.json').read_text()), 'mine_crash_fix_not_yet_verified': True}
        (b.DATA / 'candidate-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
        candidate = b.DATA / 'candidate-framework.zip'
        with zipfile.ZipFile(candidate, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for p in fw.rglob('*'):
                if p.is_file():z.write(p, p.relative_to(fw.parent))
        outputs = [b.upload_file(TAG, candidate)]
        result = dict(audit, files=outputs)
        p = b.DATA / 'output-manifest.json'; p.write_text(json.dumps(result, indent=2) + '\n')
        run(['gh', 'release', 'upload', TAG, p, b.DATA / 'candidate-audit.json', b.DATA / 'fence-test.json', '--repo', b.REPO, '--clobber'])
        print('Host-only candidate built; embedded modules, profile, runtime tables and shader chain unchanged; private output only.', flush=True)
    finally:
        b.diagnostics(TAG)


if __name__ == '__main__':
    main()
