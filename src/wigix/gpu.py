# SPDX-License-Identifier: GPL-3.0-only
"""GPU readings for the machine widgets: AMD through sysfs (amdgpu), NVIDIA through ``nvidia-smi``.

``read_gpu()`` returns ``{"name", "util" (0-100), "vram_used", "vram_total" (bytes), "temp" (C), "power" (W)}`` for
the first GPU it can read (fields it cannot read are None), or None when there is none. Cheap enough to call once a
second."""
from __future__ import annotations

import glob
import os
import shutil
import subprocess


def _num(path: str) -> float | None:
    try:
        with open(path) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return None


def _amd(root: str = "/sys/class/drm") -> dict | None:
    for dev in sorted(glob.glob(os.path.join(root, "card[0-9]*", "device"))):
        try:
            with open(os.path.join(dev, "vendor")) as f:
                if f.read().strip() != "0x1002":
                    continue
        except OSError:
            continue
        util = _num(os.path.join(dev, "gpu_busy_percent"))
        if util is None:                                  # an iGPU or a driver without the counter
            continue
        hw = sorted(glob.glob(os.path.join(dev, "hwmon", "hwmon*")))
        temp = _num(os.path.join(hw[0], "temp1_input")) if hw else None
        power = _num(os.path.join(hw[0], "power1_average")) if hw else None
        return {"name": "AMD GPU", "util": util,
                "vram_used": _num(os.path.join(dev, "mem_info_vram_used")),
                "vram_total": _num(os.path.join(dev, "mem_info_vram_total")),
                "temp": temp / 1000 if temp is not None else None,
                "power": power / 1e6 if power is not None else None}
    return None


def _nvidia() -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout
        return parse_nvidia(out.splitlines()[0])
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def parse_nvidia(line: str) -> dict | None:
    f = [p.strip() for p in line.split(",")]
    if len(f) < 6:
        return None

    def num(s, scale=1.0):
        try:
            return float(s) * scale
        except ValueError:                                # "[N/A]"
            return None
    return {"name": f[0], "util": num(f[1]), "vram_used": num(f[2], 2 ** 20), "vram_total": num(f[3], 2 ** 20),
            "temp": num(f[4]), "power": num(f[5])}


def read_gpu() -> dict | None:
    return _amd() or _nvidia()


def summary(g: dict) -> str:
    """'5.3G 61°C 119W' (VRAM in use, temperature, power) for the small line in the graph header; unreadable parts
    are left out. Kept short: it shares a half-width header with the label and the percentage."""
    parts = []
    if g.get("vram_used") is not None:
        parts.append(f"{g['vram_used'] / 2 ** 30:.1f}G")
    if g.get("temp") is not None:
        parts.append(f"{g['temp']:.0f}°C")
    if g.get("power") is not None:
        parts.append(f"{g['power']:.0f}W")
    return " ".join(parts)
