from __future__ import annotations

from typing import Any

from fastapi.responses import StreamingResponse


class DirectSSEResponse(StreamingResponse):
    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await self.stream_response(send)
        except OSError:
            pass
        if self.background is not None:
            await self.background()
