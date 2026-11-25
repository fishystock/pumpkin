import os

import pytest
import requests

from pumpkin.bundles import (
    SeenBundles,
    create_session,
    download_bundle,
    fetch_bundle,
    find_bundle_references,
    get_bundle_links,
    get_bundles,
    get_domain_response,
    get_javascript_files,
    resolve_link,
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

        response = get_domain_response(
            create_session(), "example.com", "https"
        )

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

        assert (
            get_domain_response(create_session(), "example.com", "http")
            is None
        )

    def test_redirect_on_same_host(self, requests_mock):
        requests_mock.get(
            "https://example.com/",
            status_code=302,
            headers={"Location": "https://example.com/home"},
        )
        requests_mock.get("https://example.com/home", text="<html>")

        response = get_domain_response(
            create_session(), "example.com", "https"
        )

        assert response is not None
        assert response.url == "https://example.com/home"

    def test_empty_body(self, requests_mock):
        requests_mock.get("https://example.com/", text="")

        assert (
            get_domain_response(create_session(), "example.com", "https")
            is None
        )

    def test_connection_error(self, requests_mock):
        requests_mock.get("https://example.com/", exc=requests.ConnectionError)

        assert (
            get_domain_response(create_session(), "example.com", "https")
            is None
        )


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

        path = download_bundle(
            create_session(),
            "https://example.com/app.js",
            str(tmp_path),
            SeenBundles(),
        )

        files = os.listdir(tmp_path)
        assert len(files) == 1
        assert path == str(tmp_path / files[0])
        assert (tmp_path / files[0]).read_bytes() == content

    def test_writes_across_multiple_chunks(
        self, requests_mock, tmp_path, monkeypatch
    ):
        content = b"x" * 100
        monkeypatch.setattr(settings, "download_chunk_size", 7)
        requests_mock.get(
            "https://example.com/app.js",
            content=content,
            headers={"Content-Type": "application/javascript"},
        )

        download_bundle(
            create_session(),
            "https://example.com/app.js",
            str(tmp_path),
            SeenBundles(),
        )

        files = os.listdir(tmp_path)
        assert len(files) == 1
        assert (tmp_path / files[0]).read_bytes() == content

    def test_skips_non_javascript(self, requests_mock, tmp_path):
        requests_mock.get(
            "https://example.com/style.css",
            text="body {}",
            headers={"Content-Type": "text/css"},
        )

        download_bundle(
            create_session(),
            "https://example.com/style.css",
            str(tmp_path),
            SeenBundles(),
        )

        assert os.listdir(tmp_path) == []

    def test_connection_error(self, requests_mock, tmp_path):
        requests_mock.get(
            "https://example.com/app.js", exc=requests.ConnectionError
        )

        download_bundle(
            create_session(),
            "https://example.com/app.js",
            str(tmp_path),
            SeenBundles(),
        )

        assert os.listdir(tmp_path) == []

    def test_skips_duplicate_content(self, requests_mock, tmp_path):
        for host in ("a.example.com", "b.example.com"):
            requests_mock.get(
                f"https://{host}/app.js",
                content=b"same",
                headers={"Content-Type": "application/javascript"},
            )

        seen = SeenBundles()
        session = create_session()

        paths = [
            download_bundle(
                session, f"https://{host}/app.js", str(tmp_path), seen
            )
            for host in ("a.example.com", "b.example.com")
        ]

        assert len(os.listdir(tmp_path)) == 1
        assert paths[0] is not None
        assert paths[1] is None

    def test_keeps_different_content(self, requests_mock, tmp_path):
        for name in ("a", "b"):
            requests_mock.get(
                f"https://example.com/{name}.js",
                content=name.encode(),
                headers={"Content-Type": "application/javascript"},
            )

        seen = SeenBundles()
        session = create_session()

        for name in ("a", "b"):
            download_bundle(
                session, f"https://example.com/{name}.js", str(tmp_path), seen
            )

        assert len(os.listdir(tmp_path)) == 2


class TestSeenBundles:
    def test_add_reports_whether_digest_is_new(self):
        seen = SeenBundles()

        assert seen.add("abc") is True
        assert seen.add("abc") is False
        assert seen.add("def") is True


class TestGetBundles:
    def test_prefers_https_and_dedupes(self, requests_mock):
        requests_mock.get("http://example.com/", text=PAGE)
        requests_mock.get("https://example.com/", text=PAGE)

        assert list(get_bundles(create_session(), "example.com")) == [
            "https://example.com/static/app.js",
            "https://example.com/vendor.js?v=2",
            "https://example.com/chunk.JS?v=1",
        ]

    def test_falls_back_to_http(self, requests_mock):
        requests_mock.get("https://example.com/", exc=requests.ConnectionError)
        requests_mock.get(
            "http://example.com/", text='<script src="/a.js"></script>'
        )

        assert list(get_bundles(create_session(), "example.com")) == [
            "http://example.com/a.js"
        ]

    def test_skips_empty_scheme(self, requests_mock):
        requests_mock.get("https://example.com/", text="")
        requests_mock.get(
            "http://example.com/", text='<script src="/a.js"></script>'
        )

        assert list(get_bundles(create_session(), "example.com")) == [
            "http://example.com/a.js"
        ]

    def test_unreachable_domain(self, requests_mock):
        requests_mock.get("https://example.com/", exc=requests.ConnectionError)
        requests_mock.get("http://example.com/", exc=requests.ConnectionError)

        assert list(get_bundles(create_session(), "example.com")) == []
        assert requests_mock.call_count == 2


class TestResolveLink:
    @pytest.mark.parametrize(
        ("link", "expected"),
        [
            ("b.js", "https://example.com/static/b.js"),
            ("/b.js", "https://example.com/b.js"),
            ("//example.com/b.js", "https://example.com/b.js"),
            ("https://other.com/b.js", None),
            ("data:text/javascript,1", None),
            ("http://[bad/b.js", None),
        ],
    )
    def test_resolves_same_host(self, link, expected):
        url = resolve_link(
            "https://example.com/static/a.js", link, "example.com"
        )

        assert (url.geturl() if url else None) == expected


class TestFindBundleReferences:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (b"import { a } from 'secret_file.js';", {"secret_file.js"}),
            (b'export * from "./lib/util.mjs"', {"./lib/util.mjs"}),
            (b"import(`/chunks/1.JS?v=2`)", {"/chunks/1.JS?v=2"}),
            (b'require("a.js");require("a.js")', {"a.js"}),
            (
                b'const u = "https://cdn.x.com/lib.js"',
                {"https://cdn.x.com/lib.js"},
            ),
            (b'"app.json" "a.js.map" "react" "a b.js"', set()),
            (b"\"a.js'", set()),
        ],
    )
    def test_matches(self, data, expected):
        assert find_bundle_references(data) == expected


