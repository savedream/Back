"""HTTP 시나리오 시험 도구 (계획 3-5): 가짜 부품으로 앱을 띄우고 /api/utterances로 말을 넣는다.

시계는 FakeClock이라 15초·2분을 기다리지 않고 advance()로 넘긴다. 앱은 시험과 같은 이벤트 루프에서 돈다.
"""
import tempfile
from pathlib import Path

import httpx

from app.config import Settings
from app.main import create_app
from app.schemas.event import Event
from app.schemas.utterance import Utterance
from app.services.procedure_engine.clock import FakeClock
from tests.unit.shell_kit import until_idle


class Kit:
    def __init__(self, clock: FakeClock | None = None, **settings):
        self.clock = clock or FakeClock()
        self._tmp = tempfile.TemporaryDirectory()  # 기록 DB
        settings.setdefault("records_db_path", Path(self._tmp.name) / "records.db")
        self.app = create_app(Settings(_env_file=None, **settings), clock=self.clock)
        self.events: list[Event] = []

    async def __aenter__(self) -> "Kit":
        self._lifespan = self.app.router.lifespan_context(self.app)
        await self._lifespan.__aenter__()
        if self.app.state.model_loading is not None:
            await self.app.state.model_loading  # 실제 모델(SALYEO_FAKE_MODEL=false)이면 적재·예열이 끝나기를 기다린다
        self.parts = self.app.state.parts
        self._sub = self.parts.events.subscribe(maxsize=0)  # 0: 크기 제한 없음
        # 처리 못 한 예외도 앱의 500 응답으로 받는다(명세 공통 오류)
        transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
        self.http = httpx.AsyncClient(transport=transport, base_url="http://kit")
        await self.settle()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.http.aclose()
        await self._lifespan.__aexit__(*exc)
        self._tmp.cleanup()

    @property
    def state(self):
        return self.parts.engine.state

    @property
    def model(self):
        return self.parts.model

    @property
    def records(self) -> list:
        return self.parts.records.records

    @property
    def spoken(self) -> list[Utterance]:
        """guide.say로 화면에 낸 문장(낭독 끝 되먹임 뒤에 나온 문장까지)."""
        return [Utterance(**e.data) for e in self.events if e.type == "guide.say"]

    def spoken_since(self, mark: int) -> list[Utterance]:
        return self.spoken[mark:]

    async def post(self, text: str, speak: bool = False) -> httpx.Response:
        return await self.http.post("/api/utterances", json={"text": text, "speak": speak})

    async def say(self, text: str, speak: bool = False, settle: bool = True) -> dict:
        """문장 하나를 넣고 응답 data를 돌려준다. settle이면 장치·기록 되먹임까지 끝날 때까지 기다린다."""
        res = await self.post(text, speak)
        assert res.status_code == 200, res.text
        if settle:
            await self.settle()
        return res.json()["data"]

    async def settle(self) -> None:
        """엔진 입력 큐가 비고 낭독 실행기가 쉬고 기다리는 되먹임이 없고 말하기 버튼 턴 처리도 없을 때까지.
        남은 마감 시각(최대 녹음 시간 포함)은 기다리지 않는다."""
        await until_idle(self.parts.engine, self.parts.engine.sequencer, self.app.state.speech)
        self.drain()

    def drain(self) -> None:
        """기다리지 않고 지금까지 나온 이벤트만 모은다."""
        while not self._sub.queue.empty():
            self.events.append(self._sub.queue.get_nowait())

    async def advance(self, seconds: float) -> list[Utterance]:
        mark = len(self.spoken)
        self.clock.advance(seconds)
        await self.settle()
        return self.spoken_since(mark)


def ids(say: list) -> list[tuple[str, str]]:
    """응답 say(dict)나 Utterance 목록을 (source, template_id·step_id·manual_id)로."""
    out = []
    for u in say:
        u = u if isinstance(u, Utterance) else Utterance(**u)
        out.append((u.source, u.template_id or u.step_id or u.manual_id))
    return out
