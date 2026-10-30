/* Trainer: the cheats of Arch9SK7's MyNXCheats for TOTK 1.4.3 (build 277178B7DBA1B6D4, the list the NX Optimizer
 * installs), as live switches inside the recompiled game. The code sites are in tools/lifter/patches.py (each runs the
 * cheat's instruction while trainer_sel(id) != 0 and the original otherwise); this file keeps the state, the in-game
 * menu (F1; drawn by src/vk/present.c's text overlay), the hotkeys, the "hold a game button" cheats, the two cheats
 * that write game memory (glider speed, Moon Jump) and the window title text.
 *
 *   F1 (or Plus + Minus on the controller)   opens / closes the menu: arrows or d-pad move and change, Enter / A
 *                                            toggles, Esc / B closes; the game gets no input while it is open
 *   numpad and F-keys                         direct hotkeys (TRAINER-TECLAS.txt)
 *   TOTK_CHEATS=name[=k],...                  cheats on from the start;  TOTK_TRAINER=0  everything off
 *   Local\UltraTearsTrainer (shared memory)   the desktop launcher (D:\zelda\LAUNCHER.py, "Trainer" tab) reads every
 *                                            cheat's state and switches them while the game runs (TrainerShm below)
 *
 * Ids (patches.py has the same numbers): */
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#endif

enum { C_HEALTH = 1, C_STAMINA, C_BATTERY, C_HORSE, C_STEALTH, C_THERMO, C_GLOOM, C_SKILLS, C_CHARGE, C_DAMAGE,
       C_MOVE, C_DIVEJUMP, C_RUNWALK, C_ITEMS999, C_SHOP, C_DURAB, C_ARROWS, C_ZONAI, C_FOOD, C_AMIIBO,
       C_MAP, C_ZONAILIMIT, C_TIME, C_TIMER60, C_FOG, C_BLOODMOON, C_GLIDER, C_MOONJUMP, C_COUNT };

