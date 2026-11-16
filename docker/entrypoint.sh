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

# The container's port and the whole role here. The settings file is the
# person's, so only these keys are touched.
#
# The release check stays ON: this brain never installs into itself (the
# image is the update — Coolify pulls the new tag, and HEARE_NO_SELF_UPDATE
# keeps the keeper out of it), but it is the one place that knows which
# release is out, and the machines ask it. Turning the check off on 15
# November is why no machine was ever offered 0.9.26.
python - <<'PY'
import json
import re
from pathlib import Path

from heare.config import Settings

settings = Settings()
settings.set("api_host", "0.0.0.0")
settings.set("api_port", 9790)
settings.set("role", "all")
settings.set("update_check", True)

# The parts in this image are the machine's own, carried here by the image
# and not knocking in from outside: the trusted list is what a folder in
# the parts directory means. Kept, not replaced — a part the owner adds
# later stays trusted.
#
# An image before the machine had a name trusted `native-hands@<container
# id>`, and a container's id is new on every deploy: those keys piled up in
# the list and the brain showed each one as a machine nobody owns. They are
# dropped; every other key in the list belongs to a real machine and stays.
name = Path(settings.machine_name_file).read_text(encoding="utf-8").strip()
trusted = Path(settings.parts_file)
known = []
if trusted.exists():
    try:
        known = list(json.loads(trusted.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        known = []
ephemeral = re.compile(r"^(?:native-hands|sources)@[0-9a-f]{12}$")
known = [pid for pid in known if not ephemeral.match(str(pid))]
for pid in (f"native-hands@{name}",):
    if pid not in known:
        known.append(pid)
trusted.parent.mkdir(parents=True, exist_ok=True)
trusted.write_text(json.dumps(known), encoding="utf-8")
PY

# The parts this machine can hold. The native hands act from here; a
# channel like sources or eyes belongs to the body that knows the place —
# one owner per channel on a brain — so the server image leaves it off and
# removes a manifest an older image wrote.
python -m heare.machine --bin /usr/local/bin on native-hands || true
python -m heare.machine --bin /usr/local/bin off sources || true

exec /usr/local/bin/supervisord -c /etc/supervisord.conf
