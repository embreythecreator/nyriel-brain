from nyriel_cli.absorb import detect


def test_newer_tags_orders_date_tags():
    tags = ["v2026.6.5", "v2026.6.19", "v2026.5.28", "v2026.6.19"]
    out = detect.newer_tags("v2026.6.5", tags)
    assert out == ["v2026.6.19"]


def test_newer_tags_handles_dotted_patch():
    out = detect.newer_tags("v2026.5.29", ["v2026.5.29.2", "v2026.5.29"])
    assert out == ["v2026.5.29.2"]


def test_no_newer_returns_empty():
    assert detect.newer_tags("v2026.6.19", ["v2026.6.5", "v2026.6.19"]) == []


def test_absorb_offer_line(monkeypatch):
    from nyriel_cli import banner
    from nyriel_cli.absorb import driver, detect
    monkeypatch.setattr(driver, "install_ok", lambda repo: (True, ""))
    monkeypatch.setattr(detect, "latest_absorbable", lambda repo: "v2026.7.0")
    line = banner.absorb_offer_line("/x")
    assert line and "v2026.7.0" in line and "nyriel absorb" in line


def test_absorb_offer_silent_on_non_git(monkeypatch):
    from nyriel_cli import banner
    from nyriel_cli.absorb import driver
    monkeypatch.setattr(driver, "install_ok", lambda repo: (False, "no upstream"))
    assert banner.absorb_offer_line("/x") is None
