"""파일 분류 — git ls-files 의 모든 파일을 정확히 하나의 범주로 나눈다.

범주:
  code       분석 대상 소스
  test       테스트 코드 (기능 명세로도 사용)
  asset      설정·SQL·템플릿·빌드 스크립트 등 비코드 자산 (분석 대상)
  doc        문서
  generated  생성 코드 (원본이 따로 있음)
  vendored   외부에서 복사해 온 코드 (분석 경계 밖)
  secret     비밀정보 가능성 — 내용을 절대 읽지 않음
  binary     바이너리 — 소스 분석 불가, 미확인으로 기록
  test-data  테스트 스냅샷·퍼저 코퍼스·fixture 데이터 (존재만 기록, 단위 분석 대상 아님)
  unclassified  규칙에 안 걸림 → 사람이/에이전트가 oss_file_classify 로 직접 분류해야 함
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath

LANGUAGE_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala", ".sc": "scala",
    ".groovy": "groovy", ".gradle": "groovy",
    ".clj": "clojure", ".cljs": "clojure",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".vue": "vue", ".svelte": "svelte",
    ".go": "go",
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".m": "objc", ".mm": "objc",
    ".rb": "ruby",
    ".php": "php",
    ".dart": "dart",
    ".lua": "lua",
    ".ex": "elixir", ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml", ".mli": "ocaml",
    ".pl": "perl", ".pm": "perl",
    ".r": "r", ".R": "r",
    ".zig": "zig",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".ps1": "powershell", ".psm1": "powershell",
    ".bat": "batch", ".cmd": "batch",
    ".sol": "solidity",
}

ASSET_EXT = {
    ".yml", ".yaml", ".toml", ".json", ".jsonc", ".xml", ".ini", ".cfg", ".conf", ".properties",
    ".sql", ".graphql", ".gql", ".proto", ".html", ".htm", ".css", ".scss", ".less",
    ".j2", ".jinja", ".jinja2", ".hbs", ".mustache", ".ejs", ".erb", ".tmpl", ".tpl",
    ".tf", ".hcl", ".csv", ".tsv", ".env.example", ".lock", ".mod", ".sum", ".svg",
}
ASSET_NAMES = {
    "dockerfile", "makefile", "cmakelists.txt", "jenkinsfile", "procfile", "vagrantfile",
    ".gitignore", ".gitattributes", ".dockerignore", ".editorconfig", ".gitlab-ci.yml",
    "cargo.toml", "cargo.lock", "package.json", "go.mod", "go.sum", "pom.xml",
    "build.gradle", "settings.gradle", "requirements.txt", "pyproject.toml", "setup.cfg",
}
DOC_EXT = {".md", ".markdown", ".rst", ".adoc", ".txt"}
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svgz", ".pdf",
    ".zip", ".gz", ".tgz", ".7z", ".rar", ".tar", ".jar", ".war", ".class",
    ".so", ".dll", ".dylib", ".exe", ".a", ".lib", ".o", ".obj", ".wasm", ".node",
    ".ttf", ".otf", ".woff", ".woff2", ".eot", ".mp3", ".mp4", ".wav", ".mov",
    ".bin", ".dat", ".db", ".sqlite", ".parquet", ".onnx", ".pt", ".safetensors", ".gguf",
}
SECRET_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa*", "id_ed25519*", ".npmrc", ".pypirc", "*.local.md",
]
# 이름에 secret/credential 이 들어간 '소스 코드' 는 정상 분석 대상이다 (SecretManager.java 등) → 비코드 파일에만 적용
SECRET_NONCODE_PATTERNS = ["*credentials*", "*secret*"]
SECRET_ALLOW = ["*.example", "*.sample", "*.template", "*.dist"]
JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
# 범위 밖으로 빼는 규칙은 보수적으로 — 정상 코드가 빠지면 '숨은 빈틈'이 된다 (external/, build/ 같은 이름의 진짜 소스가 흔함)
VENDORED_DIRS = {"vendor", "third_party", "third-party", "thirdparty", "node_modules"}
GENERATED_PATTERNS = [
    "*.pb.go", "*_pb2.py", "*_pb2_grpc.py", "*.pb.cc", "*.pb.h", "*.generated.*", "*.g.dart",
    "*.freezed.dart", "*.min.js", "*.min.css", "*.bundle.js", "*.map",
]
GENERATED_DIRS = {"dist", "target", "generated", "__generated__", ".next"}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testing", "testdata", "fixtures"}
TEST_DATA_DIRS = {"snapshots", "__snapshots__", "corpus", "fixtures", "testdata", "test-data", "test_data", "golden"}
TEST_DATA_EXT = {".snap", ".snapshot", ".golden", ".jsonl", ".ndjson"}
TEST_PATTERNS = [
    "test_*.py", "*_test.py", "*_test.go", "*_test.rs", "*.test.ts", "*.test.tsx", "*.test.js",
    "*.spec.ts", "*.spec.tsx", "*.spec.js", "*Test.java", "*Tests.java", "*Test.kt", "*_spec.rb",
]


def _match(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def language_of(path: str) -> str | None:
    p = PurePosixPath(path)
    return LANGUAGE_BY_EXT.get(p.suffix) or LANGUAGE_BY_EXT.get(p.suffix.lower())


SHEBANG_LANG = {"python": "python", "bash": "bash", "sh": "bash", "zsh": "bash", "node": "javascript",
                 "ruby": "ruby", "perl": "perl", "pwsh": "powershell"}


def classify(path: str, is_binary: bool = False, first_line: str = "") -> tuple[str, str | None, str]:
    """(category, language, reason) 반환. reason 은 어떤 규칙에 걸렸는지."""
    p = PurePosixPath(path)
    name = p.name.lower()
    parts = {part.lower() for part in p.parts[:-1]}
    lang = language_of(path)
    if lang is None and not p.suffix and first_line.startswith("#!"):
        interp = first_line[2:].strip().split()
        exe = PurePosixPath(interp[-1] if interp and interp[0].endswith("env") and len(interp) > 1 else (interp[0] if interp else "")).name
        lang = SHEBANG_LANG.get(re.sub(r"[\d.]+$", "", exe))

    if _match(name, SECRET_PATTERNS) and not _match(name, SECRET_ALLOW):
        return "secret", None, "비밀정보 파일명 규칙"
    if lang is None and _match(name, SECRET_NONCODE_PATTERNS) and not _match(name, SECRET_ALLOW):
        return "secret", None, "비밀정보 파일명 규칙 (비코드 파일)"
    if parts & VENDORED_DIRS:
        return "vendored", lang, f"vendored 디렉터리: {sorted(parts & VENDORED_DIRS)[0]}"
    if is_binary or p.suffix.lower() in BINARY_EXT:
        return "binary", None, "바이너리 확장자" if not is_binary else "NUL 바이트 포함"
    if _match(name, GENERATED_PATTERNS) or (parts & GENERATED_DIRS and lang):
        return "generated", lang, "생성 코드 규칙"
    if lang and (parts & TEST_DIRS or _match(p.name, TEST_PATTERNS)):
        return "test", lang, "테스트 경로·파일명 규칙"
    if not lang and (p.suffix.lower() in TEST_DATA_EXT or parts & TEST_DATA_DIRS
                     or (parts & TEST_DIRS and p.suffix.lower() not in ASSET_EXT)):
        return "test-data", None, "테스트 스냅샷·코퍼스·fixture 규칙"
    if lang:
        return "code", lang, f"확장자 {p.suffix}" if p.suffix else f"셔뱅 {first_line[:40]}"
    if name in ASSET_NAMES or p.suffix.lower() in ASSET_EXT or name.startswith("dockerfile"):
        return "asset", None, "설정·자산 규칙"
    if name.startswith(".") and (name.endswith("ignore") or name.endswith("rc") or name.endswith("config")):
        return "asset", None, "도구 설정 dotfile 규칙"
    if name in JUNK_NAMES:
        return "binary", None, "OS 메타데이터 파일"
    if (p.suffix.lower() in DOC_EXT or "docs" in parts
            or re.match(r"^(license|licence|notice|authors|changelog|contributors|copying)([-_.].*)?$", name)):
        return "doc", None, "문서 규칙"
    return "unclassified", None, "어떤 규칙에도 해당하지 않음"


ANALYZED_CATEGORIES = {"code", "test", "asset"}
UNCOVERED_CATEGORIES = {"binary"}  # 분석은 못 하지만 존재는 기록 → 미확인 생성
