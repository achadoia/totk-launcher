"""UltraTears - Launcher grafico (mods, trainer e modo de jogo), no estilo WeMod. Abra pelo LAUNCHER.bat.

Como funciona
- A interface e a pagina launcher\\ui.html, aberta numa janela do Edge em modo app (sem barra de endereco) e servida por
  este script em http://127.0.0.1:<porta> (so local). Fechou a janela, o script termina. Sem Edge, abre no navegador padrao.
- Mods: extrai os arquivos de D:\\zelda\\mods (zip nativo; rar pelo UnRAR do WinRAR ou 7-Zip; 7z pelo 7-Zip ou WinRAR;
  .bnp = BOTW, nao serve) para mods\\_extraidos uma vez, lista cada pasta "romfs" como um mod, avisa compatibilidade (1.4.3)
  e conflitos. Nao copia nada: o jogo le as pastas ligadas por cima da imagem (TOTK_ROMFS_OVERLAY=pasta1;pasta2;...,
  a primeira vence; data\\romfs-overlay, com o HeapDef, vem sempre primeiro). Ligar e desligar e instantaneo.
- Trainer: os cheats do trainer embutido (src/host/trainer.c). Com o jogo fechado, os ligados aqui entram na abertura
  (TOTK_CHEATS=...). Com o jogo aberto, ligar e desligar aqui vale na hora, e as teclas de atalho do jogo refletem aqui:
  a troca e pela memoria compartilhada Local\\UltraTearsTrainer (4096 bytes; ver TrainerShm em trainer.c).
- Modo: 30 fps / 60 fps / 60+1080p / 60+1440p (variaveis TOTK_*), igual aos JOGAR-*.bat.

Linha de comando: --console abre o launcher antigo em texto (LAUNCHER-console.py); --dry-run mostra as variaveis que o
jogo receberia; --list lista os mods.
"""
import json
import mmap
import os
import pathlib
import re
import struct
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent
BUILDER = ROOT / 'UltraTears-builder-0.1.0-windows-x86_64'
MODS = ROOT / 'mods'
EXTRACT = MODS / '_extraidos'
OVERLAY = BUILDER / 'data' / 'romfs-overlay'
GENERATED = MODS / '_gerado' / 'romfs'
CONFIG = MODS / 'mods.json'
LOG = ROOT / 'ultratears-run.log'
EXE = BUILDER / 'UltraTears.exe'
PYTHON = BUILDER / 'deps' / 'python' / 'python.exe'
UI_DIR = ROOT / 'launcher'
UI_HTML = UI_DIR / 'ui.html'
UI_LOG = UI_DIR / 'launcher.log'
EDGE_PROFILE = UI_DIR / 'edge-profile'
KEYS_TXT = ROOT / 'TRAINER-TECLAS.txt'
HEAPDEF_REL = pathlib.Path('System') / 'HeapSize' / 'Product.Nin_NX_NVN.HeapDef.byml.zs'


def _first(*paths):
    return next((p for p in paths if p.is_file()), None)


