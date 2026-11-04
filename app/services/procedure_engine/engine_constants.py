"""엔진 규칙이 직접 가리키는 단계·매뉴얼 ID와 고정 값.

안내 문장과 분기는 모두 매뉴얼 DB에서 읽는다. 여기 있는 것은 DB dispatch·명세서 문구가 이름으로 지목한 것뿐이다.
"""
# 매뉴얼
CARDIAC_MANUAL = "EM-01"  # 심정지 의심 (situation 1)
UNKNOWN_MANUAL = "EM-00"  # 상황 불명확 (situation -1). 진행 중이면 확인 없이 전환
AED_MANUAL = "COM-AED"  # I1 범위 밖 판정에 씀
CARDIAC_SITUATION = 1  # v1.7: 새 situation이 심정지면 확인 없이 전환

# I1: 의식 0 + 호흡 0 → 심정지. call119 표시가 있으면 EM01-23, 없으면 EM01-02부터
I1_STEP_WITH_CALL119 = "EM01-23"
I1_STEP_WITHOUT_CALL119 = "EM01-02"

# I2: 압박 중 회복 신호 → 회복 확인 질문. EM01-13·EM01-16에서는 적용하지 않음
I2_STEP = "EM01-13"
I2_EXCLUDED_STEPS = {"EM01-13", "EM01-16"}

# 관찰 대기(MON-06) 중 의식·호흡 값이 들어오면 MON-02로 바로
OBSERVE_WAIT_STEP = "MON-06"
OBSERVE_RECHECK_STEP = "MON-02"

# 조난 대기(EM15-09) 중이면 확인 없이 전환
DISTRESS_WAIT_STEP = "EM15-09"

# 대기 단계 진행 표현 (명세서 6-8). 정규화한 문장 전체가 이 중 하나와 같을 때
PROGRESS_PHRASES = {
    "다음", "다음이요", "다음으로", "다음 단계", "넘어가", "넘어가 줘", "넘어가 주세요", "넘어가자",
    "건너뛰어", "건너뛰어 줘", "건너뛰어 주세요",
}

# 시간 규칙 (명세서 6-6·6-7·6-8·6-9·6-11)
NO_SPEECH_S = 15  # 질문 낭독 뒤 무응답
NO_SPEECH_MAX = 3  # 세 번 연속이면 T_UNKNOWN 후 on_unknown
PROMPT_TIMEOUT_S = 15  # 확인 질문 무응답
WAIT_TIMEOUT_MAX = 3  # 같은 대기 단계에서 시간 초과로 세 번 넘어갔으면 그 뒤로는 기다리기만
MIC_MISSING_ADVANCE_S = 5  # 마이크가 없으면 질문 낭독 5초 뒤 on_unknown
GPS_RETRY_INTERVAL_S = 10
GPS_RETRY_MAX_S = 180
GPS_LAST_KNOWN_MAX_S = 30 * 60  # 30분 안의 마지막 위치만 읽는다

# 기기 (명세서 DeviceCommand)
METRONOME_BPM = 110
VOLUME_STEP = 10
VOLUME_MIN = 10  # 안내가 아예 안 들리지 않게
VOLUME_MAX = 100
