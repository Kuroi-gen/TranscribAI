"""
main.py
TranscribAI - Windows 11 リアルタイム音声文字起こし＆AI要約アプリケーション
エントリポイント。
初回起動時（APIキー未設定時）には自動的にAPI設定・接続テストダイアログを表示する。
"""

import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter, QBrush
from PyQt6.QtCore import Qt, QTimer

from main_window import MainWindow


def make_app_icon() -> QIcon:
    """タスクバー・ウィンドウタイトルバー用のアプリアイコンを動的生成する。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor("#007ACC")))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)

    painter.setPen(QColor("white"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(32)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    painter.end()
    return QIcon(pixmap)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TranscribAI")
    app.setWindowIcon(make_app_icon())

    window = MainWindow()
    window.show()

    # メインウィンドウ描画直後にAPIキー未設定なら設定・接続テストダイアログを自動表示
    QTimer.singleShot(150, window.check_first_launch_api_setup)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
