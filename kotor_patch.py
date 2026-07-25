#!/usr/bin/env python3
"""
KOTOR 1 Engine Fixes -- patcher
by VexFlint | RE assisted by Lane's KotOR 1 (GoG) symbol work

Applies four fixes to swkotor.exe:

  1. FRAME LIMITER  The main loop contains a busy-wait frame limiter whose cap
     value defaults to 0.0, so it never runs. Setting it caps the game at a
     target FPS using the engine's own code, which addresses the framerate-tied
     movement / "stuck after combat" freeze on high-refresh displays.

  2. 4GB LARGE ADDRESS AWARE Lets the 32-bit exe address up to 4GB instead of
     2GB. Required by heavy texture mod builds.

  3. TEXTURE BUCKET CLEAR CLAMP  ClearBuckets() zeroes three 5000-entry arrays
     "maxTexID + 1" times. maxTexID is an unclamped running max of texture IDs
     read from mesh data, so a bad ID runs the loop off the end of .data ->
     access violation on module load. Clamped to the array bound (4999).

  4. TEXTURE BUCKET WRITE GUARD  AddPartToMeshBuckets() indexes those same
     arrays by raw texture ID with no bounds check, and writes pointers through
     the computed address. Out-of-range parts are now skipped instead of
     corrupting adjacent memory.

Usage:
    python kotor_patch.py "path\\to\\swkotor.exe"            # apply, 60 fps
    python kotor_patch.py "path\\to\\swkotor.exe" 72         # apply, 72 fps
    python kotor_patch.py "path\\to\\swkotor.exe" 0          # apply, no fps cap
    python kotor_patch.py "path\\to\\swkotor.exe" --revert   # undo everything
    python kotor_patch.py "path\\to\\swkotor.exe" --verify   # report state only

Target: the standard PC v1.03 layout (GOG / "KOTOR Editable Executable").
Steam's shipped exe is encrypted and must be replaced with the editable one
first. A .bak backup is written next to the exe on first run.
"""
import sys, os, struct, shutil, hashlib

# ---------------------------------------------------------------- addresses
IMAGE_BASE = 0x400000

FPS_CAP_VA   = 0x7A3C64     # float, .data -- frame cap in FPS (stock 0.0)
GETTER_VA    = 0x41FEB0     # AurTextureGetMaxTexID
ADDPART_VA   = 0x46BE64     # inside AddPartToMeshBuckets, after GetTextureTID
CAVE_A_VA    = 0x73C200     # code cave: saturating clamp
CAVE_B_VA    = 0x73C220     # code cave: out-of-range skip
RESUME_VA    = 0x46BE6E     # AddPart resume point
BUCKET_MAX   = 5000         # arrays hold exactly 5000 entries (0..4999)

# fingerprint constants used by the limiter math (.rdata)
FINGERPRINT = {0x73D6FC: 1000.0, 0x73D700: 0.0, 0x73D708: 0.001}

# stock byte patterns
GETTER_STOCK  = bytes.fromhex("a1bc467a00" "c3" + "90" * 10)
ADDPART_STOCK = bytes.fromhex("8d3440" "8b04b5e8948100")

def va2off(va):     return va - IMAGE_BASE          # raw==virtual for this layout
def f32(b):         return struct.unpack("<f", b)[0]

def build_cave_a():
    # cmp eax,5000 ; jb ret ; mov eax,4999 ; ret
    return bytes.fromhex("3d88130000" "7205" "b887130000" "c3")

def build_cave_b():
    body  = bytes.fromhex("3d88130000")           # cmp eax,5000
    body += bytes.fromhex("730f")                 # jae skip
    body += ADDPART_STOCK                         # relocated original insns
    body += b"\xe9" + struct.pack("<i", RESUME_VA - (CAVE_B_VA + len(body) + 5))
    body += bytes.fromhex("5e5fc3")               # skip: pop esi; pop edi; ret
    return body

