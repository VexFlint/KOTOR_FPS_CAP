#!/usr/bin/env python3
"""
KOTOR 1 engine fixes -- patcher
by VexFlint | symbols from Lane's KotOR 1 (GoG) reverse engineering project

Applies seven fixes to swkotor.exe. Full analysis in TECHNICAL.md.

  1 FRAME CAP          The main loop has a busy-wait frame limiter whose cap value
                       defaults to 0.0, so it never runs. Setting it makes the game
                       pace itself with its own code. KOTOR ties simulation speed to
                       frame timing, so uncapped the whole game runs fast.

  2 LARGE ADDRESS      Sets the PE LARGE_ADDRESS_AWARE flag: 4GB of address space
                       instead of 2GB. Same as the standard 4GB Patcher.

  3 BUCKET CLEAR CLAMP ClearBuckets() zeroes three 5000-entry arrays "maxTexID + 1"
                       times. maxTexID is an unclamped running max of GL texture
                       names, so it can run off the end of .data -> 0xC0000005 on
                       module load. Clamped to the array bound.

  4 BUCKET WRITE GUARD AddPartToMeshBuckets() indexes those same arrays by raw
                       texture ID with no bounds check and writes through the
                       computed address. Confirmed crash at 0x46BEAE. Out-of-range
                       parts skip the bucket insert but still reach meshShadowBucket.

  5 GRASS DOUBLE FREE  DestroyGrassPolys() frees the blade buffer twice: field 0x3c
                       is only ever 0 or an alias of field 0x38, never its own
                       allocation. Heap corruption.

  6 GRASS VERTEX PTR   Two of three draw paths in RenderGrassPolys pass &field_0x40
                       -- the address of a field only ever set to 0 -- to
                       glVertexPointer instead of the allocated blade buffer. The
                       third path (ATI fragment shaders) does it correctly. This is
                       why grass renders as stretched garbage on almost all hardware.

  7 GRASS GL STATE     The same function leaks GL state: one path returns with three
                       client-state arrays still enabled, and material/texture state
                       is never restored. Breaks lighting on later draws and across
                       module reloads.

Usage:
    python kotor_patch.py "path\\to\\swkotor.exe"            apply, 60 fps cap
    python kotor_patch.py "path\\to\\swkotor.exe" 72         apply, 72 fps cap
    python kotor_patch.py "path\\to\\swkotor.exe" 0          apply, no fps cap
    python kotor_patch.py "path\\to\\swkotor.exe" --verify   report state only
    python kotor_patch.py "path\\to\\swkotor.exe" --revert   undo everything
    python kotor_patch.py --selftest                         check patch tables

Target: standard PC v1.03 layout (GOG, or DeadlyStream "KOTOR Editable Executable").
Steam's shipped exe is encrypted -- replace it with the editable one first.
A .bak is written next to the exe on first run. --revert restores stock bytes.
"""
import sys, os, struct, shutil, hashlib

IMAGE_BASE = 0x400000
def off(va): return va - IMAGE_BASE          # raw == virtual for this layout
def rel(frm, to, ilen=5): return struct.pack("<i", to - (frm + ilen))
def jmp(frm, to): return b"\xe9" + rel(frm, to)
def call_abs(slot): return b"\xff\x15" + struct.pack("<I", slot)
def load_edx(addr): return b"\x8b\x15" + struct.pack("<I", addr)
def hx(s): return bytes.fromhex(s)

# ---------------------------------------------------------------- addresses
FPS_CAP_VA = 0x7A3C64          # float, .data -- frame cap in FPS (stock 0.0)
BUCKET_MAX = 5000              # meshBuckets/fadeBuckets/capBuckets are 5000 entries

CAVE_CLAMP, CAVE_GUARD = 0x73C200, 0x73C220
CAVE_CSTATE, CAVE_PUSH, CAVE_POP = 0x73C280, 0x73C2C0, 0x73C2E0

GL_DISABLE_CLIENT_STATE = 0x73D264      # IAT slots, opengl32.dll
GL_PUSH_ATTRIB          = 0x73D268
GL_POP_ATTRIB           = 0x73D26C
GL_VERTEX_ARRAY, GL_NORMAL_ARRAY, GL_TEXCOORD_ARRAY = 0x73F3A4, 0x73F39C, 0x73F398
ATTRIB_MASK = 0x00042041       # GL_CURRENT | GL_LIGHTING | GL_ENABLE | GL_TEXTURE

