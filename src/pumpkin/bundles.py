import tempfile
import warnings
from collections.abc import Iterator
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from pumpkin.settings import settings

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def get_domain_response(domain: str, scheme: str) -> requests.Response | None:
    try:
        response = requests.get(
            f"{scheme}://{domain}/",
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout,
        )
    except requests.RequestException:
        return None

    final_url = urlsplit(response.url)

    if final_url.hostname != domain or final_url.scheme != scheme:
        return None

    if not response.text:
        return None

    return response


def get_javascript_files(
    domain: str, response: requests.Response
) -> Iterator[str]:
    soup = BeautifulSoup(response.text, "html.parser")
    selectors = (("script", "src"), ("link", "href"))

    for tag_name, attribute in selectors:
        for tag in soup.find_all(name=tag_name, attrs={attribute: True}):
            link = str(tag.attrs.get(attribute))

            try:
                url = urlsplit(urljoin(response.url, link))
            except ValueError:
                continue

            if url.scheme not in ("http", "https"):
                continue

            if url.hostname != domain:
                continue

            if tag_name == "link" and not url.path.lower().endswith(".js"):
                continue

            yield url.geturl()


def download_bundle(url: str, directory: str) -> None:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout,
        )
    except requests.RequestException:
        return

    content_type = response.headers.get("Content-Type", "").lower()

    if "javascript" not in content_type:
        return

    with tempfile.NamedTemporaryFile(
        mode="wb", dir=directory, delete=False
    ) as file:
        file.write(response.content)


def get_bundles(domain: str) -> Iterator[str]:
    seen: set[str] = set()

    for scheme in ("https", "http"):
        response = get_domain_response(domain, scheme)

        if response is None:
            continue

        for url in get_javascript_files(domain, response):
            parts = urlsplit(url)
            key = f"{parts.path}?{parts.query}"

            if key in seen:
                continue

            seen.add(key)
            yield url
