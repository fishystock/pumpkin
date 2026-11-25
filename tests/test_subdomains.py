import pytest
import requests

from pumpkin.settings import settings
from pumpkin.subdomains import (
    SubdomainLookupError,
    fetch_page,
    find_subdomains,
    get_bundle_subdomains,
    get_subdomains,
)

API_URL = settings.subdomains_api_url


def page(domains: list[str], next_state: str = "") -> dict:
    return {
        "domains": [{"domain": domain} for domain in domains],
        "next_page_state": next_state,
    }


@pytest.fixture
def session():
    with requests.Session() as session:
        yield session


class TestFetchPage:
    def test_parses_page(self, requests_mock, session):
        requests_mock.post(API_URL, json=page(["a.example.com"], "next"))

        result = fetch_page(session, "example.com", "")

        assert [i.domain for i in result.domains] == ["a.example.com"]
        assert result.next_page_state == "next"

    def test_missing_next_page_state_is_none(self, requests_mock, session):
        requests_mock.post(API_URL, json={"domains": []})

        assert fetch_page(session, "example.com", "").next_page_state is None

    def test_sends_expected_payload(self, requests_mock, session):
        requests_mock.post(API_URL, json=page([]))

        fetch_page(session, "example.com", "cursor")

        assert requests_mock.last_request.json() == {
            "domain": "example.com",
            "page_state": "cursor",
            "limit": settings.subdomains_page_size,
        }

    @pytest.mark.parametrize(
        "response",
        [
            {"status_code": 429},
            {"status_code": 500},
            {"exc": requests.ConnectTimeout},
            {"text": "not json"},
            {"json": {"unexpected": True}},
        ],
    )
    def test_failure_raises(self, requests_mock, session, response):
        requests_mock.post(API_URL, **response)

        with pytest.raises(SubdomainLookupError, match=r"example\.com"):
            fetch_page(session, "example.com", "")


class TestGetSubdomains:
    def test_single_page(self, requests_mock):
        requests_mock.post(
            API_URL, json=page(["a.example.com", "b.example.com"])
        )

        assert list(get_subdomains("example.com")) == [
            "a.example.com",
            "b.example.com",
        ]
        assert requests_mock.call_count == 1

    def test_follows_pagination_in_order(self, requests_mock):
        requests_mock.post(
            API_URL,
            [
                {"json": page(["a.example.com"], "one")},
                {"json": page(["b.example.com"], "two")},
                {"json": page(["c.example.com"])},
            ],
        )

        assert list(get_subdomains("example.com")) == [
            "a.example.com",
            "b.example.com",
            "c.example.com",
        ]
        states = [
            i.json()["page_state"] for i in requests_mock.request_history
        ]
        assert states == ["", "one", "two"]

    def test_deduplicates_across_pages(self, requests_mock):
        requests_mock.post(
            API_URL,
            [
                {"json": page(["a.example.com", "a.example.com"], "one")},
                {"json": page(["a.example.com", "b.example.com"])},
            ],
        )

        assert list(get_subdomains("example.com")) == [
            "a.example.com",
            "b.example.com",
        ]

    def test_stops_on_repeated_state(self, requests_mock):
        requests_mock.post(
            API_URL,
            [
                {"json": page(["a.example.com"], "one")},
                {"json": page(["b.example.com"], "two")},
                {"json": page(["c.example.com"], "one")},
            ],
        )

        assert list(get_subdomains("example.com")) == [
            "a.example.com",
            "b.example.com",
            "c.example.com",
        ]
        assert requests_mock.call_count == 3

    def test_empty_results(self, requests_mock):
        requests_mock.post(API_URL, json=page([]))

        assert list(get_subdomains("example.com")) == []

    def test_failure_aborts_without_further_requests(self, requests_mock):
        requests_mock.post(
            API_URL,
            [
                {"json": page(["a.example.com"], "one")},
                {"status_code": 500},
                {"json": page(["never.example.com"])},
            ],
        )
        results = get_subdomains("example.com")

        assert next(results) == "a.example.com"

        with pytest.raises(SubdomainLookupError):
            next(results)

        assert requests_mock.call_count == 2


class TestFindSubdomains:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (b'fetch("https://api.example.com/v1")', {"api.example.com"}),
            (b"const h = 'a.b.example.com';", {"a.b.example.com"}),
            (b"see API.Example.COM.", {"api.example.com"}),
            (
                b"x.example.com y.example.com x.example.com",
                {
                    "x.example.com",
                    "y.example.com",
                },
            ),
            (b"https://example.com/", set()),
            (b"notexample.com", set()),
            (b"api.example.com.evil.com", set()),
            (b"cdn.other.com", set()),
            (b"*.example.com", set()),
            (b"api.example.community", set()),
        ],
    )
    def test_matches(self, text, expected):
        assert find_subdomains(text, "example.com") == expected


class TestGetBundleSubdomains:
    def test_unions_files(self, tmp_path):
        (tmp_path / "a").write_bytes(b"\xff\xfe'a.example.com'\xff")
        (tmp_path / "b").write_bytes(b"//b.example.com/x")
        (tmp_path / "c").write_bytes(b"")

        assert get_bundle_subdomains(str(tmp_path), "example.com") == {
            "a.example.com",
            "b.example.com",
        }

    def test_empty_directory(self, tmp_path):
        assert get_bundle_subdomains(str(tmp_path), "example.com") == set()