typedef struct { const char *name, *title, *label; int max; uint64_t hold; const char *const *values; } Cheat;
static const char *const v_stamina[] = {"desligado", "LIGADO", "missao Master Sword"};
static const char *const v_damage[] = {"x1", "x2", "x4", "x10", "x30"};
static const char *const v_move[] = {"x1", "x2", "x3", "x10"};
static const char *const v_runwalk[] = {"do jogo", "correr"};
static const char *const v_time[] = {"normal", "PARADO", "avancar", "voltar", "dias 2x", "dias 3x", "dias 4x"};
static const char *const v_glider[] = {"x1", "x1.5", "x2", "x3", "x3.8"};
/* hold: libnx button bits (A 1, B 2, X 4): the cheat acts only while that button is held, as in the original codes */
static const Cheat cheats[C_COUNT] = {
    [C_HEALTH] = {"health", "HP", "Vida infinita", 1},
    [C_STAMINA] = {"stamina", "STA", "Stamina infinita", 2, 0, v_stamina},
    [C_BATTERY] = {"battery", "BAT", "Bateria infinita", 1},
    [C_DURAB] = {"durability", "DUR", "Durabilidade infinita", 1},
    [C_ARROWS] = {"arrows", "ARROWS", "Flechas infinitas", 1},
    [C_ZONAI] = {"zonai", "ZONAI", "Dispositivos Zonai infinitos (e sem acabar)", 1},
    [C_FOOD] = {"food", "FOOD", "Comida infinita (e buff sem fim)", 1},
    [C_ITEMS999] = {"items", "x999", "Itens x999 ao pegar", 1},
    [C_SHOP] = {"shop", "SHOP", "Compras de graca", 1},
    [C_STEALTH] = {"stealth", "STEALTH", "Modo furtivo", 1},
    [C_DAMAGE] = {"damage", "DMG", "Dano", 4, 0, v_damage},
    [C_MOVE] = {"move", "MOVE", "Velocidade (segurando B)", 3, 2, v_move},
    [C_GLIDER] = {"glider", "GLIDE", "Planador (segurando A)", 4, 1, v_glider},
    [C_MOONJUMP] = {"moonjump", "MOON", "Moon Jump (segurando X)", 1, 4},
    [C_DIVEJUMP] = {"jump", "JUMP", "Mergulho forte + pulo alto", 1},
    [C_RUNWALK] = {"run", "RUN", "Movimento padrao", 1, 0, v_runwalk},
    [C_TIME] = {"time", "TIME", "Tempo", 6, 0, v_time},
    [C_TIMER60] = {"timer60", "", "+60 s no cronometro de missao (Enter)", 1},
    [C_MAP] = {"map", "MAP", "Mapa: mostrar tudo", 1},
    [C_GLOOM] = {"gloom", "GLOOM", "Ignorar gloom", 1},
    [C_THERMO] = {"thermo", "THERMO", "Termometro perfeito (fogo, calor, frio)", 1},
    [C_HORSE] = {"horse", "HORSE", "Stamina do cavalo infinita", 1},
    [C_SKILLS] = {"skills", "SKILLS", "Habilidades dos sabios sem recarga", 1},
    [C_CHARGE] = {"charge", "CHARGE", "Carga rapida de arma e arco", 1},
    [C_FOG] = {"fog", "NOFOG", "Sem neblina", 1},
    [C_ZONAILIMIT] = {"nolimit", "NOLIMIT", "Sem limite de 21 dispositivos Zonai", 1},
    [C_BLOODMOON] = {"bloodmoon", "BLOODMOON", "Lua de sangue instantanea (ligue e desligue)", 1},
    [C_AMIIBO] = {"amiibo", "AMIIBO", "Amiibo infinito", 1},
};
/* the menu's order */
static const int menu_items[] = {C_HEALTH, C_STAMINA, C_BATTERY, C_DURAB, C_ARROWS, C_ZONAI, C_FOOD, C_ITEMS999, C_SHOP, C_STEALTH,
                                 C_DAMAGE, C_MOVE, C_GLIDER, C_MOONJUMP, C_DIVEJUMP, C_RUNWALK, C_TIME, C_TIMER60, C_MAP, C_GLOOM,
                                 C_THERMO, C_HORSE, C_SKILLS, C_CHARGE, C_FOG, C_ZONAILIMIT, C_BLOODMOON, C_AMIIBO};
#define MENU_N ((int)(sizeof menu_items / sizeof *menu_items))

static _Atomic int sel[C_COUNT];      /* the selector of each cheat (0 off) */
static _Atomic int take[C_COUNT];     /* one-shot requests */
static _Atomic uint64_t g_buttons;    /* the game's buttons this frame (trainer_tick) */
static _Atomic int menu_open;
static int cursor;
static int g_enabled = -1;
extern uint64_t runtime_input_buttons(void);   /* overrides_input.c */
extern void runtime_input_block(int on);
extern void runtime_input_clear(void);
extern void vkp_overlay_set(const char *text);   /* vk/present.c */

static const char *value_text(int id, int k) {
    return cheats[id].values ? cheats[id].values[k] : k ? "LIGADO" : "desligado";
}
static void menu_render(void) {
    static char text[8192];
    size_t o = 0;
    o += (size_t)snprintf(text + o, sizeof text - o, "#TRAINER   F1 ou Esc fecha   setas: mover e mudar   Enter: liga / desliga\n"
                                                     "#Controle: direcional move, A liga, B fecha, + e - juntos abre e fecha\n\n");
    for (int i = 0; i < MENU_N && o < sizeof text - 160; i++) {
        int id = menu_items[i], k = atomic_load(&sel[id]);
        o += (size_t)snprintf(text + o, sizeof text - o, "%c %-46s %s\n", i == cursor ? '>' : ' ', cheats[id].label,
                              id == C_TIMER60 ? "" : value_text(id, k));
    }
    vkp_overlay_set(text);
}
static void menu_set(int open) {
    atomic_store(&menu_open, open);
    runtime_input_block(open);
    if (open) { runtime_input_clear(); menu_render(); } else vkp_overlay_set("");
    fprintf(stderr, "[trainer] menu %s\n", open ? "open" : "closed");
}

