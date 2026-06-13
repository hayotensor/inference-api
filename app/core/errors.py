from fastapi import HTTPException, status
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str
    code: str


def unauthorized(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "unauthorized", "message": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )


def forbidden(detail: str = "Permission denied") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "forbidden", "message": detail},
    )


def bad_request(code: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": code, "message": detail},
    )


def not_found(detail: str = "Not found") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "not_found", "message": detail},
    )
