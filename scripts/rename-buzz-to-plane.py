#!/usr/bin/env python3
"""Rename the Buzz relay adapter to Plane across a Brain tree (WO-BRAIN/PLANE-RENAME-1).

    rename-buzz-to-plane.py [ROOT]           apply (re-runnable; a clean tree is a no-op)
    rename-buzz-to-plane.py [ROOT] --check   exit 1 on any "buzz" outside the carve-outs

ROOT defaults to this script's repo. A git ROOT renames tracked files with `git mv`;
any other directory (e.g. files copied out of a container) is walked.
Name-free: the rule table is found at <repo>/*_cli/absorb/plane_rules.py, so the
script runs unchanged in every Seraphim's tree.
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def load_rules(root: Path):
    here = Path(__file__).resolve().parent.parent
    found = sorted(here.glob("*_cli/absorb/plane_rules.py")) or sorted(root.glob("*_cli/absorb/plane_rules.py"))
    if not found:
        sys.exit("plane_rules.py not found under *_cli/absorb/")
    spec = importlib.util.spec_from_file_location("plane_rules", found[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def files(root: Path) -> list[str]:
    if (root / ".git").exists():
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
        return [p for p in out.decode().split("\0") if p and (root / p).is_file()]
    found = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        found += [os.path.relpath(os.path.join(d, n), root) for n in names]
    return found


def read_text(p: Path):
    data = p.read_bytes()
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check = "--check" in sys.argv
    root = Path(args[0] if args else Path(__file__).resolve().parent.parent).resolve()
    R = load_rules(root)
    is_git = (root / ".git").exists()
    bad = moved = edited = 0

    for rel in files(root):
        new = R.swap_path(rel)
        if new != rel:
            if check:
                print(f"PATH {rel}")
                bad += 1
                continue
            (root / new).parent.mkdir(parents=True, exist_ok=True)
            if is_git:
                subprocess.run(["git", "-C", str(root), "mv", rel, new], check=True)
            else:
                os.rename(root / rel, root / new)
            moved += 1
        text = read_text(root / new)
        if text is None:
            continue
        if check:
            for n, line in R.leftovers(text, new):
                print(f"{new}:{n}: {line.strip()[:160]}")
                bad += 1
            continue
        swapped = R.swap_text(text, new)
        if swapped != text:
            (root / new).write_text(swapped, encoding="utf-8")
            edited += 1

    if check:
        print(f"--check: {bad} leftover(s)" if bad else "--check: clean")
        return 1 if bad else 0
    # ponytail: emptied old dirs (untracked __pycache__ etc.) are left for git clean
    print(f"renamed {moved} path(s), rewrote {edited} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
