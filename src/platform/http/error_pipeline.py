import functools
import inspect
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Tuple, Type, Union

from fastapi import HTTPException

from src.platform.http.base_controller import APIError, APIResponse, PassThrough
from src.platform.http.errors import domain_failure
from src.platform.security.failure_text import failure_detail
from src.platform.security.user import AccountType

Detail = Callable[[Exception, bool], str]
ErrorKinds = Union[Type[Exception], Tuple[Type[Exception], ...]]


@dataclass(frozen=True)
class Fail:
    code: Union[str, Callable[[Exception], str]]
    message: Union[None, str, Callable[[Exception], str]] = None
    status: Union[None, int, Callable[[Exception], int]] = None
    detail: Optional[Detail] = None
    extra: Optional[Callable[[Exception], Mapping]] = None


ErrorTable = Mapping[ErrorKinds, Fail]


def raw_detail(exc: Exception, admin: bool) -> str:
    return str(exc)


def _value(value, exc: Exception):
    return value(exc) if callable(value) else value


def _message(fail: Fail, exc: Exception, admin: Callable[[], bool]) -> str:
    message = _value(fail.message, exc)
    if fail.detail is None:
        return str(exc) if message is None else message
    detail = fail.detail(exc, admin())
    return detail if message is None else f"{message}: {detail}"


def translate(controller, exc: Exception, table: ErrorTable, fallback: Optional[Fail], admin: Callable[[], bool],
              soft: bool = False):
    failure = domain_failure(exc)
    if failure is not None:
        controller.logger.info(f"Request refused: {failure.code}")
        if soft:
            return APIResponse(success=False, error=failure.code, message=failure.message)
        raise HTTPException(status_code=failure.status_code, detail=failure.detail)
    fail = next((entry for kinds, entry in table.items() if isinstance(exc, kinds)), None)
    if fail is None:
        if fallback is None:
            raise exc
        fail = fallback
        message = _message(fail, exc, admin)
        controller.logger.exception(f"{_value(fail.code, exc)}: {message}")
    else:
        message = _message(fail, exc, admin)
        controller.logger.error(f"API Error: {_value(fail.code, exc)} - {message}")
    code = _value(fail.code, exc)
    status = _value(fail.status, exc)
    if status is None:
        return APIResponse(success=False, error=code, message=message)
    body = APIError(error=code, message=message).model_dump()
    if fail.extra is not None:
        body = {**(fail.extra(exc) or {}), **body}
    raise HTTPException(status_code=status, detail=body)


def guarded(table: Optional[ErrorTable] = None, fallback: Optional[Fail] = None, admin: Optional[str] = None,
            soft: bool = False):
    entries = dict(table or {})

    def decorate(fn):
        signature = inspect.signature(fn)

        def admin_of(args, kwargs) -> Callable[[], bool]:
            def resolve() -> bool:
                if admin is None:
                    return False
                user = signature.bind(*args, **kwargs).arguments[admin]
                return getattr(user, "account_type", None) == AccountType.ADMIN
            return resolve

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except (PassThrough, HTTPException):
                    raise
                except Exception as exc:
                    return translate(args[0], exc, entries, fallback, admin_of(args, kwargs), soft)
        else:
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except (PassThrough, HTTPException):
                    raise
                except Exception as exc:
                    return translate(args[0], exc, entries, fallback, admin_of(args, kwargs), soft)
        return wrapper

    return decorate


def soft_guarded(code: str, message: str, admin: Optional[str] = None):
    return guarded({ValueError: Fail(code)}, Fail(code, message, detail=failure_detail), admin=admin, soft=True)
