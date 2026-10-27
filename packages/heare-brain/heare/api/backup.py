"""A backup of the owner's memory and settings, and the way it comes back.

One zip in ``~/.heare/backups``: ``memory/`` and ``settings/`` as they are
on disk, with a manifest that says what is in it and a hash for every
file. Never ``machine/``: tokens and paired parts belong to the Mac they
were made on, and a backup taken to a new one pairs its own. Never the
model keys: they live in the Keychain and are typed again.

The database is copied with SQLite's own backup, so a copy made while the
brain writes is still whole. A restore cannot swap a database the brain
has open, so it happens in two halves: ``stage`` unpacks a checked backup
beside the live one and the brain leaves; ``apply`` puts it in place on
the next start, before anything opens it, and keeps what it replaced.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from heare.core.i18n import _
from datetime import datetime
from pathlib import Path

FORMAT = 1
# heare-20261025-142000.zip — the only names the door serves or takes.
NAME = r"heare-\d{8}-\d{6}\.zip"
SECTIONS = ("memory", "settings")
DATABASE = "memory/heare.db"
SOUND = "memory/heard/"
# SQLite's side files: the backup carries the database whole instead.
SKIPPED = {"memory/heare.db", "memory/heare.db-wal", "memory/heare.db-shm"}
PENDING = "restore-pending"
RESTORED = "restored.json"


class BackupError(ValueError):
    """A backup that cannot be trusted or used, said for the owner."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(home: Path, sound: bool) -> list[tuple[str, Path]]:
    """What goes in, by its name in the zip. Links are the old places
    layout.settle left behind; they point at files that are here anyway."""
    found = []
    for section in SECTIONS:
        root = home / section
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.name.startswith("."):
                continue
            name = path.relative_to(home).as_posix()
            if name in SKIPPED or (not sound and name.startswith(SOUND)):
                continue
            found.append((name, path))
    return found


def _counts(home: Path, database: Path | None, files: list[str]) -> dict:
    facts = 0
    if database is not None:
        try:
            with sqlite3.connect(database) as conn:
                facts = conn.execute(
                    "SELECT count(*) FROM facts WHERE status = 'confirmed'").fetchone()[0]
        except sqlite3.Error:
            facts = 0
    reminders = 0
    try:
        intents = json.loads((home / "memory" / "reminders.json").read_text(encoding="utf-8"))
        # Only what still waits to be said; a delivered one is history.
        reminders = sum(1 for i in intents
                        if i.get("why") == "reminder" and i.get("status") == "held")
    except (OSError, ValueError, TypeError, AttributeError):
        reminders = 0
    return {
        "facts": facts,
        "days": sum(1 for f in files if f.startswith("memory/journal/") and f.endswith(".jsonl")),
        "recordings": sum(1 for f in files if f.startswith(SOUND) and f.endswith(".wav")),
        "reminders": reminders,
    }


def free_name(folder: Path) -> Path:
    moment = time.time()
    while True:
        path = folder / time.strftime("heare-%Y%m%d-%H%M%S.zip", time.localtime(moment))
        if not path.exists():
            return path
        moment += 1


def make(home: Path, *, sound: bool = True, version: str | None = None) -> Path:
    """Write one backup and return where it is."""
    folder = home / "backups"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    with tempfile.TemporaryDirectory(dir=folder, prefix=".making-") as work:
        database = None
        if (home / DATABASE).is_file():
            database = Path(work) / "heare.db"
            with sqlite3.connect(home / DATABASE) as source, sqlite3.connect(database) as copy:
                source.backup(copy)
        files = _files(home, sound)
        entries = ([(DATABASE, database)] if database else []) + files
        manifest = {
            "format": FORMAT,
            "made": datetime.now().astimezone().isoformat(timespec="seconds"),
            "version": version,
            "sound": sound,
            "counts": _counts(home, database, [name for name, _ in entries]),
            "files": {name: {"size": path.stat().st_size, "sha256": _sha(path)}
                      for name, path in entries},
        }
        partial = Path(work) / "backup.zip"
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as out:
            out.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            for name, path in entries:
                stored = zipfile.ZIP_STORED if name.endswith(".wav") else zipfile.ZIP_DEFLATED
                out.write(path, name, compress_type=stored)
        partial.chmod(0o600)
        target = free_name(folder)
        os.replace(partial, target)
    return target


