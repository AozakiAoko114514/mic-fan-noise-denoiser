# -*- coding: utf-8 -*-
"""列出 PortAudio 能看到的所有设备，标出我们要用的输入/输出。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import sounddevice as sd  # noqa: E402

KEY_IN = "麦克风阵列"
KEY_OUT = "Voicemeeter"

print("PortAudio:", sd.get_portaudio_version()[1])
print("=" * 96)
for hostapi_i, ha in enumerate(sd.query_hostapis()):
    print("[%d] %-12s devices=%d  default_in/out=%s/%s"
          % (hostapi_i, ha["name"], len(ha["devices"]),
             ha.get("default_input_device"), ha.get("default_output_device")))
print("=" * 96)

for i, d in enumerate(sd.query_devices()):
    name = d["name"]
    mark = ""
    if KEY_IN in name and d["max_input_channels"] > 0:
        mark = "  <== 输入候选"
    if KEY_OUT in name and d["max_output_channels"] > 0:
        mark = "  <== 输出候选"
    if mark or d["max_input_channels"] or d["max_output_channels"]:
        print("%3d  in=%-2d out=%-2d sr=%-7.0f  %-52s [%s]%s"
              % (i, d["max_input_channels"], d["max_output_channels"],
                 d["default_samplerate"], name[:52],
                 sd.query_hostapis(d["hostapi"])["name"][:18], mark))