PF, PF86 = pathlib.Path(os.environ.get('ProgramFiles', r'C:\Program Files')), pathlib.Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'))
LOCAL = pathlib.Path(os.environ.get('LOCALAPPDATA', r'C:\Users\Public'))
SEVENZIP = _first(PF / '7-Zip' / '7z.exe', PF86 / '7-Zip' / '7z.exe')
UNRAR = _first(PF / 'WinRAR' / 'UnRAR.exe', PF86 / 'WinRAR' / 'UnRAR.exe')
WINRAR = _first(PF / 'WinRAR' / 'WinRAR.exe', PF86 / 'WinRAR' / 'WinRAR.exe')
EDGE = _first(PF86 / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe', PF / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe',
              LOCAL / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe')
ARCHIVES = ('.zip', '.7z', '.rar', '.bnp')
# What boot tests on this PC showed for specific mods (part of the mod's key -> note). A note with "trava" or
# "deixe desligado" keeps the mod off by default.
KNOWN = {
    'dragon_glider_-_demon_dragon': 'trava o jogo na 1.4.3 (Player.pack e Parasail.pack da 1.2.1; testado com e sem o Second Quest)',
    'warglaives_v4_2': 'so abre com o Second Quest ligado (sem ele trava na abertura; testado)',
    '4k_v12s': 'mod de emulador (patches de exefs que o port nao usa): so o HeapDef entraria, gastando RAM. Deixe desligado',
}
NO_AUTO = ('pasta para a versao', 'trava', 'deixe desligado')

MODES = [
    ('30 fps original', 'Como no Switch. Use se algo der errado nos outros modos.',
     {'TOTK_FPS60': '0', 'TOTK_REALTIME': '0', 'TOTK_CLOTH_REALTIME': '0', 'TOTK_HIGHFPS_FIX': '0'}),
    ('60 fps dinamico', 'Cena em 1600x900 (a do Switch). O mais leve dos modos de 60 fps.',
     {'TOTK_FPS60': '1', 'TOTK_REALTIME': '1', 'TOTK_MAX_INTERVAL': '1'}),
    ('60 fps + cena 1080p', 'Experimental: heaps do jogo nao crescem. Pode faltar memoria em areas pesadas.',
     {'TOTK_FPS60': '1', 'TOTK_REALTIME': '1', 'TOTK_MAX_INTERVAL': '1', 'TOTK_RENDER_RES': '1920x1080'}),
    ('60 fps + cena 1440p', 'Recomendado: HeapDef maior pelo overlay em data\\romfs-overlay (gerado na primeira vez).',
     {'TOTK_FPS60': '1', 'TOTK_REALTIME': '1', 'TOTK_MAX_INTERVAL': '1', 'TOTK_RENDER_RES': '2560x1440'}),
]
ALL_ENV = ('TOTK_FPS60', 'TOTK_REALTIME', 'TOTK_CLOTH_REALTIME', 'TOTK_HIGHFPS_FIX', 'TOTK_MAX_INTERVAL', 'TOTK_RENDER_RES',
           'TOTK_ROMFS_OVERLAY', 'TOTK_CHEATS', 'TOTK_TRAINER')

# The trainer's cheats (ids, names and levels as in src/host/trainer.c; hotkeys as in trainer_key). cat = the tab's group,
# key = the in-game hotkey, hold = the controller button the cheat needs held, oneshot = a button instead of a switch.
CHEATS = [
    dict(id=1, name='health', label='Vida infinita', cat='Link', key='Numpad 1'),
    dict(id=2, name='stamina', label='Stamina infinita', cat='Link', key='Numpad 2', values=['Desligado', 'Ligado', 'Missao da Master Sword']),
    dict(id=3, name='battery', label='Bateria infinita', cat='Link', key='Numpad 3'),
    dict(id=16, name='durability', label='Durabilidade infinita', cat='Link', key='Numpad 4'),
    dict(id=17, name='arrows', label='Flechas infinitas', cat='Link', key='Numpad 5'),
    dict(id=18, name='zonai', label='Dispositivos Zonai infinitos', cat='Link', key='Numpad 6', hint='e nunca acabam'),
    dict(id=19, name='food', label='Comida infinita', cat='Link', key='Numpad 7', hint='buffs sem fim'),
    dict(id=14, name='items', label='Itens x999 ao pegar', cat='Link', key='Numpad 8', hint='faca backup do save'),
    dict(id=15, name='shop', label='Compras de graca', cat='Link', key='Numpad 9'),
    dict(id=5, name='stealth', label='Modo furtivo', cat='Link', key='Numpad 0'),

    dict(id=11, name='move', label='Velocidade de movimento', cat='Movimento', key='Numpad -', hold='B', values=['x1', 'x2', 'x3', 'x10']),
    dict(id=27, name='glider', label='Velocidade do planador', cat='Movimento', key='Numpad *', hold='A', values=['x1', 'x1.5', 'x2', 'x3', 'x3.8']),
    dict(id=28, name='moonjump', label='Moon Jump', cat='Movimento', key='Numpad Enter', hold='X'),
    dict(id=12, name='jump', label='Mergulho forte + pulo alto', cat='Movimento', key='F8'),
    dict(id=13, name='run', label='Movimento padrao', cat='Movimento', key='F9', values=['Do jogo', 'Correr']),

    dict(id=10, name='damage', label='Multiplicador de dano', cat='Combate', key='Numpad +', values=['x1', 'x2', 'x4', 'x10', 'x30']),
    dict(id=8, name='skills', label='Habilidades dos sabios sem recarga', cat='Combate', key='F6'),
    dict(id=9, name='charge', label='Carga rapida de arma e arco', cat='Combate', key='F7', hint='tiro rapido'),
    dict(id=4, name='horse', label='Stamina do cavalo infinita', cat='Combate', key='F5'),

    dict(id=23, name='time', label='Tempo do mundo', cat='Mundo', key='Numpad /', values=['Normal', 'Parado', 'Avancar', 'Voltar', 'Dias 2x', 'Dias 3x', 'Dias 4x']),
    dict(id=24, name='timer60', label='+60 s no cronometro de missao', cat='Mundo', key='Numpad .', oneshot=True),
    dict(id=21, name='map', label='Mapa: mostrar tudo', cat='Mundo', key='F2', hint='santuarios, cavernas, koroks, torres, lagrimas...'),
    dict(id=7, name='gloom', label='Ignorar gloom', cat='Mundo', key='F3'),
    dict(id=6, name='thermo', label='Termometro perfeito', cat='Mundo', key='F4', hint='sem fogo, calor ou frio'),
    dict(id=25, name='fog', label='Sem neblina', cat='Mundo', key='F10'),
    dict(id=22, name='nolimit', label='Sem limite de 21 dispositivos Zonai', cat='Mundo', key='F12'),
    dict(id=26, name='bloodmoon', label='Lua de sangue instantanea', cat='Mundo', hint='ligue e desligue'),
    dict(id=20, name='amiibo', label='Amiibo infinito', cat='Mundo'),
]
for _c in CHEATS:
    _c['max'] = len(_c['values']) - 1 if _c.get('values') else 1
BY_NAME = {c['name']: c for c in CHEATS}
SHM_NAME = 'Local\\UltraTearsTrainer'
SHM_MAGIC = 0x52545455
SHM_HEAD = '<8I'          # magic, version, game_tick, gui_seq, game_seq, menu_open, count, reserved
OFF_CUR, OFF_REQ, OFF_TAKE = 32, 160, 288


def nice_name(path):
    return re.sub(r'(-v?\d+)+$', '', path.stem)   # sufixos do Nexus (-156-6-0-1769720733, -28-v1-1685422563)


def load_config():
    try:
        cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
        if not isinstance(cfg, dict):
            raise ValueError
    except (OSError, ValueError):
        cfg = {}
    mode = cfg.get('mode', 4)
    cfg['mode'] = mode if isinstance(mode, int) and 1 <= mode <= len(MODES) else 4
    cfg['enabled'] = [k for k in cfg.get('enabled', []) if isinstance(k, str)]
    ch = cfg.get('cheats', {})
    cfg['cheats'] = {n: int(k) for n, k in ch.items() if n in BY_NAME and isinstance(k, int)} if isinstance(ch, dict) else {}
    seen = cfg.get('seen')   # mods already offered once; a mod seen for the first time is switched on (the launcher's default)
    cfg['seen'] = [k for k in seen if isinstance(k, str)] if isinstance(seen, list) else None
    return cfg


def save_config(cfg):
    MODS.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=1, ensure_ascii=False), encoding='utf-8')


