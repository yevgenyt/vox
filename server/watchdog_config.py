"""Watchdog configuration with environment variable loading."""

import os
from dataclasses import dataclass


@dataclass
class WatchdogConfig:
    """Configuration for the watchdog service."""

    # Required
    n8n_webhook_url: str | None

    # Intervals (seconds)
    health_interval: int = 30
    resource_interval: int = 60
    stats_interval: int = 60  # Transcriber utilization logging

    # Thresholds (percentages)
    cpu_threshold: int = 90
    memory_threshold: int = 85

    # Alert behavior
    alert_cooldown: int = 300  # seconds before re-alerting same issue
    health_failure_threshold: int = 3  # consecutive failures before alerting

    # Webhook delivery resilience (n8n restarts/proxy hiccups return 502)
    webhook_max_retries: int = 3  # total attempts per webhook POST
    webhook_retry_backoff: float = 1.0  # base seconds, multiplied by attempt number

    @classmethod
    def from_env(cls) -> "WatchdogConfig":
        """Load configuration from environment variables."""
        return cls(
            n8n_webhook_url=os.environ.get("WATCHDOG_N8N_WEBHOOK_URL"),
            health_interval=int(os.environ.get("WATCHDOG_HEALTH_INTERVAL", "30")),
            resource_interval=int(os.environ.get("WATCHDOG_RESOURCE_INTERVAL", "60")),
            stats_interval=int(os.environ.get("WATCHDOG_STATS_INTERVAL", "60")),
            cpu_threshold=int(os.environ.get("WATCHDOG_CPU_THRESHOLD", "90")),
            memory_threshold=int(os.environ.get("WATCHDOG_MEMORY_THRESHOLD", "85")),
            alert_cooldown=int(os.environ.get("WATCHDOG_ALERT_COOLDOWN", "300")),
            health_failure_threshold=int(os.environ.get("WATCHDOG_HEALTH_FAILURE_THRESHOLD", "3")),
            webhook_max_retries=int(os.environ.get("WATCHDOG_WEBHOOK_MAX_RETRIES", "3")),
            webhook_retry_backoff=float(os.environ.get("WATCHDOG_WEBHOOK_RETRY_BACKOFF", "1.0")),
        )

    @property
    def is_enabled(self) -> bool:
        """Check if watchdog is enabled (webhook URL configured)."""
        return bool(self.n8n_webhook_url)
