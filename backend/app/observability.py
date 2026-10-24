"""Small Prometheus text exporter with bounded HTTP label cardinality."""

from collections import defaultdict
from threading import Lock

BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0)


def label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class HttpMetrics:
    def __init__(self) -> None:
        self.lock = Lock()
        self.requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self.durations: dict[tuple[str, str], list[float]] = defaultdict(
            lambda: [0.0] * (len(BUCKETS) + 2)
        )

    def record(self, method: str, route: str, status: int, duration: float) -> None:
        method = method if method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} else "OTHER"
        route = route if route.startswith("/") else "unmatched"
        key = (method, route)
        with self.lock:
            self.requests[method, route, f"{status // 100}xx"] += 1
            values = self.durations[key]
            for index, bound in enumerate(BUCKETS):
                if duration <= bound:
                    values[index] += 1
            values[-2] += duration
            values[-1] += 1

    def render(self) -> str:
        lines = [
            "# HELP ma_soi_http_requests_total HTTP requests by route template and status class",
            "# TYPE ma_soi_http_requests_total counter",
        ]
        with self.lock:
            requests = dict(self.requests)
            durations = {key: list(value) for key, value in self.durations.items()}
        for (method, route, status_class), count in sorted(requests.items()):
            lines.append(f'ma_soi_http_requests_total{{method="{label(method)}",route="{label(route)}",status_class="{status_class}"}} {count}')
        lines += [
            "# HELP ma_soi_http_request_duration_seconds HTTP request duration",
            "# TYPE ma_soi_http_request_duration_seconds histogram",
        ]
        for (method, route), values in sorted(durations.items()):
            labels = f'method="{label(method)}",route="{label(route)}"'
            for index, bound in enumerate(BUCKETS):
                lines.append(f'ma_soi_http_request_duration_seconds_bucket{{{labels},le="{bound:g}"}} {int(values[index])}')
            lines.append(f'ma_soi_http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {int(values[-1])}')
            lines.append(f'ma_soi_http_request_duration_seconds_sum{{{labels}}} {values[-2]:.6f}')
            lines.append(f'ma_soi_http_request_duration_seconds_count{{{labels}}} {int(values[-1])}')
        return "\n".join(lines) + "\n"


http_metrics = HttpMetrics()


def gauge(name: str, help_text: str, value: int | float) -> str:
    return f"# HELP {name} {help_text}\n# TYPE {name} gauge\n{name} {value}\n"
