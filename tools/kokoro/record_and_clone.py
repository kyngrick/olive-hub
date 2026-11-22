#!/usr/bin/env python3
"""Record a voice sample and generate a KokoClone WAV (Windows side).

Run with the hub venv:
  .venv\Scripts\python.exe tools\kokoro\record_and_clone.py

Options:
  --ref path.wav    use an existing recording instead of the microphone
  --name Ricky      persona/ref name (default: my_voice)
  --text "..."      sentence to synthesize
  --lang en|it      language (default en)
  --seconds 12      recording length when using the microphone
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, ".venv", "Lib", "site-packages"))

REF_DIR = os.path.join(ROOT, "voice_refs")
WSL_PY = "/mnt/c/Users/Geggais and Rick/kokoclone/venv/bin/python"
WSL_SCRIPT = ("/mnt/c/Users/Geggais and Rick/Documents/Codex/olive_assist_v2/"
              "tools/kokoro/kokoclone_generate.py")


def to_wsl(path: str) -> str:
    path = os.path.abspath(path).replace("\\", "/")
    if path[1:2] == ":":
        drive = path[0].lower()
        path = path[2:]
    else:
        return path
    return f"/mnt/{drive}{path}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="")
    ap.add_argument("--name", default="my_voice")
    ap.add_argument("--text", default="Hello, this is Olive. I can now speak in my own voice.")
    ap.add_argument("--lang", default="en", choices=["en", "it"])
    ap.add_argument("--seconds", type=int, default=12)
    args = ap.parse_args()

    os.makedirs(REF_DIR, exist_ok=True)
    ref = os.path.abspath(args.ref) if args.ref else os.path.join(REF_DIR, args.name + "_ref.wav")
    out = os.path.join(REF_DIR, args.name + "_cloned.wav")

    if not args.ref:
        import sounddevice as sd
        import soundfile as sf
        input("Press ENTER, then speak naturally into the microphone...")
        print(f"Recording {args.seconds} seconds...")
        audio = sd.rec(int(args.seconds * 44100), samplerate=44100, channels=1, dtype="float32")
        sd.wait()
        sf.write(ref, audio, 44100)
        print(f"Saved reference: {ref}")

    print("Generating cloned speech with KokoClone (this takes 2-4 minutes on CPU)...")
    t0 = time.time()
    cmd = ["wsl.exe", "-e", WSL_PY, WSL_SCRIPT,
           to_wsl(ref), to_wsl(out), args.text, args.lang]
    proc = subprocess.run(cmd, text=True)
    if proc.returncode != 0 or not os.path.exists(out):
        print("KokoClone failed.", file=sys.stderr)
        return 1
    print(f"Done in {time.time()-t0:.0f}s: {out}")

    # Open in the default Windows player without blocking (sd.play/sd.wait can
    # hang when no interactive audio device is available).
    try:
        os.startfile(out)
        print("Opened the result in the default audio player.")
    except Exception as exc:
        print(f"(auto-play skipped: {exc}; open this file manually: {out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