# ---------------------------------------------------------------------------------------------------------------- mods
def extract_all(progress=lambda s: None):
    EXTRACT.mkdir(parents=True, exist_ok=True)
    skipped = []
    for a in sorted(MODS.iterdir()):
        if not a.is_file() or a.suffix.lower() not in ARCHIVES or a.suffix.lower() == '.bnp':
            continue
        dest = EXTRACT / a.stem
        if dest.exists():
            continue
        progress(f'Extraindo {a.name} ...')
        dest.mkdir(parents=True)
        try:
            ext = a.suffix.lower()
            if ext == '.zip':
                with zipfile.ZipFile(a) as z:
                    z.extractall(dest)
            elif ext == '.rar':   # WinRAR's UnRAR, else 7-Zip, else the tar of Windows (reads only some RAR files)
                if UNRAR:
                    subprocess.run([str(UNRAR), 'x', '-y', '-idq', str(a), str(dest) + '\\'], check=True, creationflags=0x08000000)
                elif SEVENZIP:
                    subprocess.run([str(SEVENZIP), 'x', '-y', f'-o{dest}', str(a)], check=True, stdout=subprocess.DEVNULL, creationflags=0x08000000)
                else:
                    subprocess.run(['tar.exe', '-xf', str(a), '-C', str(dest)], check=True, creationflags=0x08000000)
            elif ext == '.7z':   # 7-Zip, else WinRAR (it opens 7z too)
                if SEVENZIP:
                    subprocess.run([str(SEVENZIP), 'x', '-y', f'-o{dest}', str(a)], check=True, stdout=subprocess.DEVNULL, creationflags=0x08000000)
                elif WINRAR:
                    subprocess.run([str(WINRAR), 'x', '-y', '-ibck', str(a), str(dest) + '\\'], check=True, creationflags=0x08000000)
                else:
                    raise RuntimeError('precisa do 7-Zip ou do WinRAR')
        except Exception as e:   # noqa: BLE001
            import shutil
            shutil.rmtree(dest, ignore_errors=True)
            skipped.append(f'{a.name}: {e}')
    return skipped


