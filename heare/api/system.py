"""This computer, in a few numbers: what a widget asks for when the
person wants to glance at how the machine is doing.

Read with the tools the system already has; nothing is installed for it.
Cached for a moment, because a widget on the screen asks every few
seconds and each answer costs a handful of processes.
"""

from __future__ import annotations

import os
import re
import subprocess
import time

_cache: tuple[float, dict] = (0.0, {})


def _run(*command: str) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def read() -> dict:
    global _cache
    if time.time() - _cache[0] < 2.0:
        return _cache[1]
    cores = os.cpu_count() or 1
    load = os.getloadavg()[0]
    info: dict = {
        "cpu": {"cores": cores, "load": round(load, 2),
                "percent": round(min(100.0, load / cores * 100), 1)},
    }
    top = _run("ps", "-Ao", "pcpu,rss,comm", "-r").splitlines()[1:6]
    info["top"] = []
    for line in top:
        parts = line.split(None, 2)
        if len(parts) == 3:
            info["top"].append({"cpu": float(parts[0].replace(",", ".")),
                                "mb": round(int(parts[1]) / 1024),
                                "name": os.path.basename(parts[2])})
    total = _run("sysctl", "-n", "hw.memsize").strip()
    stat = _run("vm_stat")
    if total.isdigit() and stat:
        page = int((re.search(r"page size of (\d+)", stat) or [0, 4096])[1])
        pages = {m[1]: int(m[2]) for m in re.finditer(r"Pages ([\w ]+):\s+(\d+)", stat)}
        used = (pages.get("active", 0) + pages.get("wired down", 0)
                + pages.get("occupied by compressor", 0)) * page
        gb = int(total) / 2**30
        info["memory"] = {"total_gb": round(gb, 1), "used_gb": round(used / 2**30, 1),
                          "percent": round(used / int(total) * 100, 1)}
    battery = re.search(r"(\d+)%;\s*([\w ]+);", _run("pmset", "-g", "batt"))
    if battery:
        info["battery"] = {"percent": int(battery[1]), "state": battery[2].strip()}
    disk = _run("df", "-k", "/").splitlines()
    if len(disk) > 1:
        cells = disk[1].split()
        if len(cells) > 4 and cells[1].isdigit():
            info["disk"] = {"total_gb": round(int(cells[1]) / 2**20, 1),
                            "free_gb": round(int(cells[3]) / 2**20, 1)}
    _cache = (time.time(), info)
    return info
