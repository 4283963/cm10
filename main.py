"""
全自动血液细胞流式分析仪 - 应用程序入口
三甲医院检验科专用
"""

import sys
import os
import traceback
import threading
from datetime import datetime


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QPalette, QColor
from PySide6.QtCore import Qt, QObject, Signal

from main_window import MainWindow


LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "error.log"
)


class GlobalExceptionHandler(QObject):
    error_signal = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def handle_exception(self, exc_type, exc_value, exc_tb):
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] Uncaught exception:\n{tb_str}\n"

        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass

        try:
            self.error_signal.emit(str(exc_value))
        except Exception:
            pass

        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

    def handle_thread_exception(self, args):
        self.handle_exception(args.exc_type, args.exc_value, args.exc_traceback)


def setup_app_style(app: QApplication):
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(15, 23, 42))
    palette.setColor(QPalette.WindowText, QColor(226, 232, 240))
    palette.setColor(QPalette.Base, QColor(15, 23, 42))
    palette.setColor(QPalette.AlternateBase, QColor(30, 41, 59))
    palette.setColor(QPalette.ToolTipBase, QColor(15, 23, 42))
    palette.setColor(QPalette.ToolTipText, QColor(226, 232, 240))
    palette.setColor(QPalette.Text, QColor(226, 232, 240))
    palette.setColor(QPalette.Button, QColor(30, 41, 59))
    palette.setColor(QPalette.ButtonText, QColor(226, 232, 240))
    palette.setColor(QPalette.BrightText, QColor(239, 68, 68))
    palette.setColor(QPalette.Link, QColor(37, 99, 235))
    palette.setColor(QPalette.Highlight, QColor(37, 99, 235))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    font = QFont()
    font.setPointSize(10)
    app.setFont(font)

    app.setStyleSheet("""
        QWidget { background: #0f172a; color: #e2e8f0; }
        QLabel { background: transparent; }
        QComboBox, QSpinBox {
            background: #1e293b; color: #e2e8f0;
            border: 1px solid #334155; border-radius: 4px; padding: 4px 8px;
            min-height: 20px;
        }
        QComboBox:hover, QSpinBox:hover { border-color: #475569; }
        QComboBox:focus, QSpinBox:focus { border-color: #3b82f6; }
        QComboBox::drop-down { border: none; width: 24px; }
        QComboBox QAbstractItemView {
            background: #1e293b; color: #e2e8f0;
            selection-background-color: #2563eb; border: 1px solid #334155;
        }
        QSpinBox::up-button, QSpinBox::down-button { width: 16px; }
        QScrollBar:vertical {
            background: #1e293b; width: 12px; margin: 0;
        }
        QScrollBar::handle:vertical {
            background: #475569; min-height: 24px; border-radius: 4px;
        }
        QScrollBar::handle:vertical:hover { background: #64748b; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar:horizontal {
            background: #1e293b; height: 12px; margin: 0;
        }
        QScrollBar::handle:horizontal {
            background: #475569; min-width: 24px; border-radius: 4px;
        }
        QScrollBar::handle:horizontal:hover { background: #64748b; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
        QMessageBox {
            background: #1e293b; color: #e2e8f0;
        }
        QMessageBox QPushButton {
            background: #2563eb; color: white; padding: 6px 16px;
            border-radius: 4px; min-width: 80px;
        }
    """)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("全自动血液细胞流式分析仪")
    app.setOrganizationName("三甲医院检验科")

    setup_app_style(app)

    exc_handler = GlobalExceptionHandler()
    sys.excepthook = exc_handler.handle_exception
    if hasattr(threading, "excepthook"):
        threading.excepthook = exc_handler.handle_thread_exception

    window = MainWindow()
    exc_handler.error_signal.connect(window.on_global_error)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
