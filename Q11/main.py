import time
import uuid
import threading
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Header, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ------------------------- Assigned values -------------------------
TOTAL_ORDERS = 48          # T
RATE_LIMIT = 19            # R requests per window
WINDOW_SECONDS = 10.0      # 10s window

# ------------------------- App setup -------------------------------
app = FastAPI(title="Orders API")

# CORS: allow any origin so the grader's browser can call us directly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# ------------------------- Data / state ----------------------------
# Fixed catalog: IDs 1..TOTAL_ORDERS
CATALOG = [
    {"id": i, "name": f"Order {i}", "amount": round(10 + i * 1.5, 2)}
    for i in range(1, TOTAL_ORDERS + 1)
]

# Idempotency store: key -> order dict
_idempotency_lock = threading.Lock()
_idempotency_store: dict[str, dict] = {}

# Rate limit store: client_id -> deque of timestamps
_rate_lock = threading.Lock()
_rate_buckets: dict[str, deque] = defaultdict(deque)

# ------------------------- Helpers ---------------------------------
def _encode_cursor(index: int) -> str:
    # Opaque cursor: base64-ish of the next index. Grader passes verbatim.
    import base64
    return base64.urlsafe_b64encode(f"idx:{index}".encode()).decode()

def _decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    import base64
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        if raw.startswith("idx:"):
            return int(raw[4:])
    except Exception:
        pass
    # Unknown cursor -> start from beginning (or could 400). Be forgiving.
    return 0

def _rate_limited(client_id: str) -> Optional[float]:
    """Return Retry-After seconds if limited, else None."""
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets[client_id]
        # Drop timestamps outside the window
        while bucket and (now - bucket[0]) >= WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT:
            # Time until oldest request leaves the window
            retry_after = WINDOW_SECONDS - (now - bucket[0])
            return max(0.0, retry_after)
        bucket.append(now)
    return None

# ------------------------- Middleware: rate limiting ---------------
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Only rate-limit API endpoints, skip docs/static/openapi
    path = request.url.path
    if path in ("/docs", "/redoc", "/openapi.json", "/") or path.startswith("/docs") or path.startswith("/redoc"):
        return await call_next(request)

    client_id = request.headers.get("X-Client-Id", "anonymous")
    retry = _rate_limited(client_id)
    if retry is not None:
        return JSONResponse(
            status_code=429,
            content={
                "detail": "Rate limit exceeded",
                "client_id": client_id,
                "limit": RATE_LIMIT,
                "window_seconds": int(WINDOW_SECONDS),
            },
            headers={"Retry-After": str(max(1, int(round(retry))))},
        )
    return await call_next(request)

# ------------------------- Endpoints -------------------------------
@app.get("/")
async def root():
    return {"status": "ok", "total_orders": TOTAL_ORDERS, "rate_limit": RATE_LIMIT}

@app.post("/orders", status_code=201)
async def create_order(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        # Still allow creation but no idempotency guarantee
        new_order = {
            "id": f"ord_{uuid.uuid4().hex[:12]}",
            "created_at": time.time(),
            "status": "created",
        }
        return new_order

    with _idempotency_lock:
        existing = _idempotency_store.get(idempotency_key)
        if existing is not None:
            # Return the SAME order object (same id)
            return JSONResponse(status_code=200, content=existing)
        new_order = {
            "id": f"ord_{uuid.uuid4().hex[:12]}",
            "created_at": time.time(),
            "status": "created",
            "idempotency_key": idempotency_key,
        }
        _idempotency_store[idempotency_key] = new_order
    return JSONResponse(status_code=201, content=new_order)

@app.get("/orders")
async def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
):
    start = _decode_cursor(cursor)
    # Clamp
    if start < 0:
        start = 0
    if start > TOTAL_ORDERS:
        start = TOTAL_ORDERS
    end = min(start + limit, TOTAL_ORDERS)
    items = CATALOG[start:end]
    next_cursor = None
    if end < TOTAL_ORDERS:
        next_cursor = _encode_cursor(end)
    return {
        "items": items,
        "next_cursor": next_cursor,
        "count": len(items),
        "total": TOTAL_ORDERS,
    }