/* ---- Shared memory for the desktop launcher: a 4096-byte page named Local\UltraTearsTrainer, created by whichever side
   starts first (the launcher's Python opens it with mmap(tagname=...)). The game bumps game_tick every present and
   mirrors the selectors into cur[]; the launcher writes req[] (and take_mask for one-shots) then bumps gui_seq, and the
   game applies the request on its next tick and answers with game_seq = gui_seq. A request left over from a previous
   run is ignored (game_seq is set to gui_seq at start). All fields are plain 32-bit words at fixed offsets. */
typedef struct {
    uint32_t magic, version, game_tick, gui_seq, game_seq, menu_open, count, reserved;
    int32_t cur[32];       /* game -> launcher: the selector of each id */
    int32_t req[32];       /* launcher -> game: the wanted selectors */
    uint32_t take_mask;    /* launcher -> game: one-shots, bit = id; cleared by the game */
} TrainerShm;
#define SHM_MAGIC 0x52545455u   /* "UTTR" */
static volatile TrainerShm *shm;
static uint32_t shm_seen;
static void shm_init(void) {
#ifdef _WIN32
    HANDLE h = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 4096, "Local\\UltraTearsTrainer");
    if (!h) { fprintf(stderr, "[trainer] shared memory: CreateFileMapping error %lu\n", GetLastError()); return; }
    volatile TrainerShm *m = MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, 4096);
    if (!m) { fprintf(stderr, "[trainer] shared memory: MapViewOfFile error %lu\n", GetLastError()); CloseHandle(h); return; }
    for (int i = 0; i < C_COUNT; i++) m->cur[i] = atomic_load(&sel[i]);
    m->count = C_COUNT;
    m->version = 1;
    m->magic = SHM_MAGIC;
    shm_seen = m->gui_seq;
    m->game_seq = shm_seen;
    shm = m;
    fprintf(stderr, "[trainer] shared memory Local\\UltraTearsTrainer ready (launcher control)\n");
#endif
}
static void announce(int id);
static void change(int id, int dir);
static void shm_tick(void) {
    volatile TrainerShm *m = shm;
    if (!m) return;
    m->game_tick++;
    uint32_t s = m->gui_seq;
    if (s != shm_seen) {
        shm_seen = s;
        for (int i = 1; i < C_COUNT; i++) {
            if (i == C_TIMER60) continue;
            int k = m->req[i];
            if (k < 0) k = 0;
            if (k > cheats[i].max) k = cheats[i].max;
            if (k != atomic_load(&sel[i])) { atomic_store(&sel[i], k); announce(i); }
        }
        uint32_t t = m->take_mask;
        m->take_mask = 0;
        for (int i = 1; i < C_COUNT; i++)
            if ((t & (1u << i)) && i == C_TIMER60) change(i, 1);
        if (atomic_load(&menu_open)) menu_render();
        m->game_seq = s;
    }
    for (int i = 1; i < C_COUNT; i++) m->cur[i] = atomic_load(&sel[i]);
    m->menu_open = (uint32_t)atomic_load(&menu_open);
}

/* Persistence for runs without the launcher (the launcher keeps its own copy in mods.json and passes TOTK_CHEATS, which
   wins): trainer-cheats.cfg in the working directory, "name=k" lines, rewritten whenever a selector changes. The one-shot
   timer and the blood moon are not kept (they would fire at every start). */
