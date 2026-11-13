"""첨부 문서를 에이전트가 읽을 수 있는 글로 — 추가 설치 없이 (표준 라이브러리만).

docx · pptx · xlsx · hwpx 는 압축된 XML 이라 글을 바로 뽑는다. PDF·이미지는 원본을 그대로 두고 에이전트가 읽는다
(Claude Code 는 PDF·이미지를 읽는다). 웹 주소는 공개 페이지면 글로 저장하고, 로그인이 필요하면 알려 준다.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from oss_analyzer.common.response import ToolError

TEXT_LIMIT = 400_000
CONVERTIBLE = {".docx", ".pptx", ".xlsx", ".hwpx"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


PART_LIMIT = 50 * 1024 * 1024  # 압축 폭탄 막기 — 문서 한 부분이 이보다 크면 읽지 않는다
TOTAL_LIMIT = 200 * 1024 * 1024  # 문서 전체로도 이만큼까지만 풀어 본다
_budget = {"left": TOTAL_LIMIT}


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element | None:
    """신뢰할 수 없는 문서의 XML — DTD·엔티티 선언이 있으면 거절한다 (OOXML·HWPX 는 쓰지 않는다: 엔티티 폭탄·XXE 차단)."""
    try:
        info = z.getinfo(name)
        if info.file_size > PART_LIMIT or info.file_size > _budget["left"]:
            return None
        _budget["left"] -= info.file_size
        data = z.read(name)
    except KeyError:
        return None
    head = data.replace(b"\x00", b"")  # UTF-16 으로 감춘 선언도 본다
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ToolError("invalid_request", f"문서 안에 허용하지 않는 선언(DTD·엔티티)이 있음: {name}")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def _docx(z: zipfile.ZipFile) -> str:
    root = _xml(z, "word/document.xml")
    if root is None:
        return ""
    out = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "tbl":
            for tr in (e for e in el.iter() if _local(e.tag) == "tr"):
                cells = ["".join(t.text or "" for t in tc.iter() if _local(t.tag) == "t").strip()
                         for tc in tr if _local(tc.tag) == "tc"]
                out.append("| " + " | ".join(cells) + " |")
            el.clear()  # 표 안의 문단을 다시 세지 않게
        elif name == "p":
            text = "".join(t.text or "" for t in el.iter() if _local(t.tag) == "t").strip()
            if not text:
                continue
            style = next((s.get(next(iter(s.attrib), ""), "") for s in el.iter() if _local(s.tag) == "pStyle"), "")
            m = re.search(r"(?:Heading|제목)\s*(\d)", style)
            out.append(("#" * int(m.group(1)) + " " if m else "") + text)
    return "\n\n".join(out)


def _pptx(z: zipfile.ZipFile) -> str:
    slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.search(r"(\d+)", n).group(1)))
    out = []
    for i, name in enumerate(slides, 1):
        root = _xml(z, name)
        paras = []
        for p in (e for e in (root.iter() if root is not None else []) if _local(e.tag) == "p"):
            t = "".join(x.text or "" for x in p.iter() if _local(x.tag) == "t").strip()
            if t:
                paras.append(t)
        out.append(f"## 슬라이드 {i}\n\n" + "\n".join(f"- {p}" for p in paras))
    return "\n\n".join(out)


def _xlsx(z: zipfile.ZipFile) -> str:
    shared = []
    sroot = _xml(z, "xl/sharedStrings.xml")
    if sroot is not None:
        for si in (e for e in sroot if _local(e.tag) == "si"):
            shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
    out = []
    sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    for name in sheets:
        root = _xml(z, name)
        rows = []
        for row in (e for e in (root.iter() if root is not None else []) if _local(e.tag) == "row"):
            cells = []
            for c in (e for e in row if _local(e.tag) == "c"):
                v = next((x.text for x in c.iter() if _local(x.tag) in ("v", "t")), "") or ""
                if c.get("t") == "s" and v.isdigit() and int(v) < len(shared):
                    v = shared[int(v)]
                cells.append(v.strip())
            if any(cells):
                rows.append("| " + " | ".join(cells) + " |")
            if len(rows) >= 2000:
                break
        out.append(f"## {Path(name).stem}\n\n" + "\n".join(rows))
    return "\n\n".join(out)


def _hwpx(z: zipfile.ZipFile) -> str:
    sections = sorted(n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n))
    out = []
    for name in sections:
        root = _xml(z, name)
        for p in (e for e in (root.iter() if root is not None else []) if _local(e.tag) == "p"):
            t = "".join(x.text or "" for x in p.iter() if _local(x.tag) == "t").strip()
            if t:
                out.append(t)
    return "\n\n".join(out)


def to_text(path: Path) -> str | None:
    """문서에서 글만 — 바꿀 수 없는 형식이면 None."""
    ext = path.suffix.lower()
    if ext not in CONVERTIBLE:
        return None
    _budget["left"] = TOTAL_LIMIT
    try:
        with zipfile.ZipFile(path) as z:
            text = {".docx": _docx, ".pptx": _pptx, ".xlsx": _xlsx, ".hwpx": _hwpx}[ext](z)
    except (zipfile.BadZipFile, OSError) as e:
        raise ToolError("invalid_request", f"{path.name} 을 읽지 못함 ({e})") from None
    return text[:TEXT_LIMIT]


# ---------------------------------------------------------------- 웹 페이지

class _Text(HTMLParser):
    BLOCK = {"p", "div", "li", "tr", "br", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "table"}
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.out.append("\n")
            if re.fullmatch(r"h[1-6]", tag):
                self.out.append("#" * int(tag[1]) + " ")
            elif tag == "li":
                self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def fetch_url(url: str, timeout: float = 20) -> dict:
    """공개 웹 페이지를 글로. 로그인이 필요한 페이지(401·403·로그인 화면으로 이동)는 needs_login 으로 알린다."""
    if not re.match(r"https?://", url):
        raise ToolError("invalid_request", "http(s) 주소만 됩니다")
    req = urllib.request.Request(url, headers={"User-Agent": "beom-oss-analyzer (document fetch)", "Accept": "text/html,text/plain,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final = resp.geturl()
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read(5_000_000)
    except urllib.error.HTTPError as e:
        return {"ok": False, "needs_login": e.code in (401, 403), "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"ok": False, "needs_login": False, "error": str(getattr(e, "reason", e))[:200]}
    charset = re.search(r"charset=([\w-]+)", ctype)
    try:
        body = raw.decode(charset.group(1) if charset else "utf-8", "replace")
    except LookupError:  # 모르는 문자 집합 이름
        body = raw.decode("utf-8", "replace")
    if re.search(r"/login|/signin|login\.action|auth", final, re.I) and final != url:
        return {"ok": False, "needs_login": True, "error": "로그인 화면으로 이동함"}
    if "html" not in ctype and not body.lstrip().startswith("<"):
        return {"ok": True, "title": url, "text": body[:TEXT_LIMIT], "final_url": final}
    p = _Text()
    p.feed(body)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(p.out))).strip()
    if len(text) < 200 and re.search(r"(log ?in|sign ?in|로그인)", body, re.I):
        return {"ok": False, "needs_login": True, "error": "로그인이 필요한 페이지로 보임"}
    return {"ok": True, "title": p.title.strip() or url, "text": text[:TEXT_LIMIT], "final_url": final}
