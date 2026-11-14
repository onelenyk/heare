#!/bin/sh
# What a server container is before anything runs: a place for its data, a
# name the brain knows it by, and the settings the image promises.
set -eu

mkdir -p /home/heare/.heare/machine

# A container's hostname is new on every deploy; the machine's name is not.
# Written once, it is what the pult and the parts key on.
if [ ! -f /home/heare/.heare/machine/machine_name ]; then
    printf '%s' "${HEARE_MACHINE_NAME:-heare}" > /home/heare/.heare/machine/machine_name
fi

# The container's port, the whole role here, and no update check of its own:
# the image is the update (Coolify pulls the new tag; the keeper does not
# install into itself). The settings file is the person's, so only these
# four keys are touched.
python - <<'PY'
import json
from pathlib import Path

from heare.config import Settings

settings = Settings()
settings.set("api_host", "0.0.0.0")
settings.set("api_port", 9790)
settings.set("role", "all")
settings.set("update_check", False)

# The parts in this image are the machine's own, carried here by the image
# and not knocking in from outside: the trusted list is what a folder in
# the parts directory means. Kept, not replaced — a part the owner adds
# later stays trusted.
name = Path(settings.machine_name_file).read_text(encoding="utf-8").strip()
trusted = Path(settings.parts_file)
known = []
if trusted.exists():
    try:
        known = list(json.loads(trusted.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        known = []
for pid in (f"native-hands@{name}", f"sources@{name}"):
    if pid not in known:
        known.append(pid)
trusted.parent.mkdir(parents=True, exist_ok=True)
trusted.write_text(json.dumps(known), encoding="utf-8")
PY

# The parts this machine can hold. Turning one on is writing its folder; the
# keeper starts it within seconds and keeps it up.
python -m heare.machine --bin /usr/local/bin on native-hands sources || true

exec /usr/local/bin/supervisord -c /etc/supervisord.conf
