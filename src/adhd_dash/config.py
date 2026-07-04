"""Static configuration loader (`config.yaml`).

See docs/architecture.md §3 ("Config as source of truth"): config.yaml holds
every static tunable -- staleness threshold(s), poll interval, tracked SSH
hosts/roots, GitHub check TTL, log level. Secret fields ship blank here and
are overridden by env vars at deploy time. Mutable runtime state (the
tracked-project registry, snooze/archive/last-seen) lives in state.db
instead -- see adhd_dash.models -- and never belongs in this file.

Missing or malformed fields raise pydantic's ValidationError -- this module
deliberately does not catch or paper over that, per the "fail loudly" rule.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class StalenessConfig(BaseModel):
    """How long a project may go quiet before it's flagged stale."""

    default_threshold_days: int = Field(gt=0)


class PollingConfig(BaseModel):
    """How often the scheduler polls tracked projects."""

    interval_minutes: int


class HostConfig(BaseModel):
    """A remote Tailscale host reachable via SSH for Beads discovery/status."""

    name: str
    ssh_host: str
    ssh_user: str
    ssh_key_path: str
    roots: list[str]


class GithubConfig(BaseModel):
    """GitHub REST API polling settings."""

    check_ttl_minutes: int
    token: str


class LoggingConfig(BaseModel):
    """Application log level."""

    level: str


class DbConfig(BaseModel):
    """SQLite engine tuning for state.db."""

    busy_timeout_seconds: int = Field(default=5, gt=0)


class Config(BaseModel):
    """Root config.yaml schema."""

    staleness: StalenessConfig
    polling: PollingConfig
    hosts: list[HostConfig] = Field(default_factory=list)
    github: GithubConfig
    logging: LoggingConfig
    db: DbConfig = Field(default_factory=DbConfig)


def load_config(path: str | Path = "config.yaml") -> Config:
    """Read and validate `config.yaml`, returning a `Config` instance.

    Raises:
        FileNotFoundError: if `path` does not exist.
        pydantic.ValidationError: if the file is missing required fields or
            has fields of the wrong type.
    """
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text())
    return Config.model_validate(raw)
