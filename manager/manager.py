# -*- coding: utf-8 -*-
"""
manager.py -- one-click start / stop / status / verify for the denoiser.

Portable by design:
  * no hardcoded user paths (everything is relative to this file)
  * Voicemeeter executable is auto-discovered
  * process handling uses ctypes only (no psutil, no WMI)

CLI:
    python manager.py            # interactive menu
    python manager.py --status
    python manager.py --start
    python manager.py --stop
"""
import ctypes
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
DENOISE = os.path.join(BASE, "denoise")
TOOLS = os.path.join(DENOISE, "tools")
TESTS = os.path.join(DENOISE, "tests")
PY = sys.executable or "python"
PID_FILE = os.path.join(DENOISE, "pipeline.pid")
LOG = os.path.join(DENOISE, "live_run.log")
ERRLOG = os.path.join(DENOISE, "live_run.err.log")
CFG = os.path.join(DENOISE, "config.json")
LIVE = os.path.join(DENOISE, "live.py")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
TH32CS_SNAPPROCESS = 0x00000002

VM_CANDIDATES = [
    r"C:\Program Files (x86)\VB\Voicemeeter\voicemeeter_x64.exe",
    r"C:\Program Files\VB\Voicemeeter\voicemeeter_x64.exe",
    r"C:\Program Files (x86)\VB\Voicemeeter\voicemeeter.exe",
]


def find_voicemeeter():
    for p in VM_CANDIDATES:
        if os.path.exists(p):
            return p
    hits = glob.glob(r"C:\Program Files*\VB\Voicemeeter\voicemeeter*.exe")
    return hits[0] if hits else None


# ------------------------------------------------------------------ processes
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260)]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = ctypes.c_void_p
_k32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
_k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
_k32.GetExitCodeProcess.restype = ctypes.c_int
_k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
_k32.TerminateProcess.restype = ctypes.c_int
_k32.CloseHandle.argtypes = [ctypes.c_void_p]
_k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
_k32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
_k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]


def pid_alive(pid):
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
    if not h:
        return False
    code = ctypes.c_uint()
    ok = _k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
    _k32.CloseHandle(ctypes.c_void_p(h))
    return bool(ok) and code.value == STILL_ACTIVE


def kill_pid(pid):
    h = _k32.OpenProcess(PROCESS_TERMINATE, 0, int(pid))
    if not h:
        return False
    ok = _k32.TerminateProcess(ctypes.c_void_p(h), 1)
    _k32.CloseHandle(ctypes.c_void_p(h))
    return bool(ok)


def find_processes(exe_names):
    if isinstance(exe_names, str):
        exe_names = [exe_names]
    want = {n.lower() for n in exe_names}
    out = []
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return out
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _k32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() in want:
                out.append(int(e.th32ProcessID))
            ok = _k32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(e))
    finally:
        _k32.CloseHandle(ctypes.c_void_p(snap))
    return out


# ------------------------------------------------------------------ status
def pipeline_pid():
    if not os.path.exists(PID_FILE):
        return None
    try:
        pid = int(open(PID_FILE, encoding="utf-8").read().strip())
    except Exception:
        return None
    if pid_alive(pid):
        return pid
    try:
        os.remove(PID_FILE)
    except Exception:
        pass
    return None


def vm_pid():
    ps = find_processes(["voicemeeter_x64.exe", "voicemeeter.exe"])
    return ps[0] if ps else None


def get_cfg():
    try:
        return json.load(open(CFG, encoding="utf-8"))
    except Exception:
        return {}


def capture_desc():
    cd = get_cfg().get("voicemeeter", {}).get("capture_device")
    if cd is None:
        return "(not configured - run option [6])"
    try:
        sys.path.insert(0, os.path.join(DENOISE, "lib"))
        import sounddevice as sd
        return "[%d] %s" % (cd, sd.query_devices(cd)["name"])
    except Exception:
        return "[%d]" % cd


def log_age():
    return (time.time() - os.path.getmtime(LOG)) if os.path.exists(LOG) else None


def show_status():
    pp, vp = pipeline_pid(), vm_pid()
    age = log_age()
    print("=" * 70)
    print(" Mic Array Denoiser - manager")
    print(" project: %s" % DENOISE)
    print("=" * 70)
    print(" denoise pipeline : %s" % ("RUNNING  pid=%d" % pp if pp else "stopped"))
    print(" Voicemeeter      : %s" % ("RUNNING  pid=%d" % vp if vp else "not running"))
    print(" virtual mic      : %s" % capture_desc())
    print("   -> pick this device as the microphone in your apps")
    print(" live log         : %s"
          % ("live_run.log (updated %.0fs ago)" % age if age is not None else "none yet"))
    if pp and not vp:
        print(" ! pipeline is running but Voicemeeter is not -> apps get no audio")
    if vp and not pp:
        print(" i Voicemeeter is up but the pipeline is stopped -> apps hear silence")
    print("-" * 70)


