#!/usr/bin/env python3
"""SOUL.md drift guard — is §3 (The Body) still true of the live body?

Probes every :port, ~/path, socket, `skill: name` and *_up helper that
SOUL.md names. Exit code = number of FAILs. Twin of face_drift_check.py.
ponytail: regex over the markdown, no parser; if SOUL grows a second table
this still works because it scans the whole file.
"""
from __future__ import annotations
import os, re, socket, subprocess, sys
from pathlib import Path

SOUL = Path.home() / ".nyriel" / "SOUL.md"
SKILLS = Path.home() / ".nyriel" / "skills"
ZSHRC = Path.home() / ".zshrc"
rows: list[tuple[str, str, str]] = []

def add(status, what, detail): rows.append((status, what, detail))

def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0

text = SOUL.read_text()
body = text.split("## §3")[1].split("## §4")[0] if "## §3" in text else text

# ports — `:8642`, `:3000`
for port in sorted({int(p) for p in re.findall(r"`[^`]*?:(\d{4,5})[^`]*`", body)}):
    add("OK" if listening(port) else "FAIL", f"port :{port}", "listening" if listening(port) else "nothing listening")

# paths — `~/Oblivion/x`, `~/.nyriel/x`, `~/Applications/x`
for raw in sorted(set(re.findall(r"`(~/[^`]+)`", body))):  # spaces allowed: `~/Applications/oblivion DEV.app`
    p = Path(os.path.expanduser(raw))
    add("OK" if p.exists() else "FAIL", f"path {raw}", "exists" if p.exists() else "missing")

# sockets
for sock in set(re.findall(r"`(/tmp/[^`]+\.sock)`", body)):
    ok = Path(sock).exists()
    add("OK" if ok else "FAIL", f"socket {sock}", "present" if ok else "missing")

# skills — `skill: name`
for name in sorted(set(re.findall(r"skill: `([a-z0-9-]+)`", body))):
    hits = list(SKILLS.glob(f"*/{name}/SKILL.md")) + list(SKILLS.glob(f"{name}/SKILL.md"))
    add("OK" if hits else "FAIL", f"skill {name}", str(hits[0].parent.relative_to(SKILLS)) if hits else "no SKILL.md")

# boot helpers — every *_up SOUL names must exist in ~/.zshrc, and body_up must call them
zsh = ZSHRC.read_text() if ZSHRC.exists() else ""
body_up = re.search(r"^body_up\(\)\s*\{(.*)\}$", zsh, re.M)
called = set(re.findall(r"(\w+_up)", body_up.group(1))) if body_up else set()
named = set(re.findall(r"`(\w+_up)`", body))
for fn in sorted(named):
    defined = re.search(rf"^{fn}\(\)", zsh, re.M) is not None
    add("OK" if defined else "FAIL", f"helper {fn}", "defined in ~/.zshrc" if defined else "not defined")
missing = sorted(called - named - {"body_up"})
add("OK" if not missing else "WARN", "body_up coverage", "SOUL names every helper body_up calls" if not missing else f"body_up calls {missing}, SOUL does not mention them")

# launchd agents — `ai.nyriel.x` and `ai.nyriel.word-{a,b}` brace sets
labels = set()
for m in re.findall(r"`(ai\.nyriel\.[\w.{},-]+)`", body):
    br = re.search(r"\{([^}]+)\}", m)
    labels.update(m.replace(br.group(0), x) for x in br.group(1).split(",")) if br else labels.add(m)
live = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
live_labels = set(re.findall(r"(ai\.nyriel\.[\w.-]+)", live))
for lab in sorted(labels):
    add("OK" if lab in live_labels else "FAIL", f"launchd {lab}", "loaded" if lab in live_labels else "not loaded")
extra = sorted(live_labels - labels)
add("OK" if not extra else "WARN", "launchd coverage", "SOUL lists every live agent" if not extra else f"live but unnamed: {extra}")

# tools SOUL claims — `hand_run`-style names must exist somewhere in the Brain's tools/
brain = Path.home() / "Oblivion" / "nyriel-brain"
for tool in sorted(set(re.findall(r"`([a-z]+_(?:run|status|dispatch|agent))`", body))):
    hit = subprocess.run(["grep", "-rlqw", tool, str(brain / "tools"), str(brain / "plugins")], capture_output=True).returncode == 0
    add("OK" if hit else "FAIL", f"tool {tool}", "found in tools/ or plugins/" if hit else "SOUL names a tool the Brain does not have")

w = max(len(r[1]) for r in rows)
for st, what, detail in rows:
    print(f"{st:<5}{what:<{w+2}}{detail}")
fails = sum(r[0] == "FAIL" for r in rows)
print(f"\n{fails} FAIL, {sum(r[0]=='WARN' for r in rows)} WARN, {len(rows)} guards")
sys.exit(fails)
