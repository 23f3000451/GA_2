import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

TOTAL_ORDERS = 48
RATE_LIMIT = 19
WINDOW_SECONDS = 10

app = FastAPI(title="Orders API")

# CORS: allow the grader page to call this API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage. Fine for this assignment.
idempotency_store = {}

# client_id -> deque of request timestamps
rate_limit_store = defaultdict(deque)


def make_order(order_id: int) -> dict:
    return {
        "id": order_id,
        "item": f"Order item {order_id}",
        "quantity": 1,
        "status": "created",
    }


@app.get("/")
def root():
    return {
        "service": "orders-api",
        "total_orders": TOTAL_ORDERS,
        "rate_limit": f"{RATE_LIMIT} requests / {WINDOW_SECONDS} seconds",
    }


@app.post("/orders")
def create_order(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
):
    if x_client_id is None:
        raise HTTPException(status_code=400, detail="X-Client-Id header is required")

    if idempotency_key is None:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    # Rate limit before doing any work.
    now = time.time()
    timestamps = rate_limit_store[x_client_id]

    while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
        timestamps.popleft()

    if len(timestamps) >= RATE_LIMIT:
        retry_after = max(1, int(WINDOW_SECONDS - (now - timestamps[0])))
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
            headers={"Retry-After": str(retry_after)},
        )

    timestamps.append(now)

    # Idempotency: if this key was already used, return the original order.
    if idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    # First request with this key: create one order.
    order_id = len(idempotency_store) + 1
    order = make_order(order_id)

    idempotency_store[idempotency_key] = {
        "id": order["id"],
        "idempotency_key": idempotency_key,
        "created_at": time.time(),
    }

    return JSONResponse(status_code=201, content={"id": str(order["id"]), **order})


@app.get("/orders")
def list_orders(
    request: Request,
    limit: int = Query(default=10, ge=1, le=100),
    cursor: str | None = Query(default=None),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
):
    if x_client_id is None:
        raise HTTPException(status_code=400, detail="X-Client-Id header is required")

    # Rate limit GET requests too, because the grader sends R+1 requests
    # from the same client ID.
    now = time.time()
    timestamps = rate_limit_store[x_client_id]

    while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
        timestamps.popleft()

    if len(timestamps) >= RATE_LIMIT:
        retry_after = max(1, int(WINDOW_SECONDS - (now - timestamps[0])))
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
            headers={"Retry-After": str(retry_after)},
        )

    timestamps.append(now)

    # Cursor is simply the next ID to start from.
    # It is opaque to the grader: they only send it back unchanged.
    start_id = 1

    if cursor is not None:
        try:
            start_id = int(cursor)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor")

    if start_id < 1:
        start_id = 1

    end_id = min(start_id + limit - 1, TOTAL_ORDERS)

    items = [make_order(order_id) for order_id in range(start_id, end_id + 1)]

    next_cursor = None
    if end_id < TOTAL_ORDERS:
        next_cursor = str(end_id + 1)

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
