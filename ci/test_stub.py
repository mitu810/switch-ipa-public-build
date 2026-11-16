"""Execute the actual ARM64 adapter on synthetic controller/state objects."""
import importlib.util
import json
import random
import struct
import sys
import types
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X19, UC_ARM64_REG_X20, UC_ARM64_REG_SP

D = Path(__file__).resolve().parent
source = Path(sys.argv[1]) if len(sys.argv) > 1 else D.parent / 'engine-analysis/dm/games/040074A01BF12000/patches.py'
tools_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else D.parent / 'engine-analysis/dm/tools'
sys.path.insert(0, str(tools_dir))
spec = importlib.util.spec_from_file_location('adapter_under_test', source)
adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
start, state, ctrl, stop = 0x2900000, 0x2814000, 0x3100000, 0x204a58
words, sites, changes = [], [], []
ctx = types.SimpleNamespace(here=lambda: start + len(words) * 4, emit=words.append,
                            site=lambda *a: sites.append(a), put=lambda *a: changes.append(a), state=state)
adapter.FRAME_STEP['e25c90c1c136638885659fd55bf8a03b'](ctx)
assert sites == [(0x204a54, 'e00313aa')] and ctx.step_max == 3 and len(changes) == 1
code = struct.pack('<' + 'I' * len(words), *words)
rng = random.Random(61116)
cases = []
for target in [0, 30, 60]:
    for step in [0, 1, 2, 3, 4, 0xffffffff]:
        for minimum, maximum, disabled in [(1, 3, 0), (3, 3, 0), (2, 2, 0), (1, 2, 0), (1, 3, 1)]:
            own = rng.randint(minimum, maximum)
            cases.append((target, step, minimum, maximum, disabled, own))
for target, step, minimum, maximum, disabled, own in cases:
    u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for p in [start, state, ctrl, stop & ~0xfff]: u.mem_map(p, 0x1000)
    u.mem_write(start, code)
    control = bytearray(rng.randbytes(0x100))
    struct.pack_into('<II', control, 0, minimum, maximum)
    struct.pack_into('<I', control, 0x40, own)
    struct.pack_into('<II', control, 0x50, 60 // own, 60 // own)
    control[0x58] = disabled
    page = bytearray(0x100); struct.pack_into('<f', page, 0, target)
    struct.pack_into('<II', page, 0x20, step, 3)
    struct.pack_into('<Q', page, 0x30, 2**40 + 11)
    u.mem_write(ctrl, bytes(control)); u.mem_write(state, bytes(page))
    u.reg_write(UC_ARM64_REG_X20, ctrl); u.reg_write(UC_ARM64_REG_X19, 0x1234567890)
    u.reg_write(UC_ARM64_REG_X0, 0xaabbccdd)
    u.reg_write(UC_ARM64_REG_X1, 60 // own)
    u.emu_start(start, stop, count=100)
    dynamic = not disabled and (minimum, maximum) == (1, 3)
    selected = step if target and dynamic and 1 <= step <= 3 else own
    expected = bytearray(control)
    if target and dynamic and 1 <= step <= 3:
        struct.pack_into('<I', expected, 0x40, selected)
        struct.pack_into('<II', expected, 0x50, 60 // selected, 60 // selected)
    assert u.mem_read(ctrl, 0x100) == expected
    expected_page = bytearray(page)
    if target:
        struct.pack_into('<I', expected_page, 0x24, 0 if dynamic else own)
        struct.pack_into('<Q', expected_page, 0x30, 2**40 + 11 + selected)
    assert u.mem_read(state, 0x100) == expected_page
    assert u.reg_read(UC_ARM64_REG_X1) == 60 // selected
    assert u.reg_read(UC_ARM64_REG_X20) == ctrl and u.reg_read(UC_ARM64_REG_X19) == 0x1234567890
    assert u.reg_read(UC_ARM64_REG_X0) == 0x1234567890
controller_paths = 0
if len(sys.argv) > 3:
    # Exercise the real rewritten controller from its function entry. A stub-only
    # test cannot prove normal branches actually reach its patched instruction.
    image = Path(sys.argv[3]).read_bytes()
    at, expected = sites[0]
    assert image[at:at + 4].hex() == expected
    body = bytearray(image[0x204000:0x205000])
    struct.pack_into('<I', body, at - 0x204000, changes[0][1][0])
    framework, stack, clock_page = 0x3120000, 0x3140000, 0x26cd000
    for target in [0, 60]:
        for step in [1, 2, 3]:
            for own in [1, 2, 3]:
                for ticks in [0, 500000, 812500, 1562500]:
                    u = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
                    for p in [start, state, ctrl, framework, stack, clock_page, 0x204000]: u.mem_map(p, 0x1000)
                    u.mem_write(0x204000, bytes(body)); u.mem_write(start, code)
                    control = bytearray(0x100)
                    struct.pack_into('<II', control, 0, 1, 3)
                    struct.pack_into('<fII', control, 8, -0.2, 2, 6)
                    struct.pack_into('<IIIIII', control, 0x40, own, 1, 0, 0, 60 // own, 60 // own)
                    struct.pack_into('<Q', control, 0x70, ctrl + 0x68)  # empty delegate list
                    u.mem_write(ctrl, bytes(control))
                    u.mem_write(framework + 0x128, struct.pack('<q', ticks))
                    u.mem_write(clock_page + 0xfd0, struct.pack('<Q', 31250000))
                    page = bytearray(0x100); struct.pack_into('<f', page, 0, target)
                    struct.pack_into('<I', page, 0x20, step); u.mem_write(state, bytes(page))
                    u.reg_write(UC_ARM64_REG_X0, ctrl); u.reg_write(UC_ARM64_REG_X1, framework)
                    u.reg_write(UC_ARM64_REG_SP, stack + 0xff0)
                    u.emu_start(0x204a18, stop, count=2000)
                    counter = struct.unpack('<Q', u.mem_read(state + 0x30, 8))[0]
                    if target:
                        assert counter == step, (target, step, own, ticks, counter)
                        assert struct.unpack('<I', u.mem_read(ctrl + 0x40, 4))[0] == step
                        assert u.reg_read(UC_ARM64_REG_X1) == 60 // step
                    else: assert counter == 0
                    assert u.reg_read(UC_ARM64_REG_X0) == framework
                    controller_paths += 1
result = {'arm64_stub_cases_passed': len(cases), 'real_controller_paths_verified': controller_paths, 'disabled_path_unchanged': True,
          'pending_and_invalid_host_steps_keep_original': True, 'fixed_modes_kept': True,
          'fps_and_interval_consistent': True, 'vsync_counter_64bit_verified': True,
          'scope': 'ARM64 adapter execution on synthetic objects; no claim of game speed or device GPU performance validation.'}
(D / 'stub-test.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
