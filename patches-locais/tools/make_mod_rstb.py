"""RomFS overlay for the launcher's mods: a ResourceSizeTable (RSTB) for 1.4.3 that fits the mods' files, and a HeapDef
that is the larger of ours (make_heap_overlay.py) and the mods'.

usage: python tools/make_mod_rstb.py --out <dir> [--romfs data/romfs] [--heap-overlay data/romfs-overlay] <mod romfs>...
       python tools/make_mod_rstb.py --dump <mod romfs>        (the mod's HeapDef sizes against the shipped ones)

The game allocates each resource with the size the RSTB lists for it (System/Resource/ResourceSizeTable.Product.143.
Nin_NX_NVN.rsizetable.zs); a modded file bigger than its entry fails to load ("NoFile") and the game stops soon after.
Texture packs ship RSTBs only for the versions they knew (1.0.0 .. 1.2.1), so this builds the 1.4.3 one: the shipped
table (or the winning mod's 1.4.3 table, when a mod made with TKMM brings one) with every entry raised to what the
mod's file needs. Mod folders are given in priority order (the first that has a file wins, as TOTK_ROMFS_OVERLAY).

Entry formulas (measured on the shipped table: a .txtg entry is its size rounded up to 32 plus 256..287):
  .txtg                         round32(size) + 288
  other file with an entry      entry * new_size / old_size (sizes after zstd), at least the old entry, + 0x200
  file without an entry         size * 1.5 + 0x1000   (an estimate; actor mods made for 1.4.3 bring their own table)
Output: <out>/System/Resource/...rsizetable.zs, <out>/System/HeapSize/...HeapDef.byml.zs, <out>/overlay.json
(extra_kib for svc.c's application pool, like make_heap_overlay.py's).
"""
import argparse
import json
import os
import pathlib
import struct
import sys
import zlib
from compression import zstd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from romfs import RomFS  # noqa: E402
from make_heap_overlay import Byml, heap_sizes, DTA_KIB  # noqa: E402

RSTB = 'System/Resource/ResourceSizeTable.Product.143.Nin_NX_NVN.rsizetable.zs'
HEAPDEF = 'System/HeapSize/Product.Nin_NX_NVN.HeapDef.byml.zs'


# ------------------------------------------------------------------------------------------------ RESTBL
def rstb_parse(b):
    if b[:6] != b'RESTBL':
        raise ValueError('not a RESTBL')
    ver, sblk, ncrc, nname = struct.unpack_from('<IIII', b, 6)
    off = 22
    crc = dict(struct.unpack_from('<II', b, off + i * 8) for i in range(ncrc))
    off += ncrc * 8
    names = {}
    for i in range(nname):
        at = off + i * (sblk + 4)
        names[b[at:at + sblk].split(b'\0')[0].decode()] = struct.unpack_from('<I', b, at + sblk)[0]
    return ver, sblk, crc, names


def rstb_build(ver, sblk, crc, names):
    out = bytearray(b'RESTBL' + struct.pack('<IIII', ver, sblk, len(crc), len(names)))
    for c in sorted(crc):
        out += struct.pack('<II', c, crc[c])
    for n in sorted(names):
        out += n.encode().ljust(sblk, b'\0') + struct.pack('<I', names[n])
    return bytes(out)


def dsize_of_bytes(head, size):
    """The size the game sees: zstd frames' decompressed size (from the header), else the file's."""
    try:
        info = zstd.get_frame_info(head)
        return info.decompressed_size if info.decompressed_size else size * 4
    except Exception:   # noqa: BLE001
        return size


def round32(n):
    return (n + 31) & ~31


