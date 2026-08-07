# KOTOR 1 Engine Fixes — technical write-up

Reverse engineering notes, root-cause analysis, and exact patch details for seven fixes
to *Star Wars: Knights of the Old Republic* (2003, Odyssey engine).

by **VexFlint** — built on [Lane's KotOR 1 (GoG) RE project](https://deadlystream.com/topic/11948-kotor-1-gog-reverse-engineering/)

Static analysis (Ghidra + capstone) confirmed by in-game testing on a 180-mod build,
AMD hardware, 144 Hz display, UniWS widescreen. All addresses are for the standard PC
v1.03 layout (GOG / DeadlyStream "editable" executable), image base `0x400000`, where
raw file offset == VA − image base. The exe has no ASLR (`DllCharacteristics = 0x0000`),
so Ghidra addresses are runtime addresses.

---

## Summary

| # | Fix | Class | Site | Size |
|---|-----|-------|------|------|
| 1 | Arm the dormant frame limiter | disabled feature | `0x7A3C64` (.data) | 4 B |
| 2 | LARGE_ADDRESS_AWARE | PE flag | COFF characteristics | 1 bit |
| 3 | Clamp `ClearBuckets` iteration count | out-of-bounds write | `AurTextureGetMaxTexID` | 5 B + 13 B cave |
| 4 | Bounds-check bucket indexing | out-of-bounds write | `AddPartToMeshBuckets` | 10 B + 41 B cave |
| 5 | Grass buffer double free | double free | `DestroyGrassPolys` | 3 B |
| 6 | Grass vertex pointer | wrong pointer to GL | `RenderGrassPolys` | 6 B |
| 7 | Grass GL state leak | leaked GL state | `RenderGrassPolys` | 14 B + 90 B caves |

Fixes 3 and 4 share a root cause: three renderer arrays are fixed at 5000 entries and
are indexed by driver-assigned OpenGL texture names. Fixes 5–7 are three independent
bugs in the grass renderer.

---

## Method

1. **Import table triage.** `swkotor.exe` imports `QueryPerformanceCounter`,
   `GetTickCount` and `Sleep`, and drives OpenGL via `opengl32.dll`. The main loop uses
   `PeekMessageA` — run-as-fast-as-possible with no inherent cap.

2. **Caller climbing.** Imports are reached through IAT slots and thunks, so
   `getCallingFunctions` on a thunk returns nothing. You must take references to the
   *IAT slot address* and to the thunk entry point.

3. **Bulk export.** Export decompiled C for all functions once and grep locally rather
   than iterating in the GUI.

4. **Symbol overlay.** Lane's symbols turn `FUN_0046bef0` into `ClearBuckets`. Later,
   read access to the OpenKotOR Ghidra server provided a fully typed export with C++
   class names and struct fields — which is what made the grass bugs findable.

5. **Crash-driven analysis.** Event Viewer gives a fault offset; add the image base,
   find the containing function, disassemble the faulting instruction.

6. **Differential testing.** Each hypothesis shipped as a patched binary and was played.
   This mattered more than the analysis: five plausible theories about the grass bug were
   killed by testing before the real one surfaced.

---

## Fix 1 — frame limiter present but disabled

`WinMain` (`0x4041F0`), immediately after `SwapBuffers`:

```c
if (0.0f < cap) {                 // cap = _DAT_007a3c64
    target = 1000.0f / cap;
    while (elapsed < target) { /* busy-spin on the game timer */ }
}
```

| Symbol | VA | Value | Role |
|---|---|---|---|
| `_DAT_007a3c64` | `0x7A3C64` | **0.0** (.data) | frame cap, FPS |
| numerator | `0x73D6FC` | 1000.0 (.rdata) | `target_ms = 1000/cap` |
| zero | `0x73D700` | 0.0 (.rdata) | the `0.0 < cap` gate |
| timer scale | `0x73D708` | 0.001 (.rdata) | raw timer → ms |

The only writer of `_DAT_007a3c64` is `CSWSModule::LoadModuleFinish` (`0x4C5880`), gated
on `DAT_00832904` — a BSS variable with **no writer anywhere in the binary**. The guard
is never true, so the cap stays `0.0` and the limiter never runs.

```
0x7A3C64:  00 00 00 00   (0.0)   ->   00 00 70 42   (60.0)
```

**Why it matters.** Originally justified as a workaround for the post-combat movement
freeze. Testing showed a second, independent reason: KOTOR ties simulation speed to
frame timing, and uncapped the game runs noticeably fast. That is the stronger
justification — the movement freeze has a direct fix (see Open threads).

The three `.rdata` constants above are used as a fingerprint to reject the wrong
executable.

---

## Fix 2 — LARGE_ADDRESS_AWARE

Set bit `0x20` in the COFF characteristics word at `e_lfanew + 22`. Equivalent to the
well-known 4GB Patcher, and to KPM's `4gb-patch` (which expresses the same site as
VA `0x00400926`).

