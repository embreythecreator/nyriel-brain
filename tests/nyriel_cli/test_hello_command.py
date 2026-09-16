"""Tests for the top-level ``nyriel hello`` command."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from nyriel_cli.main import cmd_hello
from nyriel_cli.subcommands.hello import build_hello_parser


def test_cmd_hello_prints_brain_greeting(capsys):
    cmd_hello(argparse.Namespace())

    captured = capsys.readouterr()
    assert captured.out == "Hello from Nyriel Brain.\n"
    assert captured.err == ""


def test_hello_parser_registers_command_and_handler():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    build_hello_parser(subparsers, cmd_hello=cmd_hello)
    args = parser.parse_args(["hello"])

    assert args.command == "hello"
    assert args.func is cmd_hello


def test_hello_command_runs_through_cli_entrypoint(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["NYRIEL_HOME"] = str(tmp_path / "nyriel-home")

    result = subprocess.run(
        [sys.executable, "-m", "nyriel_cli.main", "hello"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "Hello from Nyriel Brain.\n"
    assert result.stderr == ""
