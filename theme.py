# -*- coding: utf-8 -*-
"""هویت بصری «ایمن آرا سورنا» (IMENARA SORENA):

- پالت رنگی استخراج‌شده از لوگوی شرکت (سپر دو رنگ).
- `resource_path`: مسیر فایل‌های assets هم در اجرای عادی و هم داخل exe
  ساخته‌شده با PyInstaller (sys._MEIPASS) درست کار می‌کند - برای بیلد exe
  کافی است پوشه‌ی assets با `--add-data "assets;assets"` همراه شود.
- `apply_theme(app)`: تم تیره‌ی سازگار با لوگو را روی کل برنامه اعمال
  می‌کند.
"""

import os
import sys

# ------------------------------------------------------------- پالت لوگو ---
DARK_SLATE = "#3a4b52"   # نیمه‌ی تیره‌ی سپر / رنگ متن لوگو
LOGO_BLUE = "#0f7cc1"    # نیمه‌ی آبی سپر
WARM_GRAY = "#9b978c"    # حلقه‌ی کرم دور دایره

# ------------------------------------------------------- پالت تم تیره -----
BG_DEEP = "#1b2227"      # پس‌زمینه‌ی اصلی (مشتق از DARK_SLATE، تیره‌تر)
BG_PANEL = "#242e34"     # پس‌زمینه‌ی پنل‌ها/گروه‌ها
BG_INPUT = "#20282e"     # ورودی‌ها، لیست‌ها، جدول‌ها
BORDER = "#3a4b52"       # حاشیه‌ها (همان رنگ تیره‌ی لوگو)
TEXT = "#e9eef1"         # متن اصلی
TEXT_MUTED = "#9b978c"   # متن کم‌رنگ (همان کرم لوگو)
ACCENT = LOGO_BLUE       # آبی تاکیدی لوگو
ACCENT_HOVER = "#2a9bd8"
DANGER = "#e74c3c"

APP_NAME_FA = "ایمن آرا سورنا"
APP_NAME_EN = "IMENARA SORENA"


def resource_path(relative_path):
    """مسیر مطلق یک فایل داخل پوشه‌ی assets؛ هم برای اجرای عادی و هم برای
    exe ساخته‌شده با PyInstaller (که فایل‌ها را در sys._MEIPASS باز می‌کند)."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)


LOGO_SHIELD = resource_path(os.path.join("assets", "logo_shield.png"))
LOGO_FULL = resource_path(os.path.join("assets", "logo_full.png"))
APP_ICON = resource_path(os.path.join("assets", "app.ico"))


APP_STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {BG_DEEP};
    color: {TEXT};
}}
QLabel {{
    color: {TEXT};
    background: transparent;
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 6px;
    font-weight: bold;
    color: {TEXT_MUTED};
    background-color: {BG_PANEL};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top right;
    padding: 0 6px;
    color: {TEXT_MUTED};
}}
QPushButton {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 12px;
    color: {TEXT};
}}
QPushButton:hover {{
    border-color: {ACCENT};
    background-color: #2b363d;
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: white;
}}
QPushButton:disabled {{
    color: #6b7680;
    background-color: {BG_DEEP};
    border-color: #2b343a;
}}
QLineEdit, QTextEdit, QComboBox, QDateEdit, QSpinBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 5px 8px;
    color: {TEXT};
    selection-background-color: {ACCENT};
}}
QComboBox QAbstractItemView, QListView {{
    background-color: {BG_INPUT};
    color: {TEXT};
    selection-background-color: {ACCENT};
    border: 1px solid {BORDER};
}}
QListWidget, QTreeWidget, QTableWidget {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    color: {TEXT};
    alternate-background-color: #232c33;
    selection-background-color: {ACCENT};
}}
QHeaderView::section {{
    background-color: {BG_PANEL};
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 7px 4px;
    font-weight: bold;
}}
QTableWidget::item:selected {{
    background-color: {ACCENT};
    color: white;
}}
QScrollBar:vertical {{
    background: {BG_DEEP};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 6px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {BG_DEEP};
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 6px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {ACCENT};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QSplitter::handle {{
    background-color: {BORDER};
}}
QSplitter::handle:horizontal {{
    width: 3px;
}}
QSplitter::handle:vertical {{
    height: 3px;
}}
QToolTip {{
    background-color: {BG_PANEL};
    color: {TEXT};
    border: 1px solid {ACCENT};
    padding: 4px;
}}
QMenu {{
    background-color: {BG_PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
}}
QMenu::item:selected {{
    background-color: {ACCENT};
    color: white;
}}
QMessageBox {{
    background-color: {BG_PANEL};
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 5px;
}}
QTabBar::tab {{
    background: {BG_PANEL};
    color: {TEXT_MUTED};
    padding: 8px 16px;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{
    background: {ACCENT};
    color: white;
}}
QStatusBar {{
    background-color: {BG_PANEL};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER};
}}
QStatusBar::item {{
    border: none;
}}
"""


def _ensure_vazirmatn(app):
    """فونت واحد برنامه: وزیرمتن (باندل‌شده در assets/fonts — بدون نصب روی ویندوز)."""
    try:
        from PyQt6.QtGui import QFontDatabase, QFont
        if getattr(sys, "frozen", False):
            base = os.path.join(sys._MEIPASS, "assets", "fonts")
        else:
            base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "assets", "fonts")
        for ttf in ("Vazirmatn-Regular.ttf", "Vazirmatn-Bold.ttf"):
            p = os.path.join(base, ttf)
            if os.path.exists(p):
                QFontDatabase.addApplicationFont(p)
        f = QFont("Vazirmatn")
        f.setPointSize(10)
        app.setFont(f)
    except Exception:
        pass