---

## Fix 3 — `ClearBuckets` iteration-count overrun

```
Exception code:  0xC0000005
Faulting offset: 0x0006BF22   ->  VA 0x0046BF22
```

`ClearBuckets` (`0x46BEF0`) clears three arrays `maxTexID + 1` times:

```
0046bef1  call 0x41feb0            ; AurTextureGetMaxTexID -> eax
0046bf1f  lea  edx, [eax + 1]      ; iterations = maxTexID + 1
0046bf22  mov  dword ptr [ecx], esi ; <-- FAULT
```

### Array capacities — from the C++ static constructors

```c
_eh_vector_constructor_iterator_(meshBuckets, 0xc, 5000, ...)
_eh_vector_constructor_iterator_(fadeBuckets, 0xc, 5000, ...)
_eh_vector_constructor_iterator_(capBuckets,  0xc, 5000, ...)
```

12-byte elements, exactly 5000 of them. Bases: `capBuckets 0x7FC008`,
`fadeBuckets 0x80AA80`, `meshBuckets 0x8194E0`.

**`meshBuckets` ends at `0x8194E0 + 5000*12 = 0x827F40`, which is exactly
`backgroundBucket`** — the skybox list. So `meshBuckets[5000]` *is* the skybox bucket's
`{data,size,capacity}`. That adjacency is why sky geometry is the first casualty when
the index runs over.

### Root cause

`maxTexID` is a running max of `gl_textures[]`, filled by **`glGenTextures`** — i.e.
driver-assigned GL names, not dense indices. Two functions compute it, and only one
bounds-checks:

```c
// AurTextureGetOrdering (0x421970)      -- CLAMPED
if ((id < 5000) && (0 < id)) { if (max < id) max = id; }

// AurTextureBuildAndStoreAll (0x4217F0) -- UNCLAMPED
if (max < id) max = id;
```

The observed crash implied a value near 19,552, consistent with
`(0x835498 − 0x7FC00C)/12`. That cannot be a live texture count in a 32-bit process, so
the names are climbing through create/delete churn rather than many textures existing at
once.

### Patch

`AurTextureGetMaxTexID` (`0x41FEB0`) is a two-instruction getter with exactly one caller
and 10 bytes of padding:

```
0x41FEB5  e9 ...           jmp cave           ; was: ret + padding

cave @ 0x73C200:
  3d 88 13 00 00   cmp eax, 5000
  72 05            jb  ret
  b8 87 13 00 00   mov eax, 4999              ; saturate, not mask
  c3               ret
```

Saturating rather than masking: an `AND` would wrap a legitimate max of 4500 down to 404
and leave stale buckets uncleared. (An earlier version of this patch made exactly that
mistake.)

---

## Fix 4 — `AddPartToMeshBuckets` unchecked write

```
Exception code:  0xC0000005
Faulting offset: 0x0006BEAE   ->  VA 0x0046BEAE
```

```
0046be5f  call 0x47af00            ; Material::GetTextureTID -> eax
0046be64  8d 34 40                 ; lea esi,[eax+eax*2]      esi = texID*3
0046be67  8b 04 b5 e8 94 81 00     ; mov eax,[esi*4+0x8194E8]
...
0046beae  89 56 04                 ; mov [esi+4],edx          <-- FAULT
```

`esi` is `meshBuckets + texID*12`. The fault confirms texture IDs above 5000 occur in
practice — this crash was observed after removing the guard, which settled an argument
that static analysis alone had not.

