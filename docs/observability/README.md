# Observability

## Metrics

The backend exposes Prometheus metrics at **`GET /metrics`** (no auth, excluded
from the OpenAPI schema). Always on, including the slim image.

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `http_requests_total` | counter | `method`, `path`, `status` | Requests by route template + status. `path` is the route template (e.g. `/api/films/{film_id}`) so per-id paths don't explode cardinality; unmatched paths label as `unmatched`. |
| `http_request_duration_seconds` | histogram | `method`, `path` | Request latency. |
| `llm_circuit_open` | gauge | — | `1` when the cloud-LLM circuit breaker is open (auto-tagging degraded), else `0`. Sampled at scrape time. |

Recording is wrapped so a metrics error can never turn a healthy response into
a 500.

### Scrape config (example)

```yaml
scrape_configs:
  - job_name: film-brain
    metrics_path: /metrics
    static_configs:
      - targets: ["backend:8000"]
```

### Alerts

Starter alerting rules: [`alerts.yml`](alerts.yml). Load via Prometheus
`rule_files:`. Covers breaker-open, 5xx error rate, and p95 latency. Thresholds
are starting points — tune against real traffic.

## Structured logging

Logging defaults to human-readable. Set **`LOG_FORMAT=json`** to emit one JSON
object per line (`ts`, `level`, `logger`, `msg`, and `exc` on errors) — suitable
for log aggregation. `LOG_LEVEL` (default `INFO`) sets the root level when JSON
logging is enabled.

```bash
LOG_FORMAT=json LOG_LEVEL=INFO uvicorn backend.main:app --port 8000
```
