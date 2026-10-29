"""실제 서버 통합 시험(US-008): uvicorn을 스레드로 띄우고 HTTP로 SSE 스트림·말 넣기·세션 복구·종료를 확인한다."""
import asyncio
import json
import signal
import threading
import time

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.run import Server

TIMEOUT = 2  # 모든 읽기·기다림의 제한 시간(초)
FALLEN = {"text": "사람이 쓰러졌는데 숨을 안 쉬어", "speak": False}


class Live:
    """127.0.0.1 빈 포트에 실제 uvicorn 서버를 띄운다. 서버는 자기 스레드의 이벤트 루프에서 돈다."""

    def __init__(self, tmp_path):
        settings = Settings(_env_file=None, port=0, fake_auto_ready=True, records_db_path=tmp_path / "records.db",
                            resume_from_snapshot=False)
        self.server = Server(create_app(settings))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        wait_until(lambda: self.server.started)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        wait_until(lambda: httpx.get(f"{self.url}/api/health", timeout=TIMEOUT).status_code == 200)

    async def stopped(self) -> float:
        """서버 스레드가 끝날 때까지 기다리고 걸린 시간(초)을 돌려준다."""
        start = time.monotonic()
        await asyncio.to_thread(self.thread.join, TIMEOUT)
        assert not self.thread.is_alive(), "서버가 제한 시간 안에 꺼지지 않음"
        return time.monotonic() - start


def wait_until(ready) -> None:
    deadline = time.monotonic() + TIMEOUT
    while True:
        try:
            if ready():
                return
        except httpx.TransportError:
            pass
        assert time.monotonic() < deadline, "제한 시간 안에 준비되지 않음"
        time.sleep(0.01)


class Frames:
    """SSE 응답을 줄 단위로 읽어 빈 줄로 나눈 프레임(줄 목록)을 낸다. ping 주석 프레임은 건너뛴다."""

    def __init__(self, res: httpx.Response):
        self._lines = res.aiter_lines()

    async def next(self) -> list[str] | None:
        """다음 프레임. 스트림이 끝나면 None."""
        lines: list[str] = []
        while True:
            try:
                line = await asyncio.wait_for(anext(self._lines), TIMEOUT)
            except StopAsyncIteration:
                return lines or None
            if line:
                lines.append(line)
            elif lines and lines != [": ping"]:
                return lines
            else:
                lines = []

    async def until(self, type: str) -> list[dict]:
        """type 이벤트가 올 때까지의 이벤트 봉투들."""
        events: list[dict] = []
        while not events or events[-1]["type"] != type:
            frame = await self.next()
            assert frame is not None, "스트림이 먼저 끝남"
            events.append(envelope(frame))
        return events


def envelope(frame: list[str]) -> dict:
    """id: N, data: {봉투 JSON} 두 줄짜리 프레임(event: 줄 없음)에서 봉투를 꺼낸다."""
    assert len(frame) == 2, frame
    id_line, data_line = frame
    assert id_line.startswith("id: ") and data_line.startswith("data: "), frame
    event = json.loads(data_line.removeprefix("data: "))
    assert event["seq"] == int(id_line.removeprefix("id: "))
    return event


@pytest.fixture
def live(tmp_path):
    live = Live(tmp_path)
    live.start()
    yield live
    live.server.should_exit = True  # 시험이 중간에 실패해도 서버를 끈다
    live.thread.join(TIMEOUT * 3)


async def test_stream_gets_utterance_events_and_session_recovers_missed(live):
    async with httpx.AsyncClient(base_url=live.url, timeout=TIMEOUT) as http:
        async with http.stream("GET", "/api/events") as res:
            assert res.status_code == 200
            assert res.headers["content-type"].startswith("text/event-stream")
            frames = Frames(res)
            assert await frames.next() == ["retry: 1000"]
            reply = (await http.post("/api/utterances", json=FALLEN)).json()["data"]
            events = await frames.until("turn.result")
        seqs = [e["seq"] for e in events]
        assert seqs == sorted(seqs)
        assert events[-1]["turn_id"] == reply["turn_id"]

        # 스트림이 끊긴 동안 생긴 이벤트는 다시 붙은 뒤 GET /api/session으로 복구한다
        missed = (await http.post("/api/utterances", json={"text": "네", "speak": False})).json()["data"]
        session = (await http.get("/api/session")).json()["data"]
        assert session["seq"] > seqs[-1]
        assert session["seq"] == live.server.app.state.parts.events.seq
        assert session["step_id"] == missed["step_id"] == live.server.app.state.parts.engine.state.step_id


async def test_graceful_shutdown_after_client_closed(live):
    async with httpx.AsyncClient(base_url=live.url, timeout=TIMEOUT) as http:
        async with http.stream("GET", "/api/events") as res:
            assert await Frames(res).next() == ["retry: 1000"]
    live.server.should_exit = True
    await live.stopped()


async def test_exit_signal_closes_open_stream_first(live):
    async with httpx.AsyncClient(base_url=live.url, timeout=TIMEOUT) as http:
        async with http.stream("GET", "/api/events") as res:
            frames = Frames(res)
            assert await frames.next() == ["retry: 1000"]
            live.server.handle_exit(signal.SIGINT, None)  # Ctrl+C와 같은 경로(신호 처리기)
            assert await frames.next() is None  # 서버가 스트림을 닫는다
    assert await live.stopped() < TIMEOUT < live.server.config.timeout_graceful_shutdown
