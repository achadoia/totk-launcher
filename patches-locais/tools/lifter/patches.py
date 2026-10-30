"""Per-instruction replacements applied by gen.py: guest address -> function(gen, addr) returning C code.

Every patch checks the original instruction word first, so lifting another game version (or a different build)
fails loudly instead of changing the wrong code. Runtime switches live in src/gfx/nvn/hle_nvn.c and
src/host/frame_step.c.

The 60 FPS / real-time / high-FPS / render-resolution patches are the Windows (static recompilation) form of
MacNativeGames games/totk/runtime/game_patches_v143.c (MIT): the same sites, the same instruction words checked, the
replacement written as C instead of patched ARM64 bytes. Each one is compiled in and switched at run time by its
TOTK_* variable (default on except TOTK_RENDER_RES), so one build serves both behaviours:
  TOTK_FPS60=0          the shipping frame controller (30 fps minimum interval 2)
  TOTK_REALTIME=0       the game steps its time by the present interval it chose (a slow frame slows the game)
  TOTK_CLOTH_REALTIME=0 the cloth / hair clock keeps its 30 Hz constants (runs fast at high frame rates)
  TOTK_HIGHFPS_FIX=0    the trampoline slow-motion check stays frame-rate dependent
  TOTK_RENDER_RES=WxH   scene render size (the game's own 1600x900 docked unless set; see frame_step.c)
"""


def expect(gen, addr, word, what):
    got = gen.word(addr)
    if got != word:
        raise SystemExit(f'patch {what} at {addr:#x}: expected {word:#010x}, found {got:#010x} (not TOTK 1.4.3?)')


def lift_word(gen, addr, word):
    """The C of one instruction word at addr, through the lifter itself (so flags, registers and FP are exact)."""
    ins = gen.lifter.lift(word, addr)
    if ins.kind != 'op':
        raise SystemExit(f'patch at {addr:#x}: {word:#010x} lifts to {ins.kind}, not a plain statement')
    return ins.code


def orig(gen, addr):
    return lift_word(gen, addr, gen.word(addr))


# ---------------------------------------------------------------- dynamic resolution (UltraTears' own)
def drs_off(gen, addr):
    """Dynamic resolution off (1.4.3). main+0x584300 is the scene resolution controller: each frame it fits the
    recent frame times and returns {scale (float), state} to the renderer (main+0x581968), which turns the scale into
    the scene size (+0xccc). Its first test is the controller's enable bit (+0x110 bit 0); with the bit clear it
    takes its own disabled path (main+0x584c3c): scale 1.0, history untouched. The patch takes that path unless
    TOTK_DRS=1. Why: the game flips the scene targets between 1600x896 and 1432x808 over the same memory and our
    per-size images then hand stale content to the other size (terrain gone, clouds over the scene);
    image quality is better at a fixed size and upscaling will come from FSR."""
    expect(gen, addr, 0x360042e8, 'drs_off')   # tbz w8, #0, 0x800584c3c
    return ('{ extern int totk_drs_enabled(void); '
            'if (!totk_drs_enabled() || ((c->x[8] >> 0) & 1) == 0) { goto L_800584c3c; } }')


# ---------------------------------------------------------------- 60 FPS: the present-interval controller
# main+0x2db174 (hle_nvn.c: FRAME_CONTROLLER_LR) clamps the interval it picks to [min, max] = state +0 / +4, min 2
# (30 fps). Seven instructions make the minimum 1 (60 fps) without touching the frame-time calculation
# (MacNativeGames fps60[]). TOTK_MAX_INTERVAL (hle_nvn.c) still caps the maximum.
FPS60 = {
    0x8002db204: (0x6b08015f, 0x7100055f),   # cmp w10, w8          -> cmp w10, #1
    0x8002db208: (0x1a89b10a, 0x1a9fa52a),   # csel w10, w8, w9, lt -> csinc w10, w9, wzr, ge
    0x8002db22c: (0xb940468a, 0xd503201f),   # ldr w10, [x20, #0x44] -> nop
    0x8002db340: (0xb9400288, 0x52800028),   # ldr w8, [x20]        -> mov w8, #1
    0x8002db34c: (0x2a0a03f5, 0x1a9fa555),   # mov w21, w10         -> csinc w21, w10, wzr, ge
    0x8002db360: (0x6b0801bf, 0x710005bf),   # cmp w13, w8          -> cmp w13, #1
    0x8002db36c: (0x1a88c1ac, 0x1a9fc5ac),   # csel w12, w13, w8, gt -> csinc w12, w13, wzr, gt
}


