import base64
import json
import subprocess
from unittest.mock import MagicMock

import pytest

from pumpkin.settings import settings
from pumpkin.titus import Secret, TitusError, get_secrets, parse_secrets

BINARY = "/usr/local/bin/titus"


def finding(rule: str, *matches: str) -> dict:
    return {
        "RuleName": rule,
        "Groups": [base64.b64encode(i.encode()).decode() for i in matches],
    }


def output(*findings: dict) -> bytes:
    return json.dumps(list(findings)).encode()


def completed(
    stdout: bytes = b"[]", returncode: int = 0, stderr: bytes = b""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        [], returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture
def which(monkeypatch):
    mock = MagicMock(return_value=BINARY)
    monkeypatch.setattr("shutil.which", mock)

    return mock


@pytest.fixture
def run(monkeypatch, which):
    mock = MagicMock(return_value=completed())
    monkeypatch.setattr("subprocess.run", mock)

    return mock


class TestParseSecrets:
    def test_decodes_findings(self):
        stdout = output(finding("Slack", "xoxb-1"), finding("AWS", "a", "b"))
        secrets = parse_secrets(stdout)

        assert [(i.rule, i.matches) for i in secrets] == [
            ("Slack", ["xoxb-1"]),
            ("AWS", ["a", "b"]),
        ]

    def test_empty(self):
        assert parse_secrets(b"[]") == []

    def test_invalid_utf8_is_replaced(self):
        raw = {
            "RuleName": "R",
            "Groups": [base64.b64encode(b"\xffkey").decode()],
        }

        assert parse_secrets(output(raw))[0].matches == ["�key"]

    def test_builds_by_field_name(self):
        assert Secret(rule="R", matches=[]).rule == "R"

    @pytest.mark.parametrize(
        "stdout", [b"", b"not json", b"null", b'{"a": 1}', b'[{"Groups": []}]']
    )
    def test_bad_output_raises(self, stdout):
        with pytest.raises(TitusError):
            parse_secrets(stdout)


class TestGetSecrets:
    def test_returns_secrets(self, run):
        run.return_value = completed(output(finding("Rule", "x")))

        assert [i.matches for i in get_secrets("/scan")] == [["x"]]

    def test_sends_expected_command(self, run, which, monkeypatch):
        monkeypatch.setattr(settings, "titus_binary", "titus-custom")
        monkeypatch.setattr(settings, "titus_timeout", 42.0)

        get_secrets("/scan")

        which.assert_called_once_with("titus-custom")
        run.assert_called_once_with(
            [
                BINARY,
                "scan",
                "/scan",
                "-q",
                "--format",
                "json",
                "--output",
                ":memory:",
                "--include-noisy",
            ],
            capture_output=True,
            check=False,
            timeout=42.0,
        )

    def test_omits_noisy_flag_when_disabled(self, run, monkeypatch):
        monkeypatch.setattr(settings, "titus_include_noisy", False)

        get_secrets("/scan")

        assert "--include-noisy" not in run.call_args.args[0]

    def test_missing_binary_raises_without_running(self, run, which):
        which.return_value = None

        with pytest.raises(TitusError, match="not found"):
            get_secrets("/scan")

        run.assert_not_called()

    def test_nonzero_exit_raises_with_stderr(self, run):
        run.return_value = completed(b"", 1, b"Error: target does not exist")

        with pytest.raises(TitusError, match="target does not exist"):
            get_secrets("/scan")

    def test_timeout_raises(self, run):
        run.side_effect = subprocess.TimeoutExpired("titus", 1)

        with pytest.raises(TitusError, match="timed out"):
            get_secrets("/scan")
