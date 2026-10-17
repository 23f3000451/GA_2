import time
import uuid
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# ============================================================
# YOUR ASSIGNED VALUES — EDIT THESE TWO
# ============================================================
EMAIL = "your.login@example.com"                 # <- your logged-in email
EXAM_PAGE_ORIGIN = "https://exam.example.com"    # <- the exam page origin shown in your browser

ALLOWED_ORIGIN = "https://app-qnu8sj.example.com"
BUCKET_B = 14
WINDOW_SECONDS = 10.0

app = FastAPI()

# ============================================================
# Middleware 3 — Per-client rate limiting (pure ASGI)
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

        # only rate-limit GET /ping
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
            origin_b = headers.get(b"origin")
            if origin_b and origin_b.decode() in (ALLOWED_ORIGIN, EXAM_PAGE_ORIGIN):
                resp_headers.append((b"access-control-allow-origin", origin_b))
            await send({"type": "http.response.start", "status": 429, "headers": resp_headers})
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
# Middleware 2 — Scoped CORS
# ============================================================
# Order: last added = outermost. We want rate limiter outermost so its
# own 429 still gets CORS (handled manually above), then CORS, then
# request-context wrapping the app.
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN, EXAM_PAGE_ORIGIN],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)
app.add_middleware(RateLimitMiddleware, limit=BUCKET_B, window=WINDOW_SECONDS)

# ============================================================
# Route
# ============================================================
@app.get("/ping")
async def ping(request: Request):
    request_id = request.scope["state"]["request_id"]
    return {"email": EMAIL, "request_id": request_id}


@app.get("/")
def root():
    return {"ok": True, "hint": "GET /ping"}
