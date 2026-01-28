"""Watchdog service for monitoring health and resources."""

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum

import httpx
import psutil

from watchdog_config import WatchdogConfig

logger = logging.getLogger("voxbox.watchdog")


class AlertType(str, Enum):
    """Types of alerts the watchdog can send."""

    HEALTH_CHECK_FAILED = "health_check_failed"
    HEALTH_RECOVERED = "health_recovered"
    CPU_HIGH = "cpu_high"
    CPU_RECOVERED = "cpu_recovered"
    MEMORY_HIGH = "memory_high"
    MEMORY_RECOVERED = "memory_recovered"
    STATS_HEARTBEAT = "stats_heartbeat"
    TEST = "test"


class WatchdogService:
    """Monitors server health and resources, sends alerts to n8n webhook."""

    def __init__(self, config: WatchdogConfig):
        self.config = config
        self._tasks: list[asyncio.Task] = []
        self._running = False

        # State tracking
        self._consecutive_health_failures = 0
        self._health_alerting = False
        self._cpu_alerting = False
        self._memory_alerting = False

        # Cooldown tracking (alert_type -> last_sent_timestamp)
        self._last_alert_time: dict[AlertType, float] = {}

    async def start(self):
        """Start the watchdog monitoring tasks."""
        logger.info("Starting watchdog service")
        self._running = True

        # Stats logging always runs
        self._tasks = [
            asyncio.create_task(self._stats_log_loop()),
        ]

        # Health and resource monitoring only run if alerting is enabled
        if self.config.is_enabled:
            logger.info("Alerting enabled (webhook configured)")
            self._tasks.extend([
                asyncio.create_task(self._health_check_loop()),
                asyncio.create_task(self._resource_check_loop()),
            ])
        else:
            logger.info("Alerting disabled (no webhook URL configured)")

    async def stop(self):
        """Stop the watchdog monitoring tasks."""
        if not self._running:
            return

        logger.info("Stopping watchdog service")
        self._running = False

        for task in self._tasks:
            task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        self._tasks = []

    async def _health_check_loop(self):
        """Periodically check the /health endpoint."""
        while self._running:
            try:
                await self._check_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")

            await asyncio.sleep(self.config.health_interval)

    async def _resource_check_loop(self):
        """Periodically check CPU and memory usage."""
        while self._running:
            try:
                await self._check_resources()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Resource check error: {e}")

            await asyncio.sleep(self.config.resource_interval)

    async def _stats_log_loop(self):
        """Periodically log transcriber utilization stats."""
        while self._running:
            try:
                await self._log_stats()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Stats log error: {e}")

            await asyncio.sleep(self.config.stats_interval)

    async def _log_stats(self):
        """Fetch and log transcriber statistics, send heartbeat to webhook."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("http://127.0.0.1:5000/stats", timeout=10.0)
                response.raise_for_status()
                stats = response.json()

            # Format uptime
            uptime_s = stats["uptime_seconds"]
            hours, remainder = divmod(uptime_s, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{hours}h{minutes}m" if hours else f"{minutes}m{seconds}s"

            # Format status
            status = "BUSY" if stats["is_busy"] else "idle"
            if stats["is_busy"] and stats["current_duration_ms"]:
                status += f" ({stats['current_duration_ms']}ms)"

            # Log stats locally
            logger.info(
                f"Transcriber: {status} | "
                f"total={stats['total_transcriptions']} | "
                f"last_{stats['window_seconds']}s={stats['transcriptions_last_window']} | "
                f"avg={stats['avg_duration_ms']}ms | "
                f"errors={stats['total_errors']} | "
                f"uptime={uptime_str}"
            )

            # Send heartbeat to webhook if configured
            if self.config.is_enabled:
                await self._send_heartbeat(stats)

        except Exception as e:
            logger.warning(f"Failed to fetch stats: {e}")

    async def _send_heartbeat(self, stats: dict):
        """Send stats heartbeat to n8n webhook (bypasses cooldown)."""
        status = "busy" if stats["is_busy"] else "idle"
        payload = {
            "source": "voxbox",
            "severity": "info",
            "message": f"Heartbeat: {status}, {stats['total_transcriptions']} transcriptions, {stats['total_errors']} errors",
            "context": {
                "alert_type": AlertType.STATS_HEARTBEAT.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "total_transcriptions": stats["total_transcriptions"],
                "transcriptions_last_window": stats["transcriptions_last_window"],
                "window_seconds": stats["window_seconds"],
                "avg_duration_ms": stats["avg_duration_ms"],
                "total_errors": stats["total_errors"],
                "uptime_seconds": stats["uptime_seconds"],
            },
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.config.n8n_webhook_url,
                    json=payload,
                    timeout=10.0,
                )
                response.raise_for_status()
            logger.debug("Heartbeat sent to webhook")
        except Exception as e:
            logger.warning(f"Failed to send heartbeat: {e}")

    async def _check_health(self):
        """Check the /health endpoint and alert on failures."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get("http://127.0.0.1:5000/health", timeout=10.0)
                response.raise_for_status()

            # Health check succeeded
            if self._health_alerting:
                # Recovered from failure state
                await self._send_alert(
                    AlertType.HEALTH_RECOVERED,
                    {
                        "message": "Health check recovered",
                        "previous_failures": self._consecutive_health_failures,
                    },
                )
                self._health_alerting = False

            self._consecutive_health_failures = 0

        except Exception as e:
            self._consecutive_health_failures += 1
            logger.warning(
                f"Health check failed ({self._consecutive_health_failures}/{self.config.health_failure_threshold}): {e}"
            )

            if (
                self._consecutive_health_failures >= self.config.health_failure_threshold
                and not self._health_alerting
            ):
                self._health_alerting = True
                await self._send_alert(
                    AlertType.HEALTH_CHECK_FAILED,
                    {
                        "reason": str(e),
                        "consecutive_failures": self._consecutive_health_failures,
                        "threshold": self.config.health_failure_threshold,
                    },
                )

    async def _check_resources(self):
        """Check CPU and memory usage, alert if thresholds exceeded."""
        cpu_percent = psutil.cpu_percent(interval=1.0)
        memory = psutil.virtual_memory()
        memory_percent = memory.percent

        # CPU check
        if cpu_percent >= self.config.cpu_threshold:
            if not self._cpu_alerting:
                self._cpu_alerting = True
                await self._send_alert(
                    AlertType.CPU_HIGH,
                    {
                        "cpu_percent": cpu_percent,
                        "threshold": self.config.cpu_threshold,
                    },
                )
        elif self._cpu_alerting:
            self._cpu_alerting = False
            await self._send_alert(
                AlertType.CPU_RECOVERED,
                {
                    "cpu_percent": cpu_percent,
                    "threshold": self.config.cpu_threshold,
                },
            )

        # Memory check
        if memory_percent >= self.config.memory_threshold:
            if not self._memory_alerting:
                self._memory_alerting = True
                await self._send_alert(
                    AlertType.MEMORY_HIGH,
                    {
                        "memory_percent": memory_percent,
                        "memory_used_gb": memory.used / (1024**3),
                        "memory_total_gb": memory.total / (1024**3),
                        "threshold": self.config.memory_threshold,
                    },
                )
        elif self._memory_alerting:
            self._memory_alerting = False
            await self._send_alert(
                AlertType.MEMORY_RECOVERED,
                {
                    "memory_percent": memory_percent,
                    "threshold": self.config.memory_threshold,
                },
            )

    def _get_severity(self, alert_type: AlertType) -> str:
        """Map alert type to severity level."""
        if "recovered" in alert_type.value:
            return "info"
        if alert_type in (AlertType.HEALTH_CHECK_FAILED,):
            return "error"
        if alert_type in (AlertType.CPU_HIGH, AlertType.MEMORY_HIGH):
            return "warning"
        return "info"

    def _get_message(self, alert_type: AlertType, details: dict) -> str:
        """Generate human-readable message for alert type."""
        messages = {
            AlertType.HEALTH_CHECK_FAILED: f"Health check failed after {details.get('consecutive_failures', '?')} attempts: {details.get('reason', 'unknown')}",
            AlertType.HEALTH_RECOVERED: f"Health check recovered after {details.get('previous_failures', '?')} failures",
            AlertType.CPU_HIGH: f"CPU usage high: {details.get('cpu_percent', '?')}% (threshold: {details.get('threshold', '?')}%)",
            AlertType.CPU_RECOVERED: f"CPU usage normalized: {details.get('cpu_percent', '?')}%",
            AlertType.MEMORY_HIGH: f"Memory usage high: {details.get('memory_percent', '?')}% (threshold: {details.get('threshold', '?')}%)",
            AlertType.MEMORY_RECOVERED: f"Memory usage normalized: {details.get('memory_percent', '?')}%",
        }
        return messages.get(alert_type, f"Alert: {alert_type.value}")

    async def _send_alert(self, alert_type: AlertType, details: dict):
        """Send an alert to the n8n webhook."""
        # Check cooldown (recovery alerts bypass cooldown)
        is_recovery = "recovered" in alert_type.value
        if not is_recovery:
            last_sent = self._last_alert_time.get(alert_type, 0)
            now = datetime.now(timezone.utc).timestamp()
            if now - last_sent < self.config.alert_cooldown:
                logger.debug(f"Alert {alert_type.value} skipped (cooldown)")
                return

        payload = {
            "source": "voxbox",
            "severity": self._get_severity(alert_type),
            "message": self._get_message(alert_type, details),
            "context": {
                "alert_type": alert_type.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **details,
            },
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.config.n8n_webhook_url,
                    json=payload,
                    timeout=10.0,
                )
                response.raise_for_status()

            logger.info(f"Alert sent: {alert_type.value}")
            self._last_alert_time[alert_type] = datetime.now(timezone.utc).timestamp()

        except Exception as e:
            logger.error(f"Failed to send alert {alert_type.value}: {e}")

    async def send_test_alert(self, message: str = "Test alert from voxbox") -> dict:
        """Send a test alert to verify webhook connectivity.

        Returns dict with success status and any error message.
        """
        if not self.config.is_enabled:
            return {
                "success": False,
                "error": "Alerting disabled (no webhook URL configured)",
            }

        payload = {
            "source": "voxbox",
            "severity": "info",
            "message": message,
            "context": {
                "alert_type": AlertType.TEST.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.config.n8n_webhook_url,
                    json=payload,
                    timeout=10.0,
                )
                response.raise_for_status()

            logger.info(f"Test alert sent successfully")
            return {
                "success": True,
                "webhook_url": self.config.n8n_webhook_url,
                "payload": payload,
            }

        except Exception as e:
            logger.error(f"Failed to send test alert: {e}")
            return {
                "success": False,
                "error": str(e),
            }
