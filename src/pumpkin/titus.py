import base64
import json
import shutil
import subprocess

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pumpkin.settings import settings


class TitusError(Exception):
    pass


class Secret(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rule: str = Field(validation_alias="RuleName")
    matches: list[str] = Field(validation_alias="Groups")

    @field_validator("matches")
    @classmethod
    def decode_matches(cls, value: list[str]) -> list[str]:
        return [
            base64.b64decode(i).decode("utf-8", errors="replace")
            for i in value
        ]


def get_installation_path() -> str:
    if path := shutil.which(settings.titus_binary):
        return path

    raise TitusError(
        f"'{settings.titus_binary}' binary is not found in your path."
    )


def parse_secrets(output: bytes) -> list[Secret]:
    try:
        return [Secret.model_validate(event) for event in json.loads(output)]
    except (ValueError, TypeError) as error:
        raise TitusError(f"unexpected titus output: {error}") from error


def build_command(path: str) -> list[str]:
    command = [
        get_installation_path(),
        "scan",
        path,
        "-q",
        "--format",
        "json",
        "--output",
        ":memory:",
    ]

    if settings.titus_include_noisy:
        command.append("--include-noisy")

    return command


def get_secrets(path: str) -> list[Secret]:
    try:
        completed = subprocess.run(
            build_command(path),
            capture_output=True,
            check=False,
            timeout=settings.titus_timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise TitusError(f"titus timed out scanning {path}") from error

    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise TitusError(
            f"titus exited with {completed.returncode}: {message}"
        )

    return parse_secrets(completed.stdout)
