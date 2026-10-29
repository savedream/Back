"""서버 실행: python -m app.run (프로세스 하나, reload 없이). host·port는 설정(SALYEO_HOST·SALYEO_PORT)에서 읽는다."""
import asyncio
import contextlib
import socket
from types import FrameType

import uvicorn
from fastapi import FastAPI

from app.main import create_app


class Server(uvicorn.Server):
    """종료 신호를 받으면 열린 SSE 스트림부터 닫는다(명세서 GET /api/events "서버를 끌 때 열린 스트림을 먼저 닫는다").

    uvicorn은 연결이 끝나기를 기다린 뒤에 lifespan 종료를 부르므로 lifespan에서만 닫으면 열린 스트림이 종료를 붙잡는다.
    그래도 남은 연결은 shutdown_timeout_s가 지나면 끊는다.
    """

    def __init__(self, app: FastAPI):
        settings = app.state.settings
        super().__init__(uvicorn.Config(app, host=settings.host, port=settings.port,
                                        timeout_graceful_shutdown=settings.shutdown_timeout_s))
        self.app = app
        self._loop: asyncio.AbstractEventLoop | None = None

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        await super().startup(sockets)

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        # 신호 처리기는 루프가 도는 중간에 끼어들 수 있으므로 닫기는 루프 스레드로 넘긴다
        if self.started and self._loop is not None:
            self._loop.call_soon_threadsafe(self.app.state.parts.events.close_all)
        super().handle_exit(sig, frame)


if __name__ == "__main__":
    # 정상 종료를 마친 뒤 uvicorn이 받은 SIGINT를 다시 올린다. uvicorn.run처럼 traceback 없이 끝낸다
    with contextlib.suppress(KeyboardInterrupt):
        Server(create_app()).run()
