# KOTOR 1 Engine Fixes — technical write-up

Reverse engineering notes, root-cause analysis, and exact patch details for four fixes
to *Star Wars: Knights of the Old Republic* (2003, Odyssey engine).

by **VexFlint** — built on [Lane's KotOR 1 (GoG) RE project](https://deadlystream.com/topic/11948-kotor-1-gog-reverse-engineering/)

Static analysis only (Ghidra + capstone); no debugger or runtime instrumentation.
All addresses are for the standard PC v1.03 layout (GOG / "editable" executable),
image base `0x400000`, where raw file offset == VA − image base.

---

## Summary

| # | Fix | Class | Site | Size |
|---|-----|-------|------|------|
| 1 | Arm the dormant frame limiter | disabled feature | `0x7A3C64` (.data) | 4 bytes |
| 2 | LARGE_ADDRESS_AWARE | PE flag | COFF characteristics | 1 bit |
| 3 | Clamp `ClearBuckets` iteration count | out-of-bounds write | `AurTextureGetMaxTexID` | 5 B + 13 B cave |
| 4 | Bounds-check bucket indexing | out-of-bounds write | `AddPartToMeshBuckets` | 5 B + 25 B cave |

Fixes 3 and 4 both come from the same underlying limit: the renderer's texture bucket
arrays are fixed at 5000 entries, and two code paths index them without bounds checks.

---

## Method

The chain that produced these, in order:

1. **Import table triage.** `swkotor.exe` imports `QueryPerformanceCounter`,
   `QueryPerformanceFrequency`, `GetTickCount`, `Sleep`, and drives OpenGL via
   `opengl32.dll` with `gdi32.SwapBuffers`. The main loop uses `PeekMessageA` — the
   classic "run as fast as possible" pattern with no inherent frame cap. This is consistent with the
   framerate-tied bug reports and was visible before any decompilation.

2. **Caller climbing.** Imports are reached through IAT slots and thunks, so
   `getCallingFunctions` on the thunk returns nothing useful; you must take references
   to the *IAT slot address* and to the thunk's entry point. From the timing primitives
   → the message pump → `WinMain`.

3. **Bulk export.** Rather than iterating in the GUI, export decompiled C for all
   functions once (~20k functions, ~17 MB) and grep locally. This is much faster than iterating in the GUI.

4. **Symbol overlay.** Lane's XML provides 24,242 `ENTRY_POINT` → `NAME` pairs. Mapping
   those onto the export turns `FUN_0046bef0` into `ClearBuckets` which makes it possible to reason about subsystems. Verified GOG↔this-layout address parity on known anchors
   (`WinMain @ 0x4041F0`, `messagepump @ 0x401510`, `UpdateScreen @ 0x401C10`).

5. **Crash-driven analysis.** Windows Event Viewer gives a fault offset; add the image
   base, find the containing function, disassemble the faulting instruction. This is how fix 3 was found; it is considerably
   more efficient than auditing without a target.

---

## Fix 1 — frame limiter present but disabled

### Where

`WinMain` (`0x4041F0`), immediately after `SwapBuffers` in the main loop:

```c
if (DAT_007a3c58 != 0)            // a second, also-disabled coarse limiter
    Sleep(DAT_0078d1e8);

if (0.0f < cap) {                 // cap = _DAT_007a3c64
    target = 1000.0f / cap;
    if (elapsed < target) {
        do { /* busy-spin on the game timer */ }
        while (elapsed < target);
    }
}
```

Constants (all read from the binary, not inferred):

| Symbol | VA | Value | Role |
|---|---|---|---|
| `_DAT_007a3c64` | `0x7A3C64` | **0.0** (.data) | frame cap, FPS |
| numerator | `0x73D6FC` | 1000.0 (.rdata) | `target_ms = 1000/cap` |
| zero | `0x73D700` | 0.0 (.rdata) | the `0.0 < cap` gate |
| timer scale | `0x73D708` | 0.001 (.rdata) | raw timer → ms |

### Why it doesn't run

The only writer of `_DAT_007a3c64` is `CSWSModule::LoadModuleFinish` (`0x4C5880`):

```c
if (DAT_00832904 != 0) {
    FUN_005ee4a0("30");                       // "framerate" console handler
    _DAT_007a3c64 = (float)DAT_00832904;
}
```

`DAT_00832904` is in BSS (zero-initialised) and **has no writer anywhere in the binary** —
it is a config variable exposed by address that nothing sets by default. The guard is never true, so the cap stays `0.0` and the limiter does not run.

The engine also has a `framerate` console command
(`0x5EE4A0`, returning `"Frame rate set."` / `"Fixed frame rate off."`), which sets a
fixed rate across the render and timer objects — so the mechanism is used elsewhere in the engine.

### Patch

```
0x7A3C64:  00 00 00 00   (0.0)   ->   00 00 70 42   (60.0)
```

Nothing writes this value during normal play, so a `.data` patch holds for the session.

### Verification

Tested on a 144 Hz display with external caps and driver vsync overrides removed. The
patched build held 60 FPS and the after-combat freeze did not reproduce over several
hours of play. Single machine, single configuration.

### Caveat

Busy-wait, by BioWare's design (the movie path spins identically). A better long-term fix
would redirect the gate to the engine's own `Sleep`-based limiter (`DAT_007a3c58` /
`DAT_0078d1e8`), or fix the delta-time handling so no cap is needed at all.