#define CFG_FILE "trainer-cheats.cfg"
static int cfg_saved[C_COUNT];
static int cfg_kept(int i) { return i != C_TIMER60 && i != C_BLOODMOON; }
static void cfg_load(void) {
    FILE *f = fopen(CFG_FILE, "r");
    if (!f) return;
    char line[96];
    while (fgets(line, sizeof line, f)) {
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = 0;
        int k = atoi(eq + 1);
        for (int i = 1; i < C_COUNT; i++)
            if (cfg_kept(i) && !strcmp(cheats[i].name, line)) {
                if (k < 0 || k > cheats[i].max) k = 0;
                atomic_store(&sel[i], k);
                cfg_saved[i] = k;
                if (k) fprintf(stderr, "[trainer] %s = %s (" CFG_FILE ")\n", cheats[i].name, value_text(i, k));
            }
    }
    fclose(f);
}
static void cfg_save_if_changed(void) {
    int now[C_COUNT], diff = 0;
    for (int i = 1; i < C_COUNT; i++) {
        now[i] = cfg_kept(i) ? atomic_load(&sel[i]) : 0;
        if (now[i] != cfg_saved[i]) diff = 1;
    }
    if (!diff) return;
    FILE *f = fopen(CFG_FILE, "w");
    if (!f) { memcpy(cfg_saved, now, sizeof now); return; }
    for (int i = 1; i < C_COUNT; i++)
        if (cfg_kept(i)) fprintf(f, "%s=%d\n", cheats[i].name, now[i]);
    fclose(f);
    memcpy(cfg_saved, now, sizeof now);
}

static int enabled(void) {
    if (g_enabled < 0) {
        const char *e = getenv("TOTK_TRAINER");
        g_enabled = !(e && !strcmp(e, "0"));
        if (g_enabled) {
            fprintf(stderr, "[trainer] on (TOTK_TRAINER=0 turns it off): F1 opens the menu; hotkeys numpad 1 HP, 2 stamina, 3 battery, "
                            "4 durability, 5 arrows, 6 zonai, 7 food, 8 items x999, 9 shop, 0 stealth, + damage, - move speed (hold B), "
                            "* glider (hold A), / time, . +60 s timer, Enter Moon Jump (hold X); F2 map, F3 gloom, F4 thermometer, "
                            "F5 horse, F6 skills, F7 charge, F8 jump/dive, F9 run, F10 fog, F12 no zonai limit\n");
            const char *list = getenv("TOTK_CHEATS");
            if (!list) cfg_load();   /* started without the launcher: the last session's cheats */
            for (const char *p = list; p && *p;) {
                size_t n = strcspn(p, ",");
                size_t eq = strcspn(p, "=,");
                int k = eq < n ? atoi(p + eq + 1) : 1;
                int found = 0;
                for (int i = 1; i < C_COUNT; i++)
                    if (strlen(cheats[i].name) == eq && !strncmp(cheats[i].name, p, eq)) {
                        if (k < 0 || k > cheats[i].max) k = cheats[i].max;
                        atomic_store(&sel[i], k);
                        fprintf(stderr, "[trainer] %s = %s (TOTK_CHEATS)\n", cheats[i].name, value_text(i, k));
                        found = 1;
                    }
                if (!found) fprintf(stderr, "[trainer] TOTK_CHEATS: unknown cheat \"%.*s\"\n", (int)eq, p);
                p += n + (p[n] == ',');
            }
            shm_init();
        }
    }
    return g_enabled;
}

/* The generated code asks this at every patched site: the selector, or 0 while a "hold" cheat's button is up. */
int trainer_sel(int id) {
    if (id <= 0 || id >= C_COUNT) return 0;
    int v = atomic_load_explicit(&sel[id], memory_order_relaxed);
    if (v && cheats[id].hold && !(atomic_load_explicit(&g_buttons, memory_order_relaxed) & cheats[id].hold)) return 0;
    return v;
}
int trainer_take(int id) {
    if (id <= 0 || id >= C_COUNT) return 0;
    return atomic_exchange(&take[id], 0);
}

