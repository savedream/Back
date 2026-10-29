"""부품 조립: 지금은 가짜 부품(모델·기기·GPS·스피커)으로 엔진을 꾸린다. 실제 부품은 키트 단계에서 같은 자리(Protocol)에 끼운다."""
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.fakes.fake_devices import FakeDevices
from app.fakes.fake_gps import FakeGps
from app.fakes.fake_model import FakeModel
from app.fakes.fake_speaker import FakeSpeaker
from app.services.events import EventBus
from app.services.manual_db import ManualDB
from app.services.procedure_engine.clock import Clock
from app.services.procedure_engine.inputs import LocationUpdateInput
from app.services.procedure_engine.service import EngineService
from app.services.records import SqliteRecordStore

_FAKE_FLAGS = ("fake_model", "fake_devices", "fake_audio", "fake_gps")


@dataclass
class Parts:
    clock: Clock
    events: EventBus
    records: SqliteRecordStore
    devices: FakeDevices
    gps: FakeGps
    speaker: FakeSpeaker
    model: FakeModel
    engine: EngineService


def read_boot_id(path: Path) -> str | None:
    """지금 부팅의 ID. 파일이 없으면(개발 PC 등) None이라 스냅샷에서 이어 가지 않는다."""
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


def build_parts(settings: Settings, db: ManualDB, clock: Clock) -> Parts:
    real = [name for name in _FAKE_FLAGS if not getattr(settings, name)]
    if real:
        raise RuntimeError(f"실제 부품은 아직 없음: {', '.join(f'{n}=false' for n in real)}")
    events = EventBus(clock)
    records = SqliteRecordStore(settings.records_db_path, clock, events, retry_base_s=settings.record_retry_base_s,
                                retry_max_s=settings.record_retry_max_s,
                                boot_id=read_boot_id(settings.boot_id_path))
    devices = FakeDevices()
    gps = FakeGps(clock)
    speaker = FakeSpeaker()
    engine = EngineService(db, clock, events, speaker, devices, records, location=gps.location,
                           device_timeout_s=settings.device_timeout_s,
                           on_new_session=lambda s: records.save_session(s.session_id, s.previous_session_id,
                                                                         s.started_at),
                           session_ids=records.session_ids, on_state=records.save_snapshot)
    # GPS 재시도 중 위치가 잡히면 엔진이 T_GPS_OK를 읽는다(6-11)
    gps.listeners.append(lambda loc: engine.post(LocationUpdateInput(location=loc)))
    return Parts(clock, events, records, devices, gps, speaker, FakeModel(), engine)
