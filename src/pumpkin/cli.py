import argparse
import tempfile
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import (
    FIRST_COMPLETED,
    Executor,
    ThreadPoolExecutor,
    wait,
)
from functools import partial
from urllib.parse import urlsplit

from termcolor import colored
from tqdm import tqdm

from pumpkin.bundles import (
    SeenBundles,
    create_session,
    fetch_bundle,
    get_bundles,
)
from pumpkin.settings import settings
from pumpkin.subdomains import (
    SubdomainLookupError,
    get_bundle_subdomains,
    get_subdomains,
)
from pumpkin.titus import (
    Secret,
    TitusError,
    get_installation_path,
    get_secrets,
)


def parse_domain(target: str) -> str:
    if "://" not in target:
        target = f"//{target}"

    try:
        hostname = urlsplit(target).hostname
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error

    if not hostname:
        raise argparse.ArgumentTypeError(f"no hostname in {target!r}")

    return hostname


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pumpkin",
        description="find secrets in javascript.",
    )
    parser.add_argument(
        "-u",
        "--url",
        dest="domain",
        required=True,
        type=parse_domain,
        help="target domain or url to scan",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=float,
        help="request timeout in seconds",
    )
    parser.add_argument(
        "-a",
        "--user-agent",
        help="user agent header sent with every request",
    )
    parser.add_argument(
        "-r",
        "--reduce-noise",
        action=argparse.BooleanOptionalAction,
        help="enable noisey titus detection rules",
    )
    parser.add_argument(
        "-s",
        "--subdomains",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="scan discovered subdomains",
    )
    parser.add_argument(
        "-c",
        "--threads",
        type=int,
        default=1,
        help="number of domains to scan concurrently",
    )
    parser.add_argument(
        "-d",
        "--download-threads",
        type=int,
        default=4,
        help="concurrent javascript downloads per domain",
    )

    return parser.parse_args(argv)


def label(text: str, background: str) -> str:
    return colored(f" {text} ", "black", background, attrs=["bold"])


def format_secret(domain: str, secret: Secret) -> str:
    content = ", ".join(secret.matches)

    return (
        f"{label(domain, 'on_yellow')}{label(secret.rule, 'on_green')}"
        f" {content[: settings.output_limit]}"
    )


def crawl(
    executor: Executor,
    items: Iterable[str],
    work: Callable[[str], set[str]],
) -> None:
    initial = list(items)
    seen = set(initial)
    pending = {executor.submit(work, item) for item in initial}

    try:
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)

            for future in done:
                for item in future.result() - seen:
                    seen.add(item)
                    pending.add(executor.submit(work, item))
    except BaseException:
        executor.shutdown(wait=True, cancel_futures=True)
        raise


def scan_domain(
    domain: str,
    root: str,
    discover: bool,
    status: tqdm,
    download_threads: int,
    seen: SeenBundles,
) -> set[str]:
    status.set_description_str(
        colored(f"scanning {domain}", "yellow", attrs=["bold"])
    )

    with tempfile.TemporaryDirectory() as directory:
        with (
            create_session() as session,
            ThreadPoolExecutor(max_workers=download_threads) as executor,
        ):
            crawl(
                executor,
                get_bundles(session, domain),
                partial(
                    fetch_bundle,
                    session,
                    domain=domain,
                    directory=directory,
                    seen=seen,
                ),
            )

        for secret in get_secrets(directory):
            status.write(format_secret(domain, secret))

        if not discover:
            return set()

        return get_bundle_subdomains(directory, root)


def get_domains(domain: str, subdomains: bool) -> list[str]:
    if not subdomains:
        return [domain]

    try:
        return list(get_subdomains(domain))
    except SubdomainLookupError as error:
        raise SystemExit(str(error)) from error


def main(argv: Sequence[str] | None = None) -> None:
    arguments = parse_arguments(argv)

    if arguments.timeout is not None:
        settings.timeout = arguments.timeout

    if arguments.user_agent is not None:
        settings.user_agent = arguments.user_agent

    if arguments.reduce_noise is not None:
        settings.titus_include_noisy = not arguments.reduce_noise

    try:
        get_installation_path()
    except TitusError as error:
        raise SystemExit(str(error)) from error

    domains = get_domains(arguments.domain, arguments.subdomains)
    seen = SeenBundles()

    with (
        tqdm(bar_format="{desc}", dynamic_ncols=True) as status,
        ThreadPoolExecutor(max_workers=arguments.threads) as executor,
    ):
        try:
            crawl(
                executor,
                domains,
                partial(
                    scan_domain,
                    root=arguments.domain,
                    discover=arguments.subdomains,
                    status=status,
                    download_threads=arguments.download_threads,
                    seen=seen,
                ),
            )
        except TitusError as error:
            raise SystemExit(str(error)) from error

        status.set_description_str(
            colored(
                f"finished scanning {arguments.domain}",
                "yellow",
                attrs=["bold"],
            )
        )
