"""비밀값 가리기 — 단위 컨텍스트로 코드를 내보낼 때 자격증명처럼 보이는 값을 **** 로 바꾼다.

비밀 파일(.env 등)은 아예 읽지 않지만, application-prd.properties 같은 설정 파일이나 코드에
하드코딩된 비밀번호·토큰은 일반 파일 안에 있다. 줄 번호와 구조는 유지하고 값만 가린다.
근거 검증(스니펫 해시)은 원본 기준이라 영향 없음.
"""
from __future__ import annotations

import re

KEY = r"(?:password|passwd|pwd|passphrase|secret|token|api[-_.]?key|access[-_.]?key|secret[-_.]?key|private[-_.]?key|client[-_.]?secret|credential|auth[-_.]?key|signing[-_.]?key|license[-_.]?key|dsn|salt|encrypt(?:ion)?[-_.]?key)"
SECRET_PATH_RE = re.compile(rf"(?i)(?:^|[.\-_]){KEY}(?:$|[.\-_])|^(?:jasypt|crypto|security|oauth2?|jwt|credentials?)\.")
ASSIGN_RE = re.compile(rf"""(?ix)
    (?P<key>[\w.\-]*{KEY}[\w.\-]*)          # 키 이름에 비밀 단어 포함
    (?P<sep>\s*["']?\s*(?:=|:|=>)\s*)       # = : => (properties/yaml/json/코드)
    (?P<q>["']?)(?P<val>[^"'\s,;}}#]{{4,}})(?P=q)
""")
URL_CRED_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/\s@]+:)([^@\s/]{3,})(@)")
JDBC_PW_RE = re.compile(r"(?i)([?&;]password=)([^&;\s]+)")
PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
TOKEN_RE = re.compile(r"\b(?:ghp_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b")
PLACEHOLDER_RE = re.compile(r"^(?:\$\{[^}]*\}|\$\w+.*|<[^>]*>|\*+|changeme|xxx+|your[-_].*|example.*|null|none|nil|true|false|\d{1,3}|new|new-object|ENC\(.*\))$", re.I)
YAML_KV_RE = re.compile(r"^(?P<indent>\s*)(?:-\s+)?(?P<key>[\w.\-]+)\s*:(?P<rest>\s*(?P<val>.*))$")


CONFIG_SUFFIXES = (".yml", ".yaml", ".properties", ".env", ".ini", ".cfg", ".conf", ".toml", ".json", ".xml")


def mask_line(line: str, code: bool = False) -> tuple[str, bool]:
    """code=True(소스 코드)면 따옴표로 감싼 문자열 값만 비밀로 본다.

    코드에는 `token: &str`, `let secret = derive_key(..)`, `jsonwebtoken::errors::Error` 처럼 비밀 단어가 들어간
    타입·식별자가 흔해서, 따옴표 없는 값까지 가리면 코드를 읽을 수 없게 된다 (Meilisearch 인증 코드에서 실제 발생).
    """
    if PEM_RE.search(line):
        return "<개인키 블록 — 가림>", True
    masked = False

    def _assign(m: re.Match) -> str:
        nonlocal masked
        val = m.group("val")
        if code and not m.group("q"):
            return m.group(0)
        if PLACEHOLDER_RE.match(val) or re.match(r"^[A-Za-z_][\w.]*\(", val) or val.startswith(("$", "process.env", "env::", "System.getenv")):
            return m.group(0)  # 환경변수·자리표시·함수 호출은 값이 아님
        if code and (re.search(r"[{}()<>]|::", val) or re.fullmatch(r"[A-Z][A-Za-z0-9]*", val)):
            return m.group(0)  # 타입·경로·포맷 문자열 같은 코드 조각
        masked = True
        return f"{m.group('key')}{m.group('sep')}{m.group('q')}****{m.group('q')}"

    out = ASSIGN_RE.sub(_assign, line)
    out2 = URL_CRED_RE.sub(lambda m: m.group(1) + "****" + m.group(3), out)
    out3 = JDBC_PW_RE.sub(lambda m: m.group(1) + "****", out2)
    out4 = TOKEN_RE.sub("****", out3)
    return out4, masked or out4 != out


def _mask_yaml_value(line: str, m: re.Match) -> str | None:
    val = m.group("val").strip()
    if not val or val.startswith(("#", "|", ">", "&", "*")) and len(val) <= 2:
        return None
    raw = val.split(" #", 1)[0].strip().strip("'\"")
    if len(raw) < 4 or PLACEHOLDER_RE.match(raw):
        return None
    return f"{m.group('indent')}{line[len(m.group('indent')):m.start('rest')]} ****"


def mask_lines(path: str, lines: list[str]) -> list[tuple[str, bool]]:
    """파일 전체를 가린다. YAML 은 들여쓰기로 키 경로(crypto.secret.key)를 추적해 경로 기준으로도 판정한다."""
    lower = path.lower()
    code = not (lower.endswith(CONFIG_SUFFIXES) or "/.env" in lower or lower.startswith(".env"))
    out = [mask_line(line, code=code) for line in lines]
    if not lower.endswith((".yml", ".yaml")):
        return out
    stack: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = YAML_KV_RE.match(line)
        if not m or line.lstrip().startswith("#"):
            continue
        indent = len(m.group("indent"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        full = ".".join([k for _, k in stack] + [m.group("key")])
        stack.append((indent, m.group("key")))
        if out[i][1] or not SECRET_PATH_RE.search(full):
            continue
        masked = _mask_yaml_value(line, m)
        if masked is not None:
            out[i] = (masked, True)
    return out
