import time
from collections import defaultdict, deque

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

TOTAL_ORDERS = 48
RATE_LIMIT = 19
WINDOW_SECONDS = 10

app = FastAPI(title="Orders API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

idempotency_store = {}
rate_limit_store = defaultdict(deque)


def make_order(order_id: int) -> dict:
    return {
        "id": order_id,
        "item": f"Order item {order_id}",
        "quantity": 1,
        "status": "created",
    }


def rate_limited_response() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded"},
        headers={"Retry-After": str(WINDOW_SECONDS)},
    )


def check_rate_limit(x_client_id: str | None):
    if x_client_id is None:
        raise HTTPException(
            status_code=400,
            detail="X-Client-Id header is required",
        )

    now = time.time()
    timestamps = rate_limit_store[x_client_id]

    while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
        timestamps.popleft()

    if len(timestamps) >= RATE_LIMIT:
        return rate_limited_response()

    timestamps.append(now)
    return None


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
    if idempotency_key is None:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required",
        )

    rate_limit_response = check_rate_limit(x_client_id)
    if rate_limit_response is not None:
        return rate_limit_response

    if idempotency_key in idempotency_store:
        existing_order = idempotency_store[idempotency_key]
        return JSONResponse(
            status_code=200,
            content={
                "id": existing_order["id"],
                "item": existing_order["item"],
                "quantity": existing_order["quantity"],
                "status": existing_order["status"],
            },
        )

    order_id = len(idempotency_store) + 1
    order = make_order(order_id)

    idempotency_store[idempotency_key] = order

    return JSONResponse(
        status_code=201,
        content={
            "id": str(order["id"]),
            "item": order["item"],
            "quantity": order["quantity"],
            "status": order["status"],
        },
    )


@app.get("/orders")
def list_orders(
    limit: int = Query(default=10, ge=1, le=100),
    cursor: str | None = Query(default=None),
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
):
    rate_limit_response = check_rate_limit(x_client_id)
    if rate_limit_response is not None:
        return rate_limit_response

    start_id = 1

    if cursor is not None:
        try:
            start_id = int(cursor)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid cursor",
            )

    if start_id < 1:
        start_id = 1

    end_id = min(start_id + limit - 1, TOTAL_ORDERS)

    items = [
        make_order(order_id)
        for order_id in range(start_id, end_id + 1)
    ]

    next_cursor = None
    if end_id < TOTAL_ORDERS:
        next_cursor = str(end_id + 1)

    return {
        "items": items,
        "next_cursor": next_cursor,
    }