def fps60(gen, addr):
    o, n = FPS60[addr]
    expect(gen, addr, o, 'fps60')
    return ('{ extern int totk_fps60_on(void); '
            f'if (totk_fps60_on()) {{ {lift_word(gen, addr, n)} }} else {{ {orig(gen, addr)} }} }}')


# ---------------------------------------------------------------- real-time frame step (dynamic FPS)
# The game steps its time by the nominal interval (60 / FPS) and a "30 Hz ratio" (30 / FPS), both made from the
# integer FPS it chose. With the measured time between presents instead (frame_step.c: {dt*30, dt, dt*60}, a 5-frame
# average) the game runs at the right speed at any frame rate: the DFPS / UltraCam behaviour.
#  main+0x3e5658  fmul s8, s0, s1: s0 = 60/FPS, s1 = the 0.5 / 1.0 selector (sys+0x1da bit 0). s0 := dt*60 first;
#                 the fmul and all FrameRateMgr ratio / inverse / seconds / FPS arithmetic stay.
#  main+0x2daa54  ldr s0, [x19, #0x250] .. fmul: 30 / FPS computed from the FPS int and stored to a global
#                 (main+0x3a22128). s0 := dt*30 and the five arithmetic instructions are skipped; the store stays.
def rt_interval(gen, addr):
    expect(gen, addr, 0x1e210808, 'realtime interval')   # fmul s8, s0, s1
    return ('{ extern int totk_realtime_on(void); extern float totk_frame_interval60(void); '
            f'if (totk_realtime_on()) {{ FP_SETS(0, totk_frame_interval60()); }} {orig(gen, addr)} }}')


def rt_ratio(gen, addr):
    expect(gen, addr, 0xbd425260, 'realtime ratio')   # ldr s0, [x19, #0x250]
    return ('{ extern int totk_realtime_on(void); extern float totk_frame_ratio30(void); '
            f'if (totk_realtime_on()) {{ FP_SETS(0, totk_frame_ratio30()); }} else {{ {orig(gen, addr)} }} }}')


RT_SKIP = {   # the original arithmetic, skipped while the real-time step is on (nops on the Mac)
    0x8002daa58: 0x52a84e08,   # mov w8, #0x42700000 (60.0)
    0x8002daa6c: 0x5e21d800,   # scvtf s0, s0
    0x8002daa70: 0x1e201820,   # fdiv s0, s1, s0
    0x8002daa74: 0x1e2c1001,   # fmov s1, #0.5
    0x8002daa78: 0x1e210800,   # fmul s0, s0, s1
}


def rt_skip(gen, addr):
    expect(gen, addr, RT_SKIP[addr], 'realtime skip')
    return f'{{ extern int totk_realtime_on(void); if (!totk_realtime_on()) {{ {orig(gen, addr)} }} }}'


# ---------------------------------------------------------------- cloth / hair clock at high frame rates
# The actor animation update has a second, unscaled clock for cloth / hair that builds {factor / 30, factor} every
# rendered frame (four seconds per real second at 120 fps). Only that clock gets the measured time: two inlined sites
# and the two callers of the out-of-line update at main+0x2a0e40 whose s2 / s3 are 30 Hz values (its third caller
# already passes measured time and is not touched). Pauses, slow motion and time factors stay (the factor multiplies).
def cloth_a(gen, addr):
    expect(gen, addr, 0x1e200940, 'cloth A')   # fmul s0, s10, s0; s10 = factor, [sp+0x364] = factor, then str s0 -> [sp+0x360]
    return ('{ extern int totk_cloth_on(void); extern float totk_frame_ratio30(void), totk_frame_seconds(void); '
            'if (totk_cloth_on()) { float f = VSF32(10); FP_SETS(0, f * totk_frame_ratio30()); ST32(c->sp + 0x364, VS32(0)); '
            f'FP_SETS(0, f * totk_frame_seconds()); }} else {{ {orig(gen, addr)} }} }}')