static void announce(int id) {
    fprintf(stderr, "[trainer] %s: %s%s\n", cheats[id].name, value_text(id, atomic_load(&sel[id])),
            cheats[id].hold ? (cheats[id].hold == 1 ? " (hold A)" : cheats[id].hold == 2 ? " (hold B)" : " (hold X)") : "");
}
static void change(int id, int dir) {   /* dir +1 / -1 through the selector; a one-shot fires */
    if (id == C_TIMER60) { atomic_store(&take[id], 1); fprintf(stderr, "[trainer] mission timer +60 s\n"); return; }
    int n = cheats[id].max + 1, k = (atomic_load(&sel[id]) + dir + n) % n;
    atomic_store(&sel[id], k);
    announce(id);
    if (atomic_load(&menu_open)) menu_render();
}

/* Menu navigation (keyboard through trainer_key, controller through trainer_tick). */
enum { NAV_UP, NAV_DOWN, NAV_LEFT, NAV_RIGHT, NAV_ENTER, NAV_CLOSE };
static void menu_nav(int what) {
    switch (what) {
    case NAV_UP: cursor = (cursor + MENU_N - 1) % MENU_N; menu_render(); break;
    case NAV_DOWN: cursor = (cursor + 1) % MENU_N; menu_render(); break;
    case NAV_LEFT: change(menu_items[cursor], -1); break;
    case NAV_RIGHT: case NAV_ENTER: change(menu_items[cursor], 1); break;
    case NAV_CLOSE: menu_set(0); break;
    }
}

/* window_sdl.c: key 0-9 = numpad digits, 10 '+', 11 '-', 12 '*', 13 '/', 14 '.', 15 Enter, 20..25 = up, down, left,
   right, Return, Escape, 101..112 = F1..F12, -1 = any other key. Returns 1 when the key is the trainer's (the game does
   not see it): F1 always, every key while the menu is open, the hotkeys otherwise. */
int trainer_key(int key, int down) {
    if (!enabled()) return 0;
    if (key == 101) { if (down) menu_set(!atomic_load(&menu_open)); return 1; }
    if (atomic_load(&menu_open)) {
        if (down && key >= 20 && key <= 25) menu_nav(key == 20 ? NAV_UP : key == 21 ? NAV_DOWN : key == 22 ? NAV_LEFT : key == 23 ? NAV_RIGHT : key == 24 ? NAV_ENTER : NAV_CLOSE);
        return 1;
    }
    static const int numpad[16] = {C_STEALTH, C_HEALTH, C_STAMINA, C_BATTERY, C_DURAB, C_ARROWS, C_ZONAI, C_FOOD, C_ITEMS999, C_SHOP,
                                   C_DAMAGE, C_MOVE, C_GLIDER, C_TIME, C_TIMER60, C_MOONJUMP};
    static const int fkeys[13] = {0, 0, C_MAP, C_GLOOM, C_THERMO, C_HORSE, C_SKILLS, C_CHARGE, C_DIVEJUMP, C_RUNWALK, C_FOG, 0, C_ZONAILIMIT};
    int id = key >= 0 && key < 16 ? numpad[key] : key >= 101 && key <= 112 ? fkeys[key - 100] : 0;
    if (!id) return 0;
    if (down) change(id, 1);
    return 1;
}

/* ---- the two memory cheats, applied every frame (trainer_tick, from the present) */
static uint8_t *const g_main = (uint8_t *)0x800000000ULL;   /* main's load address (tools/gen_game.py MODULES; guest == host) */
static int writable(void *p) {
#ifdef _WIN32
    DWORD old;
    return VirtualProtect((void *)((uintptr_t)p & ~0xfffULL), 0x1000, PAGE_READWRITE, &old) != 0;
#else
    (void)p; return 1;
#endif
}
static int in_guest(uint64_t a) { return a >= 0x100000000ULL && a < (1ULL << 39); }

