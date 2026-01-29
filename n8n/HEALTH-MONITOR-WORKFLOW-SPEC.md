# Voxbox Health Monitor Workflow Specification

## Purpose

Create an N8N workflow that externally monitors the voxbox transcription service and sends alerts when the service becomes unavailable. This provides resilience against scenarios where the service crashes and cannot self-report its failure.

## Context

Voxbox is a voice transcription service running in a container. It has an internal watchdog that monitors health and resources, but if the entire container crashes, the internal watchdog cannot send alerts. This external workflow solves that gap.

## Existing Infrastructure

### Alert Dispatcher Webhook

All alerts should be sent to the existing alert dispatcher:

```
POST https://n8n.toyber.us/webhook/h3THKz0peMRf13xo-alert-dispatcher
```

### Required Payload Format

The alert dispatcher expects this JSON schema:

```json
{
  "source": "voxbox",
  "severity": "error" | "warning" | "info",
  "message": "Human-readable alert message",
  "context": {
    "alert_type": "string identifying the alert type",
    "timestamp": "ISO 8601 timestamp",
    // additional fields as needed
  }
}
```

### Voxbox Endpoints

| Endpoint | Method | Purpose | Expected Response |
|----------|--------|---------|-------------------|
| `http://192.168.1.34:5000/health` | GET | Health check | `{"status": "ok"}` |
| `http://192.168.1.34:5000/stats` | GET | Service statistics | JSON with metrics |

Note: Use the appropriate IP/hostname for your network (LAN IP, Tailscale IP, or localhost if N8N runs on same host).

## Workflow Requirements

### 1. Polling Schedule

- **Interval**: Every 60 seconds (configurable)
- **Timeout**: 10 seconds per request
- **Failure threshold**: Alert after 3 consecutive failures (avoids false positives from transient network issues)

### 2. Health Check Logic

```
┌─────────────────┐
│  Schedule       │
│  (every 60s)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  HTTP Request   │
│  GET /health    │
│  timeout: 10s   │
└────────┬────────┘
         │
    ┌────┴────┐
    │ Success? │
    └────┬────┘
         │
    ┌────┴────┐
   Yes       No
    │         │
    ▼         ▼
┌────────┐ ┌─────────────┐
│ Reset  │ │ Increment   │
│ counter│ │ fail counter│
└────┬───┘ └──────┬──────┘
     │            │
     │       ┌────┴────┐
     │       │ >= 3?   │
     │       └────┬────┘
     │           Yes
     │            │
     ▼            ▼
┌─────────────┐ ┌─────────────────┐
│ If was down │ │ Send DOWN alert │
│ send UP     │ │ (if not already │
│ alert       │ │  alerted)       │
└─────────────┘ └─────────────────┘
```

### 3. Alert Types

#### Service Down Alert

Trigger: 3 consecutive health check failures

```json
{
  "source": "voxbox",
  "severity": "error",
  "message": "Voxbox service is unreachable",
  "context": {
    "alert_type": "external_health_check_failed",
    "timestamp": "2024-01-28T12:00:00.000Z",
    "consecutive_failures": 3,
    "last_error": "Connection refused" | "Timeout" | "HTTP 500",
    "endpoint": "http://192.168.1.34:5000/health"
  }
}
```

#### Service Recovered Alert

Trigger: Health check succeeds after previous DOWN state

```json
{
  "source": "voxbox",
  "severity": "info",
  "message": "Voxbox service has recovered",
  "context": {
    "alert_type": "external_health_check_recovered",
    "timestamp": "2024-01-28T12:05:00.000Z",
    "downtime_seconds": 300,
    "endpoint": "http://192.168.1.34:5000/health"
  }
}
```

### 4. State Management

The workflow needs to track state between executions:

| Variable | Type | Purpose |
|----------|------|---------|
| `consecutive_failures` | integer | Count of sequential failed checks |
| `is_alerting` | boolean | Whether we've already sent a DOWN alert |
| `last_down_time` | timestamp | When the service first went down |

Options for state storage in N8N:
- Static data (workflow variables)
- External database node
- File-based storage

### 5. Deduplication

- Only send ONE "down" alert when service fails (not on every failed poll)
- Only send ONE "recovered" alert when service comes back
- Reset state after recovery

## Optional Enhancements

### Enhancement A: Stats Collection

In addition to health checks, optionally fetch `/stats` endpoint and include metrics in heartbeat:

```json
{
  "source": "voxbox-monitor",
  "severity": "info",
  "message": "Voxbox external health check OK",
  "context": {
    "alert_type": "external_heartbeat",
    "timestamp": "2024-01-28T12:00:00.000Z",
    "stats": {
      "total_transcriptions": 42,
      "uptime_seconds": 3600,
      "is_busy": false
    }
  }
}
```

### Enhancement B: Configurable Thresholds

Use N8N workflow variables or environment to configure:
- `VOXBOX_URL`: Base URL of the service
- `CHECK_INTERVAL`: Polling interval in seconds
- `FAILURE_THRESHOLD`: Consecutive failures before alerting
- `REQUEST_TIMEOUT`: HTTP request timeout

## Testing

After building the workflow:

1. **Test normal operation**: Verify no alerts when service is healthy
2. **Test failure detection**: Stop the voxbox container, verify alert after ~3 minutes
3. **Test recovery**: Start the container, verify recovery alert
4. **Test deduplication**: Confirm only one DOWN alert during extended outage

## Workflow Naming

Suggested name: `Voxbox External Health Monitor`

## Summary

This workflow provides a safety net for monitoring the voxbox service from outside its container. It complements the internal watchdog by detecting scenarios where the entire service becomes unavailable and cannot self-report.
