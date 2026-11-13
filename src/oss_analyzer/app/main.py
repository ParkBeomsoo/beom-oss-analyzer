"""beom-oss-analyzer 데스크톱 앱 — 네이티브 창 안에 화면을 그린다 (Qt WebEngine). 브라우저도 서버도 쓰지 않는다.

화면과 툴킷은 Qt WebChannel(같은 프로세스 안의 연결)로만 통한다. 네트워크 포트를 열지 않으므로 승인·답변 같은
사람의 결정은 이 창에서만 기록되고, 에이전트가 대신 누를 경로가 없다. 바깥 주소로의 이동은 막는다.
"""
from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon

from oss_analyzer.app.bridge import Api
from oss_analyzer.config import load_settings


def web_root() -> Path:
    return Path(str(resources.files("oss_analyzer.app") / "web"))


APP_NAME = "beom-oss-analyzer"
APP_ID = "beom.oss-analyzer.app"
BG = "#f4f4f6"


def _icon_pixmap(size: int) -> QPixmap:
    """작업 표시줄·트레이 아이콘 — 코발트 바탕에 흰 마름모 테두리와 속 (사이드바 로고와 같은 모양)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64
    g = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    g.setColorAt(0, QColor("#5b6cff"))
    g.setColorAt(1, QColor("#2f3fe0"))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 15 * s, 15 * s)

    def diamond(cx: float, cy: float, r: float) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(cx * s, (cy - r) * s)
        path.lineTo((cx + r) * s, cy * s)
        path.lineTo(cx * s, (cy + r) * s)
        path.lineTo((cx - r) * s, cy * s)
        path.closeSubpath()
        return path

    pen = QPen(QColor("#ffffff"), 5.5 * s)
    pen.setJoinStyle(Qt.MiterJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(diamond(32, 32, 17))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawPath(diamond(32, 32, 6.5))
    p.end()
    return pm


def write_icon_files(home: Path) -> Path:
    """바로가기용 아이콘 파일 — app.ico(여러 크기 PNG 를 담은 ICO)와 app.png. 앱 창이 없어도 그린다."""
    import struct

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:
        _app = QGuiApplication([])  # noqa: F841 — 그림을 그리려면 하나 있어야 한다
    home.mkdir(parents=True, exist_ok=True)
    blobs = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        _icon_pixmap(size).save(buf, "PNG")
        blobs.append((size, bytes(ba)))
    head = struct.pack("<HHH", 0, 1, len(blobs))
    entries, data, offset = b"", b"", 6 + 16 * len(blobs)
    for size, png in blobs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        data += png
        offset += len(png)
    ico = home / "app" / "app.ico"  # 바로가기가 가리키므로 지워도 되는 cache/ 가 아니라 app/
    ico.parent.mkdir(parents=True, exist_ok=True)
    ico.write_bytes(head + entries + data)
    (home / "app" / "app.png").write_bytes(blobs[-1][1])
    return ico


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon.addPixmap(_icon_pixmap(size))
    return icon


def _windows_setup() -> None:
    """Windows 전용 준비.

    - 작업 표시줄이 이 창을 python 이 아니라 별도 앱으로 묶고 우리 아이콘을 쓰게 앱 ID 를 정한다.
    - 이 앱은 콘솔 없는 창 프로그램이라, 콘솔 프로그램(git·codex·claude)을 띄울 때마다 Windows 가 터미널 창을
      잠깐 열었다 닫는다. 따로 창을 정하지 않은 자식 프로세스는 창 없이 띄운다 (로그인 창처럼 일부러 여는 것은 그대로).
    """
    if sys.platform != "win32":
        return
    import ctypes
    import subprocess

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass
    if getattr(subprocess.Popen, "_oss_no_window", False):
        return
    base = subprocess.Popen

    class Popen(base):  # type: ignore[misc, valid-type]
        _oss_no_window = True

        def __init__(self, *args, **kwargs):
            if not kwargs.get("creationflags"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = Popen


class Page(QWebEnginePage):
    """앱 안의 화면만 보여 준다. 링크는 기본 브라우저로 넘기고, 창 안에서는 바깥 주소로 가지 않는다."""

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:
        if url.scheme() in ("file", "qrc", "data"):
            return True
        if url.scheme() in ("http", "https") and nav_type == QWebEnginePage.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
        return False


class Window(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings or load_settings()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1400, 880)
        self.setMinimumSize(1080, 680)
        self.view = QWebEngineView(self)
        page = Page(self.view)
        s = page.settings()
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
        s.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, False)
        page.setBackgroundColor(QColor(BG))
        self.view.setPage(page)
        self.api = Api(self.settings, self)
        self.channel = QWebChannel(page)
        self.channel.registerObject("api", self.api)
        page.setWebChannel(self.channel)
        self.view.setContextMenuPolicy(Qt.NoContextMenu)
        self.view.load(QUrl.fromLocalFile(str(web_root() / "index.html")))
        self.setCentralWidget(self.view)
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(lambda *_: (self.showNormal(), self.activateWindow()))
        self.tray.show()
        self.api.notify.connect(self._notify)

    def _notify(self, title: str, body: str) -> None:
        if not self.isActiveWindow() and QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(title, body, app_icon(), 6000)


def main(argv: list[str] | None = None) -> int:
    _windows_setup()
    try:  # 예전 배치 옮기기·오래된 휴지통 비우기 (실패해도 앱은 연다)
        from oss_analyzer.core import storage
        storage.maintenance(load_settings())
    except Exception:  # noqa: BLE001
        pass
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(app_icon())
    w = Window()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
