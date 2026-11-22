"""Expected order and read-only fixture server for the original Morrow cafe."""
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit


GOAL = "바닐라빈 라테 따뜻하게 1잔, 아이스 3잔, 자몽 에이드 2잔, 레몬 파운드 2개 주문해 줘. 카드 결제 방식을 선택해 줘."
TITLE = "모로우 커피 | 카페 키오스크"
START_HEADING = "오늘의 메뉴"
ROUTE = "/"
ROWS = [
    ("바닐라빈 라테", "따뜻하게", 1, 5800),
    ("바닐라빈 라테", "아이스", 3, 17400),
    ("자몽 에이드", "", 2, 11000),
    ("레몬 파운드", "", 2, 9000),
]
TOTAL = 43200
COUNT = 8
CARD_LABEL = "카드"
RECEIPT_METHOD = "카드"


class handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(Path(__file__).resolve().parent / "site"), **kwargs)

    def do_GET(self):
        if urlsplit(self.path).path not in ("/", "/index.html", "/cafe.css", "/cafe.js", "/assets/coffee.jpg"):
            self.send_error(404)
            return
        super().do_GET()

    def log_message(self, *_):
        pass


def exact(view):
    return (view.get("count") == COUNT and view.get("total") == TOTAL
            and sorted(tuple(row) for row in view.get("rows", [])) == sorted(ROWS))
