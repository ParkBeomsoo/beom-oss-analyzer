# beom-oss-analyzer 설치 (Windows) — 앱까지 설치하고, 시작 메뉴·바탕화면 바로가기를 만들지 묻는다.
#   irm https://raw.githubusercontent.com/ParkBeomsoo/beom-oss-analyzer/main/install.ps1 | iex
# 묻지 않고 정하려면 먼저 $env:OSS_SHORTCUT = "start,desktop" (또는 "start", "none") 를 두고 실행한다.
$ErrorActionPreference = "Stop"
$source = if ($env:OSS_SOURCE) { $env:OSS_SOURCE } else { "git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git" }

if (-not (Get-Command pipx -ErrorAction SilentlyContinue)) {
    Write-Host "pipx 가 필요합니다. 아래 두 줄을 실행한 뒤 새 터미널에서 다시 실행해 주세요:" -ForegroundColor Yellow
    Write-Host "  python -m pip install --user pipx"
    Write-Host "  python -m pipx ensurepath"
    exit 1
}

Write-Host "beom-oss-analyzer 를 설치합니다 (앱 포함)…"
pipx install --force "beom-oss-analyzer[app] @ $source"
if ($LASTEXITCODE -ne 0) { Write-Host "설치에 실패했습니다." -ForegroundColor Red; exit 1 }

$choice = $env:OSS_SHORTCUT
if (-not $choice) {
    $ans = Read-Host "시작 메뉴와 바탕화면에 바로가기를 만들까요? [Y: 둘 다 / s: 시작 메뉴만 / n: 안 함]"
    $choice = switch -Regex ($ans) { "^[sS]" { "start" } "^[nN]" { "none" } default { "start,desktop" } }
}
if ($choice -ne "none") {
    oss-analyzer app --shortcut $choice | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "바로가기를 만들었습니다 — 시작 메뉴에서 'beom' 으로 검색하세요." -ForegroundColor Green }
    else { Write-Host "바로가기를 만들지 못했습니다. 나중에 'oss-analyzer app --shortcut' 으로 만들 수 있습니다." -ForegroundColor Yellow }
}
Write-Host "앱 열기: beom-oss-analyzer    /    상태 확인: oss-analyzer doctor"
