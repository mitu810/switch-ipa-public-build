# Bananza v458752: delegate its dynamic frame interval to the existing host frame
# step. The game still advances by the interval it actually presents; no fixed-60
# presentation override and no graphics/shader changes. Experimental, not yet
# gameplay-validated. fps_target remains 0 unless explicitly enabled for testing.
from nso_asm import ADRP, B


def frame_step(ctx):
    # main+0x204a18 chooses an interval at controller+0x40 and its reciprocal FPS
    # at +0x50/+0x54. The shared exit at +0x38 first calls the framework's FPS
    # setter (+0x204c60), then its interval setter (+0x204c00), which updates both
    # NVN presentation and framework+0x88. Patching before both setters keeps
    # their values consistent. x20 is the controller, x19 the framework; w8/w9,
    # x16/x17 and flags are dead before the following calls. No x18 use.
    # Dynamic mode is enabled only for the observed [1,3] interval range and
    # controller+0x58 == 0. Other ranges/modes publish the game's chosen interval
    # as fixed, retaining cinematic and forced-rate behavior.
    site = 0x204a50
    ctx.site(site, '815240b9')  # ldr w1,[x20,#0x50]
    ctx.step_max = 3
    start = ctx.here()
    words, labels, pending = [], {}, []

    def emit(w): words.append(w)
    def label(name): labels[name] = start + 4 * len(words)
    def branch(kind, name, reg=0, cond=0):
        pending.append((len(words), kind, name, reg, cond)); emit(0)

    emit(ADRP(16, start, ctx.state))
    emit(0xb9400211)                  # ldr w17,[x16] target FPS
    branch('cbz', 'original', 17)    # 0: exact original path
    emit(0x39416288)                  # ldrb w8,[x20,#0x58]
    branch('cbnz', 'fixed', 8)
    emit(0xb9400288)                  # ldr w8,[x20] minimum interval
    emit(0x7100051f)                  # cmp w8,#1
    branch('cond', 'fixed', cond=1)  # b.ne
    emit(0xb9400688)                  # ldr w8,[x20,#4] maximum interval
    emit(0x71000d1f)                  # cmp w8,#3
    branch('cond', 'fixed', cond=1)
    emit(0xb900261f)                  # str wzr,[x16,#0x24] no fixed step
    emit(0xb9402208)                  # ldr w8,[x16,#0x20] host step
    branch('cbz', 'count_original', 8)  # host has not decided yet
    emit(0x71000d1f)                  # cmp w8,#3
    branch('cond', 'count_original', cond=8)  # b.hi (invalid host step)
    emit(0xb9004288)                  # str w8,[x20,#0x40] interval
    emit(0x52800789)                  # mov w9,#60
    emit(0x1ac80d29)                  # sdiv w9,w9,w8
    emit(0xb9005289)                  # str w9,[x20,#0x50] target FPS
    emit(0xb9005689)                  # str w9,[x20,#0x54] current FPS
    branch('b', 'count')
    label('fixed')
    emit(0xb9404288)                  # ldr w8,[x20,#0x40] game's interval
    emit(0xb9002608)                  # str w8,[x16,#0x24] fixed step
    branch('b', 'count')
    label('count_original')
    emit(0xb9404288)
    label('count')
    emit(0xf9401a11)                  # ldr x17,[x16,#0x30]
    emit(0x8b284231)                  # add x17,x17,w8,uxtw
    emit(0xf9001a11)                  # str x17,[x16,#0x30] vsyncs stepped
    label('original')
    emit(0xb9405281)                  # ldr w1,[x20,#0x50]
    emit(B(start + 4 * len(words), site + 4))
    for index, kind, target, reg, cond in pending:
        at, to = start + index * 4, labels[target]
        if kind == 'b': words[index] = B(at, to)
        else:
            off = (to - at) // 4
            assert -(1 << 18) <= off < 1 << 18
            base = 0x54000000 | cond if kind == 'cond' else (0x35000000 if kind == 'cbnz' else 0x34000000) | reg
            words[index] = base | ((off & 0x7ffff) << 5)
    for w in words: ctx.emit(w)
    ctx.put(site, [B(site, start)], 'dynamic interval: host whole-vsync step before both FPS and interval setters; other controller modes remain fixed')


FRAME_STEP = {'e25c90c1c136638885659fd55bf8a03b': frame_step}

BINDING = [{
    'what': 'dynamic frame interval from the host step', 'now': 'build', 'goal': 'build', 'purpose': 'frame rate',
    'why': 'experimental adapter for this verified controller layout and shared exit; other builds stay unchanged',
    'checked': 'v458752 disassembly: controller+0x40/+0x50/+0x54 and both setters, 2026-11-16; device gameplay validation pending',
}]