---

## Fix 2 — LARGE_ADDRESS_AWARE

Standard: set bit `0x20` in the COFF characteristics word at `e_lfanew + 22`. Allows the
32-bit process 4GB of user address space instead of 2GB. Equivalent to the well-known
4GB Patcher; folded in so the toolchain is a single step.

---

## Fix 3 — `ClearBuckets` iteration-count overrun

### The crash

```
Exception code:  0xC0000005   (access violation)
Faulting offset: 0x0006BF22   ->  VA 0x0046BF22
```

`0x46BF22` is inside `ClearBuckets` (`0x46BEF0`), which runs on module load:

```
0046bef1  call 0x41feb0            ; AurTextureGetMaxTexID -> eax
0046bf18  jl   0x46bf43            ; skip if negative
0046bf1a  mov  ecx, 0x7fc00c       ; array base
0046bf1f  lea  edx, [eax + 1]      ; iterations = maxTexID + 1
0046bf22  mov  dword ptr [ecx], esi ; *ecx = 0        <-- FAULT
0046bf24  add  ecx, 0xc            ; stride 12
0046bf27  dec  edx
0046bf28  jne  0x46bf22
```

It clears **three** arrays by this count — `0x7FC00C`, and the pair `0x8194E4` /
`0x80AA84` in a second loop.

### Array capacities

Derived from the global layout (each entry is a 12-byte `{ptr, count, capacity}` triple):

| Array | Base | Next global | Span | Entries |
|---|---|---|---|---|
| bucket set A | `0x7FC00C` | `0x80AA6C` | 60000 | **5000** |
| bucket set B | `0x80AA80` | `0x8194E0` | 60000 | **5000** |
| mesh buckets | `0x8194E0` | `0x827F40` | 60000 | **5000** |

All three hold exactly 5000 entries — valid indices `0..4999`.

### Root cause

`DAT_007A46BC` (max texture ID) is computed as a running max of IDs read from mesh data
(`mesh + 0x40` texture-ref array), in two places — and only one bounds-checks:

```c
// AurTextureGetOrdering (0x421970)      -- CLAMPED
if ((id < 5000) && (0 < id)) { if (max < id) max = id; }

// AurTextureBuildAndStoreAll (0x4217F0) -- UNCLAMPED
if (max < id) max = id;
```

A mesh carrying an out-of-range texture reference propagates a wild ID through the
unclamped path. The observed crash implies a value around 19,552 — consistent with
`(0x835498 − 0x7FC00C) / 12 ≈ 19,553`, i.e. the loop ran to the end of `.data`.

The engine's own `< 5000` test documents the intended bound.

### Patch

`AurTextureGetMaxTexID` (`0x41FEB0`) is a two-instruction getter with **exactly one
caller** (`ClearBuckets`) and 10 bytes of `nop` padding — so clamping it is surgical.

```
0x41FEB0  a1 bc 46 7a 00   mov eax,[0x7A46BC]
0x41FEB5  e9 ...           jmp cave_A            ; was: ret + padding

cave_A @ 0x73C200:
  3d 88 13 00 00   cmp eax, 5000
  72 05            jb  +5                        ; in range -> keep
  b8 87 13 00 00   mov eax, 4999                 ; saturate
  c3               ret
```

**Note on an earlier iteration:** the first version of this patch used
`and eax, 0x0FFF` (mask to 4095). That is crash-safe but *wrong* — a bitmask wraps rather
than saturates, so a legitimate max of e.g. 4500 would clear only buckets `0..404`,
leaving stale entries. The released patch saturates.

### Effect

Bucket clearing covers the full valid range and stops at the array bound. Phantom
buckets above 4999 are simply not cleared — they are not addressable by the renderer
anyway.

---

## Fix 4 — `AddPartToMeshBuckets` unguarded write

### The bug

`AddPartToMeshBuckets` (`0x46BDF0`) is the write side of the same arrays, called per mesh
part during rendering:

```c
texID = Material::GetTextureTID(part[0x11]);        // no bounds check
cap   = *(int *)(&DAT_008194e8 + texID * 0xc);      // read  bucket[texID]
if ((&DAT_008194e4)[texID*3] == cap)
    CExoArrayList::Allocate(&DAT_008194e0 + texID*3, ...);   // alloc through it
*(int **)((&DAT_008194e0)[texID*3] + (&DAT_008194e4)[texID*3] * 4) = part;  // WRITE
```

Same 5000-entry arrays, same missing clamp — but this path writes pointers and calls
`Allocate` through a computed address, rather than the zero-fill in fix 3. Fix 3 does not protect it: this function never calls the getter.

Disassembly at the relevant site:

