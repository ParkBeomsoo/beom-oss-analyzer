"""분석 단위 분할 — 같은 입력(커밋)이면 항상 같은 단위가 나오도록 결정적으로 나눈다.

1. 분석 대상 파일(code/test/asset)을 (범주, 디렉터리)로 묶는다
2. 묶음의 총 줄 수가 한도 이하면 그대로 한 단위
3. 넘으면 경로순으로 한도까지 잘라 여러 단위
4. 파일 하나가 한도를 넘으면 심볼 경계로 줄 범위를 잘라 여러 단위 (심볼이 없으면 줄 단위로 자름)
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import PurePosixPath

from oss_analyzer.core.classify import ANALYZED_CATEGORIES


def _unit_id(kind: str, members: list[dict]) -> str:
    key = kind + "|" + "|".join(f"{m['path']}:{m['start_line']}-{m['end_line']}" for m in members)
    return "u_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def _split_file(path: str, lines: int, symbols: list[dict], max_lines: int) -> list[dict]:
    """큰 파일을 최상위 심볼 경계에서 자른다. 경계가 없으면 줄 수로 강제로 자른다."""
    # 최상위 심볼 시작줄만 경계 후보 (중첩 심볼은 바깥 심볼과 같은 조각에 남김)
    boundaries: list[int] = []
    last_end = 0
    for s in sorted(symbols, key=lambda s: (s["start_line"], -s["end_line"])):
        if s["start_line"] > last_end:
            if s["start_line"] > 1:
                boundaries.append(s["start_line"])
            last_end = s["end_line"]

    pieces: list[tuple[int, int]] = []
    start = 1
    while start <= lines:
        limit_end = start + max_lines - 1
        if limit_end >= lines:
            pieces.append((start, lines))
            break
        candidates = [b - 1 for b in boundaries if start < b <= limit_end + 1]
        end = max(candidates) if candidates else limit_end
        pieces.append((start, end))
        start = end + 1
    return [{"path": path, "start_line": a, "end_line": b} for a, b in pieces]


def build_units(files: list[dict], symbols_by_path: dict[str, list[dict]], max_lines: int,
                max_chars: int | None = None) -> list[dict]:
    """줄 수와 글자 수 한도를 모두 지킨다 (긴 줄이 많은 Java·JSON 은 줄 수만으로는 한 번에 읽기 버거움)."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for f in files:
        if f["category"] not in ANALYZED_CATEGORIES:
            continue
        parent = str(PurePosixPath(f["path"]).parent)
        groups[(f["category"], parent)].append(f)

    def weight(f: dict) -> int:
        """줄 수 환산 크기 — 글자 수 한도를 줄 수 한도 비율로 바꿔 둘 중 큰 쪽."""
        n = max(f["lines"], 1)
        if not max_chars:
            return n
        return max(n, -(-f.get("chars", 0) * max_lines // max_chars))

    units = []
    for (kind, directory), members in sorted(groups.items()):
        members.sort(key=lambda f: f["path"])
        batch: list[dict] = []
        batch_weight = 0

        def flush(kind: str = kind, directory: str = directory) -> None:
            nonlocal batch, batch_weight
            if batch:
                units.append({**_make(kind, directory, batch), "_weight": batch_weight})
                batch, batch_weight = [], 0

        for f in members:
            n, w = max(f["lines"], 1), weight(f)
            if w > max_lines:
                flush()
                piece_lines = max(1, max_lines * n // w)  # 글자 수 한도도 지키도록 줄 수를 줄인다
                for piece in _split_file(f["path"], n, symbols_by_path.get(f["path"], []), piece_lines):
                    units.append({**_make(kind, directory, [piece]), "_split": True, "_weight": max_lines})
                continue
            if batch_weight + w > max_lines:
                flush()
            batch.append({"path": f["path"], "start_line": 1, "end_line": n})
            batch_weight += w
        flush()
    return _pack_siblings(units, max_lines)


def _pack_siblings(units: list[dict], max_lines: int) -> list[dict]:
    """같은 부모 아래 형제 폴더의 작은 단위를 한도까지 묶는다 (Java 패키지처럼 폴더가 잘게 나뉜 경우).

    큰 파일을 자른 조각은 묶지 않는다. 입력 순서(범주·경로순)를 유지하므로 결과도 결정적이다.
    """
    out: list[dict] = []
    for u in units:
        directory = str(PurePosixPath(u["members"][0]["path"]).parent)
        parent = str(PurePosixPath(directory).parent)
        prev = out[-1] if out else None
        if (prev is not None and not prev.get("_split") and not u.get("_split") and prev["kind"] == u["kind"]
                and prev["_parent"] == parent and prev["_weight"] + u["_weight"] <= max_lines):
            members = prev["members"] + u["members"]
            merged = _make(u["kind"], parent, members)
            dirs = sorted({str(PurePosixPath(m["path"]).parent) for m in members})
            if len(dirs) > 1:
                names = ", ".join(PurePosixPath(d).name for d in dirs[:4]) + (" …" if len(dirs) > 4 else "")
                merged["title"] = f"{parent}/{{{names}}} ({len(members)}개 파일)"
            merged["_parent"] = parent
            merged["_weight"] = prev["_weight"] + u["_weight"]
            out[-1] = merged
        else:
            out.append({**u, "_parent": parent})
    for u in out:
        for key in ("_parent", "_split", "_weight"):
            u.pop(key, None)
    return out


def _make(kind: str, directory: str, members: list[dict]) -> dict:
    lines = sum(m["end_line"] - m["start_line"] + 1 for m in members)
    first = members[0]
    if len(members) == 1:
        title = f"{first['path']}:{first['start_line']}-{first['end_line']}"
    else:
        title = f"{directory}/ ({len(members)}개 파일)"
    return {"id": _unit_id(kind, members), "kind": kind, "title": title, "members": members, "line_count": lines}


def unit_for_symbol(units_by_path: dict[str, list[dict]], symbol: dict) -> str | None:
    """심볼 시작줄을 포함하는 단위."""
    for unit, member in units_by_path.get(symbol["path"], []):
        if member["start_line"] <= symbol["start_line"] <= member["end_line"]:
            return unit["id"]
    return None
