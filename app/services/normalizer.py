"""③ 정규화: 문장을 학습 데이터와 같은 모양으로 맞춘다 (학습 문장에는 문장부호·숫자·영문이 없다).

규칙(명세서 ③): 1. 문장부호 제거 2. 아라비아 숫자를 한글 숫자로 3. 영문 치환표 적용 4. 연속 공백을 하나로 5. 앞뒤 공백 제거.
바꿀 수 없는 글자는 그대로 둔다.
"""
import re
import unicodedata

from app.schemas.turn import NormalizedText

# 한 자리씩 읽는 숫자 (명세서 예시: 119 → 일일구)
DIGIT_BY_DIGIT = {"119": "일일구"}

# TODO(미정: 학습 데이터 표기 확인 후 채움) 영문 치환표
ENGLISH_MAP: dict[str, str] = {}

_DIGITS = "영일이삼사오육칠팔구"
_SMALL_UNITS = ["", "십", "백", "천"]
_LARGE_UNITS = ["", "만", "억", "조"]
_NUMBER_RE = re.compile(r"\d+")


def _read_group(n: int) -> str:
    """0~9999를 한자어 수로. 십·백·천 앞의 '일'은 읽지 않는다(십오, 백, 천이)."""
    out = []
    for i in range(3, -1, -1):
        d = n // 10**i % 10
        if d == 0:
            continue
        out.append(("" if d == 1 and i > 0 else _DIGITS[d]) + _SMALL_UNITS[i])
    return "".join(out)


def read_sino_korean(n: int) -> str:
    """정수를 한자어 수로 읽는다 (3 → 삼, 20 → 이십, 10000 → 만)."""
    if n == 0:
        return "영"
    parts = []
    for i, unit in enumerate(_LARGE_UNITS):
        group = n // 10000**i % 10000
        if group == 0:
            continue
        # 만 단위가 1이면 '일만'이 아니라 '만'
        word = "" if group == 1 and unit == "만" else _read_group(group)
        parts.append(word + unit)
    return "".join(reversed(parts))


def _number_to_hangul(digits: str) -> str:
    if digits in DIGIT_BY_DIGIT:
        return DIGIT_BY_DIGIT[digits]
    if len(digits) > 1 and digits.startswith("0"):
        # 0으로 시작하는 숫자열(전화번호 등)은 한 자리씩, 0은 '공'
        return "".join("공" if c == "0" else _DIGITS[int(c)] for c in digits)
    return read_sino_korean(int(digits))


def _replace_numbers(text: str) -> str:
    def repl(m: re.Match) -> str:
        word = _number_to_hangul(m.group())
        nxt = text[m.end()] if m.end() < len(text) else ""
        # 단위 명사는 띄어 쓴다(3분 → 삼 분). 한 자리씩 읽는 표의 숫자는 붙인다(119에 → 일일구에)
        if m.group() not in DIGIT_BY_DIGIT and "가" <= nxt <= "힣":
            word += " "
        return word

    return _NUMBER_RE.sub(repl, text)


def normalize(text: str) -> NormalizedText:
    t = "".join(" " if unicodedata.category(c).startswith("P") else c for c in text)
    t = _replace_numbers(t)
    for en, ko in ENGLISH_MAP.items():
        t = t.replace(en, ko)
    t = re.sub(r"\s+", " ", t).strip()
    return NormalizedText(original=text, text=t)