def scan_mods():
    """One entry per romfs folder found in: the archives of mods\\ (extracted to mods\\_extraidos), folders already in
    mods\\_extraidos (the archive may have been deleted) and plain folders dropped into mods\\."""
    mods = []
    if not MODS.is_dir():
        return mods
    sources = {}   # base name -> (folder or None, archive or None)
    for a in sorted(MODS.iterdir()):
        if a.is_file() and a.suffix.lower() in ARCHIVES:
            sources[a.stem] = (EXTRACT / a.stem if (EXTRACT / a.stem).is_dir() else None, a)
        elif a.is_dir() and a != EXTRACT and not a.name.startswith(('.', '_')):   # _extraidos, _gerado: ours
            sources.setdefault(a.name, (a, None))
    if EXTRACT.is_dir():
        for d in sorted(EXTRACT.iterdir()):
            if d.is_dir():
                sources.setdefault(d.name, (d, None))
    for base in sorted(sources, key=str.lower):
        dest, a = sources[base]
        name = nice_name(pathlib.Path(base))
        if a is not None and a.suffix.lower() == '.bnp':
            mods.append(dict(key=base, name=name, romfs=None, files=[], status='Mod de BOTW (.bnp): incompativel', usable=False, kind='other'))
            continue
        if dest is None:
            mods.append(dict(key=base, name=name, romfs=None, files=[], status='Nao extraido (precisa do 7-Zip ou WinRAR)', usable=False, kind='other'))
            continue
        romfs_dirs = []
        for dirpath, dirnames, _ in os.walk(dest):
            hit = [d for d in dirnames if d.lower() == 'romfs']   # "romfs", "RomFS", ...
            if hit:
                romfs_dirs.append(pathlib.Path(dirpath) / hit[0])
                dirnames.remove(hit[0])
        if not romfs_dirs:
            tkcl = any(f.suffix.lower() == '.tkcl' for f in dest.rglob('*'))
            st = 'So .tkcl: precisa do TKMM (gere a pasta romfs com ele e coloque em mods\\)' if tkcl else 'Sem pasta romfs'
            mods.append(dict(key=base, name=name, romfs=None, files=[], status=st, usable=False, kind='other'))
            continue
        for r in sorted(romfs_dirs):
            parent = r.parent
            rel = str(parent.relative_to(dest)).replace('\\', '/')
            if rel == '.':
                rel = ''
            # the label: the mod's name plus the romfs folder's path, minus the parts that only repeat the name
            parts = [p for p in rel.split('/') if p and p.lower() not in (base.lower(), name.lower()) and not p.lower().startswith(name.lower())]
            label = f'{name} / {" / ".join(parts)}' if parts else name
            key = f'{base}|{rel}' if rel else base
            files = [str(f.relative_to(r)).replace('\\', '/') for f in r.rglob('*') if f.is_file()]
            flags = []
            kind = 'game'
            if (parent / 'exefs').is_dir():
                flags.append('exefs ignorado (so o romfs entra)')
            if any(f.suffix.lower() == '.tkcl' for f in parent.iterdir() if f.is_file()):
                flags.append('tem .tkcl (essa parte so via TKMM)')
            gv = re.search(r'(?i)(?:game\s*)?version\s*(1\.\d(?:\.\d)?)', rel)   # "Game Version 1.0.0" folders: one per game version
            if gv and gv.group(1) not in ('1.4.3', '1.4'):
                flags.append(f'pasta para a versao {gv.group(1)}: pode travar na 1.4.3 (escolha a mais proxima de 1.4.3)')
            rstb = sorted({m.group(1) for f in files for m in [re.search(r'ResourceSizeTable\.Product\.(\d+)\.rsizetable', f)] if m})
            ntex = sum(1 for f in files if f.startswith('TexToGo/'))
            if ntex and all(f.startswith(('TexToGo/', 'System/')) for f in files):
                kind = 'texture'
                flags.append(f'pacote de texturas ({ntex}); RSTB 1.4.3 gerada na abertura, BC7 na primeira vez')
            else:
                if rstb and '143' not in rstb:
                    flags.append(f'RSTB da v{"/".join(rstb)} (a 1.4.3 e gerada por estimativa na abertura)')
                if any(f.startswith('System/HeapSize/') for f in files):
                    flags.append('traz HeapDef (fundido com o nosso)')
            for part, note in KNOWN.items():
                if part.lower() in key.lower():
                    flags.insert(0, note)
            mods.append(dict(key=key, name=label, romfs=r, files=files, status='; '.join(flags) or 'OK', usable=True, kind=kind))
    return mods


def conflicts(mods, enabled):
    """[(file, [names...])] for files that two or more enabled mods both carry (the lower one in the list wins)."""
    seen, out = {}, {}
    for m in mods:
        if not m['usable'] or m['key'] not in enabled:
            continue
        for f in m['files']:
            if f in seen:
                out.setdefault(f, [seen[f]]).append(m['name'])
            else:
                seen[f] = m['name']
    return [(f, names) for f, names in out.items()]


def mod_dirs(mods, enabled):
    """The enabled mods' romfs folders from the last to the first: the lower one in the list wins."""
    return [m['romfs'] for m in reversed(mods) if m['usable'] and m['key'] in enabled]


def overlay_env(mods, enabled, generated=None):
    """The generated RSTB/HeapDef overlay (mods\\_gerado\\romfs) first, then data\\romfs-overlay (HeapDef for the scene),
    then the enabled mods from the last to the first: the lower one wins."""
    dirs = ([str(generated)] if generated else []) + [str(OVERLAY)] + [str(d) for d in mod_dirs(mods, enabled)]
    return ';'.join(dirs)


def generate_rstb(dirs, progress=lambda s: None, log_file=None):
    """tools/make_mod_rstb.py for the enabled mods (RSTB 1.4.3 raised to their files' sizes + merged HeapDef) into
    mods\\_gerado\\romfs, skipped when the same mods with the same files were done before. -> the folder, or None."""
    if not dirs:
        return None
    out = GENERATED
    sig = []
    for d in dirs:
        n = total = newest = 0
        for dirpath, _, names in os.walk(d):
            for f in names:
                try:
                    st = os.stat(os.path.join(dirpath, f))
                except OSError:
                    continue
                n += 1
                total += st.st_size
                newest = max(newest, int(st.st_mtime))
        sig.append([str(d), n, total, newest])
    for extra in (BUILDER / 'tools' / 'make_mod_rstb.py', OVERLAY / HEAPDEF_REL, OVERLAY / 'overlay.json'):   # the script or our scene overlay changed
        try:
            sig.append([extra.name, int(extra.stat().st_mtime)])
        except OSError:
            sig.append([extra.name, 0])
    sig_file = out / 'signature.json'
    try:
        if sig_file.is_file() and json.loads(sig_file.read_text(encoding='utf-8')) == sig and (out / 'overlay.json').is_file():
            return out
    except (OSError, ValueError):
        pass
    progress('Gerando a tabela de tamanhos (RSTB 1.4.3) para os mods ligados...')
    out.mkdir(parents=True, exist_ok=True)
    sig_file.unlink(missing_ok=True)
    cmd = [str(PYTHON), '-I', 'tools/make_mod_rstb.py', '--out', str(out)] + [str(d) for d in dirs]
    r = subprocess.run(cmd, cwd=BUILDER, stdout=log_file or subprocess.DEVNULL, stderr=subprocess.STDOUT, creationflags=0x08000000)
    if r.returncode != 0:
        return None
    sig_file.write_text(json.dumps(sig), encoding='utf-8')
    return out


