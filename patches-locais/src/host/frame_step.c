/* Real-time frame step, 60 FPS and high-frame-rate switches for TOTK 1.4.3, the host side of the lifter patches in
 * tools/lifter/patches.py. After MacNativeGames games/totk/runtime/game_patches_v143.c @main (MIT): there the game
 * code is patched in memory and reads a data page; here the recompiled C calls these functions.
 *
 *   runtime_frame_step_update(now_ns)  called at every nvnQueuePresentTexture (hle_nvn.c): the time between presents,
 *                                      averaged over 5 frames, cut at 1/15 s per hitch, published as {dt*30, dt, dt*60}
 *   totk_frame_ratio30 / seconds / interval60   what the patched game sites read instead of 30/FPS, 1/FPS, 60/FPS
 *   totk_fps60_on       TOTK_FPS60     (default on): the controller may pick interval 1 (60 fps)
 *   totk_realtime_on    TOTK_REALTIME  (default on): game time follows the measured frame time (dynamic FPS)
 *   totk_cloth_on       TOTK_CLOTH_REALTIME (default on, needs realtime): the cloth / hair clock too
 *   totk_highfps_fix_on TOTK_HIGHFPS_FIX (default on): trampoline bounce check independent of the frame rate
 *   totk_render_res_*   TOTK_RENDER_RES=<w>x<h> (default unset): the scene render size the renderer takes at start
 *
 * Without the real-time step the game advances its time by the present interval it chose: at interval 1 a frame
 * slower than 1/60 s plays in slow motion. With it, 45 fps is 45 fps at full speed. */
#include <math.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t bits_of(float f) { uint32_t b; memcpy(&b, &f, 4); return b; }
static float float_of(uint32_t b) { float f; memcpy(&f, &b, 4); return f; }

static int env_on(const char *name, int *cache) {   /* on unless "=0" */
    if (*cache < 0) { const char *e = getenv(name); *cache = !(e && !strcmp(e, "0")); }
    return *cache;
}

int totk_fps60_on(void) {
    static int v = -1;
    if (v < 0) { env_on("TOTK_FPS60", &v); fprintf(stderr, "[totk] 1.4.3 60 FPS: %s (TOTK_FPS60)\n", v ? "on, seven controller instructions; original frame-time calculation" : "off, shipping frame controller (30 fps)"); }
    return v;
}
int totk_realtime_on(void) {
    static int v = -1;
    if (v < 0) { env_on("TOTK_REALTIME", &v); fprintf(stderr, "[totk] 1.4.3 real-time frame step: %s (TOTK_REALTIME)\n", v ? "on, 7 sites; original half-step selector and ratio/inverse/seconds/FPS calculations retained" : "off, the game steps by its chosen interval"); }
    return v;
}
int totk_cloth_on(void) {
    static int v = -1;
    if (v < 0) { env_on("TOTK_CLOTH_REALTIME", &v); v = v && totk_realtime_on(); fprintf(stderr, "[totk] 1.4.3 cloth/hair clock: %s (TOTK_CLOTH_REALTIME)\n", v ? "real-time seconds and ratio at 4 actor sites" : "the game's 30 Hz constants"); }
    return v;
}
int totk_highfps_fix_on(void) {
    static int v = -1;
    if (v < 0) { env_on("TOTK_HIGHFPS_FIX", &v); fprintf(stderr, "[totk] 1.4.3 high-FPS fix: %s (TOTK_HIGHFPS_FIX)\n", v ? "trampoline slow-motion check uses the time scale" : "off"); }
    return v;
}

/* ---- the published step: {dt*30, dt, dt*60}; 1/60 s until the first two presents */
static _Atomic uint32_t g_ratio30 = 0x3f000000u;     /* 0.5f */
static _Atomic uint32_t g_seconds = 0x3c888889u;     /* 1/60 */
static _Atomic uint32_t g_interval60 = 0x3f800000u;  /* 1.0f */
static uint64_t last_present_ns;
static double frame_samples[5];
static unsigned frame_sample_count, frame_sample_next;

static void frame_step_store(float seconds) {
    atomic_store_explicit(&g_ratio30, bits_of(seconds * 30.0f), memory_order_release);
    atomic_store_explicit(&g_seconds, bits_of(seconds), memory_order_release);
    atomic_store_explicit(&g_interval60, bits_of(seconds * 60.0f), memory_order_release);
}
float totk_frame_ratio30(void) { return float_of(atomic_load_explicit(&g_ratio30, memory_order_acquire)); }
float totk_frame_seconds(void) { return float_of(atomic_load_explicit(&g_seconds, memory_order_acquire)); }
float totk_frame_interval60(void) { return float_of(atomic_load_explicit(&g_interval60, memory_order_acquire)); }

