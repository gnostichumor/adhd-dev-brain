from pathlib import Path

import pytest
from pydantic import ValidationError

from adhd_dash.config import load_config

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
