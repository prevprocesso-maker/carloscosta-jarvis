from __future__ import annotations

import asyncio
import time
import uuid
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
from pathlib import Path
from collections import defaultdict
import threading

from .paths import get_jarvis_paths


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SpanKind(Enum):
    INTERNAL = "internal"
    SERVER = "server"
    CLIENT = "client"
    PRODUCER = "producer"
    CONSUMER = "consumer"


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    name: str
    kind: SpanKind
    start_time: float
    end_time: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict] = field(default_factory=list)
    status: str = "OK"
    status_message: str = ""

    @property
    def duration_ms(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return (time.time() - self.start_time) * 1000

    def add_event(self, name: str, attributes: Dict = None):
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {}
        })

    def set_attribute(self, key: str, value: Any):
        self.attributes[key] = value

    def set_status(self, status: str, message: str = ""):
        self.status = status
        self.status_message = message


@dataclass
class LogEntry:
    timestamp: float
    level: LogLevel
    trace_id: Optional[str]
    span_id: Optional[str]
    message: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    service: str = "jarvis"


class MetricsCollector:
    def __init__(self):
        self.counters: Dict[str, float] = defaultdict(float)
        self.gauges: Dict[str, float] = {}
        self.histograms: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def increment(self, name: str, value: float = 1.0, labels: Dict[str, str] = None):
        key = self._make_key(name, labels)
        with self._lock:
            self.counters[key] += value

    def set_gauge(self, name: str, value: float, labels: Dict[str, str] = None):
        key = self._make_key(name, labels)
        with self._lock:
            self.gauges[key] = value

    def observe(self, name: str, value: float, labels: Dict[str, str] = None):
        key = self._make_key(name, labels)
        with self._lock:
            self.histograms[key].append(value)

    def _make_key(self, name: str, labels: Dict[str, str] = None) -> str:
        if not labels:
            return name
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def get_metrics(self) -> Dict:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "gauges": dict(self.gauges),
                "histograms": {
                    k: {
                        "count": len(v),
                        "sum": sum(v),
                        "min": min(v) if v else 0,
                        "max": max(v) if v else 0,
                        "avg": sum(v) / len(v) if v else 0,
                    }
                    for k, v in self.histograms.items()
                }
            }


class Tracer:
    def __init__(self, service_name: str = "jarvis"):
        self.service_name = service_name
        self.spans: Dict[str, Span] = {}
        self.completed_spans: List[Span] = []
        self._lock = threading.Lock()

    @asynccontextmanager
    async def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        parent: Optional[Span] = None,
        attributes: Dict = None
    ):
        trace_id = parent.trace_id if parent else str(uuid.uuid4())
        span_id = str(uuid.uuid4())[:16]
        parent_span_id = parent.span_id if parent else None

        span = Span(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=name,
            kind=kind,
            start_time=time.time(),
            attributes=attributes or {},
        )

        with self._lock:
            self.spans[span_id] = span

        try:
            yield span
            span.end_time = time.time()
            span.set_status("OK")
        except Exception as e:
            span.end_time = time.time()
            span.set_status("ERROR", str(e))
            raise
        finally:
            with self._lock:
                self.spans.pop(span_id, None)
                self.completed_spans.append(span)

    def get_trace(self, trace_id: str) -> List[Span]:
        return [s for s in self.completed_spans if s.trace_id == trace_id]

    def export_spans(self, limit: int = 1000) -> List[Dict]:
        with self._lock:
            spans = self.completed_spans[-limit:]
            return [asdict(s) for s in spans]


class Logger:
    def __init__(self, service_name: str = "jarvis", level: LogLevel = LogLevel.INFO):
        self.service_name = service_name
        self.level = level
        self.entries: List[LogEntry] = []
        self._lock = threading.Lock()
        self._handlers: List[Callable[[LogEntry], None]] = []

    def add_handler(self, handler: Callable[[LogEntry], None]):
        self._handlers.append(handler)

    def _log(self, level: LogLevel, message: str, trace_id: str = None, span_id: str = None, **kwargs):
        if self._level_value(level) < self._level_value(self.level):
            return

        entry = LogEntry(
            timestamp=time.time(),
            level=level,
            trace_id=trace_id,
            span_id=span_id,
            message=message,
            attributes=kwargs,
            service=self.service_name,
        )

        with self._lock:
            self.entries.append(entry)

        for handler in self._handlers:
            try:
                handler(entry)
            except Exception:
                pass

    def _level_value(self, level: LogLevel) -> int:
        return {
            LogLevel.DEBUG: 10,
            LogLevel.INFO: 20,
            LogLevel.WARNING: 30,
            LogLevel.ERROR: 40,
            LogLevel.CRITICAL: 50,
        }[level]

    def debug(self, message: str, **kwargs):
        self._log(LogLevel.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs):
        self._log(LogLevel.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs):
        self._log(LogLevel.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs):
        self._log(LogLevel.ERROR, message, **kwargs)

    def critical(self, message: str, **kwargs):
        self._log(LogLevel.CRITICAL, message, **kwargs)

    def get_logs(self, limit: int = 1000, level: LogLevel = None) -> List[LogEntry]:
        with self._lock:
            logs = self.entries[-limit:]
            if level:
                logs = [e for e in logs if e.level == level]
            return logs