def state(d):
    """report which patches are present"""
    s = {}
    s["fps"] = f32(d[va2off(FPS_CAP_VA):va2off(FPS_CAP_VA)+4])
    e  = struct.unpack_from("<I", d, 0x3C)[0]
    s["laa"] = bool(struct.unpack_from("<H", d, e + 22)[0] & 0x20)
    g = bytes(d[va2off(GETTER_VA):va2off(GETTER_VA)+6])
    s["clamp"]  = "stock" if g == GETTER_STOCK[:6] else ("patched" if g[5] == 0xE9 else "unknown")
    a = bytes(d[va2off(ADDPART_VA):va2off(ADDPART_VA)+1])
    s["guard"]  = "stock" if a == b"\x8d" else ("patched" if a == b"\xe9" else "unknown")
    return s

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    path = sys.argv[1]
    arg  = sys.argv[2] if len(sys.argv) > 2 else "60"

    if not os.path.isfile(path):
        print(f"ERROR: no such file: {path}"); sys.exit(1)
    d = bytearray(open(path, "rb").read())

    # ---- refuse anything that isn't the layout we know -------------------
    if len(d) < va2off(CAVE_B_VA) + 64:
        print("ERROR: file too small / wrong layout."); sys.exit(2)
    for va, want in FINGERPRINT.items():
        got = f32(d[va2off(va):va2off(va)+4])
        if abs(got - want) > 1e-6:
            print(f"REFUSING: fingerprint mismatch at {va:#x} (got {got}, expected {want}).")
            print("This is not the standard PC v1.03 / editable-exe layout.")
            print("If you are on Steam, replace swkotor.exe with the KOTOR Editable Executable first.")
            sys.exit(2)

    if arg == "--verify":
        s = state(d)
        print(f"fps cap        : {s['fps']}")
        print(f"4GB aware      : {'yes' if s['laa'] else 'no'}")
        print(f"bucket clamp   : {s['clamp']}")
        print(f"bucket guard   : {s['guard']}")
        print(f"md5            : {hashlib.md5(bytes(d)).hexdigest()}")
        sys.exit(0)

    revert = (arg == "--revert")
    fps    = 0.0 if revert else float(arg)

    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak); print(f"backup written : {bak}")

    # ---- 1. frame limiter -------------------------------------------------
    struct.pack_into("<f", d, va2off(FPS_CAP_VA), fps)
    print(f"[1] frame cap  : {fps if fps else 'disabled (stock)'}")

    # ---- 2. large address aware ------------------------------------------
    e  = struct.unpack_from("<I", d, 0x3C)[0]
    ch = struct.unpack_from("<H", d, e + 22)[0]
    struct.pack_into("<H", d, e + 22, (ch & ~0x20) if revert else (ch | 0x20))
    print(f"[2] 4GB aware  : {'removed' if revert else 'enabled'}")

    # ---- 3. ClearBuckets count clamp -------------------------------------
    g = va2off(GETTER_VA)
    if revert:
        d[g:g+len(GETTER_STOCK)] = GETTER_STOCK
        d[va2off(CAVE_A_VA):va2off(CAVE_A_VA)+len(build_cave_a())] = b"\x00" * len(build_cave_a())
    else:
        d[g+5:g+10] = b"\xe9" + struct.pack("<i", CAVE_A_VA - (GETTER_VA + 5 + 5))
        for i in range(g+10, g+16): d[i] = 0x90
        ca = build_cave_a()
        d[va2off(CAVE_A_VA):va2off(CAVE_A_VA)+len(ca)] = ca
    print(f"[3] clear clamp: {'removed' if revert else f'saturate at {BUCKET_MAX-1}'}")

    # ---- 4. AddPart out-of-range guard ------------------------------------
    a = va2off(ADDPART_VA)
    if revert:
        d[a:a+len(ADDPART_STOCK)] = ADDPART_STOCK
        d[va2off(CAVE_B_VA):va2off(CAVE_B_VA)+len(build_cave_b())] = b"\x00" * len(build_cave_b())
    else:
        d[a:a+5] = b"\xe9" + struct.pack("<i", CAVE_B_VA - (ADDPART_VA + 5))
        d[a+5:a+10] = b"\x90" * 5
        cb = build_cave_b()
        d[va2off(CAVE_B_VA):va2off(CAVE_B_VA)+len(cb)] = cb
    print(f"[4] write guard: {'removed' if revert else f'skip texID >= {BUCKET_MAX}'}")

    open(path, "wb").write(d)
    print(f"\n{'reverted to stock.' if revert else 'done.'}  md5: {hashlib.md5(bytes(d)).hexdigest()}")

if __name__ == "__main__":
    main()
