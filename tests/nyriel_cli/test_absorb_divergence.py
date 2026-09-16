import subprocess
from nyriel_cli.absorb import divergence


def _git(repo, *a, input_bytes=None):
    return subprocess.run(["git", "-C", repo, *a], input=input_bytes,
                          capture_output=True, check=True)


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


GOOD = {
    "gateway/platforms/whatsapp_common.py": 'DEFAULT_REPLY_PREFIX = "✶ *Nyriel Brain*"\n',
    "gateway/overlay/brain_settings.py": "# overlay\n",
    "gateway/platforms/api_server.py": "def _nyriel_version():\n    return 'dev'\n",
    "agent/prompt_builder.py": "# created by Embrey The Creator / The Voice\n",
    "nyriel_cli/default_soul.py": "# created by Embrey The Creator / The Voice\n",
    "agent/moa_loop.py": "def _council_route_references():\n    pass\n",
    "nyriel_cli/config.py": '"model_council": {}\n',
    "nyriel_cli/moa_config.py": "import model_council\n",
    ".env.example": "NYRIEL_MODEL_COUNCIL_ENABLED=true\n",
}


def test_all_invariants_hold(tmp_path):
    repo = _mkrepo(tmp_path, GOOD)
    assert divergence.check(repo, "HEAD") == []


def test_missing_file_is_violation(tmp_path):
    files = dict(GOOD)
    del files["gateway/overlay/brain_settings.py"]
    repo = _mkrepo(tmp_path, files)
    v = divergence.check(repo, "HEAD")
    assert len(v) == 1 and "brain_settings.py" in v[0] and "missing" in v[0]


def test_reverted_glyph_is_violation(tmp_path):
    files = dict(GOOD)
    files["gateway/platforms/whatsapp_common.py"] = 'DEFAULT_REPLY_PREFIX = "⚕ *Hermes*"\n'
    repo = _mkrepo(tmp_path, files)
    v = divergence.check(repo, "HEAD")
    assert len(v) == 1 and "whatsapp_common.py" in v[0] and "✶" in v[0]


def test_checks_a_tree_oid_not_just_head(tmp_path):
    repo = _mkrepo(tmp_path, GOOD)
    tree = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD^{tree}"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert divergence.check(repo, tree) == []
