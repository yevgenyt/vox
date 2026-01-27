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

    # Thresholds (percentages)
    cpu_threshold: int = 90
    memory_threshold: int = 85

    # Alert behavior
    alert_cooldown: int = 300  # seconds before re-alerting same issue
    health_failure_threshold: int = 3  # consecutive failures before alerting

    @classmethod
    def from_env(cls) -> "WatchdogConfig":
        """Load configuration from environment variables."""
        return cls(
            n8n_webhook_url=os.environ.get("WATCHDOG_N8N_WEBHOOK_URL"),
            health_interval=int(os.environ.get("WATCHDOG_HEALTH_INTERVAL", "30")),
            resource_interval=int(os.environ.get("WATCHDOG_RESOURCE_INTERVAL", "60")),
            cpu_threshold=int(os.environ.get("WATCHDOG_CPU_THRESHOLD", "90")),
            memory_threshold=int(os.environ.get("WATCHDOG_MEMORY_THRESHOLD", "85")),
            alert_cooldown=int(os.environ.get("WATCHDOG_ALERT_COOLDOWN", "300")),
            health_failure_threshold=int(os.environ.get("WATCHDOG_HEALTH_FAILURE_THRESHOLD", "3")),
        )

    @property
    def is_enabled(self) -> bool:
        """Check if watchdog is enabled (webhook URL configured)."""
        return bool(self.n8n_webhook_url)