def cheats_env(cheats):
    return ','.join(f'{n}={k}' for n, k in cheats.items() if k and n in BY_NAME and not BY_NAME[n].get('oneshot'))


def game_running():
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq UltraTears.exe', '/NH'], capture_output=True, text=True,
                             creationflags=0x08000000).stdout
        return 'UltraTears.exe' in out
    except OSError:
        return False


# ----------------------------------------------------------------------------------------------------------- the app
class App:
    def __init__(self):
        self.lock = threading.Lock()
        cfg = load_config()
        self.mode, self.enabled, self.cheats, self.seen = cfg['mode'], cfg['enabled'], cfg['cheats'], cfg['seen']
        self.mods, self.skipped = [], []
        self.busy = 'Lendo os mods...'
        self.events = []          # [(n, text, kind)] shown as toasts
        self.event_n = 0
        self.proc = None
        self.alive = False        # the game's trainer answers through the shared memory
        self.fps = 0.0
        self.menu_open = False
        self.last_poll = time.time()
        self.running_cache = (0.0, False)
        self.shm = None
        self.gui_seq = 0
        self._tick = (0, 0.0)
        self._applied_seq = -1
        self.shm_open()

    # -- messages
    def say(self, text, kind='info'):
        with self.lock:
            self.event_n += 1
            self.events = (self.events + [(self.event_n, text, kind)])[-20:]
        log(text)

    # -- mods
    def refresh_mods(self):
        with self.lock:
            self.busy = 'Lendo os mods...'
        try:
            skipped = extract_all(lambda s: self.set_busy(s))
            mods = scan_mods()
        except Exception as e:   # noqa: BLE001
            log(traceback.format_exc())
            self.say(f'Erro ao ler os mods: {e}', 'error')
            mods, skipped = [], []
        with self.lock:
            self.mods, self.skipped, self.busy = mods, skipped, None
            # new mods start switched on (except folders made for another game version); the first run with an old
            # mods.json (no "seen" list) keeps the user's choices and only records what exists
            keys = [m['key'] for m in mods]
            new_on = []
            if self.seen is not None:
                for m in mods:
                    if m['key'] in self.seen or not m['usable'] or m['key'] in self.enabled:
                        continue
                    if any(w in m['status'] for w in NO_AUTO):
                        continue
                    self.enabled.append(m['key'])
                    new_on.append(m['name'])
            changed = self.seen is None or set(keys) - set(self.seen)
            self.seen = sorted(set(self.seen or []) | set(keys))
        if changed:
            self.save()
        for n in new_on:
            self.say(f'Mod novo ligado: {n}', 'ok')
        for s in skipped:
            self.say(f'Nao extraido: {s}', 'warn')

    def set_busy(self, text):
        with self.lock:
            self.busy = text

    def save(self):
        with self.lock:
            cfg = {'mode': self.mode, 'enabled': list(self.enabled), 'cheats': dict(self.cheats), 'seen': list(self.seen or [])}
        try:
            save_config(cfg)
        except OSError as e:
            self.say(f'Nao consegui gravar mods.json: {e}', 'error')

    # -- trainer shared memory
    def shm_open(self):
        try:
            self.shm = mmap.mmap(-1, 4096, tagname=SHM_NAME)
            self.gui_seq = struct.unpack_from('<I', self.shm, 12)[0]
        except (OSError, ValueError) as e:
            self.shm = None
            log(f'shared memory: {e}')

    def shm_push(self, take_id=None):
        """Write every cheat's wanted level (and a one-shot) and bump gui_seq; the game applies it on its next frame."""
        if not self.shm:
            return
        req = [0] * 32
        for c in CHEATS:
            req[c['id']] = int(self.cheats.get(c['name'], 0))
        struct.pack_into('<32i', self.shm, OFF_REQ, *req)
        if take_id:
            mask = struct.unpack_from('<I', self.shm, OFF_TAKE)[0] | (1 << take_id)
            struct.pack_into('<I', self.shm, OFF_TAKE, mask)
        self.gui_seq = (self.gui_seq + 1) & 0xffffffff
        struct.pack_into('<I', self.shm, 12, self.gui_seq)

    def shm_poll(self):
        """Every 250 ms: is the game ticking? If it has applied our last request, take its state (hotkeys pressed in-game)."""
        if not self.shm:
            return
        magic, _ver, tick, _gseq, game_seq, menu, _count, _r = struct.unpack_from(SHM_HEAD, self.shm, 0)
        now = time.time()
        last_tick, last_t = self._tick
        if tick != last_tick:
            if last_t and now - last_t < 2:
                inst = ((tick - last_tick) & 0xffffffff) / (now - last_t)
                self.fps = inst if not self.fps else self.fps * 0.7 + inst * 0.3
            self._tick = (tick, now)
        alive = magic == SHM_MAGIC and self._tick[1] > 0 and now - self._tick[1] < 6   # loading screens stall the ticks
        changed = False
        with self.lock:
            was = self.alive
            self.alive = alive
            self.menu_open = bool(menu) if alive else False
            if not alive:
                self.fps = 0.0
            elif game_seq == self.gui_seq:
                cur = struct.unpack_from('<32i', self.shm, OFF_CUR)
                for c in CHEATS:
                    k = cur[c['id']]
                    if c.get('oneshot'):
                        continue
                    if k != self.cheats.get(c['name'], 0):
                        self.cheats[c['name']] = k
                        changed = True
        if changed:   # a hotkey or the F1 menu in the game: keep it for the next session too
            self.save()
        if alive and not was:
            self.say('Trainer conectado ao jogo', 'ok')
        if was and not alive:
            self.say('Jogo fechado', 'info')

    def poller(self):
        while True:
            try:
                self.shm_poll()
            except Exception:   # noqa: BLE001
                log(traceback.format_exc())
            time.sleep(0.25)

    def is_running(self):
        if self.alive or (self.proc and self.proc.poll() is None):
            return True
        t, v = self.running_cache
        if time.time() - t > 3:
            v = game_running()
            self.running_cache = (time.time(), v)
        return v

    # -- state for the page
    def state(self):
        with self.lock:
            mods = [dict(key=m['key'], name=m['name'], status=m['status'], usable=m['usable'], kind=m['kind'],
                         files=len(m['files']), on=m['key'] in self.enabled) for m in self.mods]
            conf = [dict(file=f, mods=names) for f, names in conflicts(self.mods, self.enabled)[:40]]
            nconf = len(conflicts(self.mods, self.enabled))
            cheats = [dict(c, k=int(self.cheats.get(c['name'], 0))) for c in CHEATS]
            return dict(
                mods=mods, conflicts=conf, nconflicts=nconf, skipped=self.skipped,
                mode=self.mode, modes=[dict(name=n, desc=d) for n, d, _ in MODES],
                cheats=cheats, busy=self.busy, events=self.events,
                game=dict(running=self.is_running(), alive=self.alive, fps=round(self.fps), menu_open=self.menu_open),
                tools=dict(edge=bool(EDGE), sevenzip=bool(SEVENZIP), winrar=bool(UNRAR or WINRAR), mods_dir=str(MODS)),
            )

    # -- actions
    def set_mod(self, key, on):
        with self.lock:
            m = next((m for m in self.mods if m['key'] == key), None)
            if not m:
                return 'Mod nao encontrado'
            if not m['usable']:
                return m['status']
            if on and key not in self.enabled:
                self.enabled.append(key)
            if not on and key in self.enabled:
                self.enabled.remove(key)
            running = self.alive or (self.proc and self.proc.poll() is None)
        self.save()
        if running:
            self.say('Mods so entram quando o jogo abre pelo launcher: feche e abra de novo', 'warn')
        return None

    def set_mode(self, mode):
        if not isinstance(mode, int) or not 1 <= mode <= len(MODES):
            return 'Modo invalido'
        with self.lock:
            self.mode = mode
        self.save()
        return None

    def set_cheat(self, name, k):
        c = BY_NAME.get(name)
        if not c or not isinstance(k, int):
            return 'Cheat invalido'
        k = max(0, min(k, c['max']))
        with self.lock:
            if c.get('oneshot'):
                if self.alive:
                    self.shm_push(take_id=c['id'])
                    return None
                return 'Esse botao so funciona com o jogo aberto'
            self.cheats[name] = k
            if self.alive:
                self.shm_push()
        self.save()
        return None

    def reset_cheats(self):
        with self.lock:
            self.cheats = {}
            if self.alive:
                self.shm_push()
        self.save()

    def play(self):
        with self.lock:
            if self.busy:
                return 'Espere terminar: ' + self.busy
        if self.is_running():
            return 'O jogo ja esta aberto. Feche-o para abrir de novo (mods so entram na abertura).'
        threading.Thread(target=self._play, daemon=True).start()
        return None

    def _play(self):
        try:
            with self.lock:
                mods, enabled, mode, cheats = list(self.mods), list(self.enabled), self.mode, dict(self.cheats)
            env = {k: v for k, v in os.environ.items() if k not in ALL_ENV}
            env.update(MODES[mode - 1][2])
            env['TOTK_ROMFS_OVERLAY'] = overlay_env(mods, enabled)
            ce = cheats_env(cheats)
            if ce:
                env['TOTK_CHEATS'] = ce
            # texture mods: ASTC -> BC7 once (tools/bc7_build.py), so the game does not encode 4K textures while playing
            for m in mods:
                if not m['usable'] or m['key'] not in enabled or not any(f.startswith('TexToGo/') and f.endswith('.txtg') for f in m['files']):
                    continue
                marker = m['romfs'].parent / '.bc7-cache-done'
                if marker.is_file():
                    continue
                n = sum(1 for f in m['files'] if f.startswith('TexToGo/'))
                self.set_busy(f'Convertendo as {n} texturas de "{m["name"]}" para BC7 (so na primeira vez; pode levar varios minutos)...')
                with open(UI_LOG, 'ab') as lf:
                    r = subprocess.run([str(PYTHON), 'tools/bc7_build.py', '--romfs', str(m['romfs']), '--cache', 'data/textures',
                                        '--threads', str(max(2, (os.cpu_count() or 4) - 2))], cwd=BUILDER, stdout=lf, stderr=subprocess.STDOUT,
                                       creationflags=0x08000000)
                if r.returncode == 0:
                    marker.write_text('ok\n')
                else:
                    self.say(f'Conversao de texturas falhou (codigo {r.returncode}): o jogo converte na hora, com engasgos', 'warn')
            if mode >= 3 and not (OVERLAY / 'overlay.json').is_file():
                self.set_busy('Gerando o overlay do HeapDef (tools\\make_heap_overlay.py --scene 2560x1440)...')
                with open(UI_LOG, 'ab') as lf:
                    subprocess.run([str(PYTHON), '-I', 'tools/make_heap_overlay.py', '--scene', '2560x1440'], cwd=BUILDER,
                                   stdout=lf, stderr=subprocess.STDOUT, creationflags=0x08000000)
            dirs = mod_dirs(mods, enabled)
            generated = None
            if dirs:
                with open(UI_LOG, 'ab') as lf:
                    generated = generate_rstb(dirs, self.set_busy, lf)
                if not generated:
                    self.say('Nao consegui gerar a RSTB 1.4.3 dos mods (veja launcher\\launcher.log): mods grandes podem travar', 'warn')
            env['TOTK_ROMFS_OVERLAY'] = overlay_env(mods, enabled, generated)
            self.set_busy('Abrindo o jogo...')
            names = [m['name'] for m in mods if m['usable'] and m['key'] in enabled]
            log(f'play: {MODES[mode - 1][0]}; mods: {", ".join(names) or "nenhum"}; cheats: {ce or "nenhum"}')
            logf = open(LOG, 'wb')
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, 'DETACHED_PROCESS', 0)
            p = subprocess.Popen([str(EXE)], cwd=BUILDER, env=env, stdout=logf, stderr=subprocess.STDOUT, creationflags=flags)
            with self.lock:
                self.proc = p
            time.sleep(4)
            if p.poll() is None:
                self.say(f'Jogo aberto: {MODES[mode - 1][0]}, {len(names)} mod(s)', 'ok')
            else:
                self.say(f'O jogo fechou logo apos abrir (codigo {p.returncode}). Veja o log na aba Jogo.', 'error')
        except Exception as e:   # noqa: BLE001
            log(traceback.format_exc())
            self.say(f'Erro ao abrir o jogo: {e}', 'error')
        finally:
            self.set_busy(None)

    def stop_game(self):
        try:
            subprocess.run(['taskkill', '/IM', 'UltraTears.exe', '/F'], capture_output=True, creationflags=0x08000000)
        except OSError:
            pass
        self.running_cache = (0.0, False)
        self.say('Jogo fechado pelo launcher', 'info')

    def game_log(self):
        try:
            return LOG.read_text(encoding='utf-8', errors='replace')[-12000:]
        except OSError:
            return '(sem log ainda)'


