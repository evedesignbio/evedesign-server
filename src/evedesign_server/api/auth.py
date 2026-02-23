# parts of implementation following https://github.com/AtticusZeller/fastapi_supabase_template/blob/main/backend/app/core/auth.py
"""
MIT License

Copyright (c) 2024 Atticus Zeller

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from typing import Annotated
import os
from uuid import UUID
from fastapi import Depends, HTTPException # status
from fastapi.security import OAuth2PasswordBearer
from supabase import AsyncClient, AsyncClientOptions, acreate_client
from supabase_auth.errors import AuthInvalidJwtError
from pydantic import BaseModel

auth_scheme = OAuth2PasswordBearer(tokenUrl="token")

SUPABASE_URL_ENV_VAR = "DESIGNSERVER_SUPABASE_URL"
SUPABASE_ANON_KEY_ENV_VAR = "DESIGNSERVER_SUPABASE_ANON_KEY"

async def get_supabase_client() -> AsyncClient:
    """for validation access_token init at life span event"""
    supabase_client = await acreate_client(
        os.environ.get(SUPABASE_URL_ENV_VAR),
        os.environ.get(SUPABASE_ANON_KEY_ENV_VAR),
        options=AsyncClientOptions(
            postgrest_client_timeout=10, storage_client_timeout=10
        ),
    )
    if not supabase_client:
        raise HTTPException(
            status_code=500, detail="Super client not initialized"
        )

    return supabase_client

SupabaseClient = Annotated[AsyncClient, Depends(get_supabase_client)]
TokenDep = Annotated[str, Depends(auth_scheme)]


class UserIn(BaseModel):
    sub: UUID
    access_token: str


async def get_current_user(token: TokenDep, super_client: SupabaseClient) -> UserIn:
    """get current user from token and  validate same time"""
    try:
        resp = await super_client.auth.get_claims(jwt=token)
    except AuthInvalidJwtError:
        raise HTTPException(
            status_code=401, detail="Invalid credentials"
        )

    if not resp:
        raise HTTPException(
            status_code=404, detail="User not found"
        )

    claims = resp["claims"]
    if not claims.get(
        "user_metadata", {"email_verified": False}
    ).get(
        "email_verified"
    ):
        raise HTTPException(
            status_code=401, detail="Account not activated"
        )

    return UserIn(
        sub=claims["sub"],
        access_token=token
    )

CurrentUserDep = Annotated[UserIn, Depends(get_current_user)]
