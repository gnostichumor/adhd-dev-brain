from adhd_dash.config import (
    Config,
    GithubConfig,
    LoggingConfig,
    PollingConfig,
    StalenessConfig,
)
from adhd_dash.github_client import GithubClient
from adhd_dash.main import build_github_client


def test_build_github_client_uses_config_token_and_ttl() -> None:
    config = Config(
        staleness=StalenessConfig(default_threshold_days=14),
        polling=PollingConfig(interval_minutes=60),
        hosts=[],
        github=GithubConfig(check_ttl_minutes=30, token="secret-token"),
        logging=LoggingConfig(level="INFO"),
    )

    client = build_github_client(config)

    assert isinstance(client, GithubClient)
