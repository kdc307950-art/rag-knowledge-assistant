"""Small Prometheus-compatible metrics registry.

The service is intentionally single-process. Keeping this registry in the app
avoids introducing a metrics daemon while still exposing standard text format
for a future Prometheus scrape. Values are bounded by the fixed label sets used
by the helpers below; callers must never pass user/query/file identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import platform
import threading
import time
from typing import Iterable, Sequence


def _escape(value: object) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def _labels(labels: Sequence[str], values: Sequence[object]) -> str:
    if not labels:
        return ""
    pairs = [f'{name}="{_escape(value)}"' for name, value in zip(labels, values)]
    return "{" + ",".join(pairs) + "}"


class _Metric:
    def __init__(self, name: str, help_text: str, label_names: Sequence[str]):
        self.name = name
        self.help_text = help_text
        self.label_names = tuple(label_names)
        self._values: dict[tuple[str, ...], float] = {}

    def _key(self, values: Sequence[object]) -> tuple[str, ...]:
        values = tuple(str(value) for value in values)
        if len(values) != len(self.label_names):
            raise ValueError(f"{self.name} expects {len(self.label_names)} labels")
        return values

    def clear(self) -> None:
        self._values.clear()


class _Counter(_Metric):
    type_name = "counter"

    def inc(self, amount: float = 1.0, *values: object) -> None:
        value = float(amount)
        if value < 0 or not math.isfinite(value):
            raise ValueError("counter increment must be finite and non-negative")
        key = self._key(values)
        self._values[key] = self._values.get(key, 0.0) + value


class _Gauge(_Metric):
    type_name = "gauge"

    def set(self, value: float, *values: object) -> None:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("gauge value must be finite")
        self._values[self._key(values)] = value

    def inc(self, amount: float = 1.0, *values: object) -> None:
        key = self._key(values)
        self._values[key] = self._values.get(key, 0.0) + float(amount)

    def dec(self, amount: float = 1.0, *values: object) -> None:
        self.inc(-float(amount), *values)


class _Histogram(_Metric):
    type_name = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: Sequence[str],
        buckets: Iterable[float],
    ):
        super().__init__(name, help_text, label_names)
        self.buckets = tuple(sorted(float(item) for item in buckets))
        self._counts: dict[tuple[str, ...], list[float]] = {}
        self._sums: dict[tuple[str, ...], float] = {}
        self._totals: dict[tuple[str, ...], float] = {}

    def clear(self) -> None:
        super().clear()
        self._counts.clear()
        self._sums.clear()
        self._totals.clear()

    def observe(self, value: float, *values: object) -> None:
        value = float(value)
        if not math.isfinite(value) or value < 0:
            return
        key = self._key(values)
        counts = self._counts.setdefault(key, [0.0] * len(self.buckets))
        for index, bucket in enumerate(self.buckets):
            if value <= bucket:
                counts[index] += 1.0
        self._sums[key] = self._sums.get(key, 0.0) + value
        self._totals[key] = self._totals.get(key, 0.0) + 1.0


@dataclass(frozen=True)
class _MetricSet:
    request_count: _Counter
    request_duration: _Histogram
    sse_first_token: _Histogram
    sse_duration: _Histogram
    llm_calls: _Counter
    llm_latency: _Histogram
    cache_hits: _Counter
    cache_misses: _Counter
    errors: _Counter
    auth_failures: _Counter
    upload_tasks: _Counter
    kb_generation: _Gauge
    docs_count: _Gauge
    chunks_count: _Gauge
    vector_health: _Gauge
    sse_active: _Gauge


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._metrics: list[_Metric] = []
        self.metrics = self._build_metrics()
        self._started_at = time.time()

    def _register(self, metric: _Metric) -> _Metric:
        self._metrics.append(metric)
        return metric

    def _build_metrics(self) -> _MetricSet:
        duration_buckets = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)
        return _MetricSet(
            request_count=self._register(
                _Counter(
                    "rag_requests_total",
                    "HTTP requests",
                    ("route", "method", "status", "route_type", "outcome"),
                )
            ),
            request_duration=self._register(
                _Histogram(
                    "rag_request_duration_seconds",
                    "HTTP request duration",
                    ("route", "route_type"),
                    duration_buckets,
                )
            ),
            sse_first_token=self._register(
                _Histogram(
                    "rag_sse_first_token_latency_seconds",
                    "SSE time to first token",
                    ("route",),
                    duration_buckets,
                )
            ),
            sse_duration=self._register(
                _Histogram(
                    "rag_sse_duration_seconds",
                    "SSE stream duration",
                    ("route", "terminal"),
                    duration_buckets + (120, 300, 600),
                )
            ),
            llm_calls=self._register(
                _Counter("rag_llm_calls_total", "LLM calls", ("mode",))
            ),
            llm_latency=self._register(
                _Histogram("rag_llm_latency_seconds", "LLM latency", ("mode",), duration_buckets + (120, 300))
            ),
            cache_hits=self._register(
                _Counter("rag_cache_hits_total", "Cache hits", ("level",))
            ),
            cache_misses=self._register(
                _Counter("rag_cache_misses_total", "Cache misses", ("level",))
            ),
            errors=self._register(
                _Counter("rag_errors_total", "Normalized application errors", ("code",))
            ),
            auth_failures=self._register(
                _Counter("rag_auth_failures_total", "Authentication failures", ("route",))
            ),
            upload_tasks=self._register(
                _Counter("rag_upload_tasks_total", "Upload task outcomes", ("result",))
            ),
            kb_generation=self._register(_Gauge("rag_kb_generation", "Knowledge base generation", ())),
            docs_count=self._register(_Gauge("rag_docs_count", "Knowledge base document count", ())),
            chunks_count=self._register(_Gauge("rag_chunks_count", "Knowledge base chunk count", ())),
            vector_health=self._register(_Gauge("rag_vector_health", "Vector store health", ())),
            sse_active=self._register(_Gauge("rag_sse_active_connections", "Active SSE connections", ("route",))),
        )

    def reset(self) -> None:
        with self._lock:
            for metric in self._metrics:
                metric.clear()

    def render(self) -> str:
        with self._lock:
            lines: list[str] = []
            for metric in self._metrics:
                lines.append(f"# HELP {metric.name} {metric.help_text}")
                lines.append(f"# TYPE {metric.name} {metric.type_name}")
                if isinstance(metric, _Histogram):
                    for key in sorted(set(metric._totals) | set(metric._counts)):
                        base = _labels(metric.label_names, key)
                        counts = metric._counts.get(key, [0.0] * len(metric.buckets))
                        running = 0.0
                        for bucket, bucket_count in zip(metric.buckets, counts):
                            running = bucket_count
                            labels = _labels(
                                metric.label_names + ("le",), key + (str(bucket),)
                            )
                            lines.append(f"{metric.name}_bucket{labels} {running:g}")
                        labels = _labels(metric.label_names + ("le",), key + ("+Inf",))
                        total = metric._totals.get(key, 0.0)
                        lines.append(f"{metric.name}_bucket{labels} {total:g}")
                        lines.append(f"{metric.name}_sum{base} {metric._sums.get(key, 0.0):g}")
                        lines.append(f"{metric.name}_count{base} {total:g}")
                else:
                    for key, value in sorted(metric._values.items()):
                        lines.append(f"{metric.name}{_labels(metric.label_names, key)} {value:g}")

            # These two gauges are intentionally platform-neutral. The default
            # prometheus process collector is not reliable on every Windows
            # Python build, while these values are useful and deterministic.
            lines.append("# HELP python_info Python runtime information")
            lines.append("# TYPE python_info gauge")
            lines.append(f'python_info{{version="{_escape(platform.python_version())}"}} 1')
            lines.append("# HELP process_start_time_seconds Process start time")
            lines.append("# TYPE process_start_time_seconds gauge")
            lines.append(f"process_start_time_seconds {self._started_at:g}")
            return "\n".join(lines) + "\n"

    def observe_request(
        self,
        route: str,
        method: str,
        status: int,
        duration: float,
        *,
        route_type: str = "request",
        outcome: str = "ok",
    ) -> None:
        with self._lock:
            count_values = (route, method, str(status), route_type, outcome)
            self.metrics.request_count.inc(1, *count_values)
            self.metrics.request_duration.observe(duration, route, route_type)

    def observe_sse(self, route: str, terminal: str, duration: float, first_token: float | None) -> None:
        with self._lock:
            self.metrics.sse_duration.observe(duration, route, terminal)
            if first_token is not None:
                self.metrics.sse_first_token.observe(first_token, route)


registry = MetricsRegistry()


def reset_metrics() -> None:
    with registry._lock:
        registry.reset()


def render_metrics() -> str:
    with registry._lock:
        return registry.render()


def record_error(code: str) -> None:
    normalized = str(code or "unknown").strip().lower()
    if normalized not in {
        "authentication",
        "rate_limit",
        "model_not_found",
        "retrieval_error",
        "generation_error",
        "empty_response",
        "unhandled",
        "unknown",
    }:
        normalized = "unknown"
    with registry._lock:
        registry.metrics.errors.inc(1, normalized)


def set_kb_snapshot(*, generation: int | None, documents: int, chunks: int, healthy: bool) -> None:
    with registry._lock:
        # Zero is the explicit unknown/uninitialized sentinel. Leaving the
        # previous generation visible after a failed probe is more misleading
        # than reporting that no stable snapshot is currently known.
        registry.metrics.kb_generation.set(generation if generation is not None else 0)
        registry.metrics.docs_count.set(documents)
        registry.metrics.chunks_count.set(chunks)
        registry.metrics.vector_health.set(1 if healthy else 0)


def mark_cache_hit(level: str) -> None:
    with registry._lock:
        registry.metrics.cache_hits.inc(1, str(level))


def mark_cache_miss(level: str) -> None:
    with registry._lock:
        registry.metrics.cache_misses.inc(1, str(level))


def mark_llm_call(mode: str, duration: float | None = None) -> None:
    mode = str(mode or "unknown")
    with registry._lock:
        registry.metrics.llm_calls.inc(1, mode)
        if duration is not None:
            registry.metrics.llm_latency.observe(duration, mode)


def mark_upload_result(result: str) -> None:
    with registry._lock:
        registry.metrics.upload_tasks.inc(1, str(result))


def mark_auth_failure(route: str) -> None:
    with registry._lock:
        registry.metrics.auth_failures.inc(1, route)
