"""RomFS overlay that grows the game's texture heap for a larger scene render size (TOTK_RENDER_RES).

usage: python tools/make_heap_overlay.py --scene 2560x1440 [--romfs data/romfs] [--out data/romfs-overlay] [--dump]
       python tools/make_heap_overlay.py --remove

After MacNativeGames games/totk/scripts/make_output_res_overlay.py (MIT). The game's DynamicTextureAllocatorMem is one
fixed heap (113664 KiB, shipped) carved from the Graphics heap (201728 KiB), both sized for the 1600x900 docked scene;
a bigger scene exhausts it (the allocator fails, the game stops). The overlay is the shipped
System/HeapSize/Product.Nin_NX_NVN.HeapDef.byml.zs with those two 4-byte Size fields raised by
  grow  = (sw*sh / (1600*900) - 1) * 1.6            (1.6: the scene targets' margin, measured on the Mac)
  extra = ceil(113664 * (1 + grow) / 1024) * 1024 - 113664   KiB
and recompressed. src/host/fs.c serves files under data/romfs-overlay in place of the image's; src/host/svc.c grows the
application pool by the same extra (overlay.json, written here). Game-derived data: data/ only, never shared.
"""
import argparse
import hashlib
import json
import math
import pathlib
import struct
import sys
from compression import zstd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from romfs import RomFS  # noqa: E402

HEAPDEF = 'System/HeapSize/Product.Nin_NX_NVN.HeapDef.byml.zs'
ZSDIC = 'Pack/ZsDic.pack.zs'
DTA_KIB, GRAPHICS_KIB = 113664, 201728   # the shipped values, checked below
SCENE_W, SCENE_H = 1600, 900


def extra_kib(sw, sh):
    grow = sw * sh / (SCENE_W * SCENE_H) - 1
    if grow <= 0:
        return 0
    grow *= 1.6
    return math.ceil(DTA_KIB * (1 + grow) / 1024) * 1024 - DTA_KIB


# ---------------------------------------------------------------- SARC (ZsDic.pack): name -> bytes
def sarc_files(data):
    assert data[:4] == b'SARC', 'ZsDic.pack is not a SARC'
    le = data[6:8] == b'\xff\xfe'
    e = '<' if le else '>'
    data_off = struct.unpack_from(e + 'I', data, 0xC)[0]
    sfat = 0x14
    assert data[sfat:sfat + 4] == b'SFAT'
    count = struct.unpack_from(e + 'H', data, sfat + 6)[0]
    sfnt = sfat + 0xC + count * 0x10
    assert data[sfnt:sfnt + 4] == b'SFNT'
    names = sfnt + 8
    out = {}
    for i in range(count):
        _, attrs, start, end = struct.unpack_from(e + 'IIII', data, sfat + 0xC + i * 0x10)
        name_off = names + (attrs & 0xFFFFFF) * 4
        name = data[name_off:data.index(b'\0', name_off)].decode()
        out[name] = data[data_off + start:data_off + end]
    return out


# ---------------------------------------------------------------- BYML (v4-v7 little-endian 'YB'): size fields
class Byml:
    def __init__(self, b):
        self.b = b
        if b[:2] != b'YB':
            raise SystemExit('HeapDef is not a little-endian BYML')
        self.version, self.keys_off, self.strings_off, self.root_off = struct.unpack_from('<HIII', b, 2)
        self.keys = self.string_table(self.keys_off)
        self.strings = self.string_table(self.strings_off)

    def string_table(self, off):
        if not off:
            return []
        t = self.b[off]
        assert t == 0xC2, f'string table type {t:#x}'
        count = struct.unpack_from('<I', self.b, off)[0] >> 8
        offsets = struct.unpack_from(f'<{count + 1}I', self.b, off + 4)
        return [self.b[off + o:self.b.index(b'\0', off + o)].decode() for o in offsets[:count]]

    def node(self, off, t=None):
        """-> python value; dicts carry ('__at__', key) -> value offset of each scalar for patching."""
        if t is None:
            t = self.b[off]
        if t == 0xC1:   # dict
            count = struct.unpack_from('<I', self.b, off)[0] >> 8
            d = {}
            for i in range(count):
                e = off + 4 + i * 8
                k = struct.unpack_from('<I', self.b, e)[0] & 0xFFFFFF
                nt = self.b[e + 3]
                d[self.keys[k]] = self.value(e + 4, nt)
                d.setdefault('__at__', {})[self.keys[k]] = e + 4
            return d
        if t == 0xC0:   # array
            count = struct.unpack_from('<I', self.b, off)[0] >> 8
            types = self.b[off + 4:off + 4 + count]
            vals = off + 4 + ((count + 3) & ~3)
            return [self.value(vals + i * 4, types[i]) for i in range(count)]
        raise SystemExit(f'unsupported BYML container {t:#x} at {off:#x}')

    def value(self, at, t):
        v = struct.unpack_from('<I', self.b, at)[0]
        if t in (0xC0, 0xC1):
            return self.node(v, t)
        if t == 0xA0:
            return self.strings[v]
        if t == 0xD0:
            return bool(v)
        if t == 0xD1:
            return struct.unpack_from('<i', self.b, at)[0]
        if t == 0xD2:
            return struct.unpack_from('<f', self.b, at)[0]
        if t == 0xD3:
            return v
        if t in (0xD4, 0xD5, 0xD6):
            return struct.unpack_from('<q' if t == 0xD4 else '<d' if t == 0xD6 else '<Q', self.b, v)[0]
        if t == 0xFF:
            return None
        return ('?', t, v)


