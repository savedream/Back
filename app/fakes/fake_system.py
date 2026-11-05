"""가짜 종료 훅·배터리. 실제로 끄지 않고 꺼짐 요청만 표시하며, 배터리는 못 읽는 것(None)으로 둔다."""
from collections.abc import Callable


class FakeShutdown:
    """꺼짐 요청만 표시하고 resume(엔진 동결 풀기)을 불러 서버를 계속 쓸 수 있게 한다(화면 개발용, 사용자 결정)."""

    def __init__(self, resume: Callable[[], None]):
        self.requested = False
        self._resume = resume

    async def shutdown(self) -> None:
        self.requested = True
        self._resume()


class FakeBattery:
    def read(self) -> dict | None:
        return None
