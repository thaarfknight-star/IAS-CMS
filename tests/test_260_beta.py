"""تست‌های رگرسیون نسخه 2.0.73-beta — رفع باگ‌های «سیستم اسکن شبکه».

باگ‌های رفع‌شده:
  1) ارقام فارسی/عربی در کادر رنج IP («۱۹۲.۱۶۸.۱») خطای «رنج نامعتبر» می‌داد.
  2) اکسپشن پیش‌بینی‌نشده در NetworkScanThread.run باعث مرگ بی‌صدای ترد و
     قفل ماندن دکمه‌ی «اسکن شبکه» می‌شد.
  3) اکسپشن پیش‌بینی‌نشده در DeviceDetectThread.run باعث مرگ بی‌صدای ترد و
     گیر کردن UI روی «در حال تشخیص...» می‌شد (حالا failed_signal).
  4) دابل‌کلیک روی ردیف‌های موقت («در حال اسکن...» / «هیچ دستگاهی یافت نشد.»)
     تشخیص دستگاه را با IP نامعتبر شروع می‌کرد.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _qt_available():
    try:
        import PyQt6  # noqa: F401
        return True
    except ImportError:
        return False


def _stub_cv2():
    """rtsp_utils/main در سطح ماژول cv2 را ایمپورت می‌کنند؛ برای تست‌هایی که
    cv2 واقعی لازم ندارند (ماک شده‌اند) یک MagicMock کافی است."""
    import types
    from unittest.mock import MagicMock
    if "cv2" not in sys.modules:
        sys.modules["cv2"] = MagicMock(name="cv2")


def test_persian_digits_range():
    from scanner import parse_ip_range
    ips = parse_ip_range("۱۹۲.۱۶۸.۱.۲۰-۸۰")
    assert len(ips) == 61
    assert ips[0] == "192.168.1.20"
    assert ips[-1] == "192.168.1.80"


def test_arabic_digits_subnet():
    from scanner import parse_ip_range
    ips = parse_ip_range("١٩٢.١٦٨.١")
    assert len(ips) == 254
    assert ips[0] == "192.168.1.1"


def test_mixed_digits_single_ip():
    from scanner import parse_ip_range
    assert parse_ip_range("192.168.۱.۵۰") == ["192.168.1.50"]


def test_network_scan_thread_emits_on_unexpected_error():
    """حتی اگر scan_subnet اکسپشن بدهد، finished_signal باید emit شود تا
    دکمه‌ی اسکن دوباره فعال شود (به‌جای قفل ماندن)."""
    if not _qt_available():
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QEventLoop, QTimer
    _app = QApplication.instance() or QApplication(["test"])
    import scanner
    from scanner import NetworkScanThread

    orig = scanner.scan_subnet
    scanner.scan_subnet = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        got = {}
        th = NetworkScanThread("192.168.1", None)
        th.finished_signal.connect(lambda devs: got.update(devs=devs))
        th.start()
        loop = QEventLoop()
        QTimer.singleShot(10000, loop.quit)
        th.finished.connect(loop.quit)
        loop.exec()
    finally:
        scanner.scan_subnet = orig
    assert got.get("devs") == [], f"expected [], got {got.get('devs')!r}"


def test_device_detect_thread_reports_unexpected_error():
    """اکسپشن پیش‌بینی‌نشده در تشخیص باید failed_signal بدهد، نه مرگ بی‌صدا."""
    if not _qt_available():
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import types
    # rtsp_utils در سطح ماژول cv2 را ایمپورت می‌کند؛ در این تست cv2 واقعی
    # لازم نیست چون _run_detection ماک شده و هیچ‌وقت صدا زده نمی‌شود.
    _stub_cv2()
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QEventLoop, QTimer
    _app = QApplication.instance() or QApplication(["test"])
    from device_detect import DeviceDetectThread

    got = {}
    th = DeviceDetectThread(ip="192.0.2.1", open_ports=[80])
    th._run_detection = lambda: (_ for _ in ()).throw(RuntimeError("probe boom"))
    th.failed_signal.connect(lambda msg: got.update(msg=msg))
    th.detected_signal.connect(lambda k, p: got.update(kind=k))
    th.start()
    loop = QEventLoop()
    QTimer.singleShot(10000, loop.quit)
    th.finished.connect(loop.quit)
    loop.exec()
    assert "kind" not in got, "نباید detected emit می‌شد"
    assert "probe boom" in got.get("msg", ""), f"msg={got.get('msg')!r}"


def test_scan_placeholder_rows_ignored():
    """ردیف‌های موقت لیست اسکن نباید تشخیص دستگاه را شروع کنند."""
    if not _qt_available():
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import types
    fr = types.ModuleType("face_recognition")
    fr.face_locations = lambda *a, **k: []
    fr.face_encodings = lambda *a, **k: []
    sys.modules.setdefault("face_recognition", fr)
    _stub_cv2()
    from PyQt6.QtWidgets import QApplication, QListWidgetItem
    app = _app = QApplication.instance() or QApplication(["test"])

    import user_manager
    import tempfile
    tmp = tempfile.mkdtemp(prefix="users260_")
    mgr = user_manager.UserManager(
        path=os.path.join(tmp, "users.json"))
    mgr.ensure_default_admin()
    admin = mgr.verify("admin", user_manager.DEFAULT_ADMIN_PASS)

    import main as app_main
    w = app_main.MainWindow(current_user=admin)
    app.processEvents()

    calls = []
    w._start_device_detect = lambda ip: calls.append(ip)
    w._scan_ports_by_ip = {"192.168.1.50": [80, 554]}

    w.on_scan_result_selected(QListWidgetItem("در حال اسکن 254 آدرس..."))
    w.on_scan_result_selected(QListWidgetItem("هیچ دستگاهی یافت نشد."))
    assert calls == [], f"placeholder rows must be ignored, got {calls}"

    w.on_scan_result_selected(QListWidgetItem("192.168.1.50 (پورت‌ها: 80,554)"))
    assert calls == ["192.168.1.50"], f"real device must start detect, got {calls}"
    w.close()
