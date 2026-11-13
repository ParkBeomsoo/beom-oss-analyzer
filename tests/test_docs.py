"""첨부 문서를 글로 — docx·pptx·xlsx·hwpx, 위험한 XML 거절, 웹 페이지(공개·로그인 필요)."""
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from oss_analyzer.common.response import ToolError
from oss_analyzer.core import docs

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return path


def test_office_and_hangul_documents_become_text(tmp_path):
    docx = _zip(tmp_path / "기획.docx", {"word/document.xml": f'<w:document {W}><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>주문 총액</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>10개 이상 10% 할인</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>수량</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>총액</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '</w:body></w:document>'})
    text = docs.to_text(docx)
    assert "# 주문 총액" in text and "10개 이상 10% 할인" in text and "| 수량 | 총액 |" in text
    pptx = _zip(tmp_path / "발표.pptx", {"ppt/slides/slide2.xml": f'<p:sld {A} xmlns:p="x"><a:p><a:r><a:t>둘째</a:t></a:r></a:p></p:sld>',
                                        "ppt/slides/slide1.xml": f'<p:sld {A} xmlns:p="x"><a:p><a:r><a:t>첫째</a:t></a:r></a:p></p:sld>'})
    t = docs.to_text(pptx)
    assert t.index("첫째") < t.index("둘째") and "## 슬라이드 1" in t
    xlsx = _zip(tmp_path / "표.xlsx", {"xl/sharedStrings.xml": '<sst xmlns="m"><si><t>상품</t></si><si><t>가격</t></si></sst>',
                                       "xl/worksheets/sheet1.xml": '<worksheet xmlns="m"><sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
                                                                   '<row><c><v>A</v></c><c><v>2000</v></c></row></sheetData></worksheet>'})
    assert "| 상품 | 가격 |" in docs.to_text(xlsx) and "| A | 2000 |" in docs.to_text(xlsx)
    hwpx = _zip(tmp_path / "한글.hwpx", {"Contents/section0.xml": '<hs:sec xmlns:hs="h" xmlns:hp="p"><hp:p><hp:run><hp:t>한글 문서 내용</hp:t></hp:run></hp:p></hs:sec>'})
    assert "한글 문서 내용" in docs.to_text(hwpx)
    assert docs.to_text(tmp_path / "그림.png") is None


def test_dangerous_xml_is_refused(tmp_path):
    bomb = _zip(tmp_path / "bomb.docx", {"word/document.xml": '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
                                                             f'<w:document {W}><w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>'})
    with pytest.raises(ToolError):
        docs.to_text(bomb)


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/private":
            self.send_response(403)
            self.end_headers()
            return
        body = ("<html><head><title>주문 기획</title><script>x()</script></head><body><nav>메뉴</nav>"
                "<h1>주문 총액</h1><p>개당 2000원</p><ul><li>10개 이상 할인</li></ul>" + "<p>설명</p>" * 40 + "</body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_public_page_is_saved_as_text_and_private_page_is_flagged():
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        got = docs.fetch_url(base + "/doc")
        assert got["ok"] and got["title"] == "주문 기획" and "# 주문 총액" in got["text"] and "- 10개 이상 할인" in got["text"]
        assert "x()" not in got["text"] and "메뉴" not in got["text"]
        bad = docs.fetch_url(base + "/private")
        assert not bad["ok"] and bad["needs_login"]
    finally:
        srv.shutdown()
