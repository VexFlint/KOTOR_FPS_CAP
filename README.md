# KOTOR 1 Engine Fixes

Seven small changes to `swkotor.exe`: a frame cap, the 4GB flag, two crash fixes in the
renderer's texture bookkeeping, and three fixes to the grass renderer. No launcher, no
DLL, no background process — it edits your own executable and can undo itself.

by VexFlint. Reverse engineering used [Lane's KotOR 1 (GoG) RE project](https://deadlystream.com/topic/11948-kotor-1-gog-reverse-engineering/) for symbols.

---

## What it changes

**1. Frame cap**

KOTOR ties simulation speed to frame timing — uncapped on a modern machine, the whole
game runs fast. The engine already contains a frame limiter, but the value that switches
it on defaults to 0, so it never runs. This sets it and the game paces itself. No driver
cap or 60 Hz monitor mode needed.

**2. 4GB memory support**

Sets LARGE_ADDRESS_AWARE so the 32-bit game can address 4GB instead of 2GB. Same as the
standard 4GB Patcher, folded in so it's one step.

**3. Crash on area load**

A texture bookkeeping loop runs past the end of its array (`0xC0000005`). Bounds-checked.

**4. Crash while rendering**

The renderer indexes those same arrays by raw texture ID with no bounds check and writes
through the result. Confirmed crashing at `0x46BEAE` on a heavily modded install.
Out-of-range parts now skip the bucket insert.

**5, 6, 7. Grass**

The grass renderer has three separate bugs: a double free, a wrong pointer handed to
OpenGL, and leaked GL state. Together they're why grass on Dantooine draws as beams and
filaments across the sky, and why "just turn grass off" became standard advice.

The pointer bug is the interesting one. `RenderGrassPolys` has three draw paths; two of
them pass the address of an always-zero field to `glVertexPointer` instead of the
allocated blade buffer. The third path — the one gated behind `GL_ATI_fragment_shader` —
gets it right. That extension only ever existed on ATI hardware and was dropped from
drivers years ago, so on NVIDIA, Intel, and any modern AMD card the game takes a broken
path. Grass has rendered incorrectly for essentially everyone since around 2006.

---

## Before you start

Steam's `swkotor.exe` is encrypted and can't be patched. Get **KOTOR Editable
Executable** from DeadlyStream and put it in the game folder first. GOG's exe is usually
already fine. The patcher checks and refuses files it doesn't recognise.

If you use UniWS widescreen, the high-resolution menu patch, or a mod build, apply those
first and this last. It only touches unused values and unused padding, so it stacks with
other exe mods.

---

## Install

You need Python 3 ([python.org](https://www.python.org/downloads/) — tick "Add Python to
PATH" during install).

1. Put `kotor_patch.py` anywhere
2. Open a Command Prompt and run:

```
python kotor_patch.py "C:\path\to\swkotor.exe"
```

It writes `swkotor.exe.bak` first, then reports what it changed.

```
python kotor_patch.py "...\swkotor.exe"            apply, 60 FPS cap
python kotor_patch.py "...\swkotor.exe" 72         apply, 72 FPS cap
python kotor_patch.py "...\swkotor.exe" 0          apply, no FPS cap
python kotor_patch.py "...\swkotor.exe" --verify   show current state
python kotor_patch.py "...\swkotor.exe" --revert   undo
```

`--revert` restores the original bytes exactly.

**This patches your own executable in place**, so any widescreen or menu patch you
already applied is preserved. No pre-patched binary is distributed, because one wouldn't
fit everyone's setup.

Turn **Grass on** in the in-game graphics options — it's worth seeing now.

---

## Checking it

`--verify` should show the cap set, 4GB aware, and all five code fixes `patched`:

```
fps cap             : 60.0
4GB aware           : yes
bucket clear clamp  : patched
bucket write guard  : patched
grass double free   : patched
grass vertex pointer: patched
grass client state  : patched
grass GL state      : patched
```

If any line says `unknown`, something else has modified that site and the patcher will
refuse to touch it rather than guess.

---

## Questions

**Do I still need a driver FPS cap or 60 Hz monitor mode?**
No. Leave the display at its normal refresh rate.

**Why cap at all if my PC can run it faster?**
Because the game speeds up with the framerate. The cap is what keeps it running at the
speed it was tuned for.

**Cutscenes?**
Unaffected — Bink plays them at their own encoded rate.

**Community Patch / mod builds / widescreen?**
Fine. No game content is touched, only the executable.

**Steam "Verify integrity" undid it.**
That restores the stock encrypted exe, which also removes any widescreen patch. Re-apply
your exe mods, then run the patcher again.

**Does this fix the movement freeze after combat?**
Indirectly — the cap makes it much rarer. The direct fix is J's *Post-Combat Movement
Fix* in the KotOR Patch Manager. Note that at the time of writing it also causes dialogue
lines to be skipped, so test it before committing to a playthrough.

---

## Limitations

- The frame limiter is a busy-wait; that's how BioWare implemented it. It spins a core
  pacing frames rather than sleeping.
- Fixes 3 and 4 stop the crashes but don't repair the underlying cause: the engine
  indexes fixed 5000-entry arrays with driver-assigned OpenGL texture names, which a
  heavily modded install can exceed. Parts above the limit are skipped rather than drawn.
- Tested on the standard PC v1.03 / editable-exe layout with a 180-mod build on AMD
  hardware. Other configurations may differ; the patcher refuses files it doesn't
  recognise.
- Everything is reversible with `--revert`.

---

## Credits

Lane's [KotOR 1 (GoG) reverse engineering](https://deadlystream.com/topic/11948-kotor-1-gog-reverse-engineering/)
supplied the function symbols. Finding these bugs depended on having named subsystems to
work from — `RenderGrassPolys` and `DestroyGrassPolys` are only obvious once they have
names.

Full analysis — addresses, disassembly, and how each bug was found and confirmed — is in
[`TECHNICAL.md`](TECHNICAL.md).

*Reverse engineered from a legally owned copy for interoperability and bug fixing.
Not affiliated with BioWare / LucasArts / Aspyr.*