# ------------------------------------------------------------------------------------------------------------ server
def log(text):
    try:
        UI_DIR.mkdir(parents=True, exist_ok=True)
        with open(UI_LOG, 'a', encoding='utf-8', errors='replace') as f:
            f.write(f'{time.strftime("%H:%M:%S")} {text}\n')
    except OSError:
        pass


class Handler(BaseHTTPRequestHandler):
    app: App = None
    token = ''

    def log_message(self, *a):   # quiet
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        app = self.app
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            try:
                data = UI_HTML.read_bytes()
            except OSError:
                data = b'<h1>launcher\\ui.html nao encontrado</h1>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)
        elif path == '/bg.png':
            try:
                data = (UI_DIR / 'bg.png').read_bytes()
            except OSError:
                return self.send_error(404)
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'max-age=3600')
            self.end_headers()
            self.wfile.write(data)
        elif path == '/api/state':
            app.last_poll = time.time()
            self._json(app.state())
        elif path == '/api/log':
            self._json({'log': app.game_log()})
        elif path == '/api/keys':
            try:
                self._json({'text': KEYS_TXT.read_text(encoding='utf-8', errors='replace')})
            except OSError:
                self._json({'text': 'TRAINER-TECLAS.txt nao encontrado'})
        else:
            self.send_error(404)

    def do_POST(self):
        app = self.app
        n = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            body = {}
        if self.headers.get('X-Token') != self.token:
            return self._json({'error': 'token'}, 403)
        err = None
        path = self.path.split('?')[0]
        try:
            if path == '/api/mod':
                err = app.set_mod(str(body.get('key')), bool(body.get('on')))
            elif path == '/api/mode':
                err = app.set_mode(body.get('mode'))
            elif path == '/api/cheat':
                err = app.set_cheat(str(body.get('name')), body.get('k'))
            elif path == '/api/cheats/reset':
                app.reset_cheats()
            elif path == '/api/play':
                err = app.play()
            elif path == '/api/stop':
                app.stop_game()
            elif path == '/api/rescan':
                if app.busy:
                    err = 'Espere terminar: ' + app.busy
                else:
                    threading.Thread(target=app.refresh_mods, daemon=True).start()
            elif path == '/api/open_mods':
                MODS.mkdir(parents=True, exist_ok=True)
                os.startfile(str(MODS))
            elif path == '/api/quit':
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                return self.send_error(404)
        except Exception as e:   # noqa: BLE001
            log(traceback.format_exc())
            err = str(e)
        st = app.state()
        st['error'] = err
        self._json(st)


