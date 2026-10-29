"""부품 조립: 지금은 가짜 부품(모델·기기·GPS·스피커)으로 엔진을 꾸린다. 실제 부품은 키트 단계에서 같은 자리(Protocol)에 끼운다."""
from dataclasses import dataclass, replace
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
from app.schemas.record import Record
from app.services.records import Snapshot, SqliteRecordStore

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
    boot_id: str | None = None
    resume_from_snapshot: bool = False

    async def start_engine(self) -> None:
        """준비가 끝나면 부른다. 같은 부팅에서 백엔드만 다시 떴으면(스냅샷의 boot_id가 지금과 같으면) 스냅샷에서 이어 가고,
        부팅이 다르거나 알 수 없으면 지난 세션 다음의 새 세션을 연다(6-14)."""
        snap = self.records.last_snapshot
        if snap is None or not self.resume_from_snapshot or self.boot_id is None or snap.boot_id != self.boot_id:
            snap = None
        else:
            earlier = await self.records.resume_session(snap.state.session_id)
            snap = _catch_up_record_counter(snap, earlier)
        await self.engine.start(previous_session_id=self.records.last_session_id, snapshot=snap)


def _catch_up_record_counter(snap: Snapshot, earlier: list[Record]) -> Snapshot:
    """비정상 종료 직전에 스냅샷이 마지막 기록보다 늦게 저장됐으면 기록 번호를 이미 저장된 번호 뒤로 맞춘다(번호 겹침 방지)."""
    last = max((int(r.record_id.rsplit("_", 1)[1]) for r in earlier), default=0)
    if last <= snap.state.counters.record:
        return snap
    counters = snap.state.counters.model_copy(update={"record": last})
    return replace(snap, state=snap.state.model_copy(update={"counters": counters}))


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
    boot_id = read_boot_id(settings.boot_id_path)
    records = SqliteRecordStore(settings.records_db_path, clock, events, retry_base_s=settings.record_retry_base_s,
                                retry_max_s=settings.record_retry_max_s, boot_id=boot_id)
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
    return Parts(clock, events, records, devices, gps, speaker, FakeModel(), engine, boot_id,
                 settings.resume_from_snapshot)