# ------------------------------------------------------------------------------------------------ main
def walk_mod(d):
    out = {}
    for dirpath, _, names in os.walk(d):
        for n in names:
            full = pathlib.Path(dirpath) / n
            out[str(full.relative_to(d)).replace('\\', '/')] = full
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mods', nargs='+', help='mod romfs folders, the first that has a file wins')
    ap.add_argument('--romfs', default='data/romfs')
    ap.add_argument('--heap-overlay', default='data/romfs-overlay')
    ap.add_argument('--out')
    ap.add_argument('--dump', action='store_true')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    rom = RomFS(ROOT / a.romfs)

    # files of all mods, first wins
    files = {}
    mod_heaps = []   # every mod's HeapDef: each heap takes the largest value any of them asks for
    for m in a.mods:
        for p, full in walk_mod(pathlib.Path(m)).items():
            if p == HEAPDEF:
                mod_heaps.append(full)
            files.setdefault(p, full)
    mod_rstb = files.pop(RSTB, None)
    mod_heap = files.pop(HEAPDEF, None)
    for p in [p for p in files if p.startswith('System/Resource/ResourceSizeTable.')]:
        files.pop(p)   # tables of other versions: useless here
    if a.dump:
        orig = Byml(zstd.decompress(rom.read(HEAPDEF)))
        osz = {k: struct.unpack_from('<i', orig.b, v)[0] for k, v in heap_sizes(orig.node(orig.root_off)).items()}
        if mod_heap:
            mb = Byml(zstd.decompress(mod_heap.read_bytes()))
            msz = {k: struct.unpack_from('<i', mb.b, v)[0] for k, v in heap_sizes(mb.node(mb.root_off)).items()}
            for k in sorted(set(osz) | set(msz)):
                if osz.get(k) != msz.get(k):
                    print(f'{k}: shipped {osz.get(k)} KiB, mod {msz.get(k)} KiB')
        print(f'{len(files)} files, mod rstb {"yes" if mod_rstb else "no"}, mod heapdef {"yes" if mod_heap else "no"}')
        return
    if not a.out:
        raise SystemExit('--out is needed')
    out = pathlib.Path(a.out)

    # the base table: the winning mod's 1.4.3 table, else the shipped one
    base_raw = mod_rstb.read_bytes() if mod_rstb else rom.read(RSTB)
    ver, sblk, crc, names = rstb_parse(zstd.decompress(base_raw))
    raised = added = 0
    stream = open(rom.image, 'rb') if rom.image else None
    for p, full in files.items():
        key = p[:-3] if p.endswith('.zs') else p
        size = full.stat().st_size
        if p.endswith('.zs'):
            with open(full, 'rb') as f:
                new = dsize_of_bytes(f.read(32), size)
        else:
            new = size
        c = zlib.crc32(key.encode())
        in_names = key in names
        old = names[key] if in_names else crc.get(c)
        if p.endswith('.txtg'):
            want = round32(new) + 288
        elif old is not None and p in rom.files:
            osize = rom.files[p]
            if p.endswith('.zs'):
                if stream:
                    stream.seek(rom.offset(p))
                    head = stream.read(32)
                else:
                    with open(rom.root / p, 'rb') as f:
                        head = f.read(32)
                onew = dsize_of_bytes(head, osize)
            else:
                onew = osize
            want = int(old * new / onew) + 0x200 if onew else old
        elif old is not None:
            want = int(new * 1.5) + 0x1000
        else:
            want = int(new * 1.5) + 0x1000
        if old is None:
            if c in crc:   # a crc collision with another path: the name table
                names[key] = want
            else:
                crc[c] = want
            added += 1
        elif want > old:
            if in_names:
                names[key] = want
            else:
                crc[c] = want
            raised += 1
    if stream:
        stream.close()
    data = rstb_build(ver, sblk, crc, names)
    comp = zstd.compress(data, level=12)
    assert zstd.decompress(comp) == data
    (out / RSTB).parent.mkdir(parents=True, exist_ok=True)
    (out / RSTB).write_bytes(comp)
    print(f'RSTB 1.4.3: {len(files)} mod files, {raised} entries raised, {added} added ({"base: mod" if mod_rstb else "base: shipped"}); '
          f'{len(data)} -> {len(comp)} bytes')

    # HeapDef: ours (the scene overlay) or the shipped one, each heap raised to the mod's value when larger
    ours = ROOT / a.heap_overlay / HEAPDEF
    heap_raw = ours.read_bytes() if ours.is_file() else rom.read(HEAPDEF)
    info = zstd.get_frame_info(heap_raw)
    if info.dictionary_id:
        raise SystemExit('HeapDef wants a zstd dictionary: not handled')
    hb = Byml(zstd.decompress(heap_raw))
    tree = hb.node(hb.root_off)
    at = heap_sizes(tree)
    heaps = tree.get('Heaps', {}) if isinstance(tree, dict) else {}
    roots = {k for k, v in heaps.items() if k != '__at__' and isinstance(v, dict) and 'Parent' not in v}
    patched = bytearray(hb.b)
    grown = []
    extra = 0   # the application pool must grow by what the top-level heaps grow (the children are carved from them)
    ojson = ROOT / a.heap_overlay / 'overlay.json'
    if ojson.is_file():
        try:
            extra = int(json.loads(ojson.read_text()).get('extra_kib', 0))
        except (OSError, ValueError):
            pass
    for mod_heap in mod_heaps:
        try:
            mb = Byml(zstd.decompress(mod_heap.read_bytes()))
            msz = {k: struct.unpack_from('<i', mb.b, v)[0] for k, v in heap_sizes(mb.node(mb.root_off)).items()}
        except Exception as e:   # noqa: BLE001
            print(f'mod HeapDef {mod_heap} unreadable ({e}): ignored')
            msz = {}
        for k, v in msz.items():
            old = struct.unpack_from('<i', patched, at[k])[0] if k in at else None
            if old is not None and v > old:
                grown.append(f'{k} {old} -> {v} KiB')
                struct.pack_into('<i', patched, at[k], v)
                if k in roots:
                    extra += v - old
    dta = struct.unpack_from('<i', patched, at['DynamicTextureAllocatorMem'])[0] if 'DynamicTextureAllocatorMem' in at else DTA_KIB
    hcomp = zstd.compress(bytes(patched), level=19)
    (out / HEAPDEF).parent.mkdir(parents=True, exist_ok=True)
    (out / HEAPDEF).write_bytes(hcomp)
    (out / 'overlay.json').write_text(json.dumps(dict(script='make_mod_rstb.py', version='v143', extra_kib=extra, dta_kib=dta,
                                                      mods=[str(m) for m in a.mods], heaps_grown=grown), indent=1))
    print(f'HeapDef: {", ".join(grown) if grown else "as ours"}; extra_kib {extra}')


if __name__ == '__main__':
    main()
