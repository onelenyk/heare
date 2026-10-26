"""Cut a release: one version for every distribution, checked, committed, tagged.

    make release V=0.2.0

Refuses a dirty tree and a version not above the current one. Writes the
version everywhere it is carried, relocks, runs `make check`, commits
`release: vX.Y.Z` and tags `vX.Y.Z` here. It never pushes: the push is what
publishes (CI builds the wheels on the tag), so that step is the person's.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# The first `version = "..."` in a pyproject is its [project] version.
PYPROJECT = re.compile(r'^version = "([^"]+)"$', re.M)
# The site's install command and footer name the release they install.
SITE = (re.compile(r'const VERSION = "([^"]+)";'),
        re.compile(r"<span data-version>v([^<]+)</span>"))
# The server guide's Linux one-liner names the release in three places.
SERVER = re.compile(r"(releases/download/v|heare/v|--version )\d+\.\d+\.\d+")


def stop(text: str) -> None:
    print(f"спинився: {text}", file=sys.stderr)
    raise SystemExit(1)


def run(*argv: str) -> str:
    done = subprocess.run(argv, cwd=REPO, capture_output=True, text=True)
    if done.returncode:
        raise subprocess.CalledProcessError(done.returncode, argv,
                                            done.stdout, done.stderr)
    return done.stdout.strip()


def parse(version: str) -> tuple[int, int, int]:
    found = SEMVER.match(version)
    if not found:
        stop(f"версія має бути x.y.z, а не «{version}»")
    major, minor, patch = (int(n) for n in found.groups())
    return major, minor, patch


def carriers(repo: Path = REPO) -> list[Path]:
    """Every file that says the version: each package, the site, the server guide."""
    files = sorted(repo.glob("packages/*/pyproject.toml"))
    for extra in (repo / "site" / "index.html", repo / "docs" / "server.md"):
        if extra.exists():
            files.append(extra)
    return files


def current(repo: Path = REPO) -> str:
    """The version now; every package must agree, or there is no "now"."""
    seen = {}
    for path in sorted(repo.glob("packages/*/pyproject.toml")):
        found = PYPROJECT.search(path.read_text(encoding="utf-8"))
        if found:
            seen[path.parent.name] = found.group(1)
    versions = set(seen.values())
    if len(versions) != 1:
        stop(f"пакети не згодні щодо версії: {seen}")
    return versions.pop()


def rewrite(path: Path, version: str) -> bool:
    """*path* saying *version*; whether anything changed."""
    text = path.read_text(encoding="utf-8")
    if path.name == "pyproject.toml":
        new = PYPROJECT.sub(f'version = "{version}"', text, count=1)
    elif path.name == "server.md":
        new = SERVER.sub(lambda m: m.group(1) + version, text)
    else:
        new = SITE[0].sub(f'const VERSION = "{version}";', text)
        new = SITE[1].sub(f"<span data-version>v{version}</span>", new)
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        stop("назви версію: make release V=x.y.z")
    version = argv[0].removeprefix("v")
    wanted = parse(version)
    if run("git", "status", "--porcelain"):
        stop("у дереві є незакомічені зміни — спершу закоміть або прибери їх")
    now = current()
    if wanted <= parse(now):
        stop(f"v{version} не більша за поточну v{now}")
    tag = f"v{version}"
    if run("git", "tag", "--list", tag):
        stop(f"тег {tag} уже є")

    touched = [path for path in carriers() if rewrite(path, version)]
    print(f"v{now} → {tag}: {len(touched)} файлів", flush=True)
    try:
        subprocess.run(["uv", "lock"], cwd=REPO, check=True)
        subprocess.run(["make", "check"], cwd=REPO, check=True)
    except subprocess.CalledProcessError:
        # The tree was clean before, so going back is putting it back.
        run("git", "checkout", "--", ".")
        stop("перевірка не пройшла — версію повернуто, нічого не закомічено")

    run("git", "add", "--", "uv.lock", *(str(p.relative_to(REPO)) for p in touched))
    run("git", "commit", "-m", f"release: {tag}")
    run("git", "tag", "-a", tag, "-m", f"heare {tag}")
    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD")
    print(f"готово: коміт і тег {tag} — лише тут, нічого не відправлено.")
    print("Щоб випустити (CI збере колеса й створить реліз на GitHub):")
    print(f"    git push origin {branch} {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