```
0046be5f  e8 9c f0 00 00   call 0x47af00          ; Material::GetTextureTID -> eax
0046be64  8d 34 40         lea  esi, [eax+eax*2]  ; esi = texID * 3
0046be67  8b 04 b5 e8 94 81 00   mov eax,[esi*4 + 0x8194e8]
0046be6e  8b 0c b5 e4 94 81 00   mov ecx,[esi*4 + 0x8194e4]
```

### Patch

The ID is consumed at several sites, so an inline clamp doesn't fit; a detour is used.
Skipping (rather than clamping) is deliberate: clamping an out-of-range part into a valid
bucket would attach it to an unrelated texture and mis-render. Skipping drops a part that
references a nonexistent texture and therefore could not render correctly regardless.

```
0x46BE64  e9 ...   jmp cave_B          ; replaces lea + first mov (10 bytes, 5 nop'd)

cave_B @ 0x73C220:
  3d 88 13 00 00           cmp eax, 5000
  73 0f                    jae skip
  8d 34 40                 lea esi,[eax+eax*2]        ; relocated original
  8b 04 b5 e8 94 81 00     mov eax,[esi*4+0x8194e8]   ; relocated original
  e9 ...                   jmp 0x46BE6E               ; resume
skip:
  5e                       pop esi
  5f                       pop edi
  c3                       ret
```

**Stack balance:** at the detour point the stack holds `[esi][edi][ret]` — `edi` pushed at
function entry (`0x46BDF0`), `esi` at `0x46BE5E`; the intervening `__thiscall` is
esp-neutral. The skip path's `pop esi; pop edi; ret` matches how the normal path unwinds
at `0x46BEB1`/`0x46BEBA`.

### Status

Verified by inspection; not confirmed in play. The bug is latent: it fires
only when a mesh with an out-of-range texture ID is actually *drawn*, which cannot be
triggered on demand. The patch leaves all valid rendering byte-identical (any `texID <
5000` follows the original instruction sequence exactly); only out-of-range parts take
the new path. Reports that reproduce the original fault would be useful.

### Possible secondary effect

During testing, an under-clamped intermediate version of fix 3 (the `0x0FFF` mask, which
still permitted writes up to index 8191 — roughly 38 KB past the arrays) coincided with
crashes reported as faults inside `atioglxx.dll` (the AMD OpenGL driver). After
tightening the clamp, the transition that previously crashed became stable. This is consistent with corrupted renderer state reaching the driver, but is not proof:
the sample is one machine and the earlier crash was intermittent. A fault inside a
graphics driver is not by itself evidence of a game bug.

---

## Code caves

Both detours live in `.text` trailing padding (3,632 zero bytes ending at `0x73D000`):

| Cave | VA | Size used |
|---|---|---|
| A (saturating clamp) | `0x73C200` | 13 bytes |
| B (range skip) | `0x73C220` | 25 bytes |

Non-overlapping, inside `.text`, executable. No section resizing, no relocation changes;
file size is unchanged.

---

## Distribution note

The patcher edits the user's own executable in place rather than shipping a pre-patched
binary. This matters: KOTOR exes in the wild already differ — UniWS widescreen rewrites
the resolution tables, the high-resolution menus patch modifies the exe further, and the
4GB flag may or may not be set. A single prebuilt binary silently reverts whatever the
user had. (This was caught during testing: a build made from a clean editable exe removed
a tester's widescreen setup and broke the menus.)

The four patches themselves are position-independent with respect to those mods — they
touch one `.data` float, one PE header bit, and two regions of unused `.text` padding, none
of which UniWS or the menu patch use.

## Reproducing the analysis

```bash
# decompile everything once (Ghidra headless or the GUI script in tools/)
# then overlay Lane's symbols:
python tools/apply_symbols.py kotor_decomp.c k1_win_gog_swkotor.exe.xml > named.c

# find a crash's function from an Event Viewer offset:
#   VA = image_base (0x400000) + fault_offset
```

Patch verification is built into the patcher:

```
python kotor_patch.py swkotor.exe --verify
```

`--revert` restores the executable **byte-for-byte** (verified: 0 differing bytes against
a stock copy).

---

## Open threads

- **Fix the delta-time handling instead of capping.** A cap is a workaround; the correct
  fix makes the movement/turn state machine framerate-independent. Reportedly being
  pursued elsewhere in the community (KPM).
- **Add the `< 5000` clamp to `AurTextureBuildAndStoreAll`** to match its clamped sibling.
  That fixes the *cause* rather than the two symptoms; needs a code cave.
- **Raise the limit.** The arrays are fixed 5000-entry blocks in `.data`. Allocating them
  at runtime instead would remove the ceiling, but is a much larger change.
- **Identify what emits out-of-range texture IDs.** Likely a specific mod model with a
  bad texture reference; would be worth reporting upstream.

---

## Credits

**Lane** — [KotOR 1 (GoG) reverse engineering](https://deadlystream.com/topic/11948-kotor-1-gog-reverse-engineering/):
24,242 labelled functions, data types, and class structure. Fixes 3 and 4 were found by
searching named subsystems in that map.

*Reverse engineered from a legally owned copy for interoperability and bug fixing.
Not affiliated with BioWare / LucasArts / Aspyr.*
