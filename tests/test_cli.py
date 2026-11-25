import base64

import pytest
import requests
from tqdm import tqdm

from pumpkin import cli
from pumpkin.bundles import SeenBundles
from pumpkin.settings import settings
from pumpkin.titus import Secret, TitusError


class TestParseArguments:
    @pytest.mark.parametrize(
        ("target", "domain"),
        [
            ("sub.example.com", "sub.example.com"),
            ("Example.COM", "example.com"),
            ("example.com:8080/path", "example.com"),
            ("https://example.com/a?b=c", "example.com"),
        ],
    )
    def test_accepts_domain_or_url(self, target, domain):
        assert cli.parse_arguments(["-u", target]).domain == domain

    @pytest.mark.parametrize("target", ["", "https://", "http://[bad"])
    def test_rejects_missing_hostname(self, target, capsys):
        with pytest.raises(SystemExit):
            cli.parse_arguments(["-u", target])

        assert "argument -u/--url:" in capsys.readouterr().err

    def test_requires_url(self, capsys):
        with pytest.raises(SystemExit):
            cli.parse_arguments([])

        assert "-u/--url" in capsys.readouterr().err

    def test_subdomains_enabled_by_default(self):
        assert cli.parse_arguments(["-u", "example.com"]).subdomains is True

    def test_no_subdomains_disables_lookup(self):
        arguments = cli.parse_arguments(
            ["-u", "example.com", "--no-subdomains"]
        )

        assert arguments.subdomains is False


@pytest.fixture
def titus_found(monkeypatch):
    monkeypatch.setattr(cli, "get_installation_path", lambda: "/bin/titus")


@pytest.fixture
def scanned(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "scan_domain",
        lambda domain, status, download_threads, seen: calls.append(domain),
    )

    return calls


class TestScanDomain:
    def test_downloads_bundles_then_scans_once(self, monkeypatch):
        downloaded = []
        scanned_directories = []
        monkeypatch.setattr(
            cli,
            "get_bundles",
            lambda session, domain: iter(["a.js", "b.js", "c.js"]),
        )
        monkeypatch.setattr(
            cli,
            "download_bundle",
            lambda session, url, directory, seen: downloaded.append(url),
        )
        monkeypatch.setattr(
            cli,
            "get_secrets",
            lambda path: scanned_directories.append(sorted(downloaded)) or [],
        )

        cli.scan_domain("example.com", tqdm(disable=True), 2, SeenBundles())

        assert scanned_directories == [["a.js", "b.js", "c.js"]]

    def test_download_error_propagates(self, monkeypatch):
        def fail(session, url, directory, seen):
            raise OSError("disk full")

        monkeypatch.setattr(
            cli, "get_bundles", lambda session, domain: iter(["a.js"])
        )
        monkeypatch.setattr(cli, "download_bundle", fail)

        with pytest.raises(OSError, match="disk full"):
            cli.scan_domain(
                "example.com", tqdm(disable=True), 2, SeenBundles()
            )


class TestFormatSecret:
    def test_includes_domain_rule_and_matches(self):
        secret = Secret(rule="AWS Key", matches=["YWJj", "ZGVm"])

        line = cli.format_secret("example.com", secret)

        assert "example.com" in line
        assert "AWS Key" in line
        assert line.endswith(" abc, def")

    def test_truncates_matches(self, monkeypatch):
        monkeypatch.setattr(settings, "output_limit", 50)
        secret = Secret(
            rule="rule", matches=[base64.b64encode(b"x" * 400).decode()]
        )

        line = cli.format_secret("example.com", secret)

        assert line.endswith(" " + "x" * settings.output_limit)


class TestGetDomains:
    def test_without_subdomains_makes_no_lookup(self, requests_mock):
        assert cli.get_domains("example.com", False) == ["example.com"]
        assert requests_mock.call_count == 0

    def test_lookup_failure_exits(self, requests_mock):
        requests_mock.post(str(settings.subdomains_api_url), status_code=500)

        with pytest.raises(SystemExit, match="lookup failed"):
            cli.get_domains("example.com", True)


class TestMain:
    def test_missing_binary_exits_before_lookup(
        self, monkeypatch, requests_mock
    ):
        monkeypatch.setattr(settings, "titus_binary", "no-such-titus-binary")

        with pytest.raises(SystemExit, match="not found"):
            cli.main(["-u", "example.com"])

        assert requests_mock.call_count == 0

    def test_timeout_overrides_setting(
        self, monkeypatch, titus_found, scanned
    ):
        monkeypatch.setattr(settings, "timeout", 2.5)

        cli.main(["-u", "example.com", "-t", "9", "--no-subdomains"])

        assert settings.timeout == 9

    def test_download_threads_passed_to_scan(self, monkeypatch, titus_found):
        received = []
        monkeypatch.setattr(
            cli,
            "scan_domain",
            lambda domain, status, download_threads, seen: received.append(
                download_threads
            ),
        )

        cli.main(["-u", "example.com", "-d", "8", "--no-subdomains"])

        assert received == [8]

    def test_scans_given_domain(self, titus_found, scanned):
        cli.main(["--url", "https://Example.com/path", "--no-subdomains"])

        assert scanned == ["example.com"]

    def test_scans_every_subdomain(self, titus_found, scanned, requests_mock):
        requests_mock.post(
            str(settings.subdomains_api_url),
            json={"domains": [{"domain": "a.example.com"}]},
        )

        cli.main(["-u", "example.com", "-c", "2"])

        assert scanned == ["a.example.com"]

    def test_titus_error_exits(self, monkeypatch, titus_found):
        def fail(domain, status, download_threads, seen):
            raise TitusError("titus exploded")

        monkeypatch.setattr(cli, "scan_domain", fail)

        with pytest.raises(SystemExit, match="titus exploded"):
            cli.main(["-u", "example.com", "--no-subdomains"])

    def test_prints_secrets(
        self, monkeypatch, titus_found, requests_mock, capsys
    ):
        requests_mock.get("https://example.com/", exc=requests.ConnectionError)
        requests_mock.get("http://example.com/", exc=requests.ConnectionError)
        monkeypatch.setattr(
            cli,
            "get_secrets",
            lambda path: [Secret(rule="AWS Key", matches=["YWJj"])],
        )

        cli.main(["-u", "example.com", "--no-subdomains"])

        output = capsys.readouterr().out
        assert "example.com" in output
        assert "AWS Key" in output
        assert "abc" in output
