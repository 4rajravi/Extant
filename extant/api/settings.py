"""The HTTP API's own settings: the anti-abuse rate limits and whom to trust for the client
address. Read from the environment and ``.env`` with the ``EXTANT_API_`` prefix."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EXTANT_API_", env_file=".env", extra="ignore", frozen=True
    )

    # How many proxies in front of this process append to X-Forwarded-For. 0, the default,
    # trusts no header and uses the socket's peer: right locally and behind any proxy not
    # configured on purpose, where the header is whatever the caller chose to send. Behind a
    # Google load balancer the header ends "<client>, <load balancer>", so 2; what a direct
    # run.app URL appends is confirmed against the running service in Phase 10.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)

    # Per client address, per instance, in a sliding minute. Anti-abuse only: the daily cap in
    # Postgres is the cost control. ask is the expensive path; the reads are what a page
    # fetches repeatedly.
    ask_per_minute: int = Field(default=6, ge=1)
    reads_per_minute: int = Field(default=120, ge=1)


def load_api_settings() -> ApiSettings:
    return ApiSettings()
