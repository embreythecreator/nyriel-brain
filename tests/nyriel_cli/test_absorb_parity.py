from nyriel_cli.absorb import rename_map, parity_report


def test_swap_text_hermes_and_attribution():
    out = rename_map.swap_text("Hermes Agent, created by Hermes", "nyriel_cli/x.py")
    assert "Nyriel Brain" in out
    assert "created by Embrey The Creator" in out
    assert "Hermes" not in out


def test_legal_file_keeps_nous_attribution():
    out = rename_map.swap_text("Copyright (c) 2025 Nous Research", "LICENSE")
    assert out == "Copyright (c) 2025 Nous Research"


def test_nous_service_domain_survives_rebrand():
    """The real Nous endpoints must never be aimed at a domain we don't serve.

    Regression: NOUS_RULES rewrote nousresearch.com -> embreythecreator.com,
    which is NXDOMAIN, killing OAuth + inference + all four Tool Gateway hosts.
    """
    src = (
        'PORTAL = "https://portal.nousresearch.com"\n'
        'INFERENCE = "https://inference-api.nousresearch.com/v1"\n'
        '_DEFAULT_TOOL_GATEWAY_DOMAIN = "nousresearch.com"\n'
    )
    out = rename_map.swap_text(src, "nyriel_cli/x.py")
    assert out == src, out
    assert "embreythecreator.com" not in out


def test_nous_org_handle_still_rebrands():
    """The carve-out is domain-only — the GitHub org handle must still swap."""
    out = rename_map.swap_text(
        "github.com/NousResearch/hermes-agent", "nyriel_cli/x.py")
    assert out == "github.com/embreythecreator/nyriel-brain"


def test_domain_sentinel_never_leaks():
    out = rename_map.swap_text("see nousresearch.com", "nyriel_cli/x.py")
    assert "\x01" not in out


def test_report_flags_conflict_markers(monkeypatch):
    monkeypatch.setattr(parity_report, "names", lambda t: set())
    monkeypatch.setattr(parity_report, "diff_names", lambda a, b: [])
    monkeypatch.setattr(parity_report, "grep_conflict_markers", lambda t: ["a.py"])
    monkeypatch.setattr(parity_report, "grep_stray", lambda t: [])
    r = parity_report.report("m", "t", "HEAD")
    assert r["ready"] is False and r["conflicts"] == ["a.py"]


def test_report_flags_divergence_violation(monkeypatch):
    from nyriel_cli.absorb import divergence
    monkeypatch.setattr(parity_report, "names", lambda t: set())
    monkeypatch.setattr(parity_report, "diff_names", lambda a, b: [])
    monkeypatch.setattr(parity_report, "grep_conflict_markers", lambda t: [])
    monkeypatch.setattr(parity_report, "grep_stray", lambda t: [])
    monkeypatch.setattr(divergence, "check",
                        lambda repo, tree: ["x.py: no longer contains '✶'"])
    r = parity_report.report("m", "t", "HEAD")
    assert r["ready"] is False
    assert r["divergence_violations"] == ["x.py: no longer contains '✶'"]


def test_report_ready_when_no_violations(monkeypatch):
    from nyriel_cli.absorb import divergence
    monkeypatch.setattr(parity_report, "names", lambda t: set())
    monkeypatch.setattr(parity_report, "diff_names", lambda a, b: [])
    monkeypatch.setattr(parity_report, "grep_conflict_markers", lambda t: [])
    monkeypatch.setattr(parity_report, "grep_stray", lambda t: [])
    monkeypatch.setattr(divergence, "check", lambda repo, tree: [])
    r = parity_report.report("m", "t", "HEAD")
    assert r["ready"] is True and r["divergence_violations"] == []
