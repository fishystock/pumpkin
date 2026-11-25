import hashlib
import os
import tempfile
import warnings
from collections.abc import Iterator
from threading import Lock
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from pumpkin.settings import settings

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


class SeenBundles:
    def __init__(self) -> None:
        self.digests: set[str] = set()
        self.lock = Lock()

    def add(self, digest: str) -> bool:
        with self.lock:
            if digest in self.digests:
                return False

            self.digests.add(digest)

        return True


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = settings.user_agent

    return session


def get_domain_response(
    session: requests.Session, domain: str, scheme: str
) -> requests.Response | None:
    try:
        response = session.get(
            f"{scheme}://{domain}/", timeout=settings.timeout
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


def download_bundle(
    session: requests.Session,
    url: str,
    directory: str,
    seen: SeenBundles,
) -> None:
    try:
        response = session.get(url, timeout=settings.timeout, stream=True)
    except requests.RequestException:
        return

    with response:
        content_type = response.headers.get("Content-Type", "").lower()

        if "javascript" not in content_type:
            return

        digest = hashlib.sha256()

        with tempfile.NamedTemporaryFile(
            mode="wb", dir=directory, delete=False
        ) as file:
            try:
                for chunk in response.iter_content(
                    settings.download_chunk_size
                ):
                    digest.update(chunk)
                    file.write(chunk)
            except requests.RequestException:
                os.unlink(file.name)

                return

    if not seen.add(digest.hexdigest()):
        os.unlink(file.name)


def get_bundles(session: requests.Session, domain: str) -> Iterator[str]:
    seen: set[str] = set()

    for scheme in ("https", "http"):
        response = get_domain_response(session, domain, scheme)

        if response is None:
            continue

        for url in get_javascript_files(domain, response):
            parts = urlsplit(url)
            key = f"{parts.path}?{parts.query}"

            if key in seen:
                continue

            seen.add(key)
            yield url