static void glider_tick(void) {   /* main+0x2d01d70: the glider's speed factor (1/30 shipped), written while A is held */
    static const uint32_t values[5] = {0x3d088889u, 0x3f000000u, 0x3f28f5c3u, 0x3f7d70a4u, 0x3fa08312u};
    static int ready, last = -1;
    int k = trainer_sel(C_GLIDER);
    if (k == last) return;
    uint32_t *at = (uint32_t *)(g_main + 0x2d01d70);
    if (!ready) {
        if (*at != values[0]) { fprintf(stderr, "[trainer] glider: main+0x2d01d70 holds %08x, not 1/30: cheat disabled\n", *at); atomic_store(&sel[C_GLIDER], 0); return; }
        if (!writable(at)) { fprintf(stderr, "[trainer] glider: cannot make the page writable\n"); atomic_store(&sel[C_GLIDER], 0); return; }
        ready = 1;
    }
    *at = values[k];
    last = k;
}
static void moonjump_tick(void) {   /* [[[[[[main+0x3ad99b8]+0x70]+0x40]+0xd0]+0xc0]+0x198]+0x38 + 0xf4 := 10.0 while X is held */
    if (!trainer_sel(C_MOONJUMP)) return;
    static const uint32_t steps[] = {0x70, 0x40, 0xd0, 0xc0, 0x198, 0x38};
    uint64_t p;
    memcpy(&p, g_main + 0x3ad99b8, 8);
    for (unsigned i = 0; i < sizeof steps / sizeof *steps; i++) {
        if (!in_guest(p)) return;
        memcpy(&p, (void *)(uintptr_t)(p + steps[i]), 8);
    }
    if (!in_guest(p)) return;
    uint32_t v = 0x41200000u;
    memcpy((void *)(uintptr_t)(p + 0xf4), &v, 4);
}

/* Controller: Plus + Minus together opens and closes the menu; inside it the d-pad moves and changes, A toggles, B closes.
   Edges only (a press counts once); the game gets none of it while the menu is open (runtime_input_block). */
static void pad_menu(uint64_t now) {
    static uint64_t prev;
    uint64_t pressed = now & ~prev;
    prev = now;
    if ((now & (1024 | 2048)) == (1024 | 2048) && (pressed & (1024 | 2048))) { menu_set(!atomic_load(&menu_open)); return; }
    if (!atomic_load(&menu_open)) return;
    if (pressed & 8192) menu_nav(NAV_UP);
    if (pressed & 32768) menu_nav(NAV_DOWN);
    if (pressed & 4096) menu_nav(NAV_LEFT);
    if (pressed & 16384) menu_nav(NAV_RIGHT);
    if (pressed & 1) menu_nav(NAV_ENTER);
    if (pressed & 2) menu_nav(NAV_CLOSE);
}

void trainer_tick(void) {
    if (!enabled()) return;
    uint64_t b = runtime_input_buttons();
    atomic_store_explicit(&g_buttons, b, memory_order_relaxed);
    pad_menu(b);
    shm_tick();
    cfg_save_if_changed();
    glider_tick();
    moonjump_tick();
}

/* The window title's trainer part: the active cheats' short names. */
const char *trainer_title(void) {
    static char buf[256];
    if (!enabled()) return "";
    size_t o = 0;
    for (int i = 1; i < C_COUNT && o < sizeof buf - 24; i++) {
        int k = atomic_load_explicit(&sel[i], memory_order_relaxed);
        if (!k || !cheats[i].title[0]) continue;
        o += (size_t)snprintf(buf + o, sizeof buf - o, "%s%s", o ? " " : "  |  cheats: ", cheats[i].title);
        if (cheats[i].values && cheats[i].max > 1) o += (size_t)snprintf(buf + o, sizeof buf - o, "(%s)", cheats[i].values[k]);
    }
    if (!o) buf[0] = 0;
    return buf;
}