### Why skipping is the correct semantic

`DoMeshBuckets` (`0x455710`), the render side, iterates the list returned by
`AurTextureGetOrdering` — which is clamped to `< 5000`. A part filed above the limit
could never be drawn regardless. Skipping the bucket insert therefore loses nothing that
would have rendered.

The function has two halves, and this matters:

```
0x46BE5B .. 0x46BEB0   texID-indexed insert into meshBuckets   (unsafe)
0x46BEB1 .. 0x46BEE1   unconditional append to meshShadowBucket (always safe)
```

The first version of this patch bailed with `pop esi / pop edi / ret`, which skipped
**both** halves and silently disabled shadow casting for those parts. Lane raised this in
review; he was right. The current version rejoins at `0x46BEB1`.

```
0x46BE64:  jmp cave ; 5 nops        (was: lea esi + mov eax)

cave @ 0x73C220:
  3d 88 13 00 00        cmp eax, 5000
  72 13                 jb  in_range
  a1 bc bf 7f 00        mov eax,[0x7FBFBC]   ; meshShadowBucket.capacity
  8b 0d b8 bf 7f 00     mov ecx,[0x7FBFB8]   ; meshShadowBucket.size
  3b c8                 cmp ecx, eax         ; flags for the jne at 0x46BEB2
  68 b1 be 46 00        push 0x0046BEB1      ; position-independent jump
  c3                    ret
in_range:
  8d 34 40              lea esi,[eax+eax*2]  ; relocated originals
  8b 04 b5 e8 94 81 00  mov eax,[esi*4+0x8194E8]
  e9 ...                jmp 0x46BE6E
```

`push imm32 / ret` rather than a rel32 jump so the same bytes work unmodified as a KPM
`replace` hook, where the block is placed at a runtime-allocated address.

Stack at the hook point is `[esi][edi][ret]`: `edi` pushed at function entry, `esi` at
`0x46BE5E`, and the intervening `__thiscall` is esp-neutral. `0x46BEB1` is `pop esi`.

---

## Fix 5 — grass blade buffer double free

`CreateArrays` (`0x4A87C0`) allocates one buffer and stores it in two fields:

```c
pvVar15 = operator_new(vertexSize * count * 4);
this->field17_0x38 = pvVar15;
this->field18_0x3c = this->field17_0x38;    // same pointer
```

`DestroyGrassPolys` (`0x4A8460`) frees both:

```
004a847c  8b 56 3c   mov edx,[esi+0x3c]
004a847f  52         push edx
004a8480  e8 ...     call free            ; free #1
004a8485  8b 46 38   mov eax,[esi+0x38]
004a8488  50         push eax
004a8492  e8 ...     call free            ; free #2 -- same block
004a849a  83 c4 08   add esp, 8           ; cdecl, cleans both
```

`field18_0x3c` has exactly five assignments in the entire binary — constructor,
destructor, `DestroyGrassPolys`, `CreateArrays` (`= field17_0x38`), and
`RenderGrassPolys` (`= field17_0x38`, then `= 0`). **None of them is an allocation.** So
the first `free` is always either `free(NULL)` or a double free of `field17_0x38`.

It's a double free whenever a bin is created and destroyed without being rendered inside
`nearRadiusSquared` (15 units) — because only `RenderGrassPolys` zeroes the alias. Moving
around any open area does that constantly.

### Patch

```
0x4A847C:  8b 56 3c  ->  33 d2 90     ; mov edx,[esi+0x3c] -> xor edx,edx ; nop
```

`free(NULL)` is a no-op, the `push`/`add esp,8` pairing is untouched, and the real buffer
is still released by the second call. 3 bytes.

---

## Fix 6 — grass vertex pointer (the significant one)

`RenderGrassPolys` (`0x4A6D30`) calls `GLRender::DrawLightmappedGrass` from three places:

| Site | Path | First argument |
|---|---|---|
| `0x4A6FFC` | ATI fragment shaders | `mov edx,[esi+0x38]` — **the buffer** |
| `0x4A711A` | multitexture | `lea eax,[esi+0x40]` — the *address of a field* |
| `0x4A71D4` | fallback | `lea eax,[esi+0x40]` — the *address of a field* |