def open_window(url):
    """Edge in app mode (no address bar) with its own profile under launcher\\, so the process is ours and we can wait on it."""
    if EDGE:
        EDGE_PROFILE.mkdir(parents=True, exist_ok=True)
        args = [str(EDGE), f'--app={url}', f'--user-data-dir={EDGE_PROFILE}', '--no-first-run', '--no-default-browser-check',
                '--disable-sync', '--disable-extensions', '--disable-background-networking', '--window-size=1250,860']
        try:
            return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            log(f'edge: {e}')
    webbrowser.open(url)
    return None


PID_FILE = UI_DIR / 'pid.txt'


def close_previous():
    """An older launcher window still open runs the LAUNCHER.py it started with: close it (and its Edge window) so
    LAUNCHER.bat always brings up the current code."""
    try:
        old = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        old = 0
    if old and old != os.getpid():
        try:
            q = subprocess.run(['tasklist', '/fi', f'PID eq {old}', '/fo', 'csv', '/nh'],
                               capture_output=True, text=True, creationflags=0x08000000, timeout=15)
            if 'python' in q.stdout.lower():   # still our pythonw (pids get reused)
                subprocess.run(['taskkill', '/pid', str(old), '/t', '/f'], capture_output=True, creationflags=0x08000000, timeout=15)
                log(f'launcher anterior (pid {old}) fechado')
        except (OSError, subprocess.SubprocessError) as e:
            log(f'launcher anterior: {e}')
    try:
        UI_DIR.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()), encoding='utf-8')
    except OSError:
        pass


