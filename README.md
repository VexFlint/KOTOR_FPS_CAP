# KOTOR 1 Engine Fixes

Four small changes to `swkotor.exe`: a frame cap, the 4GB flag, and two bounds checks
in the renderer's texture bookkeeping. No launcher or background process.

by VexFlint. Reverse engineering used [Lane's KotOR 1 (GoG) RE project](https://deadlystream.com/topic/11948-kotor-1-gog-reverse-engineering/) for symbols.

---

## What it changes

**1. Frame cap (movement freeze after combat)**

KOTOR ties movement and combat turns to frame timing, and on high-refresh displays the
character's action queue can stall — you press forward and nothing happens until you
switch party members or reload.

The usual advice is to cap FPS externally or set the monitor to 60 Hz. The game already
contains a frame limiter, but the value that enables it defaults to 0, so it never runs.
This sets it, and the game limits itself. External caps are no longer needed.

**2. 4GB memory support**

Sets LARGE_ADDRESS_AWARE so the 32-bit game can use 4GB instead of 2GB. Same as the
standard 4GB Patcher, included so it's one step.

**3. Crash when loading some areas**

A texture bookkeeping loop can run past the end of its array (`0xC0000005`). More likely
on heavily modded installs; Endar Spire reproduced it consistently during testing. The
loop is now bounds-checked.

**4. Unchecked write to the same arrays**

The rendering side indexes those same arrays by texture ID without a bounds check.
Out-of-range parts are now skipped. See Limitations — this one is not confirmed in play.

---

## Before you start

Steam's `swkotor.exe` is encrypted and can't be patched. Get **KOTOR Editable Executable**
from DeadlyStream and put it in the game folder first. GOG's exe is usually already fine.
The patcher checks and refuses files it doesn't recognise.

If you use UniWS widescreen or a mod build, do those first and this last. It touches one
unused value and one unused padding region, so it stacks with other exe mods.

---

## Install

You need Python 3 ([python.org](https://www.python.org/downloads/), tick "Add Python to
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

`--revert` restores the file byte-for-byte.

**This patches your own executable in place**, so any widescreen (UniWS), high-resolution
menu patch, or other exe modification you already have is preserved. No pre-patched
binary is distributed, because one wouldn't fit everyone's setup.

Launch the game from `swkotor.exe` directly rather than through Steam if you've applied
widescreen — that requirement comes from UniWS, not from this patch.

---

## Using KPM instead

If you use the [KotOR Patch Manager](https://github.com/LaneDibello/Kotor-Patch-Manager),
the same four fixes are packaged as KPM patches in [`kpm/`](kpm/). KPM injects at
runtime instead of editing the executable, and lets you enable each fix
individually. Use KPM *or* the script above, not both.

---

## Checking it

`--verify` should show the FPS cap set, 4GB aware yes, and both texture fixes patched.
In game, with no external cap enabled, framerate should sit at 60 on a high-refresh
display.

---

## Questions

**Do I still need a driver FPS cap or 60 Hz monitor mode?**
No. You can leave the display at its normal refresh rate.

**Cutscenes?**
Unaffected — Bink plays them at their own encoded rate.

**Can I use a different cap than 60?**
Yes, pass any number. 60 matches what the engine's logic assumes; higher values reduce but
may not eliminate the freeze.

**Community Patch / mod builds / widescreen?**
Fine. No game content is touched.

**Steam "Verify integrity" undid it.**
That restores the stock exe, which also removes any widescreen patch you had.
Re-apply your exe mods, then run the patcher again.

---

## Limitations

- The frame limiter (fix 1) is a busy-wait — that's how BioWare implemented it; it spins
  a core while pacing frames rather than sleeping.
- Fix 3 prevents an out-of-bounds access; it doesn't repair whatever produced the bad
  texture ID in the first place.
- Fix 4 is verified by inspection but **not confirmed in play**. It only triggers when a
  model references a texture slot that doesn't exist, which can't be produced on demand.
  Valid rendering is unchanged; only out-of-range parts are skipped.
- Tested on the standard PC v1.03 / editable-exe layout. Other builds may differ; the
  patcher refuses files it doesn't recognise.
- All four changes are reversible with `--revert`, which restores the file byte-for-byte.

---

## Credits

Lane's [KotOR 1 (GoG) reverse engineering](https://deadlystream.com/topic/11948-kotor-1-gog-reverse-engineering/)
supplied the function symbols. Finding the texture bugs depended on having named
subsystems to work from.

Technical notes — addresses, disassembly, and how each was found — are in `TECHNICAL.md`
on the GitHub repo.

*Reverse engineered from a legally owned copy for interoperability and bug fixing.
Not affiliated with BioWare / LucasArts / Aspyr.*