def cloth_b(gen, addr):
    expect(gen, addr, 0x1e210801, 'cloth B')   # fmul s1, s0, s1; s0 = factor, [sp+0x114] = factor, then str s1 -> [sp+0x110]
    return ('{ extern int totk_cloth_on(void); extern float totk_frame_ratio30(void), totk_frame_seconds(void); '
            'if (totk_cloth_on()) { float f = VSF32(0); FP_SETS(1, f * totk_frame_ratio30()); ST32(c->sp + 0x114, VS32(1)); '
            f'FP_SETS(1, f * totk_frame_seconds()); }} else {{ {orig(gen, addr)} }} }}')


CLOTH_CALLS = {0x80024944c: 0x94015e7d, 0x80028bc98: 0x9400546a}   # bl 0x8002a0e40


def cloth_call(gen, addr):
    expect(gen, addr, CLOTH_CALLS[addr], 'cloth call')
    return ('{ extern int totk_cloth_on(void); extern float totk_frame_ratio30(void); extern void f_8002a0e40(Cpu *); '
            'if (totk_cloth_on()) { float r = totk_frame_ratio30(); FP_SETS(2, VSF32(2) * r); FP_SETS(3, VSF32(3) * r); } '
            f'c->x[30] = {addr + 4:#x}ull; f_8002a0e40(c); }}')


# ---------------------------------------------------------------- trampolines above ~100 fps
# main+0x562a64: the contact code scales the bounce by 0.1 when X+0x28 (= 30 / (30 Hz ratio * time scale)) > 100,
# meant as "time slowed below 0.3"; above 100 fps it also fires with time running normally. Compare
# X+0x28 * X+0x38 (the ratio) = 30 / time scale instead: the same value at 30 Hz, independent of the frame rate.
def bounce(gen, addr):
    expect(gen, addr, 0xbd402920, 'bounce')   # ldr s0, [x9, #0x28]
    return ('{ extern int totk_highfps_fix_on(void); '
            'if (totk_highfps_fix_on()) { uint32_t ba = LD32(c->x[9] + 0x28), bb = LD32(c->x[9] + 0x38); float fa, fb; '
            'memcpy(&fa, &ba, 4); memcpy(&fb, &bb, 4); FP_SETS(0, fa * fb); } '
            f'else {{ {orig(gen, addr)} }} }}')


# ---------------------------------------------------------------- scene render size (TOTK_RENDER_RES)
# main+0x585e48: the renderer takes the mode entry's size (docked 1600x900) into +0xcc4 / +0xcc8 through s0. With
# TOTK_RENDER_RES the size comes from frame_step.c instead (s0 = width, s1 = height; s1 is scratch here, as on the
# Mac). Every path that picks the size converges here. Beware: the game's texture heaps are sized for 1600x900; a
# much larger scene can exhaust them (the Mac grows the heaps with a RomFS overlay, not ported).
def rres_a(gen, addr):
    expect(gen, addr, 0x1e220160, 'render res a')   # scvtf s0, w11
    return f'{{ extern int totk_render_res_on(void); if (!totk_render_res_on()) {{ {orig(gen, addr)} }} }}'


def rres_b(gen, addr):
    expect(gen, addr, 0xbd0cc660, 'render res b')   # str s0, [x19, #0xcc4]
    return ('{ extern int totk_render_res_on(void); extern float totk_render_w(void), totk_render_h(void); '
            'if (totk_render_res_on()) { FP_SETS(0, totk_render_w()); FP_SETS(1, totk_render_h()); } '
            f'else {{ {orig(gen, addr)} }} }}')


def rres_c(gen, addr):
    expect(gen, addr, 0x1e220140, 'render res c')   # scvtf s0, w10
    return ('{ extern int totk_render_res_on(void); if (totk_render_res_on()) { ST32(c->x[19] + 0xcc4, VS32(0)); } '
            f'else {{ {orig(gen, addr)} }} }}')


def rres_d(gen, addr):
    expect(gen, addr, 0xbd0cca60, 'render res d')   # str s0, [x19, #0xcc8]
    return ('{ extern int totk_render_res_on(void); if (totk_render_res_on()) { ST32(c->x[19] + 0xcc8, VS32(1)); } '
            f'else {{ {orig(gen, addr)} }} }}')


