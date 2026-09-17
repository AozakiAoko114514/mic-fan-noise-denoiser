import os, sys, time, json
import numpy as np
ROOT = r"C:\Users\29933\Desktop\harrness\mic-denoise"
sys.path.insert(0, os.path.join(ROOT, "lib")); sys.path.insert(0, ROOT)
import sounddevice as sd
cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
cap = cfg["voicemeeter"]["capture_device"]
buf = []
def cb(indata, frames, t, s): buf.append(indata[:,0].copy())
with sd.InputStream(device=cap, channels=1, samplerate=48000, blocksize=1024, dtype="float32", callback=cb):
    time.sleep(6)
x = np.concatenate(buf)
print("  从 Voicemeeter Out 6 录制 6 秒：RMS %.2f dBFS" % (20*np.log10(max(float(np.sqrt(np.mean(x**2))),1e-12))))
