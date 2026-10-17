import time
import uuid
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ============================================================
# YOUR ASSIGNED VALUES — EDIT THESE
# ============================================================
EMAIL = "23f3000451@ds.study.iitm.ac.in"                 # EDIT: your real logged-in email
EXAM_PAGE_ORIGIN = "https://exam.sanand.workers.dev"    # EDIT: output of `location.origin` in the exam page

ALLOWED_ORIGIN = "https://app-qnu8sj.example.com"   # do NOT change (assigned)
BUCKET_B = 14
WINDOW_SECONDS = 10.0

app = FastAPI()


# ============================================================
# Middleware 3 — Per-client rate limiting (pure ASGI, outermost)
# ============================================================
class RateLimitMiddleware:
    def __init__(self, app, limit: int, window: float):
        self.app = app
        self.limit = limit
        self.window = window
        self.hits: Dict[str, Deque[float]] = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # Only rate-limit GET /ping
        if scope.get("path") != "/ping" or scope.get("method") != "GET":
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers") or [])
        cid_b = headers.get(b"x-client-id")
        client_id = cid_b.decode() if cid_b else "anonymous"

        now = time.monotonic()
        bucket = self.hits[client_id]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()

        if len(bucket) >= self.limit:
            retry_after = max(0.0, self.window - (now - bucket[0]))
            resp_headers = [
                (b"content-type", b"application/json"),
                (b"retry-after", str(int(retry_after) + 1).encode()),
            ]
            # Preserve CORS on 429 for the allowed origins
            origin_b = headers.get(b"origin")
            if origin_b:
                origin = origin_b.decode()
                if origin in (ALLOWED_ORIGIN, EXAM_PAGE_ORIGIN):
                    resp_headers.append((b"access-control-allow-origin", origin.encode()))
                    resp_headers.append((b"access-control-expose-headers", b"X-Request-ID"))
            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": resp_headers,
            })
            await send({"type": "http.response.body", "body": b'{"detail":"Too Many Requests"}'})
            return

        bucket.append(now)
        await self.app(scope, receive, send)


# ============================================================
# Middleware 1 — Request-context propagator (pure ASGI)
# ============================================================
class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers") or [])
        rid_b = headers.get(b"x-request-id")
        request_id = rid_b.decode() if rid_b else str(uuid.uuid4())

        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers") or [])
                hdrs.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": hdrs}
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ============================================================
# Middleware 2 — Scoped CORS (pure ASGI, no wildcards ever)
#   - Explicitly allows ALLOWED_ORIGIN and EXAM_PAGE_ORIGIN
#   - Handles preflight OPTIONS
#   - Silently drops ACAO for any other origin
# ============================================================
class ScopedCORSMiddleware:
    def __init__(self, app, allowed_origins):
        self.app = app
        self.allowed_origins = set(allowed_origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers") or [])
        origin_b = headers.get(b"origin")
        origin = origin_b.decode() if origin_b else None
        origin_allowed = origin is not None and origin in self.allowed_origins

        # Preflight
        if scope.get("method") == "OPTIONS" and origin is not None:
            resp_headers = [
                (b"vary", b"Origin"),
                (b"access-control-allow-methods", b"GET, OPTIONS"),
                (b"access-control-allow-headers", b"*"),
                (b"access-control-expose-headers", b"X-Request-ID"),
                (b"access-control-max-age", b"600"),
            ]
            if origin_allowed:
                resp_headers.append((b"access-control-allow-origin", origin.encode()))
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": resp_headers,
            })
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers") or [])
                hdrs.append((b"vary", b"Origin"))
                if origin_allowed:
                    hdrs.append((b"access-control-allow-origin", origin.encode()))
                    hdrs.append((b"access-control-expose-headers", b"X-Request-ID"))
                message = {**message, "headers": hdrs}
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ============================================================
# Register middleware — order matters (last added = outermost)
#   Request context wraps the route; CORS wraps that; rate-limit outermost.
# ============================================================
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    ScopedCORSMiddleware,
    allowed_origins=[ALLOWED_ORIGIN, EXAM_PAGE_ORIGIN],
)
app.add_middleware(RateLimitMiddleware, limit=BUCKET_B, window=WINDOW_SECONDS)


# ============================================================
# Routes
# ============================================================
@app.get("/ping")
async def ping(request: Request):
    request_id = request.scope["state"]["request_id"]
    return {"email": EMAIL, "request_id": request_id}


@app.get("/")
def root():
    return {"ok": True, "hint": "GET /ping"}
