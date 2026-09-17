import os, sys, json, zipfile, urllib.request, shutil

ROOT = r"C:\Users\29933\Desktop\harrness\mic-denoise"
WHEELS = os.path.join(ROOT, "wheels")
LIB = os.path.join(ROOT, "lib")
os.makedirs(WHEELS, exist_ok=True)
os.makedirs(LIB, exist_ok=True)

probe = os.path.join(LIB, "_probe.txt")
try:
    open(probe, "w").write("x")
    os.remove(probe)
    print("[ok] 自建目录可写:", LIB)
except Exception as e:
    print("[FATAL] 无法写入", LIB, "->", e)
    sys.exit(1)

for mod in ("cffi", "numpy", "soundfile"):
    try:
        m = __import__(mod)
        print("[have] " + mod + " " + getattr(m, "__version__", "?"))
    except Exception as e:
        print("[none] " + mod)

NEED = ["sounddevice", "cffi", "pycparser"]

def pick(pkg):
    with urllib.request.urlopen("https://pypi.org/pypi/%s/json" % pkg, timeout=90) as r:
        data = json.load(r)
    ver = data["info"]["version"]
    pure = None
    win = None
    for f in data["releases"][ver]:
        n = f["filename"]
        if not n.endswith(".whl"):
            continue
        if "py3-none-any" in n:
            pure = (n, f["url"], ver)
        elif "win_amd64" in n and ("cp313" in n or "abi3" in n or "py3" in n):
            win = (n, f["url"], ver)
    return win or pure

for pkg in NEED:
    try:
        got = pick(pkg)
    except Exception as e:
        print("[FAIL] 解析 " + pkg + " ->", type(e).__name__, e)
        continue
    if not got:
        print("[FAIL] 找不到合适的 wheel:", pkg)
        continue
    n, url, ver = got
    dst = os.path.join(WHEELS, n)
    try:
        print("[dl] " + pkg + " " + ver + "  " + n)
        with urllib.request.urlopen(url, timeout=300) as r, open(dst, "wb") as f:
            shutil.copyfileobj(r, f)
        with zipfile.ZipFile(dst) as z:
            z.extractall(LIB)
        print("     -> 解包完成 (" + str(os.path.getsize(dst)) + " bytes)")
    except Exception as e:
        print("[FAIL] " + pkg + " ->", type(e).__name__, e)

sys.path.insert(0, LIB)
try:
    import sounddevice
    import numpy
    print("[ok] sounddevice", sounddevice.__version__, "| numpy", numpy.__version__)
    devs = sounddevice.query_devices()
    print("[ok] 设备总数:", len(devs))
except Exception as e:
    print("[FAIL] import ->", type(e).__name__, e)