class TestGetBundleLinks:
    def test_resolves_against_bundle_and_root(self, tmp_path):
        path = tmp_path / "bundle"
        path.write_bytes(
            b"import 'static/js/chunk.js';"
            b"import 'https://other.com/x.js';"
            b"import '/abs.js';\xff"
        )

        assert get_bundle_links(
            str(path), "https://example.com/static/js/main.js", "example.com"
        ) == {
            "https://example.com/static/js/static/js/chunk.js",
            "https://example.com/static/js/chunk.js",
            "https://example.com/abs.js",
        }

    def test_empty_file(self, tmp_path):
        path = tmp_path / "bundle"
        path.write_bytes(b"")

        assert (
            get_bundle_links(str(path), "https://example.com/a.js", "x")
            == set()
        )


class TestFetchBundle:
    def test_returns_links_of_new_bundle(self, requests_mock, tmp_path):
        requests_mock.get(
            "https://example.com/a.js",
            content=b"import './b.js';",
            headers={"Content-Type": "application/javascript"},
        )
        seen = SeenBundles()
        session = create_session()

        links = fetch_bundle(
            session,
            "https://example.com/a.js",
            "example.com",
            str(tmp_path),
            seen,
        )

        assert links == {"https://example.com/b.js"}

    def test_skipped_bundle_has_no_links(self, requests_mock, tmp_path):
        requests_mock.get(
            "https://example.com/a.js", exc=requests.ConnectionError
        )

        assert (
            fetch_bundle(
                create_session(),
                "https://example.com/a.js",
                "example.com",
                str(tmp_path),
                SeenBundles(),
            )
            == set()
        )
