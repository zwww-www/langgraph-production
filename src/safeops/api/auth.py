import secrets

from fastapi import Header, HTTPException, Request

from safeops.domain.models import Principal


async def principal(request: Request, authorization: str = Header(default="")) -> Principal:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "bearer authentication required")
    token = authorization.removeprefix("Bearer ")
    for expected, identity in request.app.state.runtime.settings.api_tokens.items():
        if secrets.compare_digest(token, expected):
            return Principal.model_validate(identity)
    raise HTTPException(401, "invalid token")
