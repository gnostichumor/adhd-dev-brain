from pathlib import Path

import pytest
from pydantic import ValidationError

from adhd_dash.config import (
    Config,
    GithubConfig,
    LoggingConfig,
    PollingConfig,
    StalenessConfig,
    load_config,
)

VALID_CONFIG = """
staleness:
  default_threshold_days: 14
polling:
  interval_minutes: 60
hosts:
  - name: example
    ssh_host: ""
    ssh_user: ""
    ssh_key_path: ""
    roots: []
github:
  check_ttl_minutes: 60
  token: ""
logging:
  level: INFO
"""


def test_load_config_valid(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(VALID_CONFIG)

    config = load_config(config_path)

    assert config.staleness.default_threshold_days == 14
    assert config.polling.interval_minutes == 60
    assert len(config.hosts) == 1
    assert config.hosts[0].name == "example"
    assert config.github.check_ttl_minutes == 60
    assert config.logging.level == "INFO"


def test_load_config_multiple_hosts(tmp_path: Path) -> None:
    """Each host entry's SSH connection settings resolve independently --
    no field leaks or aliases across entries in the list."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
staleness:
  default_threshold_days: 14
polling:
  interval_minutes: 60
hosts:
  - name: workstation
    ssh_host: 100.64.0.1
    ssh_user: josh
    ssh_key_path: /home/josh/.ssh/id_workstation
    roots:
      - /home/josh/projects
  - name: homelab-server
    ssh_host: 100.64.0.2
    ssh_user: deploy
    ssh_key_path: /home/josh/.ssh/id_homelab
    roots:
      - /srv/apps
      - /srv/scratch
github:
  check_ttl_minutes: 60
  token: ""
logging:
  level: INFO
"""
    )

    config = load_config(config_path)

    assert len(config.hosts) == 2

    assert config.hosts[0].name == "workstation"
    assert config.hosts[0].ssh_host == "100.64.0.1"
    assert config.hosts[0].ssh_user == "josh"
    assert config.hosts[0].ssh_key_path == "/home/josh/.ssh/id_workstation"
    assert config.hosts[0].roots == ["/home/josh/projects"]

    assert config.hosts[1].name == "homelab-server"
    assert config.hosts[1].ssh_host == "100.64.0.2"
    assert config.hosts[1].ssh_user == "deploy"
    assert config.hosts[1].ssh_key_path == "/home/josh/.ssh/id_homelab"
    assert config.hosts[1].roots == ["/srv/apps", "/srv/scratch"]

    assert config.hosts[0].roots != config.hosts[1].roots


def test_load_config_empty_hosts_list(tmp_path: Path) -> None:
    """hosts is allowed to be empty -- no real hosts configured yet is valid."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
staleness:
  default_threshold_days: 14
polling:
  interval_minutes: 60
hosts: []
github:
  check_ttl_minutes: 60
  token: ""
logging:
  level: INFO
"""
    )

    config = load_config(config_path)

    assert config.hosts == []


def test_load_config_missing_required_field_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
polling:
  interval_minutes: 60
hosts: []
github:
  check_ttl_minutes: 60
  token: ""
logging:
  level: INFO
"""
    )

    with pytest.raises(ValidationError):
        load_config(config_path)


def test_load_config_wrong_type_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
staleness:
  default_threshold_days: "not-a-number"
polling:
  interval_minutes: 60
hosts: []
github:
  check_ttl_minutes: 60
  token: ""
logging:
  level: INFO
"""
    )

    with pytest.raises(ValidationError):
        load_config(config_path)


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "does-not-exist.yaml")


def test_shipped_config_yaml_is_valid() -> None:
    """The repo-root config.yaml is a shipped deliverable -- lock it to the
    schema so drift between the two is caught by the suite, not only by a
    manual boot."""
    repo_root = Path(__file__).resolve().parent.parent
    config = load_config(repo_root / "config.yaml")

    assert config.hosts == []
    assert config.github.token == ""
    assert config.db.busy_timeout_seconds == 5


def test_db_config_defaults_to_five_second_busy_timeout() -> None:
    """`Config(...)` constructed without a `db=` field (as several tests do
    directly) must still default `busy_timeout_seconds` to 5, matching the
    value that was previously hardcoded in `db.create_db_engine`
    (adhd-dash-b4t)."""
    config_path_free = Config(
        staleness=StalenessConfig(default_threshold_days=14),
        polling=PollingConfig(interval_minutes=60),
        github=GithubConfig(check_ttl_minutes=60, token=""),
        logging=LoggingConfig(level="INFO"),
    )

    assert config_path_free.db.busy_timeout_seconds == 5


def test_db_config_custom_value_round_trips_through_config_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        VALID_CONFIG
        + """
db:
  busy_timeout_seconds: 10
"""
    )

    config = load_config(config_path)

    assert config.db.busy_timeout_seconds == 10
