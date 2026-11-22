import os

BASE = "/home/olive/kokoclone"
os.chdir(BASE)
path = os.path.join(BASE, "olive_kokoro_wyoming.py")
src = open(path, encoding="utf-8").read()
src = src.replace("PORT = 10220", 'PORT = int(os.getenv("KOKORO_PORT", "10222"))')
exec(compile(src, path, "exec"), {"__name__": "__main__", "__file__": path})
