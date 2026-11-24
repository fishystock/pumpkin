import os

import pytest
import requests

from pumpkin.bundles import (
    download_bundle,
    get_bundles,
    get_domain_response,
    get_javascript_files,
)
from pumpkin.settings import settings

PAGE = """
<script src="/static/app.js"></script>
<script src="vendor.js?v=2"></script>
<script src="https://cdn.other.com/lib.js"></script>
<script src="data:text/javascript,alert(1)"></script>
<link href="javascript:void(0)">
<link href="http://[bad/x.js">
<link href="//example.com/style.css">
<link href="/chunk.JS?v=1" rel="preload">
"""


def page(requests_mock, url: str, text: str = PAGE) -> requests.Response:
    requests_mock.get(url, text=text)

    return requests.get(url)


class TestGetDomainResponse:
    def test_returns_response(self, requests_mock, monkeypatch):
        monkeypatch.setattr(settings, "timeout", 7.0)
        requests_mock.get("https://example.com/", text="<html>")

        response = get_domain_response("example.com", "https")

        assert response is not None
        assert response.text == "<html>"
        assert requests_mock.last_request.timeout == 7.0

    @pytest.mark.parametrize(
        "location", ["http://other.com/", "https://example.com/"]
    )
    def test_redirect_to_other_origin(self, requests_mock, location):
        requests_mock.get(
            "http://example.com/",
            status_code=301,
            headers={"Location": location},
        )
        requests_mock.get(location, text="<html>")

        assert get_domain_response("example.com", "http") is None

    def test_redirect_on_same_host(self, requests_mock):
        requests_mock.get(
            "https://example.com/",
            status_code=302,
            headers={"Location": "https://example.com/home"},
        )
        requests_mock.get("https://example.com/home", text="<html>")

        response = get_domain_response("example.com", "https")

        assert response is not None
        assert response.url == "https://example.com/home"

    def test_empty_body(self, requests_mock):
        requests_mock.get("https://example.com/", text="")

        assert get_domain_response("example.com", "https") is None

    def test_connection_error(self, requests_mock):
        requests_mock.get("https://example.com/", exc=requests.ConnectionError)

        assert get_domain_response("example.com", "https") is None


class TestGetJavascriptFiles:
    def test_yields_same_host_links(self, requests_mock):
        response = page(requests_mock, "https://example.com/")

        assert list(get_javascript_files("example.com", response)) == [
            "https://example.com/static/app.js",
            "https://example.com/vendor.js?v=2",
            "https://example.com/chunk.JS?v=1",
        ]

    def test_resolves_relative_to_final_url(self, requests_mock):
        response = page(
            requests_mock,
            "https://example.com/app/",
            '<script src="main.js"></script>',
        )

        assert list(get_javascript_files("example.com", response)) == [
            "https://example.com/app/main.js"
        ]

    def test_script_without_js_extension(self, requests_mock):
        response = page(
            requests_mock,
            "https://example.com/",
            '<script src="/bundle"></script><link href="/page">',
        )

        assert list(get_javascript_files("example.com", response)) == [
            "https://example.com/bundle"
        ]

    def test_no_links(self, requests_mock):
        response = page(requests_mock, "https://example.com/", "<p>hi</p>")

        assert list(get_javascript_files("example.com", response)) == []


class TestDownloadBundle:
    def test_writes_javascript_bytes(self, requests_mock, tmp_path):
        content = "const s = 'é✓';".encode()
        requests_mock.get(
            "https://example.com/app.js",
            content=content,
            headers={"Content-Type": "Application/JavaScript"},
        )

        download_bundle("https://example.com/app.js", str(tmp_path))

        files = os.listdir(tmp_path)
        assert len(files) == 1
        assert (tmp_path / files[0]).read_bytes() == content

    def test_skips_non_javascript(self, requests_mock, tmp_path):
        requests_mock.get(
            "https://example.com/style.css",
            text="body {}",
            headers={"Content-Type": "text/css"},
        )

        download_bundle("https://example.com/style.css", str(tmp_path))

        assert os.listdir(tmp_path) == []

    def test_connection_error(self, requests_mock, tmp_path):
        requests_mock.get(
            "https://example.com/app.js", exc=requests.ConnectionError
        )

        download_bundle("https://example.com/app.js", str(tmp_path))

        assert os.listdir(tmp_path) == []


class TestGetBundles:
    def test_prefers_https_and_dedupes(self, requests_mock):
        requests_mock.get("http://example.com/", text=PAGE)
        requests_mock.get("https://example.com/", text=PAGE)

        assert list(get_bundles("example.com")) == [
            "https://example.com/static/app.js",
            "https://example.com/vendor.js?v=2",
            "https://example.com/chunk.JS?v=1",
        ]

    def test_falls_back_to_http(self, requests_mock):
        requests_mock.get("https://example.com/", exc=requests.ConnectionError)
        requests_mock.get(
            "http://example.com/", text='<script src="/a.js"></script>'
        )

        assert list(get_bundles("example.com")) == ["http://example.com/a.js"]

    def test_skips_empty_scheme(self, requests_mock):
        requests_mock.get("https://example.com/", text="")
        requests_mock.get(
            "http://example.com/", text='<script src="/a.js"></script>'
        )

        assert list(get_bundles("example.com")) == ["http://example.com/a.js"]

    def test_unreachable_domain(self, requests_mock):
        requests_mock.get("https://example.com/", exc=requests.ConnectionError)
        requests_mock.get("http://example.com/", exc=requests.ConnectionError)

        assert list(get_bundles("example.com")) == []
        assert requests_mock.call_count == 2
