import subprocess
from nyriel_cli.absorb import verify


def _git(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True,
                          text=True, check=True)


def _mkrepo(tmp_path, files: dict[str, str]) -> str:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(str(repo), "init", "-q", "-b", "main")
    _git(str(repo), "config", "user.email", "t@t")
    _git(str(repo), "config", "user.name", "t")
    for path, body in files.items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-q", "-m", "init")
    return str(repo)


def _commit_tree(repo, files: dict[str, str]) -> str:
    """Commit files on a throwaway branch; return the new tree oid."""
    _git(repo, "checkout", "-q", "-b", "tmp")
    for path, body in files.items():
        with open(f"{repo}/{path}", "w", encoding="utf-8") as f:
            f.write(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    tree = _git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    _git(repo, "checkout", "-q", "main")
    return tree


def test_green_tree_passes(tmp_path):
    repo = _mkrepo(tmp_path, {
        "mod.py": "X = 1\n",
        "tests/nyriel_cli/test_absorb_driver.py": "def test_ok():\n    assert True\n",
    })
    merged = _commit_tree(repo, {"mod.py": "X = 2\n"})
    res = verify.run(repo, merged)
    assert res["compile_ok"] and res["tests_ok"] and res["ok"]


def test_syntax_error_fails_compile(tmp_path):
    repo = _mkrepo(tmp_path, {"mod.py": "X = 1\n"})
    merged = _commit_tree(repo, {"mod.py": "def broken(:\n"})
    res = verify.run(repo, merged)
    assert res["compile_ok"] is False and res["ok"] is False


def test_failing_targeted_test_fails(tmp_path):
    repo = _mkrepo(tmp_path, {
        "tests/nyriel_cli/test_absorb_driver.py": "def test_ok():\n    assert True\n",
    })
    merged = _commit_tree(
        repo, {"tests/nyriel_cli/test_absorb_driver.py":
               "def test_bad():\n    assert False\n"})
    res = verify.run(repo, merged)
    assert res["tests_ok"] is False and res["ok"] is False


def test_worktree_and_tempdir_always_cleaned(tmp_path, monkeypatch):
    repo = _mkrepo(tmp_path, {"mod.py": "X = 1\n"})
    merged = _commit_tree(repo, {"mod.py": "def broken(:\n"})
    vtmp = tmp_path / "vtmp"

    def fake_mkdtemp(prefix):
        vtmp.mkdir()
        return str(vtmp)

    monkeypatch.setattr(verify.tempfile, "mkdtemp", fake_mkdtemp)
    verify.run(repo, merged)
    out = _git(repo, "worktree", "list").stdout.strip().splitlines()
    assert len(out) == 1          # only the main worktree remains
    assert not vtmp.exists()      # mkdtemp parent removed too


def test_hung_targeted_tests_time_out(tmp_path, monkeypatch):
    repo = _mkrepo(tmp_path, {
        "tests/nyriel_cli/test_absorb_driver.py":
        "import time\n\ndef test_hang():\n    time.sleep(30)\n",
    })
    merged = _commit_tree(
        repo, {"tests/nyriel_cli/test_absorb_driver.py":
               "import time\n\ndef test_hang():\n    time.sleep(30)\n    assert True\n"})
    monkeypatch.setattr(verify, "TESTS_TIMEOUT", 3)
    res = verify.run(repo, merged)
    assert res["tests_ok"] is False and "timed out" in res["tests_summary"]
    out = _git(repo, "worktree", "list").stdout.strip().splitlines()
    assert len(out) == 1          # cleanup still ran after the timeout


def test_uncollectable_targeted_file_is_skipped_not_fatal(tmp_path):
    repo = _mkrepo(tmp_path, {
        "tests/nyriel_cli/test_absorb_driver.py": "def test_ok():\n    assert True\n",
        "tests/nyriel_cli/test_absorb_detect.py":
            "import definitely_not_installed_dep\n\ndef test_x():\n    assert True\n",
    })
    merged = _commit_tree(repo, {"tests/nyriel_cli/test_absorb_driver.py":
                                 "def test_ok2():\n    assert True\n"})
    res = verify.run(repo, merged)
    assert res["tests_ok"] is True
    assert "skipped" in res["tests_summary"]


def test_all_targets_uncollectable_is_red(tmp_path):
    repo = _mkrepo(tmp_path, {
        "tests/nyriel_cli/test_absorb_driver.py": "import definitely_not_installed_dep\n",
    })
    merged = _commit_tree(repo, {"tests/nyriel_cli/test_absorb_driver.py":
                                 "import definitely_not_installed_dep\n# edit\n"})
    res = verify.run(repo, merged)
    assert res["tests_ok"] is False and "collectable" in res["tests_summary"]


def test_real_targeted_set_mostly_collectable():
    """Battery must not be red-by-construction on this host: most targeted files
    must collect in the current interpreter (optional-dep files may skip)."""
    import os as _os, sys as _sys
    repo = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    present = [t for t in verify.TARGETED_TESTS
               if _os.path.exists(_os.path.join(repo, t))]
    env = dict(_os.environ)
    env["PYTHONPATH"] = repo + _os.pathsep + env.get("PYTHONPATH", "")
    runnable, skipped = verify._collectable(repo, present, env)
    assert len(runnable) >= 8, f"only {len(runnable)} collectable; skipped: {skipped}"
