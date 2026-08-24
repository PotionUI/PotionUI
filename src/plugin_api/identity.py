"""Who is calling.

`get_current_active_user` is a FastAPI dependency: declare it on a route and the
request arrives with an authenticated `User`, or never arrives at all.

    @router.get("/things")
    async def list_things(user: User = Depends(get_current_active_user)):
        ...

`get_current_admin_user` is the same, narrowed to administrators: the request
either arrives with an admin `User` or is rejected with 403. Use it to gate a
route that manages global, cross-user state.

    @router.post("/dangerous")
    async def do_it(user: User = Depends(get_current_admin_user)):
        ...

`authenticate_websocket_token` is the WebSocket counterpart. WebSocket routes
cannot use `Depends`, so authenticate the query-string token by hand before
accepting the connection:

    @ws_router.websocket("/ws/plugins/<id>")
    async def endpoint(websocket: WebSocket, token: str = Query(None)):
        user, error = authenticate_websocket_token(token)
        if user is None:
            await websocket.accept()
            await websocket.close(code=4001, reason=error or "Authentication failed")
            return

Check `user.account_type == AccountType.ADMIN` to restrict a route to admins.
"""

from src.platform.security.current_user import (
    authenticate_websocket_token,
    get_current_active_user,
    get_current_admin_user,
)
from src.platform.security.user import AccountType, User

__all__ = [
    "AccountType",
    "User",
    "authenticate_websocket_token",
    "get_current_active_user",
    "get_current_admin_user",
]
