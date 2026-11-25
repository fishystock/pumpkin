import argparse
import tempfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import urlsplit

from termcolor import colored
from tqdm import tqdm

from pumpkin.bundles import (
    SeenBundles,
    create_session,
    download_bundle,
    get_bundles,
)
from pumpkin.settings import settings
from pumpkin.subdomains import SubdomainLookupError, get_subdomains
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


def scan_domain(
    domain: str, status: tqdm, download_threads: int, seen: SeenBundles
) -> None:
    status.set_description_str(
        colored(f"scanning {domain}", "yellow", attrs=["bold"])
    )

    with tempfile.TemporaryDirectory() as directory:
        with (
            create_session() as session,
            ThreadPoolExecutor(max_workers=download_threads) as executor,
        ):
            list(
                executor.map(
                    partial(
                        download_bundle,
                        session,
                        directory=directory,
                        seen=seen,
                    ),
                    get_bundles(session, domain),
                )
            )

        for secret in get_secrets(directory):
            status.write(format_secret(domain, secret))


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
            list(
                executor.map(
                    partial(
                        scan_domain,
                        status=status,
                        download_threads=arguments.download_threads,
                        seen=seen,
                    ),
                    domains,
                )
            )
        except TitusError as error:
            executor.shutdown(wait=True, cancel_futures=True)
            raise SystemExit(str(error)) from error

        status.set_description_str(
            colored(
                f"finished scanning {arguments.domain}",
                "yellow",
                attrs=["bold"],
            )
        )
