from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import jwt
from pwdlib import PasswordHash
from sqlmodel import Session, select

from config import SECRET_KEY
from db import get_session
from models import Admin, PasswordChangeRequest
from observability import logger
from rate_limits import enforce_rate_limit

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses={404: {"description": "Not found"}})

pwd_context = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
optional_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="auth/login", auto_error=False
)

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, 
                         expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=30)

    to_encode.update({"iat": now, "exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")
    return encoded_jwt

def verify_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub", "ver"]},
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_current_admin(token: str = Depends(oauth2_scheme),
                     session: Session = Depends(get_session)) -> Admin:
    payload = verify_access_token(token)
    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = session.exec(select(Admin).where(Admin.username == username)).first()
    if (not user or not user.is_active or
            type(payload.get("ver")) is not int or
            payload["ver"] != user.token_version):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive user or invalidated token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

def get_optional_admin(token: str | None = Depends(optional_oauth2_scheme),
                       session: Session = Depends(get_session)) -> Admin | None:
    if token is None:
        return None
    return get_current_admin(token, session)

@router.post("/login")
def login_access_token(request: Request,
                       form_data: OAuth2PasswordRequestForm = Depends(),
                       session: Session = Depends(get_session)):
    enforce_rate_limit(request, "login_ip", limit=20, window_seconds=300)
    enforce_rate_limit(
        request,
        "login",
        form_data.username.strip().lower(),
        limit=5,
        window_seconds=300,
    )
    user = session.exec(select(Admin).where(
        Admin.username == form_data.username)).first()
    if (not user or not user.is_active or
            not verify_password(form_data.password, user.password_hash)):
        logger.warning(
            "Admin login rejected",
            extra={"event": "admin_login_failed", "scope": "login"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(
        data={"sub": user.username, "ver": user.token_version}
    )
    logger.info(
        "Admin login succeeded",
        extra={"event": "admin_login_succeeded", "actor": user.username},
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/logout")
def logout(current_user: Admin = Depends(get_current_admin),
           session: Session = Depends(get_session)):
    # Bumping the version revokes every token issued to this admin, including the one used
    # here, so a copied or leaked token stops working as soon as the admin logs out.
    current_user.token_version += 1
    session.add(current_user)
    session.commit()
    logger.info(
        "Admin logged out",
        extra={"event": "admin_logout", "actor": current_user.username},
    )
    return {"message": "Logged out"}

@router.patch("/change-password")
def change_password(password_change: PasswordChangeRequest,
                          current_user: Admin = Depends(get_current_admin),
                          session: Session = Depends(get_session)):
    if not verify_password(password_change.current_password,
                           current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    current_user.password_hash = hash_password(password_change.new_password)
    current_user.token_version += 1
    session.add(current_user)
    session.commit()
    logger.info(
        "Admin password changed",
        extra={"event": "admin_password_changed", "actor": current_user.username},
    )
    return {"message": "Password changed successfully"}
