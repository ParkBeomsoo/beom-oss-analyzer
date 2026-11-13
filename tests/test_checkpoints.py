"""체크포인트 — 사용자 git 을 건드리지 않고 스냅샷·변경 보기·되돌리기."""
import subprocess

from oss_analyzer.core.checkpoints import Checkpoints


def test_snapshot_changes_and_restore(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)  # 사용자 저장소 (손대지 않아야 함)
    cp = Checkpoints(tmp_path / "run", root)
    base = cp.snapshot("시작")
    (root / "a.py").write_text("x = 2\n", encoding="utf-8")
    (root / "b.py").write_text("y = 1\n", encoding="utf-8")
    ch = cp.changes(base)
    assert {f["path"]: f["status"] for f in ch["files"]} == {"a.py": "M", "b.py": "A"}
    assert "+x = 2" in ch["diff"]
    r1 = cp.snapshot("라운드 1")
    assert r1 != base and len(cp.list()) == 2
    out = cp.restore(base)
    assert (root / "a.py").read_text(encoding="utf-8") == "x = 1\n" and not (root / "b.py").exists()
    assert out["removed_files"] == ["b.py"]
    # 사용자 저장소의 인덱스·커밋은 그대로
    st = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert "a.py" in st and subprocess.run(["git", "-C", str(root), "rev-parse", "-q", "--verify", "HEAD"],
                                             capture_output=True).returncode != 0
    cp.restore(out["undo"])  # 되돌리기도 되돌릴 수 있음
    assert (root / "b.py").exists()