def heap_sizes(tree):
    """-> {heap name: offset of its Size (an inline Int)} found anywhere under the root."""
    found = {}

    def walk(n, name):
        if isinstance(n, dict):
            at = n.get('__at__', {})
            nm = n.get('Name', name)
            if 'Size' in n and isinstance(nm, str) and isinstance(n['Size'], int):
                found[nm] = at['Size']
            for k, v in n.items():
                if k != '__at__':
                    walk(v, k)
        elif isinstance(n, list):
            for v in n:
                walk(v, name)
    walk(tree, '')
    return found


def strip(n):
    if isinstance(n, dict):
        return {k: strip(v) for k, v in n.items() if k != '__at__'}
    if isinstance(n, list):
        return [strip(v) for v in n]
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scene', help='scene render size, e.g. 2560x1440')
    ap.add_argument('--romfs', default='data/romfs')
    ap.add_argument('--out', default='data/romfs-overlay')
    ap.add_argument('--dump', action='store_true', help='print the HeapDef tree and stop')
    ap.add_argument('--remove', action='store_true', help='delete the overlay (the game uses its own heaps again)')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # heap names include Japanese
    out = ROOT / a.out
    target = out / HEAPDEF
    if a.remove:
        target.unlink(missing_ok=True)
        (out / 'overlay.json').unlink(missing_ok=True)
        print('overlay removed')
        return
    rom = RomFS(ROOT / a.romfs)
    raw = rom.read(HEAPDEF)
    info = zstd.get_frame_info(raw)
    dictionary = None
    if info.dictionary_id:
        dics = sarc_files(zstd.decompress(rom.read(ZSDIC)))
        for d in dics.values():
            zd = zstd.ZstdDict(d)
            if zd.dict_id == info.dictionary_id:
                dictionary = zd
        if dictionary is None:
            raise SystemExit(f'HeapDef wants zstd dictionary {info.dictionary_id}, not in {ZSDIC}')
    byml = zstd.decompress(raw, zstd_dict=dictionary)
    b = Byml(byml)
    tree = b.node(b.root_off)
    sizes = heap_sizes(tree)
    if a.dump:
        print(json.dumps(strip(tree), indent=1))
        print({k: (v, struct.unpack_from('<i', byml, v)[0]) for k, v in sizes.items()})
        return
    if not a.scene:
        raise SystemExit('--scene <w>x<h> is needed')
    sw, sh = (int(x) for x in a.scene.lower().split('x'))
    if not (64 <= sw <= 4096 and 64 <= sh <= 4096):
        raise SystemExit('--scene sides must be 64..4096')
    for name, want in (('DynamicTextureAllocatorMem', DTA_KIB), ('Graphics', GRAPHICS_KIB)):
        if name not in sizes:
            raise SystemExit(f'{name} not found in HeapDef')
        got = struct.unpack_from('<i', byml, sizes[name])[0]
        if got != want:
            raise SystemExit(f'{name}: shipped size {got} KiB, expected {want} (not TOTK 1.4.3, or a mod)')
    extra = extra_kib(sw, sh)
    if extra == 0:
        print(f'{sw}x{sh} is not larger than the shipped {SCENE_W}x{SCENE_H} scene: no overlay needed')
        target.unlink(missing_ok=True)
        (out / 'overlay.json').unlink(missing_ok=True)
        return
    patched = bytearray(byml)
    for name, want in (('DynamicTextureAllocatorMem', DTA_KIB), ('Graphics', GRAPHICS_KIB)):
        struct.pack_into('<i', patched, sizes[name], want + extra)
    changed = [i for i in range(len(byml)) if byml[i] != patched[i]]
    assert len(changed) <= 8 and all(any(s <= i < s + 4 for s in sizes.values()) for i in changed), 'unexpected change'
    comp = zstd.compress(bytes(patched), level=19, zstd_dict=dictionary)
    back = zstd.decompress(comp, zstd_dict=dictionary)
    assert back == bytes(patched), 'round trip'
    ninfo = zstd.get_frame_info(comp)
    assert ninfo.dictionary_id == info.dictionary_id and ninfo.decompressed_size == len(byml), 'frame header'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(comp)
    (out / 'overlay.json').write_text(json.dumps(dict(
        script='make_heap_overlay.py', version='v143', scene=f'{sw}x{sh}', extra_kib=extra,
        dta_kib=DTA_KIB + extra, graphics_kib=GRAPHICS_KIB + extra,
        original_sha256=hashlib.sha256(raw).hexdigest(), overlay_sha256=hashlib.sha256(comp).hexdigest()), indent=1))
    print(f'{HEAPDEF}: DynamicTextureAllocatorMem {DTA_KIB} -> {DTA_KIB + extra} KiB, Graphics {GRAPHICS_KIB} -> '
          f'{GRAPHICS_KIB + extra} KiB (+{extra / 1024:.0f} MiB for a {sw}x{sh} scene); {len(raw)} -> {len(comp)} bytes -> {target}')


if __name__ == '__main__':
    main()
