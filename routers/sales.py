from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from db import get_session
from models import TransactionCreate, Transactions
from routers.auth import get_current_admin

router = APIRouter(
    prefix="/transactions",
    tags=["transactions"],
    responses={404: {"description": "Not found"}}
    )

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