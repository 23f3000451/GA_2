# main.py
import time
import json
import uuid
import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, Response, Query
from fastapi.responses import JSONResponse, PlainTextResponse

START_TIME = time.time()

# In-memory log buffer (bounded)
LOG_BUFFER: deque = deque(maxlen=2000)

# In-memory metrics counters (simple, thread-safe enough for our use)
METRICS = {
    "http_requests_total": 0,
    "http_requests_by_path": {},
}
METRICS_LOCK = asyncio.Lock()

app = FastAPI(title="Observability Demo")


def utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_log(level: str, path: str, request_id: str, extra: Optional[dict] = None):
    entry = {
        "level": level,
        "ts": utc_ts(),
        "path": path,
        "request_id": request_id,
    }
    if extra:
        entry.update(extra)
    LOG_BUFFER.append(entry)
    # Also print structured JSON to stdout (real structured logging)
    print(json.dumps(entry), flush=True)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    path = request.url.path

    # Count every request (including /metrics, /healthz, /logs/tail, /work)
    async with METRICS_LOCK:
        METRICS["http_requests_total"] += 1
        METRICS["http_requests_by_path"][path] = (
            METRICS["http_requests_by_path"].get(path, 0) + 1
        )

    response = await call_next(request)
    response.headers["x-request-id"] = request_id

    add_log(
        level="info",
        path=path,
        request_id=request_id,
        extra={
            "method": request.method,
            "status_code": response.status_code,
            "query": dict(request.query_params),
        },
    )
    return response


@app.get("/work")
async def work(n: int = Query(1, ge=0, le=1_000_000)):
    # Do "K units of work" — small CPU-ish loop, scaled down to stay fast
    total = 0
    # Keep it lightweight but real: n iterations
    for i in range(n):
        total += i
    return {"email": "student@example.com", "done": n}


@app.get("/metrics")
async def metrics():
    async with METRICS_LOCK:
        total = METRICS["http_requests_total"]
        by_path = dict(METRICS["http_requests_by_path"])

    lines = []
    lines.append("# HELP http_requests_total Total number of HTTP requests received.")
    lines.append("# TYPE http_requests_total counter")
    lines.append(f"http_requests_total {total}")

    # Per-path breakdown (also counters)
    lines.append("# HELP http_requests_by_path_total Requests per path.")
    lines.append("# TYPE http_requests_by_path_total counter")
    for p, c in sorted(by_path.items()):
        safe_path = p.replace('"', '\\"')
        lines.append(f'http_requests_by_path_total{{path="{safe_path}"}} {c}')

    body = "\n".join(lines) + "\n"
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/healthz")
async def healthz():
    uptime = max(0.0, time.time() - START_TIME)
    return {"status": "ok", "uptime_s": round(uptime, 6)}


@app.get("/logs/tail")
async def logs_tail(limit: int = Query(50, ge=1, le=2000)):
    entries = list(LOG_BUFFER)[-limit:]
    return entries


@app.get("/")
async def root():
    return {"service": "observability-demo", "endpoints": ["/work", "/metrics", "/healthz", "/logs/tail"]}