extern void trainer_tick(void);   /* trainer.c: once per present, before the game's next frame */
void runtime_frame_step_update(uint64_t now_ns) {
    trainer_tick();
    if (!now_ns || !totk_realtime_on()) return;
    if (!last_present_ns) { last_present_ns = now_ns; return; }
    double dt = (double)(now_ns - last_present_ns) * 1e-9;
    last_present_ns = now_ns;
    if (dt > 0.25 || dt < 0.001) { frame_sample_count = frame_sample_next = 0; return; }   /* a pause or a glitch: start over */
    /* No 1/120 s floor (MacNativeGames: a present shorter than that counted as 1/120 s ran the game fast above 120
       fps). A hitch is still cut at 1/15 s per interval. */
    if (dt > 1.0 / 15.0) dt = 1.0 / 15.0;
    frame_samples[frame_sample_next++ % 5] = dt;
    if (frame_sample_count < 5) frame_sample_count++;
    double sum = 0;
    for (unsigned i = 0; i < frame_sample_count; i++) sum += frame_samples[i];
    frame_step_store((float)(sum / frame_sample_count));
}

/* ---- TOTK_RENDER_RES=<w>x<h>: the scene size the renderer takes at start (main+0x585e48). The game scales what it
   reads there by the canvas width over 1920 (measured on the Mac: 2560x1440 asked on a 2560-wide canvas rendered
   3408x1920), so the value handed over is the asked size divided by that factor: 1 on the 1920x1080 docked canvas,
   1280/1920 in handheld mode (TOTK_OPERATION_MODE=handheld). Unset or 0: the game's own size, nothing changes. */
extern int runtime_handheld_mode(void);
static int g_rres = -1;
static float g_rres_w, g_rres_h;
int totk_render_res_on(void) {
    if (g_rres < 0) {
        const char *e = getenv("TOTK_RENDER_RES");
        unsigned w, h; char rest;
        if (!e || !*e || !strcmp(e, "0")) g_rres = 0;
        else if (sscanf(e, "%ux%u%c", &w, &h, &rest) != 2 || w < 64 || h < 64 || w > 4096 || h > 4096) {
            fprintf(stderr, "[totk] TOTK_RENDER_RES=%s is not <width>x<height> (64..4096): the game's own size\n", e); g_rres = 0;
        } else {
            float k = runtime_handheld_mode() ? 1280.0f / 1920.0f : 1.0f;
            g_rres_w = w / k; g_rres_h = h / k; g_rres = 1;
            fprintf(stderr, "[totk] 1.4.3 render resolution: scene %ux%u (game value %.0fx%.0f, canvas factor %.4f; TOTK_RENDER_RES). "
                            "The texture heaps are the game's own, sized for 1600x900: a much larger scene may run out of them.\n", w, h, g_rres_w, g_rres_h, k);
        }
    }
    return g_rres;
}
float totk_render_w(void) { return g_rres_w; }
float totk_render_h(void) { return g_rres_h; }

/* How much the game's texture heaps grow for that scene (tools/make_heap_overlay.py, the same formula: shipped
   DynamicTextureAllocatorMem 113664 KiB for 1600x900, 1.6 margin): the application pool (svc.c) grows by it. The
   overlay must be in place (data/romfs-overlay, overlay.json with its extra_kib): if it is missing, or made for a
   smaller scene, the game would run out of its heap, so this says so loudly; the larger of the two is used. */
#define DTA_KIB 113664.0
#define SCENE_PIXELS (1600.0 * 900.0)
static long overlay_extra_kib(void) {
    /* TOTK_ROMFS_OVERLAY may list several directories (dir;dir;...: the launcher's mods): the first overlay.json found */
    const char *dirs = getenv("TOTK_ROMFS_OVERLAY");
    if (!dirs || !*dirs) dirs = "data/romfs-overlay";
    char path[4096];
    FILE *f = NULL;
    for (const char *p = dirs; *p && !f;) {
        size_t n = strcspn(p, ";");
        if (n && n < sizeof path - 16) {
            snprintf(path, sizeof path, "%.*s/overlay.json", (int)n, p);
            f = fopen(path, "rb");
        }
        p += n + (p[n] == ';');
    }
    if (!f) return -1;
    char buf[4096];
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = 0;
    const char *k = strstr(buf, "\"extra_kib\"");
    if (!k || !(k = strchr(k, ':'))) return -1;
    return atol(k + 1);
}
uint64_t totk_render_res_extra_bytes(void) {
    static int64_t v = -1;
    if (v < 0) {
        v = 0;
        long need = 0, have = overlay_extra_kib();   /* the overlay, when present, grows the heaps in every mode: the pool follows */
        if (totk_render_res_on()) {
            double px = (double)g_rres_w * g_rres_h, grow = px / SCENE_PIXELS - 1;
            if (grow > 0) { grow *= 1.6; need = (long)(ceil(DTA_KIB * (1 + grow) / 1024) * 1024 - DTA_KIB); }
            if (need > 0 && have < need)
                fprintf(stderr, "[totk] 【红】the scene %.0fx%.0f needs the texture heap grown by %ld MiB but the RomFS overlay %s (data/romfs-overlay): "
                                "run GERAR-OVERLAY (tools/make_heap_overlay.py --scene). The game may stop when its heap runs out.\n",
                        (double)g_rres_w, (double)g_rres_h, need >> 10, have < 0 ? "is missing" : "is smaller");
        }
        v = (int64_t)(have > need ? have : need) << 10;
        if (v) fprintf(stderr, "[totk] texture heaps grown by %lld MiB (overlay %s)\n", (long long)(v >> 20), have >= need ? "in place" : "MISSING");
    }
    return (uint64_t)v;
}