# .rdata constants the frame limiter divides by -- used to reject the wrong exe
FINGERPRINT = {0x73D6FC: 1000.0, 0x73D700: 0.0, 0x73D708: 0.001}

# ---------------------------------------------------------------- code caves
def cave_clamp():
    # saturate AurTextureGetMaxTexID's return; returns on both paths
    return hx("3d88130000" "7205" "b887130000" "c3")

def cave_guard():
    b  = hx("3d88130000")            # cmp eax, 5000
    b += hx("7213")                  # jb in_range  (+19, over the bail block)
    bail = hx("a1bcbf7f00")          # mov eax,[0x7FBFBC]  meshShadowBucket.capacity
    bail += hx("8b0db8bf7f00")       # mov ecx,[0x7FBFB8]  meshShadowBucket.size
    bail += hx("3bc8")               # cmp ecx, eax        flags for the jne at 0x46BEB2
    bail += hx("68b1be4600")         # push 0x46BEB1       position-independent jump
    bail += hx("c3")                 # ret                 -> rejoin the shadow path
    assert len(bail) == 0x13, len(bail)
    b += bail
    b += hx("8d3440")                # in_range: relocated originals
    b += hx("8b04b5e8948100")
    b += jmp(CAVE_GUARD + len(b), 0x46BE6E)
    return b

def cave_cstate():
    # path A returned with the client arrays still enabled; disable them like the
    # other two paths do, then rejoin the shared tail
    b  = b"\xe8" + rel(CAVE_CSTATE, 0x426230)     # call DrawLightmappedGrass
    b += hx("83c418")                             # add esp, 0x18   (relocated)
    for const in (GL_TEXCOORD_ARRAY, GL_NORMAL_ARRAY, GL_VERTEX_ARRAY):
        b += load_edx(const) + b"\x52" + call_abs(GL_DISABLE_CLIENT_STATE)
    b += jmp(CAVE_CSTATE + len(b), 0x4A71DC)
    return b

def cave_push():
    b  = b"\x68" + struct.pack("<I", ATTRIB_MASK)
    b += call_abs(GL_PUSH_ATTRIB)
    b += hx("8b15441e8300")                       # relocated mov edx,[0x831E44]
    b += jmp(CAVE_PUSH + len(b), 0x4A6E07)
    return b

def cave_pop():
    b  = call_abs(GL_POP_ATTRIB)
    b += hx("8b4e0c") + hx("a1b07f8200")          # relocated originals
    b += jmp(CAVE_POP + len(b), 0x4A71E4)
    return b

# ------------------------------------------------- fix table: id, label, sites, caves
# site = (va, stock_bytes, patched_bytes) -- equal length, always
def _sites():
    return {
    "clamp":  [(0x41FEB5, hx("c390909090"), jmp(0x41FEB5, CAVE_CLAMP))],
    "guard":  [(0x46BE64, hx("8d34408b04b5e8948100"),
                          jmp(0x46BE64, CAVE_GUARD) + b"\x90" * 5)],
    "dfree":  [(0x4A847C, hx("8b563c"), hx("33d290"))],       # mov edx,[esi+0x3c] -> xor edx,edx
    "vptr":   [(0x4A7114, hx("8d4640"), hx("8b4638")),        # lea eax,[esi+0x40] -> mov eax,[esi+0x38]
               (0x4A71CE, hx("8d4640"), hx("8b4638"))],
    "cstate": [(0x4A71D4, hx("e857f0f7ff83c418"),
                          jmp(0x4A71D4, CAVE_CSTATE) + b"\x90" * 3)],
    "glstate":[(0x4A6E01, hx("8b15441e8300"), jmp(0x4A6E01, CAVE_PUSH) + b"\x90"),
               (0x4A71DC, hx("8b4e0ca1b07f8200"), jmp(0x4A71DC, CAVE_POP) + b"\x90" * 3)],
    }

CAVES = {"clamp":   [(CAVE_CLAMP,  cave_clamp)],
         "guard":   [(CAVE_GUARD,  cave_guard)],
         "cstate":  [(CAVE_CSTATE, cave_cstate)],
         "glstate": [(CAVE_PUSH,   cave_push), (CAVE_POP, cave_pop)]}

