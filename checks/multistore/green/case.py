"""Independent expectations for a Green Burger practice checkout."""
from http.server import SimpleHTTPRequestHandler
from pathlib import Path


TITLE = "그린버거 | 테스트 키오스크"
ROUTE = "/kiosk/"
READY_SELECTOR = 'button[data-menu]'
GOAL = ("그린 베지버거 세트 2개와 단품 1개, 그린 샐러드 3개, 아이스 아메리카노 2잔, "
        "핫 아메리카노 1잔 주문해 줘. 포장으로 하고 카드 결제 방식을 선택해 줘.")
ROWS = [
    ("그린 베지버거", "세트 · 프렌치프라이 + 콜라", 2, 18600),
    ("그린 베지버거", "단품", 1, 6800),
    ("그린 샐러드", "기본", 3, 12600),
    ("아메리카노", "ICE", 2, 5600),
    ("아메리카노", "HOT", 1, 2800),
]
COUNT = 9
TOTAL = 46400
CARD_LABEL = "신용·체크카드"
RECEIPT_METHOD = CARD_LABEL
SITE = Path(__file__).resolve().parent / "site"


class handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITE), **kwargs)

    def log_message(self, *args):
        pass


def exact(view):
    if (sorted(map(tuple, view.get("rows", []))) != sorted(ROWS)
            or view.get("total") != TOTAL or view.get("count") != COUNT
            or view.get("dining") != "포장하기"):
        return False
    if view["stage"] == "review":
        return (sorted(map(tuple, view.get("review_rows", []))) == sorted(ROWS)
                and view.get("review_total") == TOTAL
                and view.get("mode_summary") == "포장하기 · 총 9개")
    if view["stage"] == "payment":
        return view.get("mode_summary") == "포장하기 · 총 46,400원"
    if view["stage"] == "receipt":
        return view.get("mode_summary", "").splitlines()[-1:] == ["포장하기 · 9개 · 46,400원"]
    return True
