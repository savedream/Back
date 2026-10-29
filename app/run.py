"""서버 실행: python -m app.run (프로세스 하나, reload 없이). host·port는 설정(SALYEO_HOST·SALYEO_PORT)에서 읽는다."""
import contextlib
import socket

import uvicorn
from fastapi import FastAPI

from app.main import create_app


class Server(uvicorn.Server):
    """서버를 끌 때 열린 SSE 스트림부터 닫는다(명세서 GET /api/events "서버를 끌 때 열린 스트림을 먼저 닫는다").

    uvicorn은 연결이 끝나기를 기다린 뒤에 lifespan 종료를 부르므로 lifespan에서만 닫으면 열린 스트림이 종료를 붙잡는다.
    종료 신호든 should_exit든 모든 종료가 지나는 shutdown 맨 앞에서 닫고, 그래도 남은 연결은 shutdown_timeout_s가 지나면 끊는다.
    """

    def __init__(self, app: FastAPI):
        settings = app.state.settings
        super().__init__(uvicorn.Config(app, host=settings.host, port=settings.port,
                                        timeout_graceful_shutdown=settings.shutdown_timeout_s))
        self.app = app

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        if self.started:  # 준비 중에는 아직 연결을 받지 않아 열린 스트림이 없다
            self.app.state.parts.events.close_all()
        await super().shutdown(sockets)


if __name__ == "__main__":
    # 정상 종료를 마친 뒤 uvicorn이 받은 SIGINT를 다시 올린다. uvicorn.run처럼 traceback 없이 끝낸다
    with contextlib.suppress(KeyboardInterrupt):
        Server(create_app()).run()