LABELS = {"clamp":  "bucket clear clamp",  "guard":  "bucket write guard",
          "dfree":  "grass double free",   "vptr":   "grass vertex pointer",
          "cstate": "grass client state",  "glstate":"grass GL state"}

# ---------------------------------------------------------------- operations
def site_state(d, sites):
    """stock / patched / unknown, for a whole fix"""
    seen = set()
    for va, stock, patched in sites:
        cur = bytes(d[off(va):off(va) + len(stock)])
        seen.add("stock" if cur == stock else "patched" if cur == patched else "unknown")
    return seen.pop() if len(seen) == 1 else "unknown"

def verify(d):
    print(f"fps cap             : {struct.unpack_from('<f', d, off(FPS_CAP_VA))[0]}")
    e = struct.unpack_from("<I", d, 0x3C)[0]
    print(f"4GB aware           : {'yes' if struct.unpack_from('<H', d, e+22)[0] & 0x20 else 'no'}")
    for k, sites in _sites().items():
        print(f"{LABELS[k]:20s}: {site_state(d, sites)}")
    print(f"md5                 : {hashlib.md5(bytes(d)).hexdigest()}")

def apply(d, fps, revert):
    struct.pack_into("<f", d, off(FPS_CAP_VA), fps)
    print(f"[1] frame cap       : {fps if fps else 'disabled (stock)'}")

    e  = struct.unpack_from("<I", d, 0x3C)[0]
    ch = struct.unpack_from("<H", d, e + 22)[0]
    struct.pack_into("<H", d, e + 22, (ch & ~0x20) if revert else (ch | 0x20))
    print(f"[2] 4GB aware       : {'removed' if revert else 'enabled'}")

    for n, (k, sites) in enumerate(_sites().items(), start=3):
        cur = site_state(d, sites)
        if cur == "unknown":
            raise SystemExit(f"REFUSING: {LABELS[k]} site is neither stock nor ours. "
                             "Another patch may have touched it.")
        for va, stock, patched in sites:
            d[off(va):off(va) + len(stock)] = stock if revert else patched
        for cave_va, build in CAVES.get(k, []):
            code = build()
            d[off(cave_va):off(cave_va) + len(code)] = b"\x00" * len(code) if revert else code
        print(f"[{n}] {LABELS[k]:16s}: {'removed' if revert else 'applied'}")

def selftest():
    sites = _sites()
    spans = [(va, va + len(s)) for l in sites.values() for va, s, _ in l]
    for k, l in sites.items():
        for va, stock, patched in l:
            assert len(stock) == len(patched), f"{k}: length mismatch at {va:#x}"
    for k, l in CAVES.items():
        for cave_va, build in l:
            spans.append((cave_va, cave_va + len(build())))
    spans.sort()
    for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
        assert b1 <= a2, f"overlap {a1:#x}-{b1:#x} and {a2:#x}-{b2:#x}"
    # jump targets round-trip
    assert struct.unpack("<i", jmp(0x46BE64, CAVE_GUARD)[1:])[0] + 0x46BE69 == CAVE_GUARD
    assert cave_guard()[5:7] == hx("7213")
    print(f"selftest OK: {len(spans)} non-overlapping regions, jump math verified")

def main():
    if "--selftest" in sys.argv:
        selftest(); return
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    path = sys.argv[1]
    arg  = sys.argv[2] if len(sys.argv) > 2 else "60"
    if not os.path.isfile(path):
        sys.exit(f"ERROR: no such file: {path}")

    d = bytearray(open(path, "rb").read())
    if len(d) < off(CAVE_POP) + 64:
        sys.exit("ERROR: file too small / wrong layout.")
    for va, want in FINGERPRINT.items():
        got = struct.unpack_from("<f", d, off(va))[0]
        if abs(got - want) > 1e-6:
            print(f"REFUSING: fingerprint mismatch at {va:#x} (got {got}, expected {want}).")
            print("Not the standard PC v1.03 / editable-exe layout.")
            sys.exit("On Steam? Replace swkotor.exe with the KOTOR Editable Executable first.")

    if arg == "--verify":
        verify(d); return

    revert = (arg == "--revert")
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak); print(f"backup written      : {bak}")
    apply(d, 0.0 if revert else float(arg), revert)
    open(path, "wb").write(d)
    print(f"\n{'reverted to stock.' if revert else 'done.'}  md5: {hashlib.md5(bytes(d)).hexdigest()}")

if __name__ == "__main__":
    main()