# ------------------------------------------------------- پالت تم روشن -----
L_BG_DEEP = "#f2f4f6"      # پس‌زمینه‌ی اصلی
L_BG_PANEL = "#ffffff"     # پنل‌ها/گروه‌ها
L_BG_INPUT = "#ffffff"     # ورودی‌ها
L_BORDER = "#d3dbe1"       # حاشیه‌ها
L_TEXT = "#1c2733"          # متن اصلی
L_TEXT_MUTED = "#6b7680"    # متن کم‌رنگ
L_HOVER = "#e8eef3"

LIGHT_STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {L_BG_DEEP};
    color: {L_TEXT};
}}
QLabel {{
    color: {L_TEXT};
    background: transparent;
}}
QGroupBox {{
    border: 1px solid {L_BORDER};
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 6px;
    font-weight: bold;
    color: {L_TEXT_MUTED};
    background-color: {L_BG_PANEL};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top right;
    padding: 0 6px;
    color: {L_TEXT_MUTED};
}}
QPushButton {{
    background-color: {L_BG_PANEL};
    border: 1px solid {L_BORDER};
    border-radius: 6px;
    padding: 6px 12px;
    color: {L_TEXT};
}}
QPushButton:hover {{
    border-color: {ACCENT};
    background-color: {L_HOVER};
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: white;
}}
QPushButton:disabled {{
    color: #a0aab3;
    background-color: {L_BG_DEEP};
    border-color: {L_BORDER};
}}
QLineEdit, QTextEdit, QComboBox, QDateEdit, QSpinBox {{
    background-color: {L_BG_INPUT};
    border: 1px solid {L_BORDER};
    border-radius: 5px;
    padding: 5px 8px;
    color: {L_TEXT};
    selection-background-color: {ACCENT};
}}
QComboBox QAbstractItemView, QListView {{
    background-color: {L_BG_INPUT};
    color: {L_TEXT};
    selection-background-color: {ACCENT};
    border: 1px solid {L_BORDER};
}}
QListWidget, QTreeWidget, QTableWidget {{
    background-color: {L_BG_INPUT};
    border: 1px solid {L_BORDER};
    border-radius: 5px;
    color: {L_TEXT};
    alternate-background-color: #f7f9fb;
    selection-background-color: {ACCENT};
}}
QHeaderView::section {{
    background-color: {L_BG_PANEL};
    color: {L_TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {L_BORDER};
    padding: 7px 4px;
    font-weight: bold;
}}
QTableWidget::item:selected {{
    background-color: {ACCENT};
    color: white;
}}
QScrollBar:vertical {{
    background: {L_BG_DEEP};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {L_BORDER};
    border-radius: 6px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {L_BG_DEEP};
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {L_BORDER};
    border-radius: 6px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {ACCENT};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QSplitter::handle {{
    background-color: {L_BORDER};
}}
QSplitter::handle:horizontal {{
    width: 3px;
}}
QSplitter::handle:vertical {{
    height: 3px;
}}
QToolTip {{
    background-color: {L_BG_PANEL};
    color: {L_TEXT};
    border: 1px solid {ACCENT};
    padding: 4px;
}}
QMenu {{
    background-color: {L_BG_PANEL};
    color: {L_TEXT};
    border: 1px solid {L_BORDER};
}}
QMenu::item:selected {{
    background-color: {ACCENT};
    color: white;
}}
QMessageBox {{
    background-color: {L_BG_PANEL};
}}
QTabWidget::pane {{
    border: 1px solid {L_BORDER};
    border-radius: 5px;
}}
QTabBar::tab {{
    background: {L_BG_PANEL};
    color: {L_TEXT_MUTED};
    padding: 8px 16px;
    border: 1px solid {L_BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}
QTabBar::tab:selected {{
    background: {ACCENT};
    color: white;
    font-weight: bold;
}}
QCheckBox, QRadioButton {{
    color: {L_TEXT};
}}
QLabel {{
    color: {L_TEXT};
}}
QStatusBar {{
    background-color: {L_BG_PANEL};
    color: {L_TEXT_MUTED};
    border-top: 1px solid {L_BORDER};
}}
QStatusBar::item {{
    border: none;
}}
"""

THEME_MODES = ("dark", "light", "system")


def get_system_theme():
    """تشخیص تم سیستم‌عامل: 'dark' یا 'light' (پیش‌فرض light)."""
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import Qt
        scheme = QApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
    except Exception:
        pass
    return "light"


def resolve_theme(mode):
    """تبدیل حالت ذخیره‌شده به تم واقعی ('dark'/'light')."""
    if mode == "system":
        return get_system_theme()
    return mode if mode in ("dark", "light") else "dark"


def apply_theme(app, mode=None):
    """اعمال تم ایمن آرا سورنا روی کل برنامه.
    mode: 'dark' | 'light' | 'system' | None (خواندن از تنظیمات)."""
    _ensure_vazirmatn(app)
    if mode is None:
        try:
            from app_settings import get_theme_mode
            mode = get_theme_mode()
        except Exception:
            mode = "dark"
    real = resolve_theme(mode)
    app.setStyleSheet(LIGHT_STYLESHEET if real == "light" else APP_STYLESHEET)