class Telemetry:
    def __init__(self, service_name: str = "jarvis"):
        self.service_name = service_name
        self.tracer = Tracer(service_name)
        self.logger = Logger(service_name)
        self.metrics = MetricsCollector()
        self._running = False

        # Setup default handlers
        self.logger.add_handler(self._console_handler)
        self.logger.add_handler(self._file_handler)

    def _console_handler(self, entry: LogEntry):
        level_colors = {
            LogLevel.DEBUG: "\033[36m",
            LogLevel.INFO: "\033[32m",
            LogLevel.WARNING: "\033[33m",
            LogLevel.ERROR: "\033[31m",
            LogLevel.CRITICAL: "\033[35m",
        }
        color = level_colors.get(entry.level, "")
        reset = "\033[0m"
        trace = f" [{entry.trace_id[:8]}]" if entry.trace_id else ""
        attrs = " ".join(f"{k}={v}" for k, v in entry.attributes.items())
        print(f"{color}[{entry.level.value}]{reset} {entry.message}{trace} {attrs}")

    def _file_handler(self, entry: LogEntry):
        try:
            paths = get_jarvis_paths()
            log_file = paths.logs / f"{entry.service}.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except Exception:
            pass

    async def start(self):
        self._running = True
        # Start metrics flush loop
        asyncio.create_task(self._flush_loop())

    async def _flush_loop(self):
        while self._running:
            await asyncio.sleep(30)  # Flush every 30s
            self._flush_metrics()

    def _flush_metrics(self):
        # Could send to Prometheus, etc.
        pass

    def get_telemetry_snapshot(self) -> Dict:
        return {
            "service": self.service_name,
            "timestamp": time.time(),
            "metrics": self.metrics.get_metrics(),
            "active_spans": len(self.tracer.spans),
            "completed_spans": len(self.tracer.completed_spans),
            "log_count": len(self.logger.entries),
        }

    async def shutdown(self):
        self._running = False
        self._flush_metrics()


# Global telemetry instance
_telemetry: Optional[Telemetry] = None


def get_telemetry(service_name: str = "jarvis") -> Telemetry:
    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry(service_name)
    return _telemetry


def init_telemetry(service_name: str = "jarvis") -> Telemetry:
    global _telemetry
    _telemetry = Telemetry(service_name)
    return _telemetry


# Decorator for automatic tracing
def trace(name: str = None, kind: SpanKind = SpanKind.INTERNAL, attributes: Dict = None):
    def decorator(func: Callable):
        if asyncio.iscoroutinefunction(func):
            async def wrapper(*args, **kwargs):
                telemetry = get_telemetry()
                async with telemetry.tracer.start_span(
                    name or func.__name__, kind, attributes=attributes
                ) as span:
                    return await func(*args, **kwargs)
            return wrapper
        else:
            def wrapper(*args, **kwargs):
                telemetry = get_telemetry()
                # Sync functions - create span manually
                trace_id = str(uuid.uuid4())
                span_id = str(uuid.uuid4())[:16]
                span = Span(
                    trace_id=trace_id,
                    span_id=span_id,
                    name=name or func.__name__,
                    kind=kind,
                    start_time=time.time(),
                    attributes=attributes or {},
                )
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    span.set_status("ERROR", str(e))
                    raise
                finally:
                    span.end_time = time.time()
                    span.set_status("OK")
            return wrapper
    return decorator


# Context propagation helpers
def get_current_span() -> Optional[Span]:
    telemetry = get_telemetry()
    # In real impl, would use contextvars
    return None


def inject_context(carrier: Dict[str, str]):
    """Inject trace context into carrier (headers, etc.)"""
    span = get_current_span()
    if span:
        carrier["traceparent"] = f"00-{span.trace_id}-{span.span_id}-01"


def extract_context(carrier: Dict[str, str]) -> Optional[Span]:
    """Extract trace context from carrier"""
    traceparent = carrier.get("traceparent")
    if traceparent:
        parts = traceparent.split("-")
        if len(parts) >= 3:
            trace_id = parts[1]
            span_id = parts[2]
            # Return a minimal span for context
            return Span(
                trace_id=trace_id,
                span_id=span_id,
                name="extracted",
                kind=SpanKind.INTERNAL,
                start_time=time.time(),
            )
    return None