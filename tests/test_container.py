"""컨테이너 실행기 경로 변환·서버 직접 실행 판정 (엔진 없이)."""
import os
import sys

from oss_analyzer.core import container, port


def test_wsl_path_translation():
    e = container.Engine("wsl", ("wsl", "-d", "x", "--", "docker"), distro="x")
    assert e.path(r"C:\Users\me\.oss-analyzer\runs\p") == "/mnt/c/Users/me/.oss-analyzer/runs/p"
    assert e.path(r"D:\study\t") == "/mnt/d/study/t"
    assert container.Engine("docker", ("docker",)).path(r"C:\a") == r"C:\a"
    assert e.argv("ps") == ["wsl", "-d", "x", "--", "docker", "ps"]


def test_direct_argv_avoids_shell_for_plain_exe():
    cmd = f'"{sys.executable}" -m http.server 18999'
    args, shell = port._direct_argv(cmd)
    assert shell is False  # 종료 신호가 셸이 아니라 서버에 바로 간다
    assert port._direct_argv("a && b")[1] is True
    if os.name == "nt":
        assert port._direct_argv("npx something")[1] is True  # .cmd 는 셸 필요
