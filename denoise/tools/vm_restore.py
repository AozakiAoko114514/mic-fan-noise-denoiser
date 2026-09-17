# -*- coding: utf-8 -*-
"""
vm_restore.py —— 用 vm_state_before.json 的快照还原 Voicemeeter 的参数

配置过程中对 Voicemeeter 做过的改动（都能还原）：
  * 虚拟输入条打开了 B1 总线
  * 关闭了所有 Strip 的 A1~A5 硬件监听（防啸叫）
  * 硬件输入条静音
运行本脚本即可恢复到配置前的状态（需要 Voicemeeter 正在运行）。
"""
import ctypes
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DLL64 = r"C:\Program Files (x86)\VB\Voicemeeter\VoicemeeterRemote64.dll"
SNAP = os.path.join(ROOT, "vm_state_before.json")


def main():
    if not os.path.exists(SNAP):
        print("找不到快照文件:", SNAP)
        print("（只有在跑过 tests/vm_setup.py 之后才会有）")
        return 1
    snap = json.load(open(SNAP, encoding="utf-8"))
    dll = ctypes.WinDLL(DLL64)
    login = dll.VBVMR_Login
    login.argtypes = [ctypes.c_char_p]
    login.restype = ctypes.c_long
    setf = dll.VBVMR_SetParameterFloat
    setf.argtypes = [ctypes.c_char_p, ctypes.c_float]
    setf.restype = ctypes.c_long

    rc = -99
    for _ in range(40):
        rc = login(b"")
        if rc == 0:
            break
        time.sleep(0.5)
    if rc != 0:
        print("登录失败（rc=%d）：请先启动 voicemeeter_x64.exe" % rc)
        return 1

    ok = 0
    for k, v in snap.items():
        if setf(k.encode(), ctypes.c_float(float(v))) == 0:
            ok += 1
    print("已还原 %d/%d 项参数（%s）" % (ok, len(snap), SNAP))
    return 0


if __name__ == "__main__":
    sys.exit(main())