# ---------------------------------------------------------------- trainer (src/host/trainer.c)
# The cheats of Arch9SK7's MyNXCheats for this build (277178B7DBA1B6D4, TOTK 1.4.3; the list the NX Optimizer uses),
# as runtime switches: each site runs the cheat's instruction while trainer_sel(id) says so and the original otherwise.
# The original words come from the list's "Restore Code" and are checked here. Code caves of the list (main+0x2bb9fxx,
# past the text) are written as C. Ids: src/host/trainer.c (keep both in step).
C_HEALTH, C_STAMINA, C_BATTERY, C_HORSE, C_STEALTH, C_THERMO, C_GLOOM, C_SKILLS, C_CHARGE, C_DAMAGE = range(1, 11)
C_MOVE, C_DIVEJUMP, C_RUNWALK, C_ITEMS999, C_SHOP, C_DURAB, C_ARROWS, C_ZONAI, C_FOOD, C_AMIIBO = range(11, 21)
C_MAP, C_ZONAILIMIT, C_TIME, C_TIMER60, C_FOG, C_BLOODMOON = range(21, 27)

PATCH_LABELS = {}   # patch address -> branch targets its C may jump to (gen.py gives them labels)

# Computed branches the table scan resolves wrongly: the function holding the address gets a label on every
# instruction, and an unmatched target goes through the function's own dispatch switch instead of failing
# ("FATAL: no function at ..."). Any address inside the function works (gen.py checks entry <= a < end), so the
# lifter's function split does not matter: the first try named the caller 0x6e9340, but the lifter puts the br in
# its own function f_8006eaa28 (0x6e9340 calls it with bl at +0x6e9588), so the build still crashed.
#  main+0x6eb84c: `adr x12, 0x6eb7b0; ldrb w13, [x28, x22]; add x12, x12, x13, lsl #2; br x12` indexes a byte table
#                 through x28, set long before; the scan saw one target (0x6eb7b0) and entering a shrine jumps to
#                 0x6eb850 (2026-10-27). Targets 0x6eb7b0 + 4*byte all lie inside f_8006eaa28 (ends 0x6ebbd0).
FORCE_ALL_LABELS = {0x8006eb84c}


def sext26(w):
    v = w & 0x3FFFFFF
    return v - (1 << 26) if v & (1 << 25) else v


def lift_any(gen, addr, word):
    """C for one instruction word at addr, whatever its kind (op, ret, b, b.cond, bl)."""
    ins = gen.lifter.lift(word, addr)
    if ins.kind == 'op':
        return ins.code
    if ins.kind == 'ret':
        return 'return;'
    if ins.kind == 'b':
        return gen._jump(ins.target)
    if ins.kind == 'bcond':
        return f'if ({ins.cond}) {{ {gen._jump(ins.target)} }}'
    if ins.kind == 'bl':
        t = ins.target
        if gen.in_text(t):
            return f'{{ extern void f_{t:x}(Cpu *); c->x[30] = {addr + 4:#x}ull; f_{t:x}(c); }}'
        return f'c->x[30] = {addr + 4:#x}ull; c->pc = {t:#x}ull; rt_dispatch(c);'
    raise SystemExit(f'patch at {addr:#x}: {word:#010x} lifts to {ins.kind}, not supported in a cheat')


def cheat_site(cid, addr, orig_word, *new_words):
    """A switch at addr: trainer_sel(cid) == k (k >= 1) runs new_words[k-1], anything else the original."""
    for w in new_words + (orig_word,):
        if (w & 0xFC000000) == 0x14000000:   # B: the target needs a label
            PATCH_LABELS.setdefault(addr, []).append(addr + sext26(w) * 4)

    def fn(gen, a):
        expect(gen, a, orig_word, f'cheat {cid}')
        cases = ' '.join(f'case {k + 1}: {{ {lift_any(gen, a, w)} }} break;' for k, w in enumerate(new_words))
        return (f'{{ extern int trainer_sel(int); switch (trainer_sel({cid})) {{ {cases} '
                f'default: {{ {lift_any(gen, a, orig_word)} }} break; }} }}')
    PATCHES[addr] = fn


def cheat_toggle(cid, *sites):
    """sites: (addr, orig_word, new_word): all take the cheat's word while trainer_sel(cid) != 0."""
    for addr, o, n in sites:
        cheat_site(cid, addr, o, *([n] * 1))


# code-cave cheats, written as C
def cave_health(gen, addr):   # main+0x4407d0: ldr x8, [x0] -> cave: current health (+8) := max (+0x18) when max != 0
    expect(gen, addr, 0xf9400008, 'cheat health')
    return ('{ extern int trainer_sel(int); if (trainer_sel(1)) { uint32_t m = LD32(c->x[0] + 0x18); if (m) ST32(c->x[0] + 8, m); } '
            f'{orig(gen, addr)} }}')


