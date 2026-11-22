#!/usr/bin/env python3
"""Run inside WSL using the working KokoClone venv.

Usage:
  /mnt/c/Users/Geggais and Rick/kokoclone/venv/bin/python \
    kokoclone_generate.py <ref.wav> <out.wav> "<text>" [lang]
"""
import os
import sys
import time

BASE = "/home/olive/kokoclone"
os.chdir(BASE)
sys.path.insert(0, BASE)

from core.cloner import KokoClone  # noqa: E402

ref, out, text = sys.argv[1], sys.argv[2], sys.argv[3]
lang = sys.argv[4] if len(sys.argv) > 4 else "en"

t0 = time.time()
cloner = KokoClone()
print(f"KokoClone loaded in {time.time()-t0:.1f}s", flush=True)
t1 = time.time()
cloner.generate(text=text, lang=lang, reference_audio=ref, output_path=out)
print(f"DONE in {time.time()-t1:.1f}s -> {out}", flush=True)
