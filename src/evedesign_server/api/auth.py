from functools import lru_cache
from typing import Annotated
from uuid import UUID
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2AuthorizationCodeBearer
import jwt
from jwt import PyJWKClient
from pydantic import BaseModel, ValidationError
from loguru import logger

from evedesign_server.config import Settings

s = Settings()

auth_scheme = OAuth2AuthorizationCodeBearer(tokenUrl="token", authorizationUrl="authorization")
TokenDep = Annotated[str, Depends(auth_scheme)]

@lru_cache
def get_jwks_client() -> PyJWKClient:
    if s.auth_jwks_url is None:
        raise ValueError("No JWKS URL configured")

    return PyJWKClient(
        s.auth_jwks_url, cache_jwk_set=True, lifespan=300, timeout=5
    )

JWKSClientDep = Annotated[PyJWKClient, Depends(get_jwks_client)]

unauthorized_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

class UserIn(BaseModel):
    id: UUID
    access_token: str


def get_current_user(token: TokenDep, jwks_client: JWKSClientDep) -> UserIn:
    """Validate token (sync function is run in threadpool by FastAPI automatically)"""
    if s.auth_provider is None or s.auth_algorithm is None or s.auth_audience is None or s.auth_issuer is None:
        raise ValueError("Invalid configuration")

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=[s.auth_algorithm],
            audience=s.auth_audience,
            issuer=s.auth_issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=30
        )

        if s.auth_provider == "entra":
            # sub is per app in Entra, use oid for global user ID in tenant (multi-tenant would also need tid)
            if "scp" not in claims:
                raise jwt.InvalidTokenError("Not a delegated user token")
            user_id = claims["oid"]
        else:
            # for now only generically cover supabase
            if claims.get("is_anonymous"):
                raise jwt.InvalidTokenError("Anonymous users are not allowed")

            # if this is not a UUID, need to further update
            user_id = claims["sub"]

        return UserIn(id=user_id, access_token=token)
    except jwt.PyJWKClientConnectionError as exc:
        logger.warning("Auth provider unreachable at {}: {}", s.auth_jwks_url, exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Auth provider unavailable") from exc
    except jwt.ExpiredSignatureError as exc:
        logger.debug("Token rejected: expired")
        raise unauthorized_exception from exc
    except (jwt.PyJWTError, ValidationError, KeyError) as exc:
        logger.info("Token rejected: {}: {}", type(exc).__name__, exc)
        raise unauthorized_exception from exc

CurrentUserDep = Annotated[UserIn, Depends(get_current_user)]