That first argument goes straight into `glVertexPointer`:

```
0042624e  mov esi,[esp+0x14]      ; param_1
00426256  push esi                ; pointer
00426257  push eax                ; stride
00426258  push 0x1406             ; GL_FLOAT
0042625d  push 3                  ; size
0042625f  call [glVertexPointer]
```

`field19_0x40` is written in exactly three places — the constructor, the destructor, and
`DestroyGrassPolys` — and all three write `0`. It is **never assigned a buffer**. So two
of the three paths hand OpenGL a pointer into the `CAurTriangleBin` object itself, and
the blade positions are read out of adjacent object fields: pointers and counters
reinterpreted as XYZ floats. The result is quads stretching from their anchor to
wherever those values land — the "beams" and filaments across the sky.

The correct form exists in the same function, which is what makes this a defect rather
than an interpretation.

### Who is affected

The correct path is gated behind `aurATIFragmentShadersBumpMapAvailable()` —
`GL_ATI_fragment_shader`, a vendor extension from the Radeon 8500 era. (The neighbouring
function is literally named `AurNonRadeon8500Validate`.)

- **NVIDIA** — never implemented it. Broken path, always.
- **Intel** — same.
- **Modern AMD** — extension dropped from the drivers years ago. Broken path.
- **ATI Radeon 8500–X850 on period drivers** — the one configuration that worked.

KOTOR 1's grass has therefore rendered incorrectly for essentially every player since
roughly 2006, which is why "turn grass off" became folk wisdom rather than a bug report.

### Patch

```
0x4A7114:  8d 46 40  ->  8b 46 38     ; lea eax,[esi+0x40] -> mov eax,[esi+0x38]
0x4A71CE:  8d 46 40  ->  8b 46 38
```

Same instruction length, so no cave is needed. The ATI path at `0x4A6FFC` is left exactly
as BioWare wrote it. 6 bytes total.

---

## Fix 7 — grass GL state leaks

Two leaks in the same function, both confirmed by testing.

### 7a — client-state arrays

```c
glEnableClientState(GL_VERTEX_ARRAY);
glEnableClientState(GL_NORMAL_ARRAY);
glEnableClientState(GL_TEXTURE_COORD_ARRAY);
...
if (((field2 & 8) == 0) || (renderTextureGrass == 0)) {
    DrawLightmappedGrass(...);
    goto LAB_004a71dc;          // returns with all three still enabled
}
```

The other two paths clean up via `LAB_004a7196`. Path A does not, so subsequent draws
inherit a stale normal array — wrong normals, wrong lighting, every frame.

```
0x4A71D4:  jmp cave ; 3 nops       (was: call DrawLightmappedGrass ; add esp,0x18)

cave @ 0x73C280:
  e8 ...                call DrawLightmappedGrass
  83 c4 18              add esp, 0x18
  8b 15 98 f3 73 00 / 52 / ff 15 64 d2 73 00     glDisableClientState(GL_TEXTURE_COORD_ARRAY)
  8b 15 9c f3 73 00 / 52 / ff 15 64 d2 73 00     glDisableClientState(GL_NORMAL_ARRAY)
  8b 15 a4 f3 73 00 / 52 / ff 15 64 d2 73 00     glDisableClientState(GL_VERTEX_ARRAY)
  e9 ...                jmp 0x4A71DC
```

`glDisableClientState` is `__stdcall` (the existing cleanup path pushes and calls with no
stack fixup), so no `add esp` is needed.

### 7b — material, colour and texture state

`RenderGrassPolys` sets material ambient and diffuse from `grassInfo`, changes texture
environment state and toggles lighting across its branches, and restores none of it.
Anything drawn afterwards that doesn't set its own material inherits the grass's — the
"plain colours" symptom, which survives until the GL context is reset (alt-tab).

Rather than patch each branch, save and restore the block:

```
0x4A6E01:  jmp push_cave ; nop      (was: mov edx,[0x831E44])
0x4A71DC:  jmp pop_cave ; 3 nops    (was: mov ecx,[esi+0xc] ; mov eax,[0x827FB0])

push_cave @ 0x73C2C0:               pop_cave @ 0x73C2E0:
  68 41 20 04 00   push 0x42041       ff 15 6c d2 73 00  call [glPopAttrib]
  ff 15 68 d2 73 00 call [glPushAttrib]  8b 4e 0c        mov ecx,[esi+0xc]
  8b 15 44 1e 83 00 mov edx,[0x831E44]   a1 b0 7f 82 00  mov eax,[0x827FB0]
  e9 ...            jmp 0x4A6E07         e9 ...          jmp 0x4A71E4
```

Mask `0x42041` = `GL_CURRENT_BIT | GL_LIGHTING_BIT | GL_ENABLE_BIT | GL_TEXTURE_BIT`.
`GL_TEXTURE_BIT` was required — a mask without it fixed per-frame lighting but not the
across-module-reload case.

### Balance proof

An unmatched push or pop would corrupt the GL attribute stack, so the control flow was
checked exhaustively:

- `RenderGrassPolys` contains exactly **one** `ret` (`0x4A71FD`), reached only by
  fall-through from the shared tail.
- Everything branching to the pop site `0x4A71DC` — `0x4A71B4`, `0x4A71C2`, and the
  fall-through from the client-state cave — originates **after** the push at `0x4A6E01`.
- The early-exit labels `0x4A7200` / `0x4A7204` / `0x4A7206` are reached only from
  `0x4A6D5B`, `0x4A6D6A`, `0x4A6D79`, `0x4A6D86`, `0x4A6DD3`, `0x4A6DFB` — **all before**
  the push — and they carry their own epilogue and `ret 0xc`, never touching `0x4A71DC`.

Client-state arrays live on a separate stack (`glPushClientAttrib`), so 7a and 7b do not
interact.

---

## Verification

- `kotor_patch.py --selftest` asserts 13 non-overlapping patch regions and checks the
  jump arithmetic round-trips.
- Stock bytes at all nine patch sites were verified against an executable with the grass
  sites untouched, and the three grass caves confirmed to be zero padding.
- Apply → `--verify` → `--revert` → compare: reverting reproduces the pre-patch file
  byte-for-byte.
- Applying to a stock exe reproduces the tested build exactly (md5
  `af0ac593778bab622aaf1e62ac27caf7`, 0 differing bytes).
- Fixes 1–4 and 5–7 were each confirmed in play. Fix 6 is the only one that produces an
  immediately visible before/after.

---

## Open threads

**The 5000-entry ceiling is not repaired.** Fixes 3 and 4 are damage control. The engine
indexes fixed arrays with driver-assigned GL texture names, which climb monotonically as
modules load and unload. The proper fixes are either to recycle texture names densely (a
free-list around `glGenTextures`/`glDeleteTextures`, ~30 lines, keeps names bounded by
*peak live* textures) or to relocate the three arrays to a larger allocation. The latter
needs KPM's planned "Global Data Redirection" hook type, which does not exist yet.

**J's Post-Combat Movement Fix skips dialogue.** `post-combat-movement-fix-v1` in KPM
replaces a virtual accessor at `0x623B9C` inside `ProcessInput` with a direct read of
`[esp+0x10]` / `[esp+0x14]`. That bypasses input-context gating, so during conversations
the raw key state still reaches `0x63D470` / `0x63CAD0` and lines get skipped. Confirmed
by isolation: reverting only those 11 bytes restores correct dialogue. Reported upstream.

**The `kpm/` directory in this repo is stale.** It was written against an assumed schema.
The real one differs: `[[hooks]]` not `[[hook]]`, bare-integer `address` not a quoted
string, `original_bytes` as an integer array not a hex string, plus a required
`[metadata] target_versions` block. `LargeAddressAware` should be dropped entirely — it
duplicates KPM's `4gb-patch` and used a file offset where a VA was required.

**Grass distance band.** `nearRadiusSquared` (225) gates the wind update while
`farRadiusSquared` (900) gates drawing, so bins between 15 and 30 units render without a
wind pass. With fixes 5–7 applied this is no longer visible, and forcing the radii equal
made things worse, so it is left alone — noted only because it looks like a bug and isn't
worth patching.

---

*Reverse engineered from a legally owned copy for interoperability and bug fixing.
Not affiliated with BioWare / LucasArts / Aspyr.*
