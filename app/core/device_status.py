"""기기 상태 주기 갱신 (명세서 GET /api/devices, AC-D4·D5).

요청마다 장치를 검사하지 않도록 GPS·모델·배터리·저장 공간·시계는 주기적으로 다시 읽어 두고, 앞 값과 다른 부분을
status.changed(GET /api/devices와 같은 모양의 바뀐 부분)로 보낸다. 장치 연결(마이크·스피커·부저)은 엔진 상태에서 계산해
바뀌었는지만 함께 본다. 음량(speaker.volume)과 미저장 기록 수(storage.unsaved_records)는 엔진·기록 저장소가 바뀔 때 이미
내므로 여기서 다시 내지 않는다.
"""
import asyncio
import logging
import shutil
from pathlib import Path

from app.config import Settings
from app.core.parts import Parts
from app.core.readiness import Component, Readiness
from app.schemas.session import SessionState

log = logging.getLogger(__name__)

_MB = 1024 * 1024


def engine_state(parts: Parts) -> SessionState:
    """엔진 상태. 준비 전(엔진 시작 전)이면 기본 상태(GET /api/session과 같음)."""
    return parts.engine.state or SessionState(session_id="", started_at="")


class DeviceStatus:
    def __init__(self, parts: Parts, readiness: Readiness, settings: Settings):
        self._parts = parts
        self._readiness = readiness
        self._settings = settings
        self.latest = self._read()  # 마지막으로 갱신한 장치·시스템 칸
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(self._parts.clock.now()))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def refresh(self) -> None:
        """장치·시스템 칸을 다시 읽고 앞 값과 다른 부분만 status.changed로 보낸다."""
        new = self._read()
        changes = _changed(self.latest, new)
        self.latest = new
        if changes:
            self._parts.events.publish("status.changed", None, changes)

    async def _run(self, last: float) -> None:
        """last: 마지막으로 읽은 시각(엔진 시계). 늦게 깨어나도 밀린 갱신을 몰아 하지 않고 한 번만 한다."""
        clock = self._parts.clock
        while True:
            await clock.sleep_until(last + self._settings.device_status_interval_s)
            last = clock.now()
            try:
                self.refresh()
            except Exception:
                log.exception("기기 상태 갱신 실패")

    def _read(self) -> dict:
        parts, settings, ready = self._parts, self._settings, self._readiness.is_ready
        location = parts.gps.location()  # None이면 GPS 장치 없음
        state = engine_state(parts)
        devices = state.devices
        return {
            "gps": {"connected": location is not None, "fix": location is not None and location.fix,
                    "accuracy_m": location.accuracy_m if location else None,
                    "satellites": location.satellites if location else None},
            "microphone": {"connected": state.mic.connected},
            "speaker": {"connected": devices["rescue_signal"].connected},  # 스피커 = 안내 음성·구조신호(⑦)
            "buzzer": {"connected": devices["metronome"].connected},  # 부저 = 메트로놈 전용
            "models": {
                "llm": {"loaded": ready(Component.LLM), "warmed": ready(Component.LLM_WARMUP),
                        "version": settings.model_version},
                "vad": {"loaded": ready(Component.VAD)},
                "stt": {"loaded": ready(Component.STT)},
                "tts": {"loaded": ready(Component.TTS), "cached_sentences": None},  # 문장 캐시가 아직 없음
            },
            "battery": parts.battery.read(),
            "storage": {
                "data_mb": {"manual_db": _file_mb(settings.manual_db_path), "models": None, "tts_cache": None},
                "free_mb": _free_mb(settings.records_db_path.parent),
            },
            "clock": {"source": parts.engine.clock_source},
        }


def _changed(old: dict, new: dict) -> dict:
    """new 중 old와 다른 부분만(안쪽 dict까지 내려가 바뀐 값만 남긴다)."""
    out = {}
    for key, value in new.items():
        before = old.get(key)
        if isinstance(value, dict) and isinstance(before, dict):
            inner = _changed(before, value)
            if inner:
                out[key] = inner
        elif value != before:
            out[key] = value
    return out


def _file_mb(path: Path) -> float | None:
    """파일 크기(MB, 소수 둘째 자리). 못 읽으면 None."""
    try:
        return round(path.stat().st_size / _MB, 2)
    except OSError:
        return None


def _free_mb(folder: Path) -> int | None:
    """그 폴더가 있는 디스크의 남은 공간(MB). 못 읽으면 None."""
    try:
        return shutil.disk_usage(folder).free // _MB
    except OSError:
        return None
