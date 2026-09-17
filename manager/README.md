# manager

One-click control panel for the denoiser. See the root
[README.md](../README.md) / [README.zh-CN.md](../README.zh-CN.md) for the full story.

## Use

| Entry point | What it does |
|---|---|
| `start-all.cmd` | start Voicemeeter + the denoise pipeline, then print status |
| `stop-all.cmd` | stop both |
| `status.cmd` | print status only |
| `python manager.py` | interactive menu (same as the .cmd files plus extras) |

Menu:

```
  [1] start everything (Voicemeeter + pipeline)
  [2] stop everything
  [3] restart pipeline
  [4] live compare: raw mic vs virtual mic      <- is it actually working?
  [5] record a 10s A/B sample
  [6] re-detect devices and routing
  [7] show live log
  [8] restore Voicemeeter parameters
  [9] open project folder
```

The status panel always answers the three questions that matter:
**is the pipeline running, is Voicemeeter running, and which microphone should the game pick.**

## Notes

* The pipeline is started with `DETACHED_PROCESS`, so closing the manager window
  leaves the denoiser running; use `[2]`/`stop-all.cmd` to actually stop it.
* The pipeline writes its PID to `denoise/pipeline.pid`, which is how the manager
  detects state and stays idempotent (pressing "start" twice will not spawn a second copy).
* No dependencies beyond the Python standard library + `ctypes` (no psutil, no WMI).