def cave_durability(gen, addr):   # ldr w1, [x8, #8] -> cave: w1 := max (+0x18), stored to +8
    expect(gen, addr, 0xb9400901, 'cheat durability')
    return ('{ extern int trainer_sel(int); if (trainer_sel(16)) { uint32_t m = LD32(c->x[8] + 0x18); ST32(c->x[8] + 8, m); c->x[1] = m; } '
            f'else {{ {orig(gen, addr)} }} }}')


def cave_food_buff(gen, addr):   # main+0x3067f4: ldr s0, [x20, #0x15c] -> cave: timer (+0x15c) := its start (+0x160)
    expect(gen, addr, 0xbd415e80, 'cheat food buff')
    return ('{ extern int trainer_sel(int); if (trainer_sel(19)) { uint32_t v = LD32(c->x[20] + 0x160); ST32(c->x[20] + 0x15c, v); c->v[0] = v; } '
            f'else {{ {orig(gen, addr)} }} }}')


def cave_timer60(gen, addr):   # main+0x2aa1034: ldr s0, [x8, #0x1c] (mission timer, frames) -> +1800 once per hotkey press
    expect(gen, addr, 0xbd401d00, 'cheat timer')
    return (f'{{ {orig(gen, addr)} extern int trainer_take(int); if (trainer_take(24)) {{ float t = VSF32(0); '
            'if (t != 0.0f && LD32(c->x[8] + 0x10)) { t += 1800.0f; FP_SETS(0, t); ST32(c->x[8] + 0x1c, VS32(0)); } } }')


PATCHES = {
    0x8005843e0: drs_off,
    0x8004407d0: cave_health,
    0x800282e58: cave_durability,
    0x8002829f4: cave_durability,
    0x8003067f4: cave_food_buff,
    0x802aa1034: cave_timer60,
    0x8003e5658: rt_interval,
    0x8002daa54: rt_ratio,
    0x800246eac: cloth_a,
    0x800345904: cloth_b,
    0x800562a64: bounce,
    0x800585e48: rres_a,
    0x800585e50: rres_b,
    0x800585e54: rres_c,
    0x800585e60: rres_d,
}
PATCHES.update({a: fps60 for a in FPS60})
PATCHES.update({a: rt_skip for a in RT_SKIP})
PATCHES.update({a: cloth_call for a in CLOTH_CALLS})

