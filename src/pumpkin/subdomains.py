from collections.abc import Iterator

import requests
from pydantic import BaseModel

from pumpkin.settings import settings


class SubdomainLookupError(Exception):
    pass


class DomainRecord(BaseModel):
    domain: str


class SubdomainPage(BaseModel):
    domains: list[DomainRecord]
    next_page_state: str | None = None


def fetch_page(
    session: requests.Session, domain: str, state: str
) -> SubdomainPage:
    try:
        response = session.post(
            str(settings.subdomains_api_url),
            headers={
                "Accept": "application/json",
                "User-Agent": settings.user_agent,
            },
            json={
                "domain": domain,
                "page_state": state,
                "limit": settings.subdomains_page_size,
            },
            timeout=settings.timeout,
        )
        response.raise_for_status()

        return SubdomainPage.model_validate(response.json())
    except (requests.RequestException, ValueError) as error:
        raise SubdomainLookupError(
            f"lookup failed for {domain}: {error}"
        ) from error


def get_subdomains(domain: str) -> Iterator[str]:
    seen_domains: set[str] = set()
    seen_states: set[str] = {""}
    state = ""

    with requests.Session() as session:
        while True:
            page = fetch_page(session, domain, state)

            for record in page.domains:
                if record.domain not in seen_domains:
                    seen_domains.add(record.domain)
                    yield record.domain

            if not page.next_page_state or page.next_page_state in seen_states:
                return

            seen_states.add(page.next_page_state)
            state = page.next_page_state
