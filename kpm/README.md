# KPM patches

The same four fixes packaged for the
[KotOR Patch Manager](https://github.com/LaneDibello/Kotor-Patch-Manager).

KPM applies patches by injecting a DLL at runtime, so your executable is not
modified and patches can be enabled individually. If you use KPM, prefer these
over the standalone `kotor_patch.py` in the repository root — use one or the
other, not both.

| Directory | id | Type |
|---|---|---|
| `FrameCap` | `frame-cap` | 60 FPS cap using the engine's own limiter |
| `LargeAddressAware` | `large-address-aware` | 4GB address space (static hook) |
| `TextureBucketClearClamp` | `texture-bucket-clear-clamp` | crash fix (module load) |
| `TextureBucketWriteGuard` | `texture-bucket-write-guard` | crash fix (rendering) |

Full analysis for all four is in [`../TECHNICAL.md`](../TECHNICAL.md).

## Building

Copy a directory into your KPM `Patches` folder, then from inside it run:

```
..\create-patch.bat
```

That produces the `.kpatch` file. Point KPM's "Patches" path at wherever you
collect them.

## Target version

All four declare support for:

```
kotor1_cdcrack_103 = 761F9466F456A83909036BAEBB5C43167D722387BE66E54617BA20A8C49E9886
```

That is the executable the analysis was done against — the same hash Lane lists
as `kotor1_cdcrack_103`, which is also what the "KOTOR Editable Executable"
download provides.

**Other versions are not yet declared.** The addresses very likely match the GOG
build (Lane notes the versions often share addresses), but that has not been
verified, so the hashes are deliberately absent rather than guessed. To add one:
confirm the bytes at each hook address match the `original_bytes` in
`hooks.toml`, then add the version's SHA-256 to `[patch.supported_versions]`.
KPM verifies `original_bytes` before applying, so a mismatched version fails
safely rather than corrupting the game.

## Notes on the hooks

**`frame-cap`** is a `simple` hook: a four-byte data write to the cap float. It
works at runtime because nothing in the game writes that value during normal
play.

**`large-address-aware`** must be a `static` hook — the PE header has to be
patched before the image loads, so runtime injection cannot do it. Its address
is a file offset, not a virtual address.

**Both texture fixes** are `replace` hooks. The clear-clamp returns on every
path, so it never reaches KPM's automatic jump-back (which would land in
inter-function padding). The write-guard is arranged so the in-range path falls
off the end of the replacement, letting KPM's jump-back land exactly on
`0x46BE6E`, the instruction that would have executed next.

## Status

Fixes 1–3 were tested in play. Fix 4 is verified by inspection only — see the
caveat in its manifest and in `TECHNICAL.md`.
