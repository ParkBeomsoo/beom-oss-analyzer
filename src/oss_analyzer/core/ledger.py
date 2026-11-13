"""분석 원장 — 프로젝트별 SQLite. 세션이 바뀌어도 진행 상태가 그대로 이어진다.

스키마 버전은 PRAGMA user_version 으로 관리하고, MIGRATIONS 에 순서대로 추가한다.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

MIGRATIONS: list[str] = [
    # v1 — MVP 스키마
    """
    CREATE TABLE project (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        commit_sha TEXT NOT NULL,
        repo_dir TEXT NOT NULL,
        created_at TEXT NOT NULL,
        inventory_at TEXT,
        capabilities TEXT
    );
    CREATE TABLE file (
        path TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        language TEXT,
        lines INTEGER NOT NULL DEFAULT 0,
        sha TEXT,
        reason TEXT
    );
    CREATE TABLE unit (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        members TEXT NOT NULL,
        line_count INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        level INTEGER,
        target_level INTEGER NOT NULL,
        ceiling_level INTEGER NOT NULL,
        lease_owner TEXT,
        lease_expires_at REAL,
        summary TEXT,
        completed_at TEXT
    );
    CREATE TABLE symbol (
        id TEXT PRIMARY KEY,
        qualified_name TEXT NOT NULL,
        name TEXT NOT NULL,
        label TEXT NOT NULL,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        unit_id TEXT,
        reviewed INTEGER NOT NULL DEFAULT 0,
        reviewed_by TEXT
    );
    CREATE INDEX symbol_unit ON symbol(unit_id);
    CREATE INDEX symbol_path ON symbol(path);
    CREATE TABLE feature (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        framework TEXT,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        handler TEXT,
        source TEXT NOT NULL,
        evidence_id TEXT
    );
    CREATE TABLE evidence (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        operation TEXT NOT NULL,
        parameters TEXT NOT NULL,
        result TEXT NOT NULL,
        locations TEXT NOT NULL,
        confidence TEXT NOT NULL,
        level INTEGER NOT NULL,
        limitations TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE claim (
        id TEXT PRIMARY KEY,
        unit_id TEXT NOT NULL,
        text TEXT NOT NULL,
        severity TEXT NOT NULL,
        evidence_ids TEXT NOT NULL,
        symbol_ids TEXT NOT NULL,
        level INTEGER NOT NULL,
        author TEXT NOT NULL,
        review_status TEXT NOT NULL,
        reviewer TEXT,
        review_note TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX claim_unit ON claim(unit_id);
    CREATE TABLE unknown (
        id TEXT PRIMARY KEY,
        revision INTEGER NOT NULL,
        question TEXT NOT NULL,
        domain TEXT NOT NULL,
        scope TEXT,
        status TEXT NOT NULL,
        severity TEXT NOT NULL,
        required_level INTEGER NOT NULL,
        supporting_evidence_ids TEXT NOT NULL,
        contradicting_evidence_ids TEXT NOT NULL,
        recommended_probes TEXT NOT NULL,
        resolution TEXT,
        origin TEXT NOT NULL,
        history TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE customization (
        id TEXT PRIMARY KEY,
        description TEXT NOT NULL,
        symbol_ids TEXT NOT NULL,
        unit_ids TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    # v2 — 구조 지도 호출 간선 캐시 (단위마다 엔진을 다시 부르지 않도록)
    """
    CREATE TABLE call_edge (
        caller TEXT NOT NULL,
        caller_path TEXT NOT NULL,
        caller_line INTEGER NOT NULL,
        callee TEXT NOT NULL,
        callee_path TEXT,
        callee_line INTEGER,
        call_line INTEGER,
        confidence REAL,
        strategy TEXT
    );
    CREATE INDEX call_edge_caller_path ON call_edge(caller_path);
    CREATE INDEX call_edge_callee_path ON call_edge(callee_path);
    ALTER TABLE feature ADD COLUMN in_test INTEGER NOT NULL DEFAULT 0;
    """,
    # v3 — 라우트 조합 결과 (마운트·클래스 접두·동적 해석)
    """
    ALTER TABLE feature ADD COLUMN method TEXT;
    ALTER TABLE feature ADD COLUMN full_path TEXT;
    ALTER TABLE feature ADD COLUMN assumptions TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE feature ADD COLUMN derived_from TEXT;
    """,
    # v4 — 라우트를 등록한 라우터 객체(변수) 이름: 마운트 조합에 사용
    """
    ALTER TABLE feature ADD COLUMN receiver TEXT;
    """,
    # v5 — 확장 포인트 + 커스텀 원장 보강 (상태·앵커로 업스트림 변경 감지)
    """
    CREATE TABLE extension_point (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        language TEXT,
        path TEXT NOT NULL,
        line INTEGER NOT NULL,
        symbol_id TEXT,
        implementations TEXT NOT NULL DEFAULT '[]',
        usages TEXT NOT NULL DEFAULT '[]',
        note TEXT,
        evidence_id TEXT
    );
    CREATE INDEX extension_point_path ON extension_point(path);
    ALTER TABLE customization ADD COLUMN kind TEXT NOT NULL DEFAULT 'modify';
    ALTER TABLE customization ADD COLUMN status TEXT NOT NULL DEFAULT 'planned';
    ALTER TABLE customization ADD COLUMN author TEXT;
    ALTER TABLE customization ADD COLUMN extension_point_id TEXT;
    ALTER TABLE customization ADD COLUMN anchors TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE customization ADD COLUMN commit_sha TEXT;
    ALTER TABLE customization ADD COLUMN history TEXT NOT NULL DEFAULT '[]';
    """,
    # v6 — 실행 검증(L3) 커버리지
    """
    CREATE TABLE coverage_run (
        id TEXT PRIMARY KEY,
        tool TEXT NOT NULL,
        format TEXT NOT NULL,
        commit_sha TEXT NOT NULL,
        command TEXT,
        report_path TEXT,
        files_matched INTEGER NOT NULL,
        files_unmatched INTEGER NOT NULL,
        symbols_hit INTEGER NOT NULL,
        symbols_missed INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE coverage_hit (
        run_id TEXT NOT NULL,
        symbol_id TEXT NOT NULL,
        evidence_id TEXT,
        lines_hit INTEGER NOT NULL,
        lines_measured INTEGER NOT NULL
    );
    CREATE INDEX coverage_hit_symbol ON coverage_hit(symbol_id);
    CREATE TABLE license_finding (
        path TEXT NOT NULL,
        license TEXT NOT NULL,
        category TEXT NOT NULL,
        source TEXT NOT NULL,
        line INTEGER NOT NULL,
        scope TEXT NOT NULL,
        evidence_id TEXT
    );
    """,
    # v7 — 포팅 원장 (내 저장소로 옮기기·다른 언어로 다시 만들기)
    """
    CREATE TABLE port (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        targets TEXT NOT NULL,
        target_language TEXT NOT NULL,
        target_root TEXT NOT NULL,
        slice TEXT NOT NULL,
        author TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE port_map (
        port_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        label TEXT NOT NULL,
        status TEXT NOT NULL,
        target_ref TEXT,
        note TEXT,
        actor TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (port_id, item_id)
    );
    CREATE TABLE port_check (
        id TEXT PRIMARY KEY,
        port_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        cases INTEGER NOT NULL,
        passed INTEGER NOT NULL,
        failed INTEGER NOT NULL,
        detail TEXT NOT NULL,
        evidence_id TEXT,
        created_at TEXT NOT NULL
    );
    """,
    # v8: ported 대상 줄 내용 — 대상 저장소가 바뀌어 줄이 밀리거나 내용이 바뀐 것을 잡는다
    """
    ALTER TABLE port_map ADD COLUMN target_text TEXT;
    """,
    # v9: 포팅 기록 해시 사슬 — 사람 승인, 툴킷 밖 수정 탐지 (core/port_guard.py)
    """
    CREATE TABLE port_log (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        port_id TEXT NOT NULL,
        action TEXT NOT NULL,
        payload TEXT NOT NULL,
        at TEXT NOT NULL,
        prev TEXT NOT NULL,
        hash TEXT NOT NULL
    );
    CREATE INDEX port_log_port ON port_log (port_id, seq);
    ALTER TABLE port ADD COLUMN log_head TEXT;
    """,
    # v10: 요청사항 기반 만들기 (core/make.py) — 원본 없이 요구사항이 항목이 되는 원장
    """
    ALTER TABLE port ADD COLUMN mode TEXT NOT NULL DEFAULT 'oss';
    ALTER TABLE port ADD COLUMN request TEXT;
    ALTER TABLE port ADD COLUMN context TEXT;
    """,
]

JSON_COLUMNS = {
    "capabilities", "members", "parameters", "result", "locations", "limitations",
    "evidence_ids", "symbol_ids", "unit_ids", "supporting_evidence_ids",
    "contradicting_evidence_ids", "recommended_probes", "resolution", "history", "assumptions",
    "implementations", "usages", "anchors", "targets", "slice", "detail",
}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: 자동 트랜잭션을 끄고 쓰기는 tx() 로 명시적으로 묶는다
    # 여러 프로세스(병렬 에이전트의 CLI)가 같은 원장을 쓰므로 잠금 대기를 넉넉히 둔다
    conn = sqlite3.connect(db_path, timeout=120, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 120000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE 트랜잭션 — 임대(lease) 경합을 직렬화한다.

    트랜잭션 안에서는 쓰기만 한다 (파일 읽기·외부 명령은 밖에서 끝낸 뒤 들어온다).
    시작 시 잠금 실패는 백오프하며 재시도한다.
    """
    for attempt in range(8):
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) and "busy" not in str(e):
                raise
            if attempt == 7:
                raise
            time.sleep(min(2 ** attempt, 15))
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = {}
    for key in row.keys():
        value = row[key]
        out[key] = json.loads(value) if key in JSON_COLUMNS and value is not None else value
    return out


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [row_to_dict(r) for r in rows]