# ------------------------------------------------------------------ actions
def start_all(quiet=False):
    if not vm_pid():
        exe = find_voicemeeter()
        if not exe:
            print(" x Voicemeeter not found. Install Voicemeeter Potato first.")
            return 1
        subprocess.Popen([exe], creationflags=DETACHED_PROCESS,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(" -> starting Voicemeeter, waiting for its engine...")
        for _ in range(40):
            time.sleep(0.5)
            if vm_pid():
                break
        time.sleep(2.0)

    pp = pipeline_pid()
    if pp:
        print(" -> pipeline already running (pid=%d), nothing to do" % pp)
    else:
        os.makedirs(DENOISE, exist_ok=True)
        try:
            fo = open(LOG, "w", encoding="utf-8")
            fe = open(ERRLOG, "w", encoding="utf-8")
            subprocess.Popen([PY, "-u", LIVE, "--run"], cwd=DENOISE, stdout=fo, stderr=fe,
                             creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
            print(" -> pipeline started, waiting ~6s for the noise estimate to converge...")
        except Exception as e:
            print(" x failed to start pipeline: %s" % e)
            return 1
        for _ in range(20):
            time.sleep(0.5)
            if pipeline_pid():
                break
        time.sleep(6.0)

    if not quiet:
        show_status()
        tail_log(12)
        if not pipeline_pid() and os.path.exists(ERRLOG):
            print(" ! pipeline did not start; stderr tail:")
            print(open(ERRLOG, encoding="utf-8", errors="replace").read()[-1500:])
    return 0


def stop_all():
    pp = pipeline_pid()
    if pp:
        kill_pid(pp)
        print(" -> pipeline stopped (pid=%d)" % pp)
    else:
        print(" -> pipeline was not running")
    try:
        os.remove(PID_FILE)
    except Exception:
        pass
    vp = vm_pid()
    if vp:
        kill_pid(vp)
        print(" -> Voicemeeter stopped (pid=%d)" % vp)
    print(" microphone is back to normal. Voicemeeter settings are kept.")
    return 0


def tail_log(n=20):
    if not os.path.exists(LOG):
        print(" (no log yet)")
        return
    lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()
    print("---- live_run.log (last %d lines) ----" % n)
    for ln in lines[-n:]:
        print("   " + ln)
    print("-" * 38)


def run_script(rel, extra=None):
    path = os.path.join(DENOISE, rel)
    if not os.path.exists(path):
        print(" x missing script: %s" % path)
        return 1
    cmd = [PY, path] + (extra or [])
    print(" $ %s" % " ".join(cmd))
    try:
        return subprocess.call(cmd, cwd=DENOISE)
    except Exception as e:
        print(" x failed: %s" % e)
        return 1


# ------------------------------------------------------------------ menu
def menu():
    while True:
        show_status()
        print("  [1] start everything (Voicemeeter + pipeline)")
        print("  [2] stop everything")
        print("  [3] restart pipeline")
        print("  [4] live compare: raw mic vs virtual mic")
        print("  [5] record a 10s A/B sample")
        print("  [6] re-detect devices and routing")
        print("  [7] show live log")
        print("  [8] restore Voicemeeter parameters")
        print("  [9] open project folder")
        print("  [0] quit")
        try:
            c = input(" choose: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if c == "1":
            start_all()
        elif c == "2":
            stop_all()
        elif c == "3":
            stop_all()
            start_all()
        elif c == "4":
            run_script(os.path.join("tools", "level_compare.py"))
        elif c == "5":
            run_script("live.py", ["--ab", "10"])
            run_script(os.path.join("tests", "compare_ab.py"))
        elif c == "6":
            if not vm_pid():
                start_all(quiet=True)
            run_script(os.path.join("tools", "vm_setup.py"))
        elif c == "7":
            tail_log(25)
        elif c == "8":
            run_script(os.path.join("tools", "vm_restore.py"))
        elif c == "9":
            try:
                os.startfile(DENOISE)
            except Exception as e:
                print(" x %s" % e)
        elif c == "0":
            return 0
        else:
            print(" invalid choice")
        try:
            input("\n press Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            return 0


def main():
    args = sys.argv[1:]
    if args:
        a = args[0].lower()
        if a == "--status":
            show_status()
            return 0
        if a == "--start":
            return start_all()
        if a == "--stop":
            return stop_all()
        if a == "--restart":
            stop_all()
            return start_all()
        if a == "--log":
            tail_log(30)
            return 0
        print("unknown option: %s" % a)
        return 2
    return menu()


if __name__ == "__main__":
    sys.exit(main())
