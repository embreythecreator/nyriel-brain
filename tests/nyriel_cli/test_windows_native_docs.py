from pathlib import Path


def test_windows_native_install_path_docs_match_installer() -> None:
    doc = Path("website/docs/user-guide/windows-native.md").read_text()
    install = Path("scripts/install.ps1").read_text()

    # The launchers live in the managed binary dir OUTSIDE the git checkout
    # (NYRIEL_HOME\bin, next to the managed uv) — NOT the whole venv\Scripts
    # (which would shadow the user's python, #83797) and NOT a dir inside
    # the checkout (which `nyriel update`'s autostash swept off disk).
    assert "%LOCALAPPDATA%\\nyriel\\bin" in doc
    assert (
        "Get-Command nyriel        # should print "
        "C:\\Users\\<you>\\AppData\\Local\\nyriel\\bin\\nyriel.exe"
    ) in doc
    # Installer exposes $NyrielHome\bin, and must copy the launchers into it.
    assert '$nyrielBin = "$NyrielHome\\bin"' in install
    assert "nyriel.exe" in install and "nyriel-acp.exe" in install
    # Guard against regressions to either legacy layout.
    assert '$nyrielBin = "$InstallDir\\venv\\Scripts"' not in install
    assert '$nyrielBin = "$InstallDir\\bin"' not in install
