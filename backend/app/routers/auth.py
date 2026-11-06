from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.models import User
from app.routers._deps import account_service
from app.schemas.auth import AuthResponse, LoginRequest, RefreshRequest, TokenPair
from app.schemas.errors import ERRORS
from app.schemas.users import RegisterRequest, UserProfile
from app.security.principals import get_user_principal
from app.services.accounts import AccountService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthResponse,
    responses=ERRORS(409, 422),
    summary="Create a user",
)
async def register(
    body: RegisterRequest, request: Request, svc: AccountService = Depends(account_service)
) -> AuthResponse:
    user, tokens = await svc.register(body, request.headers.get("user-agent"))
    return AuthResponse(user=UserProfile.model_validate(user), tokens=tokens)


@router.post(
    "/login",
    response_model=AuthResponse,
    responses=ERRORS(401, 422, 429),
    summary="Username + password login",
)
async def login(
    body: LoginRequest, request: Request, svc: AccountService = Depends(account_service)
) -> AuthResponse:
    user, tokens = await svc.login(body.username, body.password, request.headers.get("user-agent"))
    return AuthResponse(user=UserProfile.model_validate(user), tokens=tokens)


@router.post("/refresh", response_model=TokenPair, responses=ERRORS(401, 422), summary="Rotate tokens")
async def refresh(
    body: RefreshRequest, request: Request, svc: AccountService = Depends(account_service)
) -> TokenPair:
    return await svc.refresh(body.refresh_token, request.headers.get("user-agent"))


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=ERRORS(401, 422),
    summary="Revoke the refresh token family",
)
async def logout(
    body: RefreshRequest,
    user: User = Depends(get_user_principal),
    svc: AccountService = Depends(account_service),
) -> Response:
    await svc.logout(user, body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
