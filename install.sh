#!/usr/bin/env sh
# beom-oss-analyzer 설치 (macOS · Linux) — 앱까지 설치하고, Linux 는 앱 메뉴 바로가기를 만들지 묻는다.
#   curl -fsSL https://raw.githubusercontent.com/ParkBeomsoo/beom-oss-analyzer/main/install.sh | sh
# 묻지 않고 정하려면 OSS_SHORTCUT=start,desktop (또는 start, none) 를 두고 실행한다.
set -e
SOURCE="${OSS_SOURCE:-git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git}"

if ! command -v pipx >/dev/null 2>&1; then
  echo "pipx 가 필요합니다: python3 -m pip install --user pipx && python3 -m pipx ensurepath (새 터미널에서 다시 실행)"
  exit 1
fi

echo "beom-oss-analyzer 를 설치합니다 (앱 포함)…"
pipx install --force "beom-oss-analyzer[app] @ $SOURCE"

if [ "$(uname -s)" = "Linux" ]; then
  CHOICE="${OSS_SHORTCUT:-}"
  if [ -z "$CHOICE" ] && [ -t 0 ]; then
    printf "앱 메뉴와 바탕화면에 바로가기를 만들까요? [Y: 둘 다 / s: 메뉴만 / n: 안 함] "
    read -r ANS
    case "$ANS" in s*|S*) CHOICE=start ;; n*|N*) CHOICE=none ;; *) CHOICE=start,desktop ;; esac
  fi
  if [ -n "$CHOICE" ] && [ "$CHOICE" != "none" ]; then
    oss-analyzer app --shortcut "$CHOICE" >/dev/null && echo "바로가기를 만들었습니다."
  fi
fi
echo "앱 열기: beom-oss-analyzer    /    상태 확인: oss-analyzer doctor"
