import os
from datetime import date

from fastapi import Depends, FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import service
from .db import connect, init_db
from .errors import ApiError
from .proration import prorate_plan_change


class LineItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    amount_minor: int = Field(gt=0)


class InvoiceIn(BaseModel):
    customer_id: str = Field(min_length=1, max_length=100)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    line_items: list[LineItemIn] = Field(min_length=1, max_length=100)


class PaymentIn(BaseModel):
    amount_minor: int = Field(gt=0)


class CreditIn(BaseModel):
    amount_minor: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=200)


class ProrationIn(BaseModel):
    old_price_minor: int = Field(ge=0)
    new_price_minor: int = Field(ge=0)
    period_start: date
    period_end: date
    change_on: date


def _require_key(value: str | None) -> str:
    if value is None or not (1 <= len(value) <= 255):
        raise ApiError(
            400,
            "idempotency_key_required",
            "This endpoint moves money, so it needs an Idempotency-Key header (1 to 255 characters).",
            "Generate a unique key (a UUID works) once per logical request and resend it on every retry.",
        )
    return value


def _respond(outcome: service.Outcome) -> JSONResponse:
    headers = {"Idempotent-Replayed": "true"} if outcome.replayed else {}
    return JSONResponse(outcome.body, status_code=outcome.status, headers=headers)


def create_app(db_path: str | None = None, busy_timeout_s: float = 5.0) -> FastAPI:
    path = db_path or os.environ.get("BILLING_DB", "billing.db")
    init_db(path)
    app = FastAPI(title="billing-core", version="0.1.0")

    def get_conn():
        conn = connect(path, busy_timeout_s)
        try:
            yield conn
        finally:
            conn.close()

    @app.exception_handler(ApiError)
    def handle_api_error(_request, exc: ApiError):
        return JSONResponse(exc.to_body(), status_code=exc.status)

    @app.post("/invoices", status_code=201)
    def create_invoice(
        body: InvoiceIn,
        idempotency_key: str | None = Header(default=None),
        conn=Depends(get_conn),
    ):
        key = _require_key(idempotency_key)
        data = body.model_dump()
        outcome = service.execute(
            conn, "POST /invoices", key, data, service.create_invoice(data)
        )
        return _respond(outcome)

    @app.get("/invoices/{invoice_id}")
    def get_invoice(invoice_id: str, conn=Depends(get_conn)):
        return service.summarize(conn, invoice_id)

    @app.post("/invoices/{invoice_id}/payments", status_code=201)
    def pay_invoice(
        invoice_id: str,
        body: PaymentIn,
        idempotency_key: str | None = Header(default=None),
        conn=Depends(get_conn),
    ):
        key = _require_key(idempotency_key)
        outcome = service.execute(
            conn,
            f"POST /invoices/{invoice_id}/payments",
            key,
            body.model_dump(),
            service.apply_payment(invoice_id, body.amount_minor, key),
        )
        return _respond(outcome)

    @app.post("/invoices/{invoice_id}/credits", status_code=201)
    def credit_invoice(
        invoice_id: str,
        body: CreditIn,
        idempotency_key: str | None = Header(default=None),
        conn=Depends(get_conn),
    ):
        key = _require_key(idempotency_key)
        outcome = service.execute(
            conn,
            f"POST /invoices/{invoice_id}/credits",
            key,
            body.model_dump(),
            service.apply_credit(invoice_id, body.amount_minor, body.reason, key),
        )
        return _respond(outcome)

    @app.post("/proration-preview")
    def proration_preview(body: ProrationIn):
        try:
            result = prorate_plan_change(
                body.old_price_minor,
                body.new_price_minor,
                body.period_start,
                body.period_end,
                body.change_on,
            )
        except ValueError as exc:
            raise ApiError(
                422,
                "invalid_period",
                str(exc),
                "Use period_start < period_end and a change_on date inside that period.",
            )
        return {
            "days_total": result.days_total,
            "days_remaining": result.days_remaining,
            "credit_minor": result.credit_minor,
            "charge_minor": result.charge_minor,
            "net_minor": result.net_minor,
        }

    return app
