"""③ 정규화 (명세서 내부 모듈 계약 ③)."""
import pytest

from app.services.normalizer import normalize, read_sino_korean


@pytest.mark.parametrize(
    "text, expected",
    [
        ("네, 맞아요.", "네 맞아요"),
        ("숨을 안 쉬어요!!", "숨을 안 쉬어요"),
        ("그게...아니고", "그게 아니고"),
        ("  여기   어디야  ", "여기 어디야"),
        ("3분 지났어", "삼 분 지났어"),  # 명세서 예시
        ("119", "일일구"),  # 명세서 예시
        ("119에 신고했어", "일일구에 신고했어"),
        ("15초", "십오 초"),
        ("20대 남자", "이십 대 남자"),
        ("010", "공일공"),
        ("AED 가져왔어", "AED 가져왔어"),  # 영문 치환표는 아직 비어 있어 그대로
    ],
)
def test_normalize(text, expected):
    assert normalize(text).text == expected


def test_normalize_keeps_original():
    assert normalize("네, 맞아요.").original == "네, 맞아요."


@pytest.mark.parametrize(
    "n, expected",
    [(0, "영"), (1, "일"), (10, "십"), (11, "십일"), (20, "이십"), (100, "백"), (110, "백십"),
     (1000, "천"), (2025, "이천이십오"), (10000, "만"), (20000, "이만"), (110000, "십일만"), (100000000, "일억")],
)
def test_read_sino_korean(n, expected):
    assert read_sino_korean(n) == expected
