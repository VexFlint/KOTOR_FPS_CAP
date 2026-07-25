#!/usr/bin/env python3
"""Overlay Lane's Ghidra XML symbols onto a decompiled-C export.

Usage: python apply_symbols.py kotor_decomp.c k1_win_gog_swkotor.exe.xml > named.c
Turns FUN_0046bef0 into ClearBuckets/*FUN_0046bef0*/ so the export is greppable
by subsystem instead of by address.
"""
import re, sys

def load(xml):
    names = {}
    for line in open(xml, errors="ignore"):
        m = re.search(r'<FUNCTION ENTRY_POINT="([0-9a-fA-Fx]+)"[^>]*NAME="([^"]+)"', line)
        if m:
            names[m.group(1).lower().replace("0x", "")] = m.group(2)
    return names

def main():
    src, xml = sys.argv[1], sys.argv[2]
    names = load(xml)
    text = open(src, errors="ignore").read()
    out = re.sub(r"FUN_([0-9a-f]{8})",
                 lambda m: f"{names[m.group(1).lower()]}/*{m.group(0)}*/"
                           if m.group(1).lower() in names else m.group(0), text)
    sys.stdout.write(out)
    print(f"# {len(names)} symbols available", file=sys.stderr)

if __name__ == "__main__":
    main()
