import os
import subprocess
import pytest
from nyriel_cli.absorb import driver


def _git(repo, *a):
    subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)


def test_install_ok_requires_upstream_remote(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _git(str(repo), "init", "-q")
    ok, msg = driver.install_ok(str(repo))
    assert ok is False and "upstream" in msg.lower()


def test_absorb_refuses_prerelease_tag(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _git(str(repo), "init", "-q")
    _git(str(repo), "remote", "add", "upstream", "https://example.invalid/x.git")
    with pytest.raises(driver.AbsorbRefused, match="pre-release"):
        driver.absorb(str(repo), "v2026.7.0-rc1")


import os
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_fidelity_gate_passes_on_current_tree():
    """T(base) must still reproduce HEAD modulo genuine divergence — 0 stray tokens."""
    ok, detail = driver.gate(REPO, driver.current_base(REPO))
    assert ok, f"rebrand map drifted; stray tokens:\n{detail}"


def _ns(**kw):
    base = dict(command="absorb", tag=None, base=None, check=False, gate=False,
                commit=False, abort=False, cont=False, verify=False,
                status=False, skip_verify=False)
    base.update(kw)
    return type("A", (), base)()


def test_cli_absorb_gate_runs(capsys):
    from nyriel_cli import main as m
    rc = m.cmd_absorb(_ns(gate=True))
    assert rc in (0, 1)
    assert "gate" in capsys.readouterr().out.lower()


def _mkrepo(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(str(repo), "init", "-q", "-b", "main")
    _git(str(repo), "config", "user.email", "t@t")
    _git(str(repo), "config", "user.name", "t")
    (repo / "a.txt").write_text("hello\n")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-q", "-m", "init")
    return str(repo)


def test_state_none_when_not_in_flight(tmp_path):
    repo = _mkrepo(tmp_path)
    assert driver.state(repo) is None


def test_state_roundtrip_and_clear(tmp_path):
    repo = _mkrepo(tmp_path)
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastMerged", "deadbeef")
    _git(repo, "config", "--local", "absorb.oursHead", "cafebabe")
    _git(repo, "config", "--local", "absorb.verifyOk", "true")
    st = driver.state(repo)
    assert st == {"tag": "v2026.7.0", "merged": "deadbeef",
                  "ours_head": "cafebabe", "verify_ok": True,
                  "verify_summary": ""}
    driver.clear_state(repo)
    assert driver.state(repo) is None


def test_abort_clears_state_and_branch(tmp_path):
    repo = _mkrepo(tmp_path)
    _git(repo, "branch", "absorb/v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastMerged", "deadbeef")
    driver.abort(repo)                      # tag resolved from state
    assert driver.state(repo) is None
    r = subprocess.run(["git", "-C", repo, "rev-parse", "-q", "--verify",
                        "refs/heads/absorb/v2026.7.0"], capture_output=True)
    assert r.returncode != 0


def test_abort_refuses_without_state_or_tag(tmp_path):
    repo = _mkrepo(tmp_path)
    with pytest.raises(driver.AbsorbRefused, match="no absorb in flight"):
        driver.abort(repo)


def test_absorb_refuses_when_head_divergence_drifted(tmp_path, monkeypatch):
    repo = _mkrepo(tmp_path)
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    (tmp_path / "r" / "UPSTREAM_BASE.md").write_text("| Upstream tag | `v2026.6.19` |\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base doc")
    _git(repo, "tag", "v2026.7.0")
    monkeypatch.setattr(driver, "gate", lambda r, b: (True, ""))
    monkeypatch.setattr(driver.rebrand_tree, "build_rebranded_tree",
                        lambda ref, attribution=True: "unused")
    with pytest.raises(driver.AbsorbRefused, match="divergence manifest drifted"):
        driver.absorb(repo, "v2026.7.0")


def test_absorb_refuses_second_tag_while_in_flight(tmp_path):
    repo = _mkrepo(tmp_path)
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    with pytest.raises(driver.AbsorbRefused, match="already in flight"):
        driver.absorb(repo, "v2026.8.0")


def test_abort_refuses_when_it_cannot_step_off_branch(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(str(repo), "init", "-q", "-b", "trunk")   # no `main` branch exists
    _git(str(repo), "config", "user.email", "t@t")
    _git(str(repo), "config", "user.name", "t")
    (repo / "a.txt").write_text("hello\n")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-q", "-m", "init")
    repo = str(repo)
    _git(repo, "checkout", "-q", "-b", "absorb/v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    # no ours_head stashed and no `main` → both step-off attempts fail
    with pytest.raises(driver.AbsorbRefused, match="could not step off"):
        driver.abort(repo)
    assert driver.state(repo) is not None   # state preserved for retry


def test_materialize_refuses_without_state(tmp_path):
    repo = _mkrepo(tmp_path)
    with pytest.raises(driver.AbsorbRefused, match="no absorb in flight"):
        driver.materialize(repo)


def _arm_merged_state(repo, tmp_path):
    """Stash a merged tree (with a conflict-marker file) as in-flight state."""
    (tmp_path / "r" / "a.txt").write_text("<<<<<<< ours\nX\n=======\nY\n>>>>>>> theirs\n")
    _git(repo, "add", "-A")
    merged = subprocess.run(["git", "-C", repo, "write-tree"],
                            capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "reset", "-q", "--hard")   # back to clean HEAD (index + working tree)
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "branch", "absorb/v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastMerged", merged)
    _git(repo, "config", "--local", "absorb.oursHead", head)
    return merged, head


def test_materialize_uses_sidecar_and_never_touches_live_tree(tmp_path):
    repo = _mkrepo(tmp_path)
    _arm_merged_state(repo, tmp_path)
    wt = driver.materialize(repo)
    assert wt == driver.worktree_path(repo)
    # conflict markers land in the sidecar, the live tree stays pristine
    assert "<<<<<<<" in open(os.path.join(wt, "a.txt")).read()
    assert (tmp_path / "r" / "a.txt").read_text() == "hello\n"
    # idempotent: a second --continue reuses the sidecar, never clobbers edits
    open(os.path.join(wt, "a.txt"), "w").write("resolving...\n")
    assert driver.materialize(repo) == wt
    assert open(os.path.join(wt, "a.txt")).read() == "resolving...\n"


def test_materialize_refuses_live_repo_on_absorb_branch(tmp_path):
    repo = _mkrepo(tmp_path)
    _arm_merged_state(repo, tmp_path)
    _git(repo, "checkout", "-q", "absorb/v2026.7.0")
    with pytest.raises(driver.AbsorbRefused, match="live checkout"):
        driver.materialize(repo)


def test_verify_current_refuses_live_repo_on_absorb_branch(tmp_path):
    repo = _mkrepo(tmp_path)
    _arm_merged_state(repo, tmp_path)
    _git(repo, "checkout", "-q", "absorb/v2026.7.0")
    with pytest.raises(driver.AbsorbRefused, match="live checkout"):
        driver.verify_current(repo)


def test_verify_current_snapshots_resolved_tree(tmp_path, monkeypatch):
    repo = _mkrepo(tmp_path)
    _arm_merged_state(repo, tmp_path)
    wt = driver.materialize(repo)
    open(os.path.join(wt, "a.txt"), "w").write("resolved\n")
    _git(repo, "config", "--local", "absorb.lastMerged", "0" * 40)
    monkeypatch.setattr(driver.rebrand_tree, "build_rebranded_tree",
                        lambda ref, attribution=True: "unused-theirs")
    fake_rep = {"ready": True, "conflicts": [], "stray": [],
                "divergence_violations": [], "re_added": 0, "removed": 0,
                "divergence": 0}
    monkeypatch.setattr(driver.parity_report, "report",
                        lambda m, t, h, repo=".": fake_rep)
    monkeypatch.setattr(driver.verify, "run",
                        lambda repo, merged, head="HEAD":
                        {"ok": True, "compile_ok": True, "compile_errors": "",
                         "tests_ok": True, "tests_summary": "stub"})
    res = driver.verify_current(repo)
    assert res["merged"] != "0" * 40                       # snapshot happened
    assert driver.state(repo)["merged"] == res["merged"]   # state updated
    assert driver.state(repo)["verify_ok"] is True


BOOK_FILES = {
    "pyproject.toml": '[project]\nname = "nyriel-brain"\nversion = "0.17.0"\n',
    "UPSTREAM_BASE.md": (
        "| | value |\n|---|---|\n"
        "| Current tree corresponds to | **Hermes v0.17.0** |\n"
        "| Upstream tag | `v2026.6.19` |\n"
        "| Upstream commit | `2bd1977d8` |\n"
        "| Our version (independent line) | `0.17.0` (pyproject.toml — source of truth) |\n"),
    "CHANGELOG.md": "# Changelog\n\n## [Unreleased]\n\n### Added\n- thing\n",
    # Deliberately stale (not old-version-minus-one): bookkeeping must SYNC it
    # to the new version, not bump it independently.
    "nyriel_cli/__init__.py": '__version__ = "0.16.9"\n',
}


def _mkrepo_book(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(str(repo), "init", "-q", "-b", "main")
    _git(str(repo), "config", "user.email", "t@t")
    _git(str(repo), "config", "user.name", "t")
    for path, body in BOOK_FILES.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(body, encoding="utf-8")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-q", "-m", "init")
    _git(str(repo), "tag", "v2026.7.0")
    return str(repo)


def _arm_state(repo, verify_ok="true"):
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    tree = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD^{tree}"],
                          capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "branch", "absorb/v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    _git(repo, "config", "--local", "absorb.lastMerged", tree)
    _git(repo, "config", "--local", "absorb.oursHead", head)
    _git(repo, "config", "--local", "absorb.verifyOk", verify_ok)
    return head, tree


READY = {"ready": True, "conflicts": [], "stray": [], "divergence_violations": [],
         "re_added": 3, "removed": 1, "divergence": 4}


def _stub_parity(monkeypatch, rep=READY):
    monkeypatch.setattr(driver.rebrand_tree, "build_rebranded_tree",
                        lambda ref, attribution=True: "unused-theirs")
    monkeypatch.setattr(driver.parity_report, "report",
                        lambda m, t, h, repo=".": rep)


def test_commit_refuses_without_state(tmp_path):
    repo = _mkrepo_book(tmp_path)
    with pytest.raises(driver.AbsorbRefused, match="no absorb in flight"):
        driver.commit(repo)


def test_commit_refuses_tag_mismatch(tmp_path):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    with pytest.raises(driver.AbsorbRefused, match="tag mismatch"):
        driver.commit(repo, "v2026.8.0")


def test_commit_refuses_moved_head(tmp_path, monkeypatch):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    (tmp_path / "r" / "new.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "moved")
    _stub_parity(monkeypatch)
    with pytest.raises(driver.AbsorbRefused, match="HEAD moved"):
        driver.commit(repo)


def test_commit_refuses_red_verify_unless_skipped(tmp_path, monkeypatch):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo, verify_ok="false")
    _stub_parity(monkeypatch)
    with pytest.raises(driver.AbsorbRefused, match="verify battery"):
        driver.commit(repo)
    oid = driver.commit(repo, skip_verify=True)
    assert len(oid) == 40


def test_commit_bookkeeping_and_state_clear(tmp_path, monkeypatch):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    _stub_parity(monkeypatch)
    oid = driver.commit(repo)
    def show(path):
        return subprocess.run(["git", "-C", repo, "show", f"{oid}:{path}"],
                              capture_output=True, text=True, check=True).stdout
    assert 'version = "0.18.0"' in show("pyproject.toml")
    ub = show("UPSTREAM_BASE.md")
    assert "| Upstream tag | `v2026.7.0` |" in ub
    assert "**Hermes v0.18.0**" in ub and "`0.18.0` (pyproject.toml" in ub
    ch = show("CHANGELOG.md")
    assert "## [0.18.0]" in ch and "`v2026.6.19` → `v2026.7.0`" in ch
    assert "re-added 3, removed 1, divergence 4" in ch
    assert ch.index("[Unreleased]") < ch.index("[0.18.0]")
    msg = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%s",
                          "absorb/v2026.7.0"], capture_output=True, text=True,
                         check=True).stdout.strip()
    assert msg == "absorb: v2026.7.0 (full parity)"
    assert '__version__ = "0.18.0"' in show("nyriel_cli/__init__.py")
    assert driver.state(repo) is None


def test_commit_version_bumps_from_ours_head_not_merged(tmp_path, monkeypatch):
    # Upstream bumped their own pyproject version inside the merged tree. The
    # independent Nyriel line must still bump from OUR previous HEAD
    # (0.17.0 -> 0.18.0), not ride upstream's number + 1.
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    _stub_parity(monkeypatch)
    r = tmp_path / "r"
    (r / "pyproject.toml").write_text(
        '[project]\nname = "nyriel-brain"\nversion = "0.31.0"\n')
    _git(repo, "add", "-A")
    merged = subprocess.run(["git", "-C", repo, "write-tree"],
                            capture_output=True, text=True, check=True).stdout.strip()
    _git(repo, "reset", "-q", "--hard")
    _git(repo, "config", "--local", "absorb.lastMerged", merged)
    oid = driver.commit(repo)
    py = subprocess.run(["git", "-C", repo, "show", f"{oid}:pyproject.toml"],
                        capture_output=True, text=True, check=True).stdout
    assert 'version = "0.18.0"' in py
    init = subprocess.run(["git", "-C", repo, "show", f"{oid}:nyriel_cli/__init__.py"],
                          capture_output=True, text=True, check=True).stdout
    assert '__version__ = "0.18.0"' in init


def test_commit_invalidates_check_cache(tmp_path, monkeypatch):
    # A fresh detect cache from before the absorb must not survive --commit,
    # or --check keeps offering the tag that was just absorbed until the TTL
    # expires.
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    _stub_parity(monkeypatch)
    cache = tmp_path / "r" / ".git" / "absorb_check.json"
    cache.write_text('{"t": 99999999999, "tag": "v2026.7.0"}')
    driver.commit(repo)
    assert not cache.exists()


def test_commit_refuses_parity_not_ready(tmp_path, monkeypatch):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    not_ready = dict(READY, ready=False, conflicts=["a.py"])
    _stub_parity(monkeypatch, rep=not_ready)
    with pytest.raises(driver.AbsorbRefused, match="parity not READY"):
        driver.commit(repo)


def test_commit_refuses_live_repo_on_absorb_branch(tmp_path, monkeypatch):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    _git(repo, "checkout", "-q", "absorb/v2026.7.0")
    _stub_parity(monkeypatch)
    with pytest.raises(driver.AbsorbRefused, match="live checkout"):
        driver.commit(repo)


def test_commit_retires_sidecar_and_updates_branch(tmp_path, monkeypatch):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    wt = driver.materialize(repo)
    assert os.path.isdir(wt)
    _stub_parity(monkeypatch)
    oid = driver.commit(repo)
    assert not os.path.isdir(wt)   # sidecar retired after commit
    branch_oid = subprocess.run(
        ["git", "-C", repo, "rev-parse", "absorb/v2026.7.0"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert branch_oid == oid       # branch ref carries the finalized commit
    # clean tree on main → auto-installed: one command, fully absorbed
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert head == oid


def test_commit_skips_autoinstall_on_dirty_tracked_file(tmp_path, monkeypatch):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    edited = tmp_path / "r" / "CHANGELOG.md"
    edited.write_text(edited.read_text() + "\noperator work in progress\n")
    _stub_parity(monkeypatch)
    oid = driver.commit(repo)
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert head != oid   # not installed — operator work is never touched
    assert "operator work in progress" in edited.read_text()


def test_changelog_insert_between_sections_keeps_blank_lines():
    ch = ("# Changelog\n\n## [Unreleased]\n\n### Added\n- thing\n\n"
          "## [0.17.0] — 2026-06-30\n\n### Absorbed\n- old\n")
    entry = "## [0.18.0] — 2026-07-02\n\n### Absorbed\n- new\n"
    out = driver._changelog_insert(ch, entry)
    assert out.index("[Unreleased]") < out.index("[0.18.0]") < out.index("[0.17.0]")
    assert "- thing\n\n## [0.18.0]" in out
    assert "- new\n\n## [0.17.0]" in out


def test_cli_status_no_absorb(capsys, tmp_path, monkeypatch):
    from nyriel_cli import main as m
    repo = _mkrepo(tmp_path)
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    monkeypatch.chdir(repo)
    rc = m.cmd_absorb(_ns(status=True))
    assert rc == 0
    assert "no absorb in flight" in capsys.readouterr().out


def test_cli_status_in_flight(capsys, tmp_path, monkeypatch):
    from nyriel_cli import main as m
    repo = _mkrepo(tmp_path)
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    _git(repo, "config", "--local", "absorb.lastTag", "v2026.7.0")
    _git(repo, "config", "--local", "absorb.verifyOk", "true")
    _git(repo, "config", "--local", "absorb.verifySummary", "11 target files · 295 passed")
    monkeypatch.chdir(repo)
    rc = m.cmd_absorb(_ns(status=True))
    out = capsys.readouterr().out
    assert rc == 0 and "v2026.7.0" in out and "green" in out


def test_cli_continue_reports_refusal(capsys, tmp_path, monkeypatch):
    from nyriel_cli import main as m
    repo = _mkrepo(tmp_path)
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    monkeypatch.chdir(repo)
    rc = m.cmd_absorb(_ns(cont=True))
    assert rc == 2
    assert "no absorb in flight" in capsys.readouterr().out


def test_commit_refuses_unverified_edits_in_sidecar(tmp_path, monkeypatch):
    repo = _mkrepo_book(tmp_path)
    _arm_state(repo)
    wt = driver.materialize(repo)
    open(os.path.join(wt, "pyproject.toml"), "w").write("tampered\n")
    _stub_parity(monkeypatch)
    with pytest.raises(driver.AbsorbRefused, match="verified snapshot"):
        driver.commit(repo)


def test_cli_commit_passes_skip_verify(tmp_path, monkeypatch, capsys):
    from nyriel_cli import main as m
    from nyriel_cli.absorb import driver as d
    repo = _mkrepo(tmp_path)
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    monkeypatch.chdir(repo)
    seen = {}
    monkeypatch.setattr(d, "commit",
                        lambda repo, tag=None, skip_verify=False:
                        seen.update(skip=skip_verify) or "a" * 40)
    rc = m.cmd_absorb(_ns(commit=True, skip_verify=True))
    assert rc == 0 and seen["skip"] is True


def test_cli_verify_rc_maps_readiness(tmp_path, monkeypatch, capsys):
    from nyriel_cli import main as m
    from nyriel_cli.absorb import driver as d
    repo = _mkrepo(tmp_path)
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    monkeypatch.chdir(repo)
    ok = {"parity": {"ready": True, "conflicts": [], "stray": [],
                     "divergence_violations": []},
          "verify": {"ok": True, "tests_summary": "s"}, "merged": "m"}
    monkeypatch.setattr(d, "verify_current", lambda repo: ok)
    assert m.cmd_absorb(_ns(verify=True)) == 0
    bad = {"parity": dict(ok["parity"], ready=False),
           "verify": dict(ok["verify"], ok=False), "merged": "m"}
    monkeypatch.setattr(d, "verify_current", lambda repo: bad)
    assert m.cmd_absorb(_ns(verify=True)) == 1