def _manifest(archive: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(archive.read("manifest.json"))
    except (KeyError, ValueError):
        raise BackupError(_("this is not a heare backup: no description inside")) from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise BackupError(_("the backup's description is damaged"))
    return manifest


def listing(home: Path) -> list[dict]:
    """The backups there are, newest first, each with what it holds."""
    found = []
    for path in sorted((home / "backups").glob("heare-*.zip"), reverse=True):
        if not re.fullmatch(NAME, path.name):
            continue
        entry = {"name": path.name, "size": path.stat().st_size}
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = _manifest(archive)
            entry.update(made=manifest.get("made"), sound=manifest.get("sound"),
                         counts=manifest.get("counts") or {}, version=manifest.get("version"))
        except (BackupError, zipfile.BadZipFile, OSError):
            entry["broken"] = True
        found.append(entry)
    return found


def check(path: Path) -> dict:
    """The manifest of a backup whose every file is there and unchanged;
    BackupError otherwise."""
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = _manifest(archive)
            if not isinstance(manifest.get("format"), int) or manifest["format"] > FORMAT:
                raise BackupError(_("the backup is from a newer heare — update heare first"))
            names = set(archive.namelist()) - {"manifest.json"}
            files = manifest["files"]
            for name in names | set(files):
                parts = Path(name).parts
                if (name.startswith("/") or ".." in parts or not parts
                        or parts[0] not in SECTIONS):
                    raise BackupError(_("a foreign path in the backup: {name}").format(name=name))
            if names != set(files):
                raise BackupError(_("the backup lacks files or has extra ones"))
            for name, about in files.items():
                digest = hashlib.sha256()
                with archive.open(name) as member:
                    for block in iter(lambda: member.read(1 << 20), b""):
                        digest.update(block)
                if digest.hexdigest() != about.get("sha256"):
                    raise BackupError(_("a file in the backup is damaged: {name}").format(name=name))
    except zipfile.BadZipFile:
        raise BackupError(_("the file is damaged or not a zip")) from None
    return manifest


def stage(home: Path, path: Path) -> dict:
    """Unpack a checked backup beside the live memory, for apply() on the
    next start. A stage not yet applied is replaced."""
    manifest = check(path)
    pending = home / PENDING
    shutil.rmtree(pending, ignore_errors=True)
    work = Path(tempfile.mkdtemp(dir=home, prefix=".staging-"))
    try:
        with zipfile.ZipFile(path) as archive:
            archive.extractall(work)
        (work / "from.json").write_text(json.dumps(
            {"name": path.name, "made": manifest.get("made"), "sound": manifest.get("sound")},
            ensure_ascii=False), encoding="utf-8")
        os.replace(work, pending)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return manifest


def apply(home: Path) -> dict | None:
    """Put a staged backup in place of memory/ and settings/; what was there
    goes to backups/before-restore-<time>/. None when nothing was staged."""
    pending = home / PENDING
    try:
        origin = json.loads((pending / "from.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    aside = home / "backups" / time.strftime("before-restore-%Y%m%d-%H%M%S")
    aside.mkdir(mode=0o700, parents=True, exist_ok=True)
    for section in SECTIONS:
        if (home / section).exists():
            os.replace(home / section, aside / section)
    for section in SECTIONS:
        if (pending / section).exists():
            os.replace(pending / section, home / section)
        else:
            (home / section).mkdir(mode=0o700)
    # A backup made without sound keeps the recordings this Mac has.
    heard = aside / "memory" / "heard"
    if not origin.get("sound") and heard.is_dir() and not (home / SOUND).exists():
        os.replace(heard, home / SOUND)
    shutil.rmtree(pending, ignore_errors=True)
    done = {**origin, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "aside": aside.name}
    (home / RESTORED).write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
    return done


def restored(home: Path) -> dict | None:
    """The last restore, for the page to say what happened."""
    try:
        return json.loads((home / RESTORED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