def main():
    if '--console' in sys.argv:
        sys.argv.remove('--console')
        import runpy
        runpy.run_path(str(ROOT / 'LAUNCHER-console.py'), run_name='__main__')
        return 0
    if sys.stdout is None or sys.stderr is None:   # pythonw
        sys.stdout = sys.stderr = open(UI_LOG, 'a', encoding='utf-8', errors='replace')
    else:
        sys.stdout.reconfigure(errors='replace')
    if not EXE.is_file():
        print(f'UltraTears.exe nao encontrado em {BUILDER}')
        return 1
    app = App()
    if '--dry-run' in sys.argv or '--list' in sys.argv:
        app.refresh_mods()
        if '--list' in sys.argv:
            for i, m in enumerate(app.mods):
                print(f'{i + 1:2}  {"[x]" if m["key"] in app.enabled else "[ ]"} {m["name"]:<52} {m["status"]}  [{len(m["files"])} arquivos]')
            return 0
        env = dict(MODES[app.mode - 1][2])
        env['TOTK_ROMFS_OVERLAY'] = overlay_env(app.mods, app.enabled)
        env['TOTK_CHEATS'] = cheats_env(app.cheats)
        print('modo:', MODES[app.mode - 1][0])
        for k, v in env.items():
            print(f'{k}={v}')
        return 0
    threading.Thread(target=app.refresh_mods, daemon=True).start()
    threading.Thread(target=app.poller, daemon=True).start()
    import secrets
    Handler.app = app
    Handler.token = secrets.token_hex(8)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    url = f'http://127.0.0.1:{port}/#{Handler.token}'
    log(f'launcher em {url}')
    print(f'Launcher em {url} (feche a janela para sair)')
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    if '--no-window' in sys.argv:   # tests: serve only, until the page stops polling (or Ctrl+C)
        try:
            (UI_DIR / 'url.txt').write_text(url, encoding='utf-8')
            while t.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        server.shutdown()
        return 0
    close_previous()
    win = open_window(url)
    try:
        if win:
            while win.poll() is None and t.is_alive():
                time.sleep(0.5)
        else:   # a browser tab: live while the page keeps polling
            while t.is_alive():
                time.sleep(1)
                if time.time() - app.last_poll > 15:
                    break
    except KeyboardInterrupt:
        pass
    server.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
