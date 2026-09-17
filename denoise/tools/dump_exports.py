# -*- coding: utf-8 -*-
"""dump_exports.py —— 纯标准库解析 PE 文件的导出表，看看 DLL 到底导出了什么名字。
（VoicemeeterRemote64.dll 的导出名与预期不符，需要确认真实名字/是否只有序号导出）"""
import struct
import sys


def parse(path):
    b = open(path, "rb").read()
    e_lfanew = struct.unpack_from("<I", b, 0x3C)[0]
    if b[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise SystemExit("不是有效的 PE 文件")
    coff = e_lfanew + 4
    machine, nsec, _, _, _, size_opt, _ = struct.unpack_from("<HHIIIHH", b, coff)
    opt = coff + 20
    magic = struct.unpack_from("<H", b, opt)[0]
    bits = "PE32+" if magic == 0x20B else "PE32"
    dd = opt + (112 if magic == 0x20B else 96)
    exp_rva, exp_size = struct.unpack_from("<II", b, dd)

    secs = []
    sec = opt + size_opt
    for i in range(nsec):
        o = sec + i * 40
        nm = b[o:o + 8].rstrip(b"\0").decode("latin1")
        vsz, va, rawsz, praw = struct.unpack_from("<IIII", b, o + 8)
        secs.append((va, vsz, praw, rawsz, nm))

    def rva2off(rva):
        for va, vsz, praw, rawsz, _ in secs:
            if va <= rva < va + max(vsz, rawsz):
                return praw + (rva - va)
        return None

    print("%s  %s  machine=0x%04X  节数=%d" % (path, bits, machine, nsec))
    if not exp_rva:
        print("  没有导出表")
        return
    eoff = rva2off(exp_rva)
    dllname_rva, base, nfunc, nnames = struct.unpack_from("<IIII", b, eoff + 12)
    af, an, ao = struct.unpack_from("<III", b, eoff + 28)
    no = rva2off(dllname_rva)
    print("  导出表大小=%d  DLL名=%s  序号基=%d  函数数=%d  具名导出数=%d"
          % (exp_size, b[no:b.index(b"\0", no)].decode("latin1"), base, nfunc, nnames))
    names = []
    for i in range(nnames):
        r = struct.unpack_from("<I", b, rva2off(an) + 4 * i)[0]
        o = rva2off(r)
        end = b.index(b"\0", o)
        names.append(b[o:end].decode("latin1"))
    print("  具名导出（%d 个）:" % len(names))
    for n in names:
        print("     " + n)
    if not names:
        print("     （无具名导出，只能按序号调用）")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        parse(p)
        print()
