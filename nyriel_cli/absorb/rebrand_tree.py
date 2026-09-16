#!/usr/bin/env python3
"""Apply transform T (see rename_map.py) to a git ref's tree.

Produces a NEW tree (or commit) whose paths and text bodies are rebranded
Hermes -> Nyriel, reusing the original blob oid wherever the swap is a no-op
(binaries, carve-outs, files with no Hermes token). Pure git plumbing — no
working-tree checkout, no mutation of the repo's index or HEAD.

Usage:
    rebrand_tree.py <ref> --tree-only
    rebrand_tree.py <ref> [--parent <commit>] [-m <msg>]   # prints commit oid

Run from inside the repo (uses the ambient .git).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

try:
    from . import rename_map as T          # packaged
except ImportError:                         # direct-script fallback
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rename_map as T  # noqa: E402


def git(*args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], input=input_bytes, capture_output=True, check=True
    ).stdout


def ls_tree(ref: str) -> list[tuple[str, str, str, str]]:
    """Return (mode, type, oid, path) for every entry under ref (recursive)."""
    raw = git("ls-tree", "-r", "-z", ref)
    entries = []
    for rec in raw.split(b"\x00"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, otype, oid = meta.decode().split()
        entries.append((mode, otype, oid, path.decode("utf-8", "surrogateescape")))
    return entries


def read_blobs(oids: list[str]) -> dict[str, bytes]:
    """Batch-read blob contents via `git cat-file --batch`.

    Uses communicate() (threaded I/O) to avoid the write-stdin/read-stdout pipe
    deadlock that bites when the blob payload exceeds the OS pipe buffer.
    """
    if not oids:
        return {}
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    stdout, _ = proc.communicate(("\n".join(oids) + "\n").encode())
    out: dict[str, bytes] = {}
    pos = 0
    for _ in oids:
        nl = stdout.index(b"\n", pos)
        header = stdout[pos:nl].decode().split()
        pos = nl + 1
        blob_oid, _otype, size = header[0], header[1], int(header[2])
        out[blob_oid] = stdout[pos:pos + size]
        pos += size + 1  # skip blob + trailing newline
    return out


def hash_blobs(blobs: list[bytes]) -> list[str]:
    """Batch-write blobs via `git hash-object -w --stdin-paths`; returns oids in order."""
    if not blobs:
        return []
    tmpdir = tempfile.mkdtemp(prefix="rebrand-blob-")
    paths = []
    for i, b in enumerate(blobs):
        p = os.path.join(tmpdir, f"b{i}")
        with open(p, "wb") as fh:
            fh.write(b)
        paths.append(p)
    out = git("hash-object", "-w", "--stdin-paths", input_bytes=("\n".join(paths) + "\n").encode())
    oids = out.decode().split()
    for p in paths:
        os.unlink(p)
    os.rmdir(tmpdir)
    assert len(oids) == len(blobs), (len(oids), len(blobs))
    return oids


def build_rebranded_tree(ref: str, attribution: bool = True) -> str:
    entries = ls_tree(ref)

    # Read every blob once (dedup by oid).
    blob_oids = sorted({oid for mode, otype, oid, path in entries if otype == "blob"})
    contents = read_blobs(blob_oids)

    # The swap result depends on the post-swap PATH (per-family carve-outs), so it
    # is computed per entry. Record the resulting oid per entry; dedup the blobs we
    # actually have to write by their swapped *content* to avoid re-hashing dupes.
    entry_oid: list[str] = []          # parallel to `entries`
    pending: dict[bytes, int] = {}     # swapped bytes -> index into write list
    write_blobs: list[bytes] = []
    entry_pending_idx: list[int] = []  # parallel to `entries`; -1 if reused

    for mode, otype, oid, path in entries:
        newpath = T.swap_path(path)
        if otype != "blob":
            entry_oid.append(oid)          # gitlinks/submodules pass through
            entry_pending_idx.append(-1)
            continue
        data = contents[oid]
        reuse = (
            T.is_self_authored(newpath)
            or T.looks_binary(data)
        )
        if not reuse:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                reuse = True
        if reuse:
            entry_oid.append(oid)
            entry_pending_idx.append(-1)
            continue
        swapped = T.swap_text(text, newpath, attribution=attribution)
        if swapped == text:
            entry_oid.append(oid)
            entry_pending_idx.append(-1)
            continue
        sb = swapped.encode("utf-8")
        idx = pending.get(sb)
        if idx is None:
            idx = len(write_blobs)
            pending[sb] = idx
            write_blobs.append(sb)
        entry_oid.append(None)             # filled in after hashing
        entry_pending_idx.append(idx)

    new_oids = hash_blobs(write_blobs)

    # Build index lines: "<mode> <oid>\t<newpath>"
    index_lines = []
    for i, (mode, otype, oid, path) in enumerate(entries):
        newpath = T.swap_path(path)
        pidx = entry_pending_idx[i]
        new_oid = new_oids[pidx] if pidx >= 0 else entry_oid[i]
        index_lines.append(f"{mode} {new_oid}\t{newpath}")

    # Materialise into a throwaway index, then write-tree.
    with tempfile.NamedTemporaryFile(prefix="rebrand-idx-", delete=False) as idxf:
        idx_path = idxf.name
    try:
        env = dict(os.environ, GIT_INDEX_FILE=idx_path)
        subprocess.run(["git", "read-tree", "--empty"], env=env, check=True)
        subprocess.run(
            ["git", "update-index", "--index-info"],
            env=env, input=("\n".join(index_lines) + "\n").encode(), check=True,
        )
        tree = subprocess.run(
            ["git", "write-tree"], env=env, capture_output=True, check=True
        ).stdout.decode().strip()
    finally:
        os.unlink(idx_path)
    return tree


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("--parent", default=None)
    ap.add_argument("-m", "--message", default=None)
    ap.add_argument("--tree-only", action="store_true")
    ap.add_argument("--no-attribution", action="store_true",
                    help="pure rename only; skip the self-attribution rule (gate use)")
    args = ap.parse_args()

    tree = build_rebranded_tree(args.ref, attribution=not args.no_attribution)
    if args.tree_only:
        print(tree)
        return 0
    msg = args.message or f"rebrand(T): {args.ref}"
    cmd = ["commit-tree", tree, "-m", msg]
    if args.parent:
        cmd += ["-p", args.parent]
    commit = git(*cmd).decode().strip()
    print(commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