# selectors (value k = new_words[k-1]) and toggles; addresses main+offset, words from the cheat list
cheat_site(C_STAMINA, 0x80031d398, 0x1f080800, 0x1e204040, 0x1f080800)          # 1 normal; 2 Master Sword quest variant below
cheat_site(C_STAMINA, 0x8006e0838, 0x9b292908, 0x9b292908, 0xd2a8b76f)   # (8-byte codes: low word first) movz w15, #0x45bb0000 = 6000.0
cheat_site(C_STAMINA, 0x8006e083c, 0xbd401d00, 0xbd401d00, 0xf2b0000f)   # movk w15, #0x8000, lsl 16
cheat_site(C_STAMINA, 0x8006e0840, 0xbd000260, 0xbd000260, 0xb900026f)
cheat_toggle(C_BATTERY, (0x80078251c, 0xbd004668, 0xbd004662), (0x800782568, 0xbd004668, 0xbd004662))
cheat_toggle(C_HORSE, (0x80233765c, 0x71000508, 0xd503201f))
cheat_toggle(C_STEALTH, (0x8004c4d14, 0x1e204100, 0x1e2703e0), (0x8004cb9bc, 0x1e200c20, 0x1e2703e0))
cheat_toggle(C_THERMO, (0x800324c14, 0xb9400008, 0x52800068), (0x80072ae28, 0x1a9f57e0, 0x2a1f03e0), (0x80072b124, 0xbd400009, 0xbd400809))
cheat_toggle(C_GLOOM, (0x8004414fc, 0x1a89c115, 0x1a89c135), (0x800441520, 0x52800095, 0x2a1f03f5))
cheat_toggle(C_SKILLS, (0x8001ae99c, 0x1e212000, 0x1e212020), (0x8001b5b2c, 0x1e280400, 0x1e282100), (0x8006343d0, 0x1e212000, 0x1e212020))
cheat_toggle(C_CHARGE, (0x801b7a460, 0x1e201c20, 0x1e211000), (0x8023bbe00, 0x1e204100, 0x1e209000), (0x8023bcd2c, 0x2d1f8260, 0x291fa268))
cheat_site(C_DAMAGE, 0x800335c40, 0x1e2e1008, 0x1e201008, 0x1e221008, 0x1e249008, 0x1e27d008)   # x2 x4 x10 x30
cheat_site(C_DAMAGE, 0x800335ca0, 0xbd400008, 0x1e201008, 0x1e221008, 0x1e249008, 0x1e27d008)
cheat_site(C_MOVE, 0x80047e09c, 0x52a7f008, 0x52a80008, 0x52a80808, 0x52a82408)                  # x2 x3 x10 (hold B)
cheat_toggle(C_DIVEJUMP, (0x8023f0864, 0x97dd371f, 0x1e211000), (0x800bd3788, 0x1e211c41, 0x1e209001))
cheat_site(C_RUNWALK, 0x8023f6448, 0x396b6288, 0x52800028)                                        # 1 = running by default
cheat_toggle(C_ITEMS999, (0x800a77b40, 0x0b0300c3, 0x52807ce3))
cheat_toggle(C_SHOP, (0x80250767c, 0x5a980715, 0x1a9813f5))
cheat_toggle(C_DURAB, (0x8021eca9c, 0x1b09a148, 0x1b09a3e8))   # + the two cave sites above
cheat_toggle(C_ARROWS, (0x801b78cd0, 0x12800001, 0x2a1f03e1))
cheat_toggle(C_ZONAI, (0x8020b2a1c, 0x12800001, 0x2a1f03e1), (0x801ccc718, 0x1e201c42, 0x1e2703e2))
cheat_toggle(C_FOOD, (0x8020b3abc, 0x12800001, 0x2a1f03e1), (0x8020b3b98, 0x54fffe2c, 0x17fffff2))   # + the buff cave above
cheat_toggle(C_AMIIBO, (0x801f87e08, 0x370001e0, 0xd503201f))
cheat_toggle(C_MAP,
             (0x8020f6f10, 0xd10183ff, 0x52800020), (0x8020f6f14, 0xfd0013ea, 0xd65f03c0),   # shrines and caves
             (0x8007362d0, 0xd101c3ff, 0x52800020), (0x8007362d4, 0xfd001bea, 0xd65f03c0),
             (0x8020fc7a4, 0x39407688, 0x1400002e),                                           # koroks (zoomed in)
             (0x8020f6ff0, 0x34000248, 0xd503201f),                                           # shrines and roots
             (0x800736614, 0x340002c8, 0xd503201f),                                           # skyview towers
             (0x8020fd158, 0x34fffbe8, 0xd503201f),                                           # tears of the dragon
             (0x8020fedd4, 0x34fffbe8, 0xd503201f),                                           # bargainer statues
             (0x800736ce0, 0x394073a8, 0x52800028),                                           # villages and stables
             (0x8020f9f9c, 0x340000e8, 0xd503201f),                                           # unvisited places
             (0x800736488, 0x34fffe4d, 0xd503201f), (0x8007364f8, 0x394063e8, 0x52800028),    # caves, wells, chasms
             (0x8020fe2bc, 0x34fffba8, 0xd503201f),                                           # great fairies / Misko
             (0x8020fdc68, 0x34fffb48, 0xd503201f),                                           # old maps
             (0x8020f8068, 0x34000988, 0xd503201f),                                           # the five temples
             (0x8020fcce4, 0x34fffa88, 0xd503201f),                                           # device dispensers
             (0x800eccdbc, 0x34fff7e8, 0xd503201f))                                           # minigames and restaurants
cheat_toggle(C_ZONAILIMIT, (0x801c0ba70, 0xb9401268, 0x52800028))
cheat_site(C_TIME, 0x80068c15c, 0xbd403e69, 0xd503201f, 0x1e27d009, 0x1e37d009, 0x1e2e1009, 0x1e2c1009, 0x1e2a1009)   # stop, FF, RW, 2x 3x 4x days
cheat_toggle(C_FOG, (0x80067fcc4, 0x1e2e1003, 0x1e2703e3))
cheat_toggle(C_BLOODMOON, (0x801fd39cc, 0x1e2e1001, 0x1e3e1001))
