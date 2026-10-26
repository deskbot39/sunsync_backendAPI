from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, func, select
from db import get_session
from models import TransactionCreate, Transactions
from routers.auth import get_current_admin

# The kiosk runs in the Philippines (UTC+8, no daylight saving), so sales days follow Manila
# time no matter where the admin's browser is.
SALES_TIMEZONE = timezone(timedelta(hours=8))
SALES_CHART_DAYS = 7

router = APIRouter(
    prefix="/transactions",
    tags=["transactions"],
    responses={404: {"description": "Not found"}}
    )

@router.get("/summary")
def sales_summary(session: Session = Depends(get_session),
                  current_admin=Depends(get_current_admin)):
    now_local = datetime.now(SALES_TIMEZONE)
    today = now_local.date()
    first_chart_day = today - timedelta(days=SALES_CHART_DAYS - 1)
    first_day = min(first_chart_day, today.replace(day=1))
    since_utc = (datetime.combine(first_day, datetime.min.time(), SALES_TIMEZONE)
                 .astimezone(timezone.utc).replace(tzinfo=None))

    daily = {first_chart_day + timedelta(days=n): Decimal("0") for n in range(SALES_CHART_DAYS)}
    today_total = month_total = Decimal("0")
    for timestamp, amount in session.exec(
            select(Transactions.timestamp, Transactions.total_amount)
            .where(Transactions.timestamp >= since_utc)).all():
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        day = timestamp.astimezone(SALES_TIMEZONE).date()
        amount = Decimal(amount or 0)
        if day == today:
            today_total += amount
        if (day.year, day.month) == (today.year, today.month):
            month_total += amount
        if day in daily:
            daily[day] += amount

    count = session.exec(select(func.count()).select_from(Transactions)).one()
    return {
        "timezone": "Asia/Manila",
        "today_total": today_total,
        "month_total": month_total,
        "transaction_count": count,
        "daily": [{"date": day.isoformat(), "total": total} for day, total in daily.items()],
    }

@router.get("/", response_model=list[Transactions])
def list_transactions(
        limit: int = Query(default=100, ge=1, le=500),
        session: Session = Depends(get_session),
        current_admin=Depends(get_current_admin)):
    return session.exec(
        select(Transactions)
        .order_by(Transactions.timestamp.desc())
        .limit(limit)
    ).all()

@router.get("/{id}", response_model=Transactions)
def get_transaction(id: int, session: Session = Depends(get_session),
                    current_admin=Depends(get_current_admin)):
    transaction = session.get(Transactions, id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction

@router.post("/", response_model=Transactions)
def create_transaction(transaction: TransactionCreate,
                       session: Session = Depends(get_session),
                       current_admin=Depends(get_current_admin)):
    transaction_record = Transactions(
        device_id=transaction.device_id,
        coin_denomination=transaction.coin_denomination,
        coins_count=transaction.coins_count,
        total_amount=Decimal(transaction.coin_denomination * transaction.coins_count),
        transaction_type=transaction.transaction_type,
    )
    session.add(transaction_record)
    session.commit()
    session.refresh(transaction_record)
    return transaction_record