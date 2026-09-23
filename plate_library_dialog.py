# -*- coding: utf-8 -*-
"""صفحه‌ی «پلاک‌خوان» - مثل Face Library یک صفحه‌ی جداگانه داخل QStackedWidget
پنجره‌ی اصلی (قابل دسترسی از هدر بالای برنامه) با دو تب داخلی:

  تب ۱ «تعریف پلاک‌ها»:   تعریف حرفه‌ای پلاک (ورودی بخش‌بندی‌شده‌ی پلاک ایرانی
                          با اعتبارسنجی، مشخصات مالک/خودرو، تصویر نمونه،
                          تشخیص تکراری) + انتخاب دوربین‌های فعال پلاک‌خوان
  تب ۲ «گزارش عبور»:      گزارش عبور پلاک‌های تعریف‌شده و تعریف‌نشده با فیلتر
                          تاریخ/دوربین/وضعیت، تصویر هر عبور، تعریف سریع پلاک
                          ناشناس از روی همان ردیف، و خروجی CSV

نکته: خوانش OCR ممکن است خطا داشته باشد؛ برای همین تطبیق با پلاک‌های تعریف‌شده
هم دقیق و هم فازی (تحمل خطای OCR) انجام می‌شود و هر عبورِ کم‌اطمینان با تصویر
برش‌خورده ذخیره می‌شود تا کاربر با یک کلیک آن را تعریف کند.
"""

import os
import re
import threading
import uuid

import cv2

from PyQt6.QtCore import Qt, QDate, QTimer
from PyQt6.QtGui import QPixmap, QIcon
from PyQt6.QtWidgets import (
    QBoxLayout,
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QLineEdit, QTextEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QPushButton, QMessageBox, QDialogButtonBox, QHeaderView, QLabel,
    QFileDialog, QDateEdit, QGroupBox, QListWidget, QListWidgetItem,
    QCheckBox, QDoubleSpinBox, QSpinBox, QSplitter,
)

from plate_store import (
    plate_store, normalize_plate_text, prettify_plate, prettify_plate_html,
    validate_iranian_plate, validate_motorcycle_plate, validate_phone,
    detect_plate_kind, plate_kind_label, vehicle_type_label,
    vehicle_color_label,
    IRANIAN_PLATE_LETTERS, VEHICLE_TYPES, VEHICLE_COLORS,
)

import i18n

# --------------------------------------------------------------------------
# (2.0.39-beta) رشته‌های دوزبانه‌ی صفحه‌ی پلاک‌خوان (فارسی/English).
# زبان از app_settings خوانده می‌شود و با راه‌اندازی مجدد اعمال می‌شود.
# --------------------------------------------------------------------------
PLATE_STRINGS = {
    # --- فرم تعریف/ویرایش پلاک ---
    "form_title_new": {"fa": "تعریف پلاک جدید", "en": "Define new plate"},
    "form_title_edit": {"fa": "ویرایش پلاک", "en": "Edit plate"},
    "tab_car": {"fa": "🚗 پلاک خودرو", "en": "🚗 Car plate"},
    "tab_motorcycle": {"fa": "🏍 پلاک موتورسیکلت", "en": "🏍 Motorcycle plate"},
    "tab_other": {"fa": "سایر پلاک‌ها", "en": "Other plates"},
    "iran_code": {"fa": "ایران:", "en": "Iran:"},
    "plate_number": {"fa": "شماره پلاک:", "en": "Plate number:"},
    "mc_top": {"fa": "ردیف بالا:", "en": "Top row:"},
    "mc_bottom": {"fa": "ردیف پایین:", "en": "Bottom row:"},
    "mc_hint": {"fa": "قالب پلاک موتورسیکلت: ۳ رقم در ردیف بالا، ۱ رقم و ۱ حرف در ردیف پایین",
                "en": "Motorcycle plate format: 3 digits on the top row, 1 digit and 1 letter on the bottom row"},
    "other_text": {"fa": "متن پلاک:", "en": "Plate text:"},
    "other_ph": {"fa": "مثلاً: 12ABC345 یا پلاک تشریفاتی",
                 "en": "e.g.: 12ABC345 or a ceremonial plate"},
    "plate_specs": {"fa": "مشخصات پلاک:", "en": "Plate details:"},
    "no_sample": {"fa": "تصویر نمونه ثبت نشده", "en": "No sample image captured"},
    "capture_btn": {"fa": "📷 گرفتن تصویر نمونه از دوربین فعال",
                    "en": "📷 Capture sample image from active camera"},
    "owner_name": {"fa": "نام مالک: *", "en": "Owner name: *"},
    "phone": {"fa": "شماره تلفن:", "en": "Phone number:"},
    "vehicle_type": {"fa": "نوع خودرو:", "en": "Vehicle type:"},
    "vehicle_model": {"fa": "مدل خودرو:", "en": "Vehicle model:"},
    "vehicle_model_ph": {"fa": "مثلاً: پژو ۲۰۶", "en": "e.g.: Peugeot 206"},
    "vehicle_color": {"fa": "رنگ خودرو:", "en": "Vehicle color:"},
    "description": {"fa": "توضیحات:", "en": "Description:"},
    "active_check": {"fa": "پلاک فعال باشد (در تطبیق شرکت کند)",
                     "en": "Plate is active (included in matching)"},
    "ok_btn": {"fa": "ثبت پلاک", "en": "Save plate"},
    "cancel_btn": {"fa": "انصراف", "en": "Cancel"},
    "ph_12": {"fa": "۱۲", "en": "12"},
    "ph_345": {"fa": "۳۴۵", "en": "345"},
    "ph_67": {"fa": "۶۷", "en": "67"},
    "ph_123": {"fa": "۱۲۳", "en": "123"},
    "ph_4": {"fa": "۴", "en": "4"},
    "err_plate_min": {"fa": "متن پلاک باید حداقل ۳ نویسه باشد.",
                      "en": "Plate text must be at least 3 characters."},
    "err_owner_required": {"fa": "نام مالک الزامی است.", "en": "Owner name is required."},
    "err_phone": {"fa": "شماره تلفن باید به شکل 09xxxxxxxxx باشد (یا خالی بماند).",
                  "en": "Phone number must look like 09xxxxxxxxx (or be left empty)."},
    "err_duplicate": {"fa": "این پلاک قبلاً برای «{owner}» ثبت شده است.",
                      "en": "This plate is already registered for “{owner}”."},
    "err_no_camera": {"fa": "دسترسی به تصویر دوربین در دسترس نیست.",
                      "en": "Camera image is not available."},
    "err_connect_camera": {"fa": "ابتدا یک دوربین را متصل و انتخاب کنید تا تصویر نمونه از آن گرفته شود.",
                           "en": "Please connect and select a camera first so a sample image can be captured from it."},
    "err_connect_camera_live": {"fa": "ابتدا یک دوربین را متصل و انتخاب کنید تا پلاک از تصویر زنده‌ی آن خوانده شود.",
                                "en": "Please connect and select a camera first so the plate can be read from its live image."},
    "capturing": {"fa": "در حال تشخیص پلاک...", "en": "Detecting plate…"},
    "detect_title": {"fa": "تشخیص پلاک", "en": "Plate detection"},
    "msg_detected": {"fa": "پلاک «{plate}» تشخیص داده شد و در فرم قرار گرفت؛ لطفاً صحت آن را بررسی و سپس ثبت کنید.",
                     "en": "Plate “{plate}” was detected and filled into the form; please verify it and then save."},
    "msg_no_text": {"fa": "ناحیه‌ی پلاک پیدا شد ولی متنی خوانده نشد؛ تصویر نمونه ثبت شد و می‌توانید شماره را دستی وارد کنید.",
                    "en": "The plate area was found but no text could be read; the sample image was saved and you can enter the number manually."},
    # --- عمومی ---
    "err_title": {"fa": "خطا", "en": "Error"},
    "done_title": {"fa": "انجام شد", "en": "Done"},
    "info_title": {"fa": "اطلاع", "en": "Notice"},
    "close_btn": {"fa": "بستن", "en": "Close"},
    "warn_select_row": {"fa": "لطفاً یک ردیف را انتخاب کنید.", "en": "Please select a row."},
    "warn_select_plate": {"fa": "لطفاً یک پلاک را از لیست انتخاب کنید.",
                          "en": "Please select a plate from the list."},
    "msg_plate_added": {"fa": "پلاک «{plate}» با موفقیت تعریف شد.",
                        "en": "Plate “{plate}” was defined successfully."},
    "msg_plate_added_event": {"fa": "پلاک «{plate}» تعریف شد و این عبور به آن متصل شد.",
                              "en": "Plate “{plate}” was defined and this crossing was linked to it."},
    "msg_already_defined": {"fa": "این پلاک قبلاً تعریف شده است.", "en": "This plate is already defined."},
    "confirm_delete_title": {"fa": "تأیید حذف", "en": "Confirm deletion"},
    "confirm_delete_plate": {"fa": "پلاک «{plate}» ({owner}) حذف شود؟\nرویدادهای عبورِ قبلاً ثبت‌شده باقی می‌مانند.",
                             "en": "Delete plate “{plate}” ({owner})?\nPreviously recorded crossings will be kept."},
    "err_camera_save": {"fa": "ذخیره‌ی تنظیم دوربین ناموفق بود:\n{err}",
                        "en": "Could not save camera setting:\n{err}"},
    "err_update": {"fa": "به‌روزرسانی ناموفق بود.", "en": "Update failed."},
    "err_open_stats": {"fa": "باز کردن آمار ناموفق بود:\n{err}",
                       "en": "Could not open statistics:\n{err}"},
    "st_defined": {"fa": "✅ تعریف‌شده", "en": "✅ Defined"},
    "st_undefined": {"fa": "⚠️ تعریف‌نشده", "en": "⚠️ Undefined"},
    "status_active": {"fa": "✅ فعال", "en": "✅ Active"},
    "status_inactive": {"fa": "⏸ غیرفعال", "en": "⏸ Inactive"},
    "all": {"fa": "همه", "en": "All"},
    # --- دیالوگ جزئیات عبور ---
    "detail_title": {"fa": "جزئیات عبور پلاک", "en": "Plate crossing details"},
    "no_image": {"fa": "تصویری ثبت نشده", "en": "No image recorded"},
    "status_l": {"fa": "وضعیت:", "en": "Status:"},
    "read_plate": {"fa": "پلاک خوانده‌شده:", "en": "Read plate:"},
    "owner_l": {"fa": "مالک:", "en": "Owner:"},
    "camera_l": {"fa": "دوربین:", "en": "Camera:"},
    "date_j": {"fa": "تاریخ (شمسی):", "en": "Date (Jalali):"},
    "time_l": {"fa": "ساعت:", "en": "Time:"},
    "confidence_l": {"fa": "اطمینان خوانش:", "en": "Read confidence:"},
    "define_this": {"fa": "➕ تعریف این پلاک", "en": "➕ Define this plate"},
    "delete_event": {"fa": "🗑 حذف این رویداد", "en": "🗑 Delete this event"},
    "confirm_delete_event": {"fa": "این رویداد عبور حذف شود؟", "en": "Delete this crossing event?"},
    # --- دیالوگ وضعیت زنده ---
    "live_title": {"fa": "وضعیت زنده‌ی پلاک‌خوان (تشخیصی)", "en": "Live plate-reader status (diagnostics)"},
    "live_hint": {"fa": "این شمارنده‌ها مسیر واقعی پلاک‌خوان را نشان می‌دهند:\n"
                        "• اگر «کادر پلاک پیداشده» صفر است و دوربین روشن است: مدل تشخیص "
                        "پلاک لود نشده یا پلاکی در دید دوربین نیست (علت را در «علت خطای "
                        "تشخیص» ببینید).\n"
                        "• اگر کادر پیدا می‌شود ولی «خوانش معتبر» صفر است: OCR جواب "
                        "نمی‌دهد — «موتور OCR» و «علت خطای OCR» را ببینید.\n"
                        "• اگر «رأی‌گیری موفق» صفر است ولی خوانش معتبر هست: متن‌ها قالب "
                        "پلاک ایرانی را ندارند (حرف نامعتبر/نویز).\n"
                        "• اگر «رویداد تأییدشده» صفر است: هنوز به‌اندازه‌ی کافی خوانش "
                        "یکسان برای رأی‌گیری جمع نشده (چند ثانیه صبر کنید).\n"
                        "فایل plate_debug.log (کنار دیتابیس) هم همین شمارنده‌ها را "
                        "هر ۶۰ ثانیه ذخیره می‌کند تا برای پشتیبانی بفرستید.",
                  "en": "These counters show the real plate-reader pipeline:\n"
                        "• If “Detected plate boxes” is zero while the camera is on: the detection "
                        "model is not loaded or no plate is in view (see “Detection error cause”).\n"
                        "• If boxes are found but “Valid reads” is zero: OCR is not answering — "
                        "check “OCR engine” and “OCR error cause”.\n"
                        "• If “Successful votes” is zero despite valid reads: the texts do not match "
                        "the Iranian plate format (invalid letter/noise).\n"
                        "• If “Confirmed events” is zero: not enough identical reads have been "
                        "collected for voting yet (wait a few seconds).\n"
                        "The plate_debug.log file (next to the database) also stores these counters "
                        "every 60 seconds so you can send it to support."},
    "refresh_btn": {"fa": "🔄 به‌روزرسانی", "en": "🔄 Refresh"},
    "open_log_btn": {"fa": "📄 باز کردن فایل لاگ", "en": "📄 Open log file"},
    "diag_metric": {"fa": "شاخص", "en": "Metric"},
    "no_cam_diag": {"fa": "هنوز هیچ دوربینی پلاک‌خوانش را روشن نکرده است؛ "
                          "در تب «تعریف پلاک‌ها» دوربین را تیک بزنید و چند ثانیه "
                          "صبر کنید، بعد دوباره به‌روزرسانی بزنید.",
                    "en": "No camera has plate reading enabled yet; "
                          "tick a camera in the “Define plates” tab, wait a few "
                          "seconds, then refresh."},
    "log_title": {"fa": "فایل لاگ", "en": "Log file"},
    "log_notfound": {"fa": "هنوز فایل plate_debug.log ساخته نشده است؛ وقتی حداقل یک "
                           "دوربین پلاک‌خوانش فعال شود، فایل کنار دیتابیس ساخته می‌شود.",
                     "en": "plate_debug.log has not been created yet; it will appear next to "
                           "the database once at least one camera has plate reading enabled."},
    "log_path": {"fa": "مسیر فایل:\n{path}", "en": "File path:\n{path}"},
    # سطرهای جدول تشخیصی
    "dg_enabled": {"fa": "وضعیت پلاک‌خوان", "en": "Plate reader status"},
    "dg_detector": {"fa": "مدل تشخیص پلاک", "en": "Plate detection model"},
    "dg_ocr": {"fa": "موتور OCR", "en": "OCR engine"},
    "dg_ocr_bundled": {"fa": "مدل‌های EasyOCR داخل برنامه", "en": "EasyOCR models bundled"},
    "dg_ticks": {"fa": "دور تشخیص (ticks)", "en": "Detection ticks"},
    "dg_boxes": {"fa": "کادر پلاک پیداشده", "en": "Detected plate boxes"},
    "dg_detect_errors": {"fa": "خطای تشخیص", "en": "Detection errors"},
    "dg_ocr_runs": {"fa": "اجرای OCR", "en": "OCR runs"},
    "dg_ocr_calls": {"fa": "فراخوانی OCR روی کراپ", "en": "OCR calls on crops"},
    "dg_ocr_empty": {"fa": "OCR بدون نتیجه", "en": "OCR with no result"},
    "dg_blur": {"fa": "ردشده به‌خاطر تاری تصویر", "en": "Skipped (blurry image)"},
    "dg_reads_ok": {"fa": "خوانش معتبر (وارد رأی‌گیری)", "en": "Valid reads (entered voting)"},
    "dg_reads_rejected": {"fa": "خوانش نامعتبر", "en": "Invalid reads"},
    "dg_votes": {"fa": "رأی‌گیری موفق (اکثریت کاراکتری)", "en": "Successful votes (char majority)"},
    "dg_events": {"fa": "رویداد تأییدشده", "en": "Confirmed events"},
    "dg_cooldown": {"fa": "ردشده در کول‌داون", "en": "Skipped in cooldown"},
    "dg_tracks": {"fa": "ترک فعال", "en": "Active tracks"},
    "dg_det_err": {"fa": "علت خطای تشخیص", "en": "Detection error cause"},
    "dg_ocr_err": {"fa": "علت خطای OCR", "en": "OCR error cause"},
    "dg_upd_err": {"fa": "علت خطای به‌روزرسانی", "en": "Update error cause"},
    # --- صفحه‌ی اصلی ---
    "page_title": {"fa": "🚗 پلاک‌خوان - تشخیص و گزارش عبور پلاک‌ها",
                   "en": "🚗 Plate reader — plate detection & crossing reports"},
    "diag_btn": {"fa": "🔍 وضعیت زنده‌ی پلاک‌خوان (تشخیصی)",
                 "en": "🔍 Live plate-reader status (diagnostics)"},
    "tab_define": {"fa": "📝 تعریف پلاک‌ها", "en": "📝 Define plates"},
    "tab_report": {"fa": "📋 گزارش عبور", "en": "📋 Crossing report"},
    "tab_direction": {"fa": "🛣 مسیرها و قوانین", "en": "🛣 Lanes & rules"},
    "tab_violations": {"fa": "🚨 تخلفات تردد", "en": "🚨 Traffic violations"},
    "tab_watchlist": {"fa": "⭐ لیست تحت‌نظر", "en": "⭐ Watchlist"},
    "sys_model_ok": {"fa": "مدل تشخیص پلاک: ✅ داخل برنامه است",
                     "en": "Plate detection model: ✅ bundled in the app"},
    "sys_model_missing": {"fa": "مدل تشخیص پلاک: ⚠️ در این بیلد پیدا نشد — در بیلد جدید "
                                "برنامه (مدل داخل exe) درست می‌شود؛ چیزی روی سیستم نصب نکنید.",
                          "en": "Plate detection model: ⚠️ not found in this build — it will be fixed "
                                "in the new build (model inside the exe); do not install anything."},
    "ocr_ok": {"fa": "موتور خوانش متن: مدل هزار (CRNN مخصوص پلاک فارسی) ✅ (داخل برنامه است؛ خوانش پلاک ایرانی فعال است)",
               "en": "Text reader engine: Hezar model (CRNN for Persian plates) ✅ (inside the app; Iranian plate reading is active)"},
    "ocr_no_model": {"fa": "موتور خوانش متن: هزار ✅ (مدلش در این بیلد نیست؛ با بیلد جدید درست می‌شود)",
                     "en": "Text reader engine: Hezar ✅ (its model is not in this build; fixed with the new build)"},
    "ocr_missing": {"fa": "موتور خوانش متن: ⚠️ در این بیلد نیست — پلاک پیدا می‌شود ولی "
                          "متنی خوانده نمی‌شود. با بیلد جدید برنامه درست می‌شود؛ "
                          "چیزی روی سیستم نصب نکنید.",
                    "en": "Text reader engine: ⚠️ not in this build — plates are found but no text "
                          "is read. It will be fixed with the new build; do not install anything."},
    # ستون‌های جدول پلاک‌ها
    "col_plate": {"fa": "پلاک", "en": "Plate"},
    "col_plate_type": {"fa": "نوع پلاک", "en": "Plate type"},
    "col_owner": {"fa": "مالک", "en": "Owner"},
    "col_phone": {"fa": "تلفن", "en": "Phone"},
    "col_vehicle_type": {"fa": "نوع خودرو", "en": "Vehicle type"},
    "col_model": {"fa": "مدل", "en": "Model"},
    "col_color": {"fa": "رنگ", "en": "Color"},
    "col_status": {"fa": "وضعیت", "en": "Status"},
    "col_created": {"fa": "تاریخ ثبت", "en": "Registered"},
    # ستون‌های جدول عبور
    "ev_col_image": {"fa": "تصویر", "en": "Image"},
    "ev_col_date": {"fa": "تاریخ", "en": "Date"},
    "ev_col_time": {"fa": "ساعت", "en": "Time"},
    "ev_col_camera": {"fa": "دوربین", "en": "Camera"},
    "ev_col_plate": {"fa": "پلاک", "en": "Plate"},
    "ev_col_kind": {"fa": "نوع", "en": "Type"},
    "ev_col_owner": {"fa": "مالک", "en": "Owner"},
    "ev_col_status": {"fa": "وضعیت", "en": "Status"},
    "ev_col_conf": {"fa": "اطمینان", "en": "Confidence"},
    # تب تعریف
    "cam_group": {"fa": "🎥 پلاک‌خوان برای کدام دوربین‌ها فعال باشد؟",
                  "en": "🎥 Enable plate reader for which cameras?"},
    "cam_hint": {"fa": "فقط دوربین‌های تیک‌خورده پلاک را تشخیص می‌دهند (تشخیص در پس‌زمینه و "
                       "بدون کند کردن پخش زنده انجام می‌شود).",
                 "en": "Only ticked cameras detect plates (detection runs in the background "
                       "without slowing live view)."},
    "add_plate": {"fa": "➕ افزودن پلاک", "en": "➕ Add plate"},
    "add_from_cam": {"fa": "📷 افزودن از تصویر دوربین", "en": "📷 Add from camera image"},
    "add_from_cam_tip": {"fa": "از تصویر زنده‌ی دوربینِ انتخاب‌شده پلاک را تشخیص می‌دهد و فرم را پر می‌کند",
                         "en": "Detects the plate from the selected camera's live image and fills the form"},
    "edit_btn": {"fa": "✏️ ویرایش", "en": "✏️ Edit"},
    "delete_btn": {"fa": "🗑 حذف", "en": "🗑 Delete"},
    "toggle_btn": {"fa": "⏸ فعال/غیرفعال", "en": "⏸ Enable/Disable"},
    "threshold_l": {"fa": "آستانه‌ی تطبیق:", "en": "Match threshold:"},
    "threshold_tip": {"fa": "اگر خوانش OCR کمی با پلاک تعریف‌شده فرق داشت (مثلاً یک رقم اشتباه)، "
                            "تا چه حد شباهت قابل قبول است. کمتر = بخشنده‌تر، بیشتر = سخت‌گیرانه‌تر.",
                      "en": "How much similarity is acceptable when the OCR read slightly differs "
                            "from a defined plate (e.g. one wrong digit). Lower = more forgiving, "
                            "higher = stricter."},
    "cooldown_l": {"fa": "کول‌داون (ثانیه):", "en": "Cooldown (seconds):"},
    "cooldown_tip": {"fa": "حداقل فاصله‌ی بین دو ثبت عبور برای یک پلاک در یک دوربین (جلوگیری از "
                           "ثبت تکراری وقتی خودرو جلوی دوربین توقف کرده).",
                     "en": "Minimum gap between two crossing records for one plate on one camera "
                           "(prevents duplicates when a car is stopped in front of the camera)."},
    "stats_line": {"fa": "🚗 {plates} پلاک تعریف‌شده ({active} فعال) | "
                         "📋 {total} عبور ثبت‌شده ({defined} تعریف‌شده / "
                         "{undefined} تعریف‌نشده) | امروز: {today} عبور",
                   "en": "🚗 {plates} defined plates ({active} active) | "
                         "📋 {total} recorded crossings ({defined} defined / "
                         "{undefined} undefined) | today: {today} crossings"},
    # تب گزارش
    "from_date": {"fa": "از تاریخ:", "en": "From date:"},
    "to_date": {"fa": "تا تاریخ:", "en": "To date:"},
    "kind_l": {"fa": "نوع پلاک:", "en": "Plate type:"},
    "kind_car": {"fa": "🚗 خودرو", "en": "🚗 Car"},
    "kind_motorcycle": {"fa": "🏍 موتورسیکلت", "en": "🏍 Motorcycle"},
    "kind_other": {"fa": "سایر", "en": "Other"},
    "search_l": {"fa": "جست‌وجو:", "en": "Search:"},
    "search_ph": {"fa": "پلاک یا نام مالک...", "en": "Plate or owner name…"},
    "apply_btn": {"fa": "🔍 اعمال", "en": "🔍 Apply"},
    "detail_btn": {"fa": "🔍 جزئیات", "en": "🔍 Details"},
    "define_tip": {"fa": "پلاک تعریف‌نشده‌ی انتخاب‌شده را با همین تصویر تعریف می‌کند",
                   "en": "Defines the selected undefined plate using this same image"},
    "del_event_btn": {"fa": "🗑 حذف رویداد", "en": "🗑 Delete event"},
    "export_csv_btn": {"fa": "📤 خروجی CSV", "en": "📤 Export CSV"},
    "stats_btn": {"fa": "📊 آمار تردد", "en": "📊 Traffic stats"},
    "stats_tip": {"fa": "نمودار ساعتی/روزانه‌ی عبورها به تفکیک مسیر",
                  "en": "Hourly/daily crossing charts per lane"},
    "rep_summary": {"fa": "{n} عبور یافت شد ({d} تعریف‌شده / {u} تعریف‌نشده)",
                    "en": "{n} crossings found ({d} defined / {u} undefined)"},
    "export_title": {"fa": "ذخیره‌ی خروجی گزارش عبور", "en": "Save crossing report export"},
    "export_done": {"fa": "{n} ردیف در فایل ذخیره شد:\n{path}",
                    "en": "{n} rows saved to file:\n{path}"},
    "export_fail": {"fa": "خروجی گرفتن ناموفق بود:\n{err}", "en": "Export failed:\n{err}"},
    # تب مسیرها و قوانین
    "dir_hint": {"fa": "برای هر دوربین پلاک‌خوان نقش ورود/خروج و مسیر آن را مشخص کنید:\n"
                       "• خروجِ بدون ورودِ ثبت‌شده → تخلف\n"
                       "• ورودِ مجددِ بدون خروجِ قبلی → تخلف\n"
                       "• تردد در مسیری که جهت مجاز دیگری دارد → تخلف خلاف جهت\n"
                       "قرارداد: دوربین «ورود» یعنی رفت، دوربین «خروج» یعنی برگشت.",
                 "en": "Set the entry/exit role and lane for each plate-reading camera:\n"
                       "• Exit without a recorded entry → violation\n"
                       "• Re-entry without a prior exit → violation\n"
                       "• Driving in a lane whose allowed direction differs → wrong-way violation\n"
                       "Convention: an “entry” camera means outbound, an “exit” camera means inbound."},
    "dir_col_camera": {"fa": "دوربین", "en": "Camera"},
    "dir_col_role": {"fa": "نقش", "en": "Role"},
    "dir_col_lane": {"fa": "مسیر", "en": "Lane"},
    "role_none": {"fa": "— غیرپلاکی", "en": "— Non-plate"},
    "role_entry": {"fa": "⬅ ورود", "en": "⬅ Entry"},
    "role_exit": {"fa": "➡ خروج", "en": "➡ Exit"},
    "lane_group": {"fa": "🛣 تعریف مسیرها (هر مسیر فقط یک جهت مجاز دارد)",
                   "en": "🛣 Define lanes (each lane allows only one direction)"},
    "lane_add": {"fa": "➕ مسیر جدید", "en": "➕ New lane"},
    "lane_del": {"fa": "🗑 حذف مسیر", "en": "🗑 Delete lane"},
    "no_lane": {"fa": "— بدون مسیر —", "en": "— No lane —"},
    "dir_going": {"fa": "فقط رفت", "en": "Outbound only"},
    "dir_return": {"fa": "فقط برگشت", "en": "Inbound only"},
    "dir_undefined": {"fa": "تعریف‌نشده", "en": "Undefined"},
    "lane_default_name": {"fa": "مسیر {k}", "en": "Lane {k}"},
    "lane_new_title": {"fa": "مسیر جدید", "en": "New lane"},
    "lane_new_name": {"fa": "نام مسیر (مثلاً مسیر ۱):", "en": "Lane name (e.g. Lane 1):"},
    "lane_dir_title": {"fa": "جهت مجاز", "en": "Allowed direction"},
    "lane_dir_label": {"fa": "جهت مجاز این مسیر:", "en": "Allowed direction of this lane:"},
    "lane_del_title": {"fa": "حذف مسیر", "en": "Delete lane"},
    "lane_del_msg": {"fa": "اول یک مسیر را از لیست انتخاب کنید.",
                     "en": "Please select a lane from the list first."},
    "err_role_save": {"fa": "ذخیره‌ی نقش دوربین ناموفق بود:\n{err}",
                      "en": "Could not save camera role:\n{err}"},
    "err_lane_save": {"fa": "ذخیره‌ی مسیر دوربین ناموفق بود:\n{err}",
                      "en": "Could not save camera lane:\n{err}"},
    "channel_l": {"fa": "کانال", "en": "Channel"},
    # تب تخلفات
    "viol_col_date": {"fa": "تاریخ", "en": "Date"},
    "viol_col_time": {"fa": "ساعت", "en": "Time"},
    "viol_col_type": {"fa": "نوع تخلف", "en": "Violation type"},
    "viol_col_plate": {"fa": "پلاک", "en": "Plate"},
    "viol_col_owner": {"fa": "مالک", "en": "Owner"},
    "viol_col_camera": {"fa": "دوربین", "en": "Camera"},
    "viol_col_lane": {"fa": "مسیر", "en": "Lane"},
    "viol_col_detail": {"fa": "جزئیات", "en": "Details"},
    "viol_col_status": {"fa": "وضعیت", "en": "Status"},
    "viol_type_l": {"fa": "نوع تخلف:", "en": "Violation type:"},
    "only_unacked": {"fa": "فقط بررسی‌نشده‌ها", "en": "Only unreviewed"},
    "ack_btn": {"fa": "✓ تأیید بررسی", "en": "✓ Mark reviewed"},
    "ack_tip": {"fa": "تخلف انتخاب‌شده به‌عنوان بررسی‌شده علامت می‌خورد",
                "en": "Marks the selected violation as reviewed"},
    "unack_btn": {"fa": "↩ برگرداندن به بررسی‌نشده", "en": "↩ Mark unreviewed"},
    "viol_ack_title": {"fa": "تأیید بررسی", "en": "Review confirmation"},
    "viol_ack_msg": {"fa": "اول یک تخلف را از جدول انتخاب کنید.",
                     "en": "Please select a violation from the table first."},
    "err_ack_save": {"fa": "ثبت وضعیت ناموفق بود:\n{err}", "en": "Could not save status:\n{err}"},
    "viol_search_err": {"fa": "خطا در جست‌وجو: {err}", "en": "Search error: {err}"},
    "acked_l": {"fa": "✅ بررسی‌شده", "en": "✅ Reviewed"},
    "unacked_l": {"fa": "⚠️ بررسی‌نشده", "en": "⚠️ Unreviewed"},
    "viol_summary": {"fa": "مجموع: {n} تخلف — بررسی‌نشده: {u}",
                     "en": "Total: {n} violations — unreviewed: {u}"},
    "viol_csv_title": {"fa": "خروجی CSV تخلفات", "en": "Violations CSV export"},
    "viol_csv_done": {"fa": "✅ {n} تخلف در فایل CSV ذخیره شد.",
                      "en": "✅ {n} violations saved to CSV file."},
    "viol_csv_fail": {"fa": "خروجی CSV ناموفق بود:\n{err}", "en": "CSV export failed:\n{err}"},
    # تب لیست تحت‌نظر
    "watch_hint": {"fa": "پلاک‌های لیست سیاه/سفید: به‌محض دیده‌شدن توسط هر دوربین پلاک‌خوان، "
                         "تخلف ثبت و آلارم پخش می‌شود (در تب «🚨 تخلفات تردد» هم دیده می‌شود).",
                   "en": "Black/white-listed plates: as soon as any plate-reading camera sees one, "
                         "a violation is recorded and an alarm plays (also visible in the "
                         "“🚨 Traffic violations” tab)."},
    "watch_plate_l": {"fa": "پلاک:", "en": "Plate:"},
    "watch_plate_ph": {"fa": "مثلاً ۱۲ب۳۴۵ ایران ۱۱", "en": "e.g. 12ب345 Iran 11"},
    "watch_list_l": {"fa": "لیست:", "en": "List:"},
    "watch_black": {"fa": "⛔ سیاه", "en": "⛔ Black"},
    "watch_white": {"fa": "⭐ سفید", "en": "⭐ White"},
    "watch_note_l": {"fa": "یادداشت:", "en": "Note:"},
    "watch_note_ph": {"fa": "اختیاری", "en": "Optional"},
    "watch_add": {"fa": "➕ افزودن", "en": "➕ Add"},
    "watch_del": {"fa": "🗑 حذف انتخاب‌شده", "en": "🗑 Delete selected"},
    "col_list_type": {"fa": "نوع لیست", "en": "List type"},
    "col_note": {"fa": "یادداشت", "en": "Note"},
    "watch_summary": {"fa": "مجموع: {n} پلاک — سیاه: {b}، سفید: {w}",
                      "en": "Total: {n} plates — black: {b}, white: {w}"},
    "watch_err": {"fa": "خطا: {err}", "en": "Error: {err}"},
    "watch_title": {"fa": "لیست تحت‌نظر", "en": "Watchlist"},
    "watch_need_text": {"fa": "اول متن پلاک را وارد کنید.", "en": "Please enter the plate text first."},
    "watch_need_row": {"fa": "اول یک ردیف را انتخاب کنید.", "en": "Please select a row first."},
    "watch_add_fail": {"fa": "افزودن ناموفق بود:\n{err}", "en": "Could not add:\n{err}"},
    "watch_del_title": {"fa": "حذف", "en": "Delete"},
    "watch_del_confirm": {"fa": "پلاک «{plate}» از لیست حذف شود؟",
                          "en": "Remove plate “{plate}” from the list?"},
    # دیالوگ آمار تردد
    "stats_title": {"fa": "📊 آمار تردد پلاک‌ها", "en": "📊 Plate traffic statistics"},
    "lane_l": {"fa": "مسیر:", "en": "Lane:"},
    "type_l": {"fa": "نوع:", "en": "Type:"},
    "all_lanes": {"fa": "همه‌ی مسیرها", "en": "All lanes"},
    "chart_hourly": {"fa": "🕐 توزیع ساعتی عبورها (مجموع بازه)", "en": "🕐 Hourly crossing distribution (whole range)"},
    "chart_daily": {"fa": "📅 عبور روزانه", "en": "📅 Daily crossings"},
    "no_data": {"fa": "داده‌ای در این بازه نیست.", "en": "No data in this range."},
    "stats_summary": {"fa": "مجموع عبورها: {total} — شلوغ‌ترین ساعت: {peak_h} — شلوغ‌ترین روز: {peak_d}",
                      "en": "Total crossings: {total} — busiest hour: {peak_h} — busiest day: {peak_d}"},
    "csv_title": {"fa": "خروجی CSV", "en": "CSV export"},
    "stats_csv_title": {"fa": "خروجی CSV آمار تردد", "en": "Plate traffic CSV export"},
    "csv_no_data": {"fa": "داده‌ای برای خروجی نیست.", "en": "No data to export."},
    "csv_saved": {"fa": "✅ {n} ردیف ذخیره شد.", "en": "✅ {n} rows saved."},
    "err_read_stats": {"fa": "خواندن آمار ناموفق بود:\n{err}", "en": "Could not read statistics:\n{err}"},
    "err_save": {"fa": "ذخیره ناموفق بود:\n{err}", "en": "Could not save:\n{err}"},
    # خطاهای تشخیص از فریم
    "err_module_load": {"fa": "خطا در بارگذاری ماژول پلاک‌خوان: {err}",
                        "en": "Could not load the plate-reader module: {err}"},
    "err_no_plate_frame": {"fa": "پلاکی در تصویر فعلی دوربین تشخیص داده نشد؛ خودرو را نزدیک‌تر بیاورید.",
                           "en": "No plate was detected in the current camera image; bring the vehicle closer."},
    "err_model_unavailable": {"fa": "مدل پلاک‌خوان در دسترس نیست.",
                              "en": "Plate detection model is unavailable."},
}


def _tr(key, **kwargs):
    """میان‌بر ترجمه برای همین ماژول."""
    return i18n.t(PLATE_STRINGS, key, **kwargs)

# اندیس تب‌های نوع پلاک در فرم تعریف
TAB_CAR, TAB_MOTORCYCLE, TAB_OTHER = 0, 1, 2


def _bgr_to_pixmap(frame, max_w=320):
    """تبدیل فریم BGR به QPixmap برای پیش‌نمایش."""
    if frame is None:
        return None
    try:
        h, w = frame.shape[:2]
        scale = min(1.0, max_w / max(1, w))
        if scale < 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)),
                               interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        hh, ww = rgb.shape[:2]
        from PyQt6.QtGui import QImage
        qimg = QImage(rgb.data, ww, hh, ww * 3, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())
    except Exception:
        return None


def _save_sample_image(frame):
    """ذخیره‌ی تصویر نمونه‌ی پلاک؛ خروجی مسیر فایل یا رشته‌ی خالی."""
    if frame is None:
        return ""
    try:
        d = os.path.join(plate_store.base_dir, "samples")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{uuid.uuid4().hex}.jpg")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if ok:
            with open(path, "wb") as f:
                f.write(buf.tobytes())
            return path
    except Exception:
        pass
    return ""


def _detect_plate_in_frame(frame):
    """اجرای تشخیص+OCR روی یک فریم (برای «افزودن از دوربین»).
    خروجی: (crop_bgr یا None, متن خوانده‌شده یا "", پیام خطا یا "")."""
    try:
        from plate_detector import get_shared_plate_detector, get_shared_plate_ocr
    except Exception as e:
        return None, "", _tr("err_module_load", err=e)
    det = get_shared_plate_detector()
    if det is None or not det.available:
        return None, "", getattr(det, "load_error", "") or _tr("err_model_unavailable")
    boxes = det.detect(frame)
    if not boxes:
        return None, "", _tr("err_no_plate_frame")
    # بزرگ‌ترین باکس = نزدیک‌ترین/واضح‌ترین پلاک
    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    x1, y1, x2, y2, _c = boxes[0]
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    crop = frame[y1:y2, x1:x2].copy()
    text = ""
    try:
        ocr = get_shared_plate_ocr()
        reads = ocr.read(crop)
        if reads:
            text = reads[0][0]
    except Exception:
        pass
    return crop, text, ""


class PlateFormDialog(QDialog):
    """فرم حرفه‌ای تعریف/ویرایش پلاک: ورودی بخش‌بندی‌شده‌ی پلاک ایرانی با
    اعتبارسنجی زنده، مشخصات کامل مالک و خودرو، تصویر نمونه از دوربین، و
    کنترل تکراری بودن."""

    def __init__(self, parent=None, existing=None, prefill_text="",
                 prefill_snapshot=None, get_frame_callback=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("form_title_new") if existing is None else _tr("form_title_edit"))
        self.setMinimumWidth(460)
        i18n.apply_direction(self)
        self.existing = existing
        self.get_frame_callback = get_frame_callback
        self.sample_frame = prefill_snapshot  # numpy BGR یا None
        self._capture_timer = None

        # ------------------------------------------------- تب‌های نوع پلاک -
        self.kind_tabs = QTabWidget()
        # --- پلاک خودروی ایرانی (بخش‌بندی‌شده: ۲ رقم + حرف + ۳ رقم + کد ایران)
        ir_widget = QWidget()
        ir_form = QFormLayout(ir_widget)
        seg_row = QHBoxLayout()
        seg_row.setDirection(QBoxLayout.Direction.RightToLeft)
        self.d1_input = QLineEdit()
        self.d1_input.setMaxLength(2)
        self.d1_input.setFixedWidth(60)
        self.d1_input.setPlaceholderText(_tr("ph_12"))
        self.letter_combo = QComboBox()
        self.letter_combo.addItems(IRANIAN_PLATE_LETTERS)
        self.letter_combo.setFixedWidth(70)
        self.d2_input = QLineEdit()
        self.d2_input.setMaxLength(3)
        self.d2_input.setFixedWidth(70)
        self.d2_input.setPlaceholderText(_tr("ph_345"))
        self.code_input = QLineEdit()
        self.code_input.setMaxLength(2)
        self.code_input.setFixedWidth(60)
        self.code_input.setPlaceholderText(_tr("ph_67"))
        # ترتیب راست‌به‌چپ: کد ایران | ۳ رقم | حرف | ۲ رقم (چیدمان فیزیکی پلاک؛ ثابت)
        seg_row.addWidget(QLabel(_tr("iran_code")))
        seg_row.addWidget(self.code_input)
        seg_row.addWidget(self.d2_input)
        seg_row.addWidget(self.letter_combo)
        seg_row.addWidget(self.d1_input)
        seg_row.addStretch()
        ir_form.addRow(_tr("plate_number"), seg_row)
        self.kind_tabs.addTab(ir_widget, _tr("tab_car"))
        # --- پلاک موتورسیکلت ایرانی (بخش‌بندی‌شده: ۳ رقم بالا + ۱ رقم و حرف پایین)
        mc_widget = QWidget()
        mc_form = QFormLayout(mc_widget)
        mc_row = QHBoxLayout()
        mc_row.setDirection(QBoxLayout.Direction.RightToLeft)
        self.mc_top_input = QLineEdit()
        self.mc_top_input.setMaxLength(3)
        self.mc_top_input.setFixedWidth(70)
        self.mc_top_input.setPlaceholderText(_tr("ph_123"))
        self.mc_bottom_digit = QLineEdit()
        self.mc_bottom_digit.setMaxLength(1)
        self.mc_bottom_digit.setFixedWidth(50)
        self.mc_bottom_digit.setPlaceholderText(_tr("ph_4"))
        self.mc_letter_combo = QComboBox()
        self.mc_letter_combo.addItems(IRANIAN_PLATE_LETTERS)
        self.mc_letter_combo.setFixedWidth(70)
        # ترتیب راست‌به‌چپ: ردیف بالا (۳ رقم) | ردیف پایین (۱ رقم + حرف) (چیدمان فیزیکی؛ ثابت)
        mc_row.addWidget(QLabel(_tr("mc_top")))
        mc_row.addWidget(self.mc_top_input)
        mc_row.addWidget(QLabel(_tr("mc_bottom")))
        mc_row.addWidget(self.mc_bottom_digit)
        mc_row.addWidget(self.mc_letter_combo)
        mc_row.addStretch()
        mc_form.addRow(_tr("plate_number"), mc_row)
        mc_hint = QLabel(_tr("mc_hint"))
        mc_hint.setStyleSheet("color: #9e9e9e; font-size: 11px;")
        mc_hint.setWordWrap(True)
        mc_form.addRow("", mc_hint)
        self.kind_tabs.addTab(mc_widget, _tr("tab_motorcycle"))
        # --- سایر پلاک‌ها
        other_widget = QWidget()
        other_form = QFormLayout(other_widget)
        self.other_input = QLineEdit()
        self.other_input.setPlaceholderText(_tr("other_ph"))
        other_form.addRow(_tr("other_text"), self.other_input)
        self.kind_tabs.addTab(other_widget, _tr("tab_other"))

        self.preview_label = QLabel("")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #4fc3f7; "
            "background: #1e1e1e; border-radius: 8px; padding: 8px;")
        self.preview_label.setMinimumHeight(52)

        for w in (self.d1_input, self.d2_input, self.code_input,
                  self.mc_top_input, self.mc_bottom_digit):
            w.textChanged.connect(self._update_preview)
        self.letter_combo.currentIndexChanged.connect(self._update_preview)
        self.mc_letter_combo.currentIndexChanged.connect(self._update_preview)
        self.other_input.textChanged.connect(self._update_preview)
        self.kind_tabs.currentChanged.connect(self._on_kind_tab_changed)

        # ---------------------------------------------------- تصویر نمونه -
        sample_row = QHBoxLayout()
        self.sample_label = QLabel(_tr("no_sample"))
        self.sample_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sample_label.setFixedSize(240, 120)
        self.sample_label.setStyleSheet(
            "background-color: #1e1e1e; color: #aaaaaa; border-radius: 8px;")
        sample_col = QVBoxLayout()
        sample_col.addWidget(self.sample_label)
        self.capture_btn = QPushButton(_tr("capture_btn"))
        self.capture_btn.clicked.connect(self.capture_from_camera)
        sample_col.addWidget(self.capture_btn)
        sample_row.addStretch()
        sample_row.addLayout(sample_col)
        sample_row.addStretch()

        # ---------------------------------------------------- مشخصات مالک -
        self.owner_input = QLineEdit()
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("09xxxxxxxxx")
        self.phone_input.setMaxLength(11)
        self.vehicle_type_combo = QComboBox()
        # (2.0.39-beta) نمایش انگلیسی در حالت EN؛ مقدار فارسی در itemData برای دیتابیس.
        for vt in VEHICLE_TYPES:
            self.vehicle_type_combo.addItem(vehicle_type_label(vt), vt)
        self.vehicle_model_input = QLineEdit()
        self.vehicle_model_input.setPlaceholderText(_tr("vehicle_model_ph"))
        self.vehicle_color_combo = QComboBox()
        for vc in VEHICLE_COLORS:
            self.vehicle_color_combo.addItem(vehicle_color_label(vc), vc)
        self.desc_input = QTextEdit()
        self.desc_input.setFixedHeight(56)
        self.active_check = QCheckBox(_tr("active_check"))
        self.active_check.setChecked(True)

        form = QFormLayout()
        form.addRow(_tr("owner_name"), self.owner_input)
        form.addRow(_tr("phone"), self.phone_input)
        form.addRow(_tr("vehicle_type"), self.vehicle_type_combo)
        form.addRow(_tr("vehicle_model"), self.vehicle_model_input)
        form.addRow(_tr("vehicle_color"), self.vehicle_color_combo)
        form.addRow(_tr("description"), self.desc_input)
        form.addRow("", self.active_check)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #ff9e80; font-size: 11px;")
        self.status_label.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_tr("ok_btn"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_tr("cancel_btn"))
        buttons.accepted.connect(self.handle_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(QLabel(_tr("plate_specs")))
        layout.addWidget(self.kind_tabs)
        layout.addWidget(self.preview_label)
        layout.addLayout(sample_row)
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        layout.addWidget(buttons)
        self.setLayout(layout)

        # مقداردهی اولیه (ویرایش یا پیش‌فرض از رویداد)
        if existing:
            self._fill_from_plate(existing)
        elif prefill_text:
            self._prefill_text(prefill_text)
        if prefill_snapshot is not None:
            self._set_sample_frame(prefill_snapshot)
        self._update_preview()

    # ------------------------------------------------------------- کمکی -

    def _on_kind_tab_changed(self, _idx):
        """با رفتن به تب موتورسیکلت (در حالت افزودن)، نوع خودرو هم
        خودکار روی «موتورسیکلت» می‌رود؛ کاربر می‌تواند عوضش کند."""
        if self.kind_tabs.currentIndex() == TAB_MOTORCYCLE and self.existing is None:
            idx = self.vehicle_type_combo.findData("موتورسیکلت")
            if idx >= 0:
                self.vehicle_type_combo.setCurrentIndex(idx)
        self._update_preview()

    @staticmethod
    def _combo_value(combo):
        """مقدار فارسی ذخیره‌شونده (itemData)؛ اگر نبود متن نمایشی."""
        data = combo.currentData()
        return data if data else combo.currentText()

    def _fill_from_plate(self, p):
        self.owner_input.setText(p.get("owner_name", ""))
        self.phone_input.setText(p.get("phone", ""))
        idx = self.vehicle_type_combo.findData(p.get("vehicle_type", ""))
        if idx >= 0:
            self.vehicle_type_combo.setCurrentIndex(idx)
        self.vehicle_model_input.setText(p.get("vehicle_model", ""))
        idx = self.vehicle_color_combo.findData(p.get("vehicle_color", ""))
        if idx >= 0:
            self.vehicle_color_combo.setCurrentIndex(idx)
        self.desc_input.setPlainText(p.get("description", ""))
        self.active_check.setChecked(bool(p.get("active", True)))
        canon = p.get("plate_text", "")
        kind = p.get("plate_type") or detect_plate_kind(canon)
        m = re.match(r"^([0-9]{2})([^0-9]{1,2})([0-9]{3})([0-9]{2})$", canon)
        mm = re.match(r"^([0-9]{3})([0-9])([^0-9]{1,2})$", canon)
        if kind == "motorcycle" and mm:
            top, bottom_digit, letter = mm.groups()
            self.mc_top_input.setText(top)
            self.mc_bottom_digit.setText(bottom_digit)
            li = self.mc_letter_combo.findText(letter)
            if li >= 0:
                self.mc_letter_combo.setCurrentIndex(li)
            self.kind_tabs.setCurrentIndex(TAB_MOTORCYCLE)
        elif m:
            d1, letter, d2, code = m.groups()
            self.d1_input.setText(d1)
            li = self.letter_combo.findText(letter)
            if li >= 0:
                self.letter_combo.setCurrentIndex(li)
            self.d2_input.setText(d2)
            self.code_input.setText(code)
            self.kind_tabs.setCurrentIndex(TAB_CAR)
        else:
            self.other_input.setText(canon)
            self.kind_tabs.setCurrentIndex(TAB_OTHER)
        sp = p.get("sample_image", "")
        if sp and os.path.isfile(sp):
            pix = QPixmap(sp)
            if not pix.isNull():
                self.sample_label.setPixmap(pix.scaled(
                    240, 120, Qt.AspectRatioMode.KeepAspectRatio))

    def _prefill_text(self, text):
        canon = normalize_plate_text(text)
        m = re.match(r"^([0-9]{2})([^0-9]{1,2})([0-9]{3})([0-9]{2})$", canon)
        if m:
            d1, letter, d2, code = m.groups()
            self.d1_input.setText(d1)
            li = self.letter_combo.findText(letter)
            if li >= 0:
                self.letter_combo.setCurrentIndex(li)
            else:
                # حرف ناشناخته: تب «سایر»
                self.other_input.setText(canon)
                self.kind_tabs.setCurrentIndex(TAB_OTHER)
                return
            self.d2_input.setText(d2)
            self.code_input.setText(code)
            self.kind_tabs.setCurrentIndex(TAB_CAR)
            return
        mm = re.match(r"^([0-9]{3})([0-9])([^0-9]{1,2})$", canon)
        if mm:
            top, bottom_digit, letter = mm.groups()
            li = self.mc_letter_combo.findText(letter)
            if li < 0:
                self.other_input.setText(canon)
                self.kind_tabs.setCurrentIndex(TAB_OTHER)
                return
            self.mc_top_input.setText(top)
            self.mc_bottom_digit.setText(bottom_digit)
            self.mc_letter_combo.setCurrentIndex(li)
            self.kind_tabs.setCurrentIndex(TAB_MOTORCYCLE)
        else:
            self.other_input.setText(canon)
            self.kind_tabs.setCurrentIndex(TAB_OTHER)

    def _set_sample_frame(self, frame):
        self.sample_frame = frame
        pix = _bgr_to_pixmap(frame, max_w=240)
        if pix is not None:
            self.sample_label.setPixmap(pix.scaled(
                240, 120, Qt.AspectRatioMode.KeepAspectRatio))

    def _update_preview(self):
        canon, kind, _err = self._current_canonical()
        if canon:
            emoji = "🏍" if kind == "motorcycle" else "🚗"
            self.preview_label.setText(f"{emoji} {prettify_plate_html(canon)}")
        else:
            self.preview_label.setText("—")

    def _current_canonical(self):
        """خوانش فعلی فرم -> (کانونیکال, نوع پلاک, پیام خطا)."""
        tab = self.kind_tabs.currentIndex()
        if tab == TAB_CAR:
            ok, err, canon = validate_iranian_plate(
                self.d1_input.text().strip(),
                self.letter_combo.currentText(),
                self.d2_input.text().strip(),
                self.code_input.text().strip())
            return (canon, "car", err) if ok else ("", "car", err)
        if tab == TAB_MOTORCYCLE:
            ok, err, canon = validate_motorcycle_plate(
                self.mc_top_input.text().strip(),
                self.mc_bottom_digit.text().strip(),
                self.mc_letter_combo.currentText())
            return (canon, "motorcycle", err) if ok else ("", "motorcycle", err)
        canon = normalize_plate_text(self.other_input.text())
        if len(canon) < 3:
            return "", "other", _tr("err_plate_min")
        return canon, detect_plate_kind(canon), ""

    # ---------------------------------------------------- گرفتن از دوربین -

    def capture_from_camera(self):
        if self.get_frame_callback is None:
            QMessageBox.warning(self, _tr("err_title"), _tr("err_no_camera"))
            return
        frame = self.get_frame_callback()
        if frame is None:
            QMessageBox.warning(self, _tr("err_title"), _tr("err_connect_camera"))
            return
        self.capture_btn.setEnabled(False)
        self.capture_btn.setText(_tr("capturing"))
        self._capture_out = {}
        threading.Thread(target=self._capture_worker,
                         args=(frame.copy(),), daemon=True).start()
        self._capture_timer = QTimer(self)
        self._capture_timer.timeout.connect(self._check_capture)
        self._capture_timer.start(300)

    def _capture_worker(self, frame):
        try:
            crop, text, err = _detect_plate_in_frame(frame)
            self._capture_out = {"crop": crop, "text": text, "err": err}
        except Exception as e:
            self._capture_out = {"crop": None, "text": "", "err": str(e)}
        self._capture_out["done"] = True

    def _check_capture(self):
        if not self._capture_out.get("done"):
            return
        self._capture_timer.stop()
        self.capture_btn.setEnabled(True)
        self.capture_btn.setText(_tr("capture_btn"))
        err = self._capture_out.get("err", "")
        if err:
            QMessageBox.warning(self, _tr("detect_title"), err)
            return
        crop = self._capture_out.get("crop")
        text = self._capture_out.get("text", "")
        if crop is not None:
            self._set_sample_frame(crop)
        if text:
            self._prefill_text(text)
            self._update_preview()
            QMessageBox.information(
                self, _tr("detect_title"),
                _tr("msg_detected", plate=prettify_plate(normalize_plate_text(text))))
        else:
            QMessageBox.information(self, _tr("detect_title"), _tr("msg_no_text"))

    # ------------------------------------------------------------- ثبت -

    def handle_accept(self):
        canon, kind, err = self._current_canonical()
        if not canon:
            self.status_label.setText(err)
            return
        if not self.owner_input.text().strip():
            self.status_label.setText(_tr("err_owner_required"))
            return
        ok_phone, phone_norm = validate_phone(self.phone_input.text())
        if not ok_phone:
            self.status_label.setText(_tr("err_phone"))
            return
        # کنترل تکراری بودن (به‌جز وقتی همین رکورد در حال ویرایش است)
        existing_id = (self.existing or {}).get("id")
        match, _s, _k = plate_store.find_match(canon)
        if match is not None and match["id"] != existing_id:
            self.status_label.setText(
                _tr("err_duplicate", owner=match.get("owner_name", "")))
            return
        self._result_canonical = canon
        self._result_kind = kind
        self._result_phone = phone_norm
        self.accept()

    def get_data(self):
        sample_path = ""
        if self.sample_frame is not None:
            # اگر در حالت ویرایش تصویر قبلی بود و کاربر عکسی تازه نگرفت، همان بماند
            existing_sample = (self.existing or {}).get("sample_image", "")
            if existing_sample and self.sample_frame is None:
                sample_path = existing_sample
            else:
                sample_path = _save_sample_image(self.sample_frame)
                if not sample_path and existing_sample:
                    sample_path = existing_sample
        elif self.existing:
            sample_path = self.existing.get("sample_image", "")
        return {
            "plate_text": getattr(self, "_result_canonical", ""),
            "plate_display": prettify_plate(getattr(self, "_result_canonical", "")),
            "plate_type": getattr(self, "_result_kind", "other"),
            "owner_name": self.owner_input.text().strip(),
            "phone": getattr(self, "_result_phone", ""),
            "vehicle_type": self._combo_value(self.vehicle_type_combo),
            "vehicle_model": self.vehicle_model_input.text().strip(),
            "vehicle_color": self._combo_value(self.vehicle_color_combo),
            "description": self.desc_input.toPlainText().strip(),
            "active": self.active_check.isChecked(),
            "sample_image": sample_path,
        }


# --------------------------------------------------------------------------
# دیالوگ جزئیات یک عبور
# --------------------------------------------------------------------------

class PlateEventDetailDialog(QDialog):
    """نمایش بزرگ تصویر عبور + همه‌ی مشخصات + «تعریف این پلاک» برای ناشناس‌ها."""

    def __init__(self, event, parent=None):
        super().__init__(parent)
        self.event = event
        self.setWindowTitle(_tr("detail_title"))
        self.setMinimumWidth(420)
        i18n.apply_direction(self)
        self.defined_plate_id = None

        layout = QVBoxLayout()

        # تصویر بزرگ
        img_label = QLabel()
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_label.setMinimumSize(380, 190)
        img_label.setStyleSheet("background: #1e1e1e; border-radius: 8px;")
        snap = event.get("snapshot_path", "")
        pix = QPixmap(snap) if snap and os.path.isfile(snap) else QPixmap()
        if pix.isNull():
            img_label.setText(_tr("no_image"))
            img_label.setStyleSheet(
                "background: #1e1e1e; color: #888; border-radius: 8px;")
        else:
            img_label.setPixmap(pix.scaled(
                380, 190, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(img_label)

        info = QFormLayout()
        status = _tr("st_defined") if event.get("is_defined") else _tr("st_undefined")
        info.addRow(_tr("status_l"), QLabel(status))
        info.addRow(_tr("read_plate"),
                   QLabel(prettify_plate_html(event.get("plate_text", ""))
                          or "—"))
        info.addRow(_tr("owner_l"), QLabel(event.get("owner_name", "") or "—"))
        info.addRow(_tr("camera_l"), QLabel(event.get("camera_name", "") or "—"))
        info.addRow(_tr("date_j"), QLabel(event.get("date_j", "") or "—"))
        info.addRow(_tr("time_l"), QLabel(event.get("time_g", "") or "—"))
        conf = event.get("confidence") or 0
        info.addRow(_tr("confidence_l"), QLabel(f"{conf:.0%}"))
        layout.addLayout(info)

        btn_row = QHBoxLayout()
        if not event.get("is_defined"):
            self.define_btn = QPushButton(_tr("define_this"))
            self.define_btn.clicked.connect(self.define_this_plate)
            btn_row.addWidget(self.define_btn)
        self.delete_btn = QPushButton(_tr("delete_event"))
        self.delete_btn.clicked.connect(self.delete_this_event)
        btn_row.addWidget(self.delete_btn)
        close_btn = QPushButton(_tr("close_btn"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        self.setLayout(layout)

    def define_this_plate(self):
        page = self.parent()
        get_frame = getattr(page, "get_frame_callback", None)
        dlg = PlateFormDialog(
            self, prefill_text=self.event.get("plate_text", ""),
            prefill_snapshot=cv2.imread(self.event.get("snapshot_path", ""))
            if self.event.get("snapshot_path") and os.path.isfile(
                self.event.get("snapshot_path", "")) else None,
            get_frame_callback=get_frame)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            ok, result = plate_store.add_plate(**data)
            if not ok:
                QMessageBox.warning(self, _tr("err_title"), result)
                return
            plate = plate_store.get_plate(result)
            plate_store.attach_event_to_plate(self.event["id"], plate)
            self.defined_plate_id = result
            QMessageBox.information(
                self, _tr("done_title"),
                _tr("msg_plate_added_event", plate=data['plate_display']))
            # (2.0.39-beta) باگ: پلاک از «گزارش عبور» تعریف می‌شد ولی در تب
            # «تعریف پلاک‌ها» دیده نمی‌شد چون جدول رفرش نمی‌شد.
            refresh = getattr(page, "refresh_plates_table", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:
                    pass
            self.accept()

    def delete_this_event(self):
        confirm = QMessageBox.question(
            self, _tr("confirm_delete_title"), _tr("confirm_delete_event"))
        if confirm == QMessageBox.StandardButton.Yes:
            plate_store.delete_event(self.event["id"])
            self.accept()


# --------------------------------------------------------------------------
# دیالوگ «وضعیت زنده‌ی پلاک‌خوان (تشخیصی)»
# --------------------------------------------------------------------------

# (2.0.39-beta) سطرها: (کلید ترجمه، کلید شمارنده) — برچسب در reload() ترجمه می‌شود.
_PLATE_DIAG_ROWS = [
    ("dg_enabled", "enabled"),
    ("dg_detector", "detector_available"),
    ("dg_ocr", "ocr_engine"),
    ("dg_ocr_bundled", "ocr_models_bundled"),
    ("dg_ticks", "ticks"),
    ("dg_boxes", "boxes_total"),
    ("dg_detect_errors", "detect_errors"),
    ("dg_ocr_runs", "ocr_runs"),
    ("dg_ocr_calls", "ocr_calls"),
    ("dg_ocr_empty", "ocr_empty"),
    ("dg_blur", "ocr_skipped_blur"),
    ("dg_reads_ok", "reads_total"),
    ("dg_reads_rejected", "reads_rejected"),
    ("dg_votes", "votes_cast"),
    ("dg_events", "events"),
    ("dg_cooldown", "cooldown_skips"),
    ("dg_tracks", "tracks_active"),
    ("dg_det_err", "detector_error"),
    ("dg_ocr_err", "ocr_error"),
    ("dg_upd_err", "update_error"),
]


class LivePlateStatusDialog(QDialog):
    """نمایش زنده‌ی شمارنده‌های تشخیصی پلاک‌خوان هر دوربین + راهنمای
    خوانش آن‌ها (کجای مسیر detect → OCR → vote → event می‌ایستد)."""

    def __init__(self, get_diag_callback, parent=None):
        super().__init__(parent)
        self.get_diag_callback = get_diag_callback
        self.setWindowTitle(_tr("live_title"))
        self.setMinimumSize(640, 480)
        i18n.apply_direction(self)
        layout = QVBoxLayout(self)

        hint = QLabel(_tr("live_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: #9e9e9e;")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton(_tr("refresh_btn"))
        self.refresh_btn.clicked.connect(self.reload)
        btn_row.addWidget(self.refresh_btn)
        self.log_btn = QPushButton(_tr("open_log_btn"))
        self.log_btn.clicked.connect(self.open_log_file)
        btn_row.addWidget(self.log_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton(_tr("close_btn"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self.reload()

    def _diags(self):
        try:
            d = self.get_diag_callback() if self.get_diag_callback else None
        except Exception:
            d = None
        return d if isinstance(d, dict) else {}

    def reload(self):
        diags = self._diags()
        cams = sorted(diags.keys())
        self.table.setRowCount(len(_PLATE_DIAG_ROWS))
        self.table.setColumnCount(len(cams) + 1)
        headers = [_tr("diag_metric")] + [str(c) or "—" for c in cams]
        self.table.setHorizontalHeaderLabels(headers)
        for r, (tr_key, key) in enumerate(_PLATE_DIAG_ROWS):
            self.table.setItem(r, 0, QTableWidgetItem(_tr(tr_key)))
            for c, cam in enumerate(cams):
                d = diags[cam] or {}
                val = d.get(key, "")
                if key in ("enabled", "detector_available", "ocr_models_bundled"):
                    txt = "✅" if val else "❌"
                elif key in ("detector_error", "ocr_error", "update_error"):
                    txt = str(val)[:80] if val else "—"
                else:
                    txt = str(val)
                self.table.setItem(r, c + 1, QTableWidgetItem(txt))
        if not cams:
            self.table.setRowCount(1)
            self.table.setColumnCount(1)
            self.table.setHorizontalHeaderLabels([_tr("diag_metric")])
            self.table.setItem(0, 0, QTableWidgetItem(_tr("no_cam_diag")))
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)

    def open_log_file(self):
        try:
            from plate_store import plate_store
            path = os.path.join(os.path.dirname(plate_store.db_path),
                                "plate_debug.log")
        except Exception:
            path = ""
        if not path or not os.path.isfile(path):
            QMessageBox.information(self, _tr("log_title"), _tr("log_notfound"))
            return
        try:
            from PyQt6.QtGui import QDesktopServices
            from PyQt6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception:
            QMessageBox.information(self, _tr("log_title"), _tr("log_path", path=path))


# --------------------------------------------------------------------------
# صفحه‌ی اصلی پلاک‌خوان (دو تب)
# --------------------------------------------------------------------------

class PlateLibraryPage(QWidget):
    """صفحه‌ی «پلاک‌خوان» داخل QStackedWidget پنجره‌ی اصلی."""

    # (2.0.39-beta) ستون‌ها بر اساس زبان فعلی، در __init__ ساخته می‌شوند.
    PLATE_COLUMNS = None
    EVENT_COLUMNS = None

    def __init__(self, get_frame_callback, camera_store, on_plate_toggle=None,
                 get_plate_diag_callback=None, parent=None):
        super().__init__(parent)
        self.get_frame_callback = get_frame_callback
        self.camera_store = camera_store
        self.on_plate_toggle = on_plate_toggle  # (cam_id, enabled) -> None
        self.get_plate_diag_callback = get_plate_diag_callback  # () -> {cam_name: diag}
        # (2.0.39-beta) ستون‌های جدول‌ها بر اساس زبان فعلی.
        self.PLATE_COLUMNS = [_tr("col_plate"), _tr("col_plate_type"), _tr("col_owner"),
                              _tr("col_phone"), _tr("col_vehicle_type"), _tr("col_model"),
                              _tr("col_color"), _tr("col_status"), _tr("col_created")]
        self.EVENT_COLUMNS = [_tr("ev_col_image"), _tr("ev_col_date"), _tr("ev_col_time"),
                              _tr("ev_col_camera"), _tr("ev_col_plate"), _tr("ev_col_kind"),
                              _tr("ev_col_owner"), _tr("ev_col_status"), _tr("ev_col_conf")]
        self.VIOLATION_COLUMNS = [_tr("viol_col_date"), _tr("viol_col_time"),
                                  _tr("viol_col_type"), _tr("viol_col_plate"),
                                  _tr("viol_col_owner"), _tr("viol_col_camera"),
                                  _tr("viol_col_lane"), _tr("viol_col_detail"),
                                  _tr("viol_col_status")]
        self.WATCHLIST_COLUMNS = [_tr("col_plate"), _tr("col_list_type"),
                                  _tr("col_note"), _tr("col_created")]
        i18n.apply_direction(self)

        layout = QVBoxLayout(self)
        title = QLabel(_tr("page_title"))
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")
        layout.addWidget(title)

        # بنر وضعیت واقعی سیستم پلاک‌خوان (استاتیک؛ چیزی لود نمی‌کند)
        self.system_status_label = QLabel(self._system_status_text())
        self.system_status_label.setStyleSheet("font-size: 11px; padding: 2px 4px;")
        self.system_status_label.setWordWrap(True)
        layout.addWidget(self.system_status_label)

        self.ocr_status_label = QLabel(self._ocr_status_text())
        self.ocr_status_label.setStyleSheet("font-size: 11px; padding: 2px 4px;")
        self.ocr_status_label.setWordWrap(True)
        layout.addWidget(self.ocr_status_label)

        diag_row = QHBoxLayout()
        self.diag_btn = QPushButton(_tr("diag_btn"))
        self.diag_btn.clicked.connect(self.open_live_plate_status)
        diag_row.addWidget(self.diag_btn)
        diag_row.addStretch(1)
        layout.addLayout(diag_row)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_define_tab(), _tr("tab_define"))
        self.tabs.addTab(self._build_report_tab(), _tr("tab_report"))
        self.tabs.addTab(self._build_direction_tab(), _tr("tab_direction"))
        self.tabs.addTab(self._build_violations_tab(), _tr("tab_violations"))
        self.tabs.addTab(self._build_watchlist_tab(), _tr("tab_watchlist"))
        layout.addWidget(self.tabs, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #9e9e9e; font-size: 11px;")
        layout.addWidget(self.status_label)

        self.refresh()

    def _system_status_text(self):
        """بنر وضعیت واقعی باندل پلاک‌خوان (استاتیک؛ مدل لود نمی‌شود): آیا
        فایل مدل plate_detector.pt داخل برنامه هست؟ اگر نه، در بیلد رسمی
        هست و فقط باید برنامه به‌روز شود."""
        try:
            from plate_detector import _find_plate_model
            model = _find_plate_model()
        except Exception:
            model = None
        if model:
            return _tr("sys_model_ok")
        return _tr("sys_model_missing")

    def _ocr_status_text(self):
        """متن وضعیت موتور OCR برای نمایش در هدر صفحه (سبک؛ چیزی لود نمی‌کند).
        نکته: در بیلد رسمی مدل هزار (CRNN مخصوص پلاک فارسی) داخل exe است؛
        هیچ دانلود/نصبی روی سیستم کاربر لازم نیست."""
        try:
            from plate_detector import ocr_install_status, hezar_model_bundled
            (hezar_ok,) = ocr_install_status()
            bundled = hezar_model_bundled()
        except Exception:
            hezar_ok, bundled = False, False
        if hezar_ok and bundled:
            return _tr("ocr_ok")
        if hezar_ok:
            return _tr("ocr_no_model")
        return _tr("ocr_missing")

    def open_live_plate_status(self):
        """دیالوگ «وضعیت زنده‌ی پلاک‌خوان (تشخیصی)»: شمارنده‌های واقعی هر
        دوربین — معلوم می‌کند مسیر detect → OCR → vote → event کجا می‌ایستد."""
        dlg = LivePlateStatusDialog(self.get_plate_diag_callback, self)
        dlg.exec()

    def refresh(self):
        """هر بار که صفحه از هدر باز می‌شود صدا زده می‌شود."""
        self.system_status_label.setText(self._system_status_text())
        self.ocr_status_label.setText(self._ocr_status_text())
        self._reload_camera_checklist()
        self.refresh_plates_table()
        self._reload_report_camera_combo()
        self.run_report_search()
        self._update_stats()
        try:
            self._reload_direction_tab()
        except Exception:
            pass
        try:
            self.run_violations_search()
        except Exception:
            pass

    # ============================================================ تب تعریف -

    def _build_define_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # --- دوربین‌های فعال پلاک‌خوان
        cam_group = QGroupBox(_tr("cam_group"))
        cam_layout = QVBoxLayout()
        self.camera_checklist = QListWidget()
        self.camera_checklist.setMaximumHeight(110)
        self.camera_checklist.itemChanged.connect(self._on_camera_check_changed)
        cam_layout.addWidget(self.camera_checklist)
        cam_hint = QLabel(_tr("cam_hint"))
        cam_hint.setStyleSheet("color: #9e9e9e; font-size: 11px;")
        cam_hint.setWordWrap(True)
        cam_layout.addWidget(cam_hint)
        cam_group.setLayout(cam_layout)
        layout.addWidget(cam_group)

        # --- جدول پلاک‌ها
        self.plates_table = QTableWidget(0, len(self.PLATE_COLUMNS))
        self.plates_table.setHorizontalHeaderLabels(self.PLATE_COLUMNS)
        self.plates_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.plates_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.plates_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.plates_table.doubleClicked.connect(self.edit_plate)
        layout.addWidget(self.plates_table, 1)

        # --- دکمه‌ها
        btn_row = QHBoxLayout()
        add_btn = QPushButton(_tr("add_plate"))
        add_btn.clicked.connect(self.add_plate)
        btn_row.addWidget(add_btn)
        add_cam_btn = QPushButton(_tr("add_from_cam"))
        add_cam_btn.setToolTip(_tr("add_from_cam_tip"))
        add_cam_btn.clicked.connect(self.add_plate_from_camera)
        btn_row.addWidget(add_cam_btn)
        edit_btn = QPushButton(_tr("edit_btn"))
        edit_btn.clicked.connect(self.edit_plate)
        btn_row.addWidget(edit_btn)
        del_btn = QPushButton(_tr("delete_btn"))
        del_btn.clicked.connect(self.delete_plate)
        btn_row.addWidget(del_btn)
        toggle_btn = QPushButton(_tr("toggle_btn"))
        toggle_btn.clicked.connect(self.toggle_plate_active)
        btn_row.addWidget(toggle_btn)
        btn_row.addStretch()
        # تنظیمات تطبیق
        btn_row.addWidget(QLabel(_tr("threshold_l")))
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.50, 1.00)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setValue(plate_store.match_threshold)
        self.threshold_spin.setToolTip(_tr("threshold_tip"))
        self.threshold_spin.valueChanged.connect(
            lambda v: setattr(plate_store, "match_threshold", float(v)))
        btn_row.addWidget(self.threshold_spin)
        btn_row.addWidget(QLabel(_tr("cooldown_l")))
        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(5, 600)
        self.cooldown_spin.setValue(plate_store.cooldown_seconds)
        self.cooldown_spin.setToolTip(_tr("cooldown_tip"))
        self.cooldown_spin.valueChanged.connect(
            lambda v: plate_store.set_setting("cooldown_seconds", str(int(v))))
        btn_row.addWidget(self.cooldown_spin)
        layout.addLayout(btn_row)
        return tab

    def _all_cameras(self):
        """لیست همه‌ی دوربین‌ها: [(cam_id, label)] شامل مستقل و کانال‌های NVR."""
        cams = []
        try:
            for cam in self.camera_store.standalone_cameras():
                cams.append((cam.get("id"), cam.get("name") or cam.get("ip") or "؟"))
            for nvr in self.camera_store.nvrs:
                nvr_name = nvr.get("name") or nvr.get("ip") or ""
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    label = (cam.get("name")
                             or f"{_tr('channel_l')} {cam.get('channel', '')}")
                    cams.append((cam.get("id"), f"{label} ({nvr_name})"))
        except Exception:
            pass
        return cams

    def _reload_camera_checklist(self):
        self.camera_checklist.blockSignals(True)
        self.camera_checklist.clear()
        cam_by_id = {}
        try:
            for cam in self.camera_store.standalone_cameras():
                cam_by_id[cam.get("id")] = cam
            for nvr in self.camera_store.nvrs:
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    cam_by_id[cam.get("id")] = cam
        except Exception:
            pass
        for cam_id, label in self._all_cameras():
            cam = cam_by_id.get(cam_id, {})
            item = QListWidgetItem(f"🎥 {label}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked
                               if cam.get("plate_detection") else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, cam_id)
            self.camera_checklist.addItem(item)
        self.camera_checklist.blockSignals(False)

    def _on_camera_check_changed(self, item):
        cam_id = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        try:
            self.camera_store.update_camera(cam_id, plate_detection=enabled)
        except Exception as e:
            QMessageBox.warning(self, _tr("err_title"), _tr("err_camera_save", err=e))
            return
        # اعمال زنده روی دوربینی که همین حالا باز است
        if callable(self.on_plate_toggle):
            try:
                self.on_plate_toggle(cam_id, enabled)
            except Exception:
                pass

    # ------------------------------------------------------- عملیات پلاک -

    def _selected_plate_id(self):
        row = self.plates_table.currentRow()
        if row < 0:
            return None
        item = self.plates_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_plates_table(self):
        plates = plate_store.list_plates()
        self.plates_table.setRowCount(0)
        for p in plates:
            r = self.plates_table.rowCount()
            self.plates_table.insertRow(r)
            item0 = QTableWidgetItem("")
            item0.setData(Qt.ItemDataRole.UserRole, p["id"])
            self.plates_table.setItem(r, 0, item0)
            # پلاک با کادر مربعی دور کد ایران (مثل پلاک فیزیکی)؛
            # آیتم مخفی بالا فقط برای نگهداری ID سطر است.
            plate_label = QLabel(prettify_plate_html(p.get("plate_text", "")))
            plate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fnt = plate_label.font()
            fnt.setBold(True)
            plate_label.setFont(fnt)
            self.plates_table.setCellWidget(r, 0, plate_label)
            kind_item = QTableWidgetItem(
                plate_kind_label(p.get("plate_type", "other")))
            kind_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.plates_table.setItem(r, 1, kind_item)
            self.plates_table.setItem(r, 2, QTableWidgetItem(p["owner_name"]))
            self.plates_table.setItem(r, 3, QTableWidgetItem(p["phone"]))
            self.plates_table.setItem(
                r, 4, QTableWidgetItem(vehicle_type_label(p["vehicle_type"])))
            self.plates_table.setItem(r, 5, QTableWidgetItem(p["vehicle_model"]))
            self.plates_table.setItem(
                r, 6, QTableWidgetItem(vehicle_color_label(p["vehicle_color"])))
            status_item = QTableWidgetItem(
                _tr("status_active") if p["active"] else _tr("status_inactive"))
            if not p["active"]:
                status_item.setForeground(Qt.GlobalColor.gray)
            self.plates_table.setItem(r, 7, status_item)
            self.plates_table.setItem(r, 8, QTableWidgetItem(p["created_jalali"]))
        self._update_stats()

    def add_plate(self):
        dlg = PlateFormDialog(self, get_frame_callback=self.get_frame_callback)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            ok, result = plate_store.add_plate(**data)
            if not ok:
                QMessageBox.warning(self, _tr("err_title"), result)
                return
            QMessageBox.information(
                self, _tr("done_title"),
                _tr("msg_plate_added", plate=data['plate_display']))
            self.refresh_plates_table()

    def add_plate_from_camera(self):
        """فرم تعریف با تصویر نمونه و متنِ ازپیش‌تشخیص‌شده از دوربین فعال."""
        if self.get_frame_callback is None:
            QMessageBox.warning(self, _tr("err_title"), _tr("err_no_camera"))
            return
        frame = self.get_frame_callback()
        if frame is None:
            QMessageBox.warning(self, _tr("err_title"), _tr("err_connect_camera_live"))
            return
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            crop, text, err = _detect_plate_in_frame(frame.copy())
        finally:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        if err:
            QMessageBox.warning(self, _tr("detect_title"), err)
            return
        dlg = PlateFormDialog(
            self, prefill_text=text, prefill_snapshot=crop,
            get_frame_callback=self.get_frame_callback)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            ok, result = plate_store.add_plate(**data)
            if not ok:
                QMessageBox.warning(self, _tr("err_title"), result)
                return
            QMessageBox.information(
                self, _tr("done_title"),
                _tr("msg_plate_added", plate=data['plate_display']))
            self.refresh_plates_table()

    def edit_plate(self):
        pid = self._selected_plate_id()
        if not pid:
            QMessageBox.warning(self, _tr("err_title"), _tr("warn_select_plate"))
            return
        existing = plate_store.get_plate(pid)
        dlg = PlateFormDialog(self, existing=existing,
                              get_frame_callback=self.get_frame_callback)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            data.pop("plate_text", None)
            data.pop("plate_display", None)
            ok, err = plate_store.update_plate(pid, **data)
            if not ok:
                QMessageBox.warning(self, _tr("err_title"), err or _tr("err_update"))
                return
            self.refresh_plates_table()

    def delete_plate(self):
        pid = self._selected_plate_id()
        if not pid:
            QMessageBox.warning(self, _tr("err_title"), _tr("warn_select_plate"))
            return
        p = plate_store.get_plate(pid)
        confirm = QMessageBox.question(
            self, _tr("confirm_delete_title"),
            _tr("confirm_delete_plate", plate=p['plate_display'],
                 owner=p['owner_name']))
        if confirm == QMessageBox.StandardButton.Yes:
            plate_store.delete_plate(pid)
            self.refresh_plates_table()

    def toggle_plate_active(self):
        pid = self._selected_plate_id()
        if not pid:
            QMessageBox.warning(self, _tr("err_title"), _tr("warn_select_plate"))
            return
        p = plate_store.get_plate(pid)
        plate_store.set_plate_active(pid, not p["active"])
        self.refresh_plates_table()

    def _update_stats(self):
        s = plate_store.stats()
        self.status_label.setText(_tr(
            "stats_line", plates=s['plates'], active=s['plates_active'],
            total=s['total'], defined=s['defined'], undefined=s['undefined'],
            today=s['today']))

    # ============================================================ تب گزارش -

    def _build_report_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # --- فیلترها
        frow = QHBoxLayout()
        frow.addWidget(QLabel(_tr("from_date")))
        self.from_date = QDateEdit(calendarPopup=True)
        self.from_date.setDate(QDate.currentDate().addDays(-7))
        self.from_date.setDisplayFormat("yyyy/MM/dd")
        frow.addWidget(self.from_date)
        frow.addWidget(QLabel(_tr("to_date")))
        self.to_date = QDateEdit(calendarPopup=True)
        self.to_date.setDate(QDate.currentDate())
        self.to_date.setDisplayFormat("yyyy/MM/dd")
        frow.addWidget(self.to_date)
        frow.addWidget(QLabel(_tr("camera_l")))
        self.rep_camera_combo = QComboBox()
        frow.addWidget(self.rep_camera_combo)
        frow.addWidget(QLabel(_tr("status_l")))
        self.rep_status_combo = QComboBox()
        self.rep_status_combo.addItem(_tr("all"), None)
        self.rep_status_combo.addItem(_tr("st_defined"), True)
        self.rep_status_combo.addItem(_tr("st_undefined"), False)
        frow.addWidget(self.rep_status_combo)
        frow.addWidget(QLabel(_tr("kind_l")))
        self.rep_kind_combo = QComboBox()
        self.rep_kind_combo.addItem(_tr("all"), None)
        self.rep_kind_combo.addItem(_tr("kind_car"), "car")
        self.rep_kind_combo.addItem(_tr("kind_motorcycle"), "motorcycle")
        self.rep_kind_combo.addItem(_tr("kind_other"), "other")
        frow.addWidget(self.rep_kind_combo)
        frow.addWidget(QLabel(_tr("search_l")))
        self.rep_search = QLineEdit()
        self.rep_search.setPlaceholderText(_tr("search_ph"))
        self.rep_search.returnPressed.connect(self.run_report_search)
        frow.addWidget(self.rep_search)
        search_btn = QPushButton(_tr("apply_btn"))
        search_btn.clicked.connect(self.run_report_search)
        frow.addWidget(search_btn)
        layout.addLayout(frow)

        # --- جدول
        self.events_table = QTableWidget(0, len(self.EVENT_COLUMNS))
        self.events_table.setHorizontalHeaderLabels(self.EVENT_COLUMNS)
        self.events_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.events_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.events_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.events_table.verticalHeader().setDefaultSectionSize(56)
        self.events_table.doubleClicked.connect(self.open_event_detail)
        layout.addWidget(self.events_table, 1)

        # --- دکمه‌ها
        brow = QHBoxLayout()
        detail_btn = QPushButton(_tr("detail_btn"))
        detail_btn.clicked.connect(self.open_event_detail)
        brow.addWidget(detail_btn)
        define_btn = QPushButton(_tr("define_this"))
        define_btn.setToolTip(_tr("define_tip"))
        define_btn.clicked.connect(self.define_selected_event_plate)
        brow.addWidget(define_btn)
        del_btn = QPushButton(_tr("del_event_btn"))
        del_btn.clicked.connect(self.delete_selected_event)
        brow.addWidget(del_btn)
        brow.addStretch()
        refresh_btn = QPushButton(_tr("refresh_btn"))
        refresh_btn.clicked.connect(self.run_report_search)
        brow.addWidget(refresh_btn)
        export_btn = QPushButton(_tr("export_csv_btn"))
        export_btn.clicked.connect(self.export_report_csv)
        brow.addWidget(export_btn)
        stats_btn = QPushButton(_tr("stats_btn"))
        stats_btn.setToolTip(_tr("stats_tip"))
        stats_btn.clicked.connect(self.open_plate_stats)
        brow.addWidget(stats_btn)
        layout.addLayout(brow)

        self.rep_summary = QLabel("")
        layout.addWidget(self.rep_summary)
        return tab

    # ============================================= تب مسیرها و قوانین (2.0.15-beta) =

    def _role_label(self, role):
        return {"entry": _tr("role_entry"),
                "exit": _tr("role_exit")}.get(role or "", _tr("role_none"))

    def _build_direction_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        hint = QLabel(_tr("dir_hint"))
        hint.setStyleSheet("color: #9e9e9e; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # --- جدول نقش دوربین‌ها
        self.dir_table = QTableWidget(0, 3)
        self.dir_table.setHorizontalHeaderLabels(
            [_tr("dir_col_camera"), _tr("dir_col_role"), _tr("dir_col_lane")])
        self.dir_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.dir_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.dir_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.dir_table, 2)

        # --- مدیریت مسیرها
        lane_group = QGroupBox(_tr("lane_group"))
        lane_layout = QVBoxLayout()
        lane_row = QHBoxLayout()
        self.lanes_list = QListWidget()
        self.lanes_list.setMaximumHeight(100)
        lane_row.addWidget(self.lanes_list, 1)
        lane_btn_col = QVBoxLayout()
        lane_add_btn = QPushButton(_tr("lane_add"))
        lane_add_btn.clicked.connect(self._add_lane)
        lane_btn_col.addWidget(lane_add_btn)
        lane_del_btn = QPushButton(_tr("lane_del"))
        lane_del_btn.clicked.connect(self._delete_lane)
        lane_btn_col.addWidget(lane_del_btn)
        lane_btn_col.addStretch(1)
        lane_row.addLayout(lane_btn_col)
        lane_layout.addLayout(lane_row)
        # (2.0.17-beta) پنجره‌ی اغماض ورود تکراری حذف شد؛ کول‌داون
        # ۱۵ثانیه‌ای دتکتور برای خوانش تکراری کافی است.
        lane_group.setLayout(lane_layout)
        layout.addWidget(lane_group, 1)
        return tab

    def _reload_direction_tab(self):
        """جدول نقش دوربین‌ها + لیست مسیرها را تازه می‌کند."""
        lanes = plate_store.get_lanes()
        lane_items = [("", _tr("no_lane"))]
        for lid, lane in lanes.items():
            nm = (lane or {}).get("name") or lid
            lane_items.append((lid, nm))

        self.dir_table.blockSignals(True)
        self.dir_table.setRowCount(0)
        cam_by_id = {}
        for cam in self.camera_store.standalone_cameras():
            cam_by_id[cam.get("id")] = cam
        for nvr in self.camera_store.nvrs:
            for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                cam_by_id[cam.get("id")] = cam
        for cam_id, label in self._all_cameras():
            cam = cam_by_id.get(cam_id, {})
            r = self.dir_table.rowCount()
            self.dir_table.insertRow(r)
            name_item = QTableWidgetItem(label)
            name_item.setData(Qt.ItemDataRole.UserRole, cam_id)
            self.dir_table.setItem(r, 0, name_item)
            # نقش
            role_combo = QComboBox()
            role_combo.addItem(_tr("role_none"), "")
            role_combo.addItem(_tr("role_entry"), "entry")
            role_combo.addItem(_tr("role_exit"), "exit")
            role = cam.get("plate_role") or ""
            idx = role_combo.findData(role)
            if idx >= 0:
                role_combo.setCurrentIndex(idx)
            role_combo.currentIndexChanged.connect(
                lambda _i, cid=cam_id, cb=role_combo: self._on_role_changed(cid, cb))
            self.dir_table.setCellWidget(r, 1, role_combo)
            # مسیر
            lane_combo = QComboBox()
            for lid, lname in lane_items:
                lane_combo.addItem(lname, lid)
            cl = cam.get("lane_id") or ""
            li = lane_combo.findData(cl)
            if li >= 0:
                lane_combo.setCurrentIndex(li)
            lane_combo.currentIndexChanged.connect(
                lambda _i, cid=cam_id, cb=lane_combo: self._on_lane_changed(cid, cb))
            self.dir_table.setCellWidget(r, 2, lane_combo)
        self.dir_table.blockSignals(False)

        # لیست مسیرها
        self.lanes_list.blockSignals(True)
        self.lanes_list.clear()
        for lid, lane in lanes.items():
            lane = lane or {}
            allowed = lane.get("allowed", "")
            dir_txt = {"going": _tr("dir_going"),
                       "return": _tr("dir_return")}.get(allowed, _tr("dir_undefined"))
            item = QListWidgetItem(f"{lane.get('name') or lid} — {dir_txt}")
            item.setData(Qt.ItemDataRole.UserRole, lid)
            self.lanes_list.addItem(item)
        self.lanes_list.blockSignals(False)

    def _on_role_changed(self, cam_id, combo):
        role = combo.currentData() or ""
        try:
            self.camera_store.update_camera(cam_id, plate_role=role)
        except Exception as e:
            QMessageBox.warning(self, _tr("err_title"), _tr("err_role_save", err=e))

    def _on_lane_changed(self, cam_id, combo):
        lane_id = combo.currentData() or ""
        try:
            self.camera_store.update_camera(cam_id, lane_id=lane_id)
        except Exception as e:
            QMessageBox.warning(self, _tr("err_title"), _tr("err_lane_save", err=e))

    def _add_lane(self):
        lanes = plate_store.get_lanes()
        lid = f"lane{len(lanes) + 1}"
        k = 1
        while f"lane{k}" in lanes:
            k += 1
        lid = f"lane{k}"
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, _tr("lane_new_title"), _tr("lane_new_name"),
            text=_tr("lane_default_name", k=k))
        if not ok:
            return
        allowed, ok2 = QInputDialog.getItem(
            self, _tr("lane_dir_title"), _tr("lane_dir_label"),
            [_tr("dir_going"), _tr("dir_return")], 0, False)
        if not ok2:
            return
        lanes[lid] = {
            "name": name.strip() or _tr("lane_default_name", k=k),
            "allowed": "going" if allowed == _tr("dir_going") else "return",
        }
        plate_store.set_lanes(lanes)
        self._reload_direction_tab()

    def _delete_lane(self):
        item = self.lanes_list.currentItem()
        if item is None:
            QMessageBox.information(self, _tr("lane_del_title"), _tr("lane_del_msg"))
            return
        lid = item.data(Qt.ItemDataRole.UserRole)
        lanes = plate_store.get_lanes()
        lanes.pop(lid, None)
        plate_store.set_lanes(lanes)
        # دوربین‌هایی که این مسیر را داشتند، بدون مسیر شوند
        try:
            for cam in list(self.camera_store.standalone_cameras()):
                if cam.get("lane_id") == lid:
                    self.camera_store.update_camera(cam.get("id"), lane_id="")
            for nvr in self.camera_store.nvrs:
                for cam in self.camera_store.cameras_for_nvr(nvr.get("id")):
                    if cam.get("lane_id") == lid:
                        self.camera_store.update_camera(cam.get("id"), lane_id="")
        except Exception:
            pass
        self._reload_direction_tab()

    # ============================================= تب تخلفات تردد (2.0.15-beta) =

    # (2.0.39-beta) در __init__ ساخته می‌شود.
    VIOLATION_COLUMNS = None

    def _build_violations_tab(self):
        tab = QWidget()
        self.violations_tab = tab  # برای تشخیص «دیده شدن» در به‌روزرسانی زنده
        layout = QVBoxLayout(tab)

        frow = QHBoxLayout()
        frow.addWidget(QLabel(_tr("from_date")))
        self.viol_from = QDateEdit(calendarPopup=True)
        self.viol_from.setDate(QDate.currentDate().addDays(-7))
        self.viol_from.setDisplayFormat("yyyy/MM/dd")
        frow.addWidget(self.viol_from)
        frow.addWidget(QLabel(_tr("to_date")))
        self.viol_to = QDateEdit(calendarPopup=True)
        self.viol_to.setDate(QDate.currentDate())
        self.viol_to.setDisplayFormat("yyyy/MM/dd")
        frow.addWidget(self.viol_to)
        frow.addWidget(QLabel(_tr("viol_type_l")))
        self.viol_type_combo = QComboBox()
        self.viol_type_combo.addItem(_tr("all"), None)
        for vt in plate_store.VIOLATION_LABELS:
            self.viol_type_combo.addItem(plate_store.violation_label(vt), vt)
        frow.addWidget(self.viol_type_combo)
        self.viol_unacked = QCheckBox(_tr("only_unacked"))
        self.viol_unacked.setChecked(True)
        frow.addWidget(self.viol_unacked)
        frow.addWidget(QLabel(_tr("search_l")))
        self.viol_search = QLineEdit()
        self.viol_search.setPlaceholderText(_tr("search_ph"))
        self.viol_search.returnPressed.connect(self.run_violations_search)
        frow.addWidget(self.viol_search)
        search_btn = QPushButton(_tr("apply_btn"))
        search_btn.clicked.connect(self.run_violations_search)
        frow.addWidget(search_btn)
        layout.addLayout(frow)

        self.violations_table = QTableWidget(0, len(self.VIOLATION_COLUMNS))
        self.violations_table.setHorizontalHeaderLabels(self.VIOLATION_COLUMNS)
        self.violations_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.violations_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.violations_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.violations_table, 1)

        brow = QHBoxLayout()
        ack_btn = QPushButton(_tr("ack_btn"))
        ack_btn.setToolTip(_tr("ack_tip"))
        ack_btn.clicked.connect(self.acknowledge_selected_violation)
        brow.addWidget(ack_btn)
        unack_btn = QPushButton(_tr("unack_btn"))
        unack_btn.clicked.connect(
            lambda: self.acknowledge_selected_violation(False))
        brow.addWidget(unack_btn)
        brow.addStretch()
        refresh_btn = QPushButton(_tr("refresh_btn"))
        refresh_btn.clicked.connect(self.run_violations_search)
        brow.addWidget(refresh_btn)
        export_btn = QPushButton(_tr("export_csv_btn"))
        export_btn.clicked.connect(self.export_violations_csv)
        brow.addWidget(export_btn)
        layout.addLayout(brow)

        self.viol_summary = QLabel("")
        layout.addWidget(self.viol_summary)
        return tab

    def run_violations_search(self):
        try:
            df = self.viol_from.date().toString("yyyy-MM-dd")
            dt = self.viol_to.date().toString("yyyy-MM-dd")
            vtype = self.viol_type_combo.currentData()
            acked = False if self.viol_unacked.isChecked() else None
            search = self.viol_search.text().strip()
            rows = plate_store.list_violations(
                date_from=df, date_to=dt, violation_type=vtype,
                search=search, acknowledged=acked)
        except Exception as e:
            self.viol_summary.setText(_tr("viol_search_err", err=e))
            return
        self.violations_table.setRowCount(0)
        for r in rows:
            row = self.violations_table.rowCount()
            self.violations_table.insertRow(row)
            vtype_lbl = plate_store.violation_label(r.get("violation_type"))
            acked_lbl = _tr("acked_l") if r.get("acknowledged") else _tr("unacked_l")
            vals = [r.get("date_j", ""), r.get("time_g", ""), vtype_lbl,
                    r.get("plate_display", ""), r.get("owner_name", ""),
                    r.get("camera_name", ""), r.get("lane_id", ""),
                    r.get("detail", ""), acked_lbl]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, r.get("id"))
                self.violations_table.setItem(row, c, item)
        unacked = sum(1 for r in rows if not r.get("acknowledged"))
        self.viol_summary.setText(_tr("viol_summary", n=len(rows), u=unacked))

    def acknowledge_selected_violation(self, acknowledged=True):
        row = self.violations_table.currentRow()
        if row < 0:
            QMessageBox.information(self, _tr("viol_ack_title"), _tr("viol_ack_msg"))
            return
        item = self.violations_table.item(row, 0)
        vid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not vid:
            return
        try:
            plate_store.acknowledge_violation(vid, acknowledged)
        except Exception as e:
            QMessageBox.warning(self, _tr("err_title"), _tr("err_ack_save", err=e))
            return
        self.run_violations_search()

    def export_violations_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, _tr("viol_csv_title"), "plate_violations.csv",
            "CSV (*.csv)")
        if not path:
            return
        try:
            n = plate_store.export_violations_csv(
                path,
                date_from=self.viol_from.date().toString("yyyy-MM-dd"),
                date_to=self.viol_to.date().toString("yyyy-MM-dd"),
                violation_type=self.viol_type_combo.currentData(),
                search=self.viol_search.text().strip())
            self.viol_summary.setText(_tr("viol_csv_done", n=n))
        except Exception as e:
            QMessageBox.warning(self, _tr("err_title"), _tr("viol_csv_fail", err=e))

    # ================================== تب لیست تحت‌نظر پلاک (2.0.18-beta) =

    # (2.0.39-beta) در __init__ ساخته می‌شود.
    WATCHLIST_COLUMNS = None

    def _build_watchlist_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        hint = QLabel(_tr("watch_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8fa3b8; font-size:11px;")
        layout.addWidget(hint)

        frow = QHBoxLayout()
        frow.addWidget(QLabel(_tr("watch_plate_l")))
        self.watch_plate = QLineEdit()
        self.watch_plate.setPlaceholderText(_tr("watch_plate_ph"))
        frow.addWidget(self.watch_plate)
        frow.addWidget(QLabel(_tr("watch_list_l")))
        self.watch_kind = QComboBox()
        self.watch_kind.addItem(_tr("watch_black"), "black")
        self.watch_kind.addItem(_tr("watch_white"), "white")
        frow.addWidget(self.watch_kind)
        frow.addWidget(QLabel(_tr("watch_note_l")))
        self.watch_note = QLineEdit()
        self.watch_note.setPlaceholderText(_tr("watch_note_ph"))
        frow.addWidget(self.watch_note, 1)
        add_btn = QPushButton(_tr("watch_add"))
        add_btn.clicked.connect(self._add_watchlist_entry)
        frow.addWidget(add_btn)
        layout.addLayout(frow)

        self.watchlist_table = QTableWidget(0, len(self.WATCHLIST_COLUMNS))
        self.watchlist_table.setHorizontalHeaderLabels(self.WATCHLIST_COLUMNS)
        self.watchlist_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.watchlist_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.watchlist_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.watchlist_table, 1)

        brow = QHBoxLayout()
        del_btn = QPushButton(_tr("watch_del"))
        del_btn.clicked.connect(self._remove_watchlist_entry)
        brow.addWidget(del_btn)
        brow.addStretch()
        ref_btn = QPushButton(_tr("refresh_btn"))
        ref_btn.clicked.connect(self._refresh_watchlist)
        brow.addWidget(ref_btn)
        layout.addLayout(brow)

        self.watch_summary = QLabel("")
        layout.addWidget(self.watch_summary)
        self._refresh_watchlist()
        return tab

    def _refresh_watchlist(self):
        try:
            rows = plate_store.list_watchlist()
        except Exception as e:
            self.watch_summary.setText(_tr("watch_err", err=e))
            return
        self.watchlist_table.setRowCount(0)
        for r in rows:
            row = self.watchlist_table.rowCount()
            self.watchlist_table.insertRow(row)
            kind_lbl = plate_store.watchlist_label(r.get("kind"))
            vals = [r.get("plate_display", ""), kind_lbl,
                    r.get("note", ""), r.get("created_date_j", "")]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole,
                                 (r.get("plate_text"), r.get("kind")))
                self.watchlist_table.setItem(row, c, item)
        nb = sum(1 for r in rows if r.get("kind") == "black")
        nw = sum(1 for r in rows if r.get("kind") == "white")
        self.watch_summary.setText(_tr("watch_summary", n=len(rows), b=nb, w=nw))

    def _add_watchlist_entry(self):
        text = self.watch_plate.text().strip()
        kind = self.watch_kind.currentData()
        note = self.watch_note.text().strip()
        if not text:
            QMessageBox.information(self, _tr("watch_title"), _tr("watch_need_text"))
            return
        try:
            plate_store.add_watchlist_entry(text, kind, note)
        except Exception as e:
            QMessageBox.warning(self, _tr("err_title"), _tr("watch_add_fail", err=e))
            return
        self.watch_plate.clear()
        self.watch_note.clear()
        self._refresh_watchlist()

    def _remove_watchlist_entry(self):
        row = self.watchlist_table.currentRow()
        if row < 0:
            QMessageBox.information(self, _tr("watch_title"), _tr("watch_need_row"))
            return
        item = self.watchlist_table.item(row, 0)
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not data:
            return
        plate_text, kind = data
        if QMessageBox.question(
                self, _tr("watch_del_title"),
                _tr("watch_del_confirm", plate=item.text())
                ) != QMessageBox.StandardButton.Yes:
            return
        plate_store.remove_watchlist_entry(plate_text, kind)
        self._refresh_watchlist()

    def open_plate_stats(self):
        """باز کردن داشبورد آماری تردد (2.0.18-beta)."""
        try:
            from plate_stats_dialog import PlateStatsDialog
        except Exception as e:
            QMessageBox.warning(self, _tr("err_title"), _tr("err_open_stats", err=e))
            return
        dlg = PlateStatsDialog(plate_store, self)
        dlg.exec()

    def _reload_report_camera_combo(self):
        current = self.rep_camera_combo.currentData()
        self.rep_camera_combo.blockSignals(True)
        self.rep_camera_combo.clear()
        self.rep_camera_combo.addItem(_tr("all"), None)
        names = set(plate_store.distinct_event_cameras())
        for _cid, label in self._all_cameras():
            names.add(label.split(" (")[0])
        for n in sorted(names):
            self.rep_camera_combo.addItem(n, n)
        idx = self.rep_camera_combo.findData(current)
        self.rep_camera_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.rep_camera_combo.blockSignals(False)

    def _current_report_filters(self):
        return {
            "date_from": self.from_date.date().toString("yyyy-MM-dd"),
            "date_to": self.to_date.date().toString("yyyy-MM-dd"),
            "camera_name": self.rep_camera_combo.currentData(),
            "defined": self.rep_status_combo.currentData(),
            "kind": self.rep_kind_combo.currentData(),
            "search": normalize_plate_text(self.rep_search.text().strip()),
        }

    def run_report_search(self):
        f = self._current_report_filters()
        rows = plate_store.query_events(**f)
        self.events_table.setRowCount(0)
        for ev in rows:
            r = self.events_table.rowCount()
            self.events_table.insertRow(r)
            # تصویر
            img_item = QTableWidgetItem("")
            snap = ev.get("snapshot_path", "")
            if snap and os.path.isfile(snap):
                pix = QPixmap(snap)
                if not pix.isNull():
                    img_item.setIcon(QIcon(pix.scaledToHeight(
                        48, Qt.TransformationMode.SmoothTransformation)))
            img_item.setData(Qt.ItemDataRole.UserRole, ev["id"])
            self.events_table.setItem(r, 0, img_item)
            self.events_table.setItem(r, 1, QTableWidgetItem(ev.get("date_j", "")))
            self.events_table.setItem(r, 2, QTableWidgetItem(ev.get("time_g", "")))
            self.events_table.setItem(r, 3, QTableWidgetItem(ev.get("camera_name", "")))
            # پلاک با کادر مربعی دور کد ایران (مثل پلاک فیزیکی)
            plate_label = QLabel(
                prettify_plate_html(ev.get("plate_text", "")) or "—")
            plate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pfont = plate_label.font()
            pfont.setBold(True)
            plate_label.setFont(pfont)
            self.events_table.setCellWidget(r, 4, plate_label)
            kind_item = QTableWidgetItem(
                plate_kind_label(detect_plate_kind(ev.get("plate_text", ""))))
            kind_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.events_table.setItem(r, 5, kind_item)
            self.events_table.setItem(r, 6, QTableWidgetItem(ev.get("owner_name", "") or "—"))
            st_item = QTableWidgetItem(
                _tr("st_defined") if ev.get("is_defined") else _tr("st_undefined"))
            st_item.setForeground(Qt.GlobalColor.darkGreen if ev.get("is_defined")
                                  else Qt.GlobalColor.darkRed)
            self.events_table.setItem(r, 7, st_item)
            conf = ev.get("confidence") or 0
            self.events_table.setItem(r, 8, QTableWidgetItem(f"{conf:.0%}"))
        n_def = sum(1 for e in rows if e.get("is_defined"))
        self.rep_summary.setText(_tr(
            "rep_summary", n=len(rows), d=n_def, u=len(rows) - n_def))

    def _selected_event_id(self):
        row = self.events_table.currentRow()
        if row < 0:
            return None
        item = self.events_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _get_event_by_id(self, eid):
        for ev in plate_store.query_events(limit=100000):
            if ev["id"] == eid:
                return ev
        return None

    def open_event_detail(self):
        eid = self._selected_event_id()
        if not eid:
            QMessageBox.warning(self, _tr("err_title"), _tr("warn_select_row"))
            return
        ev = self._get_event_by_id(eid)
        if not ev:
            return
        dlg = PlateEventDetailDialog(ev, parent=self)
        dlg.exec()
        self.run_report_search()
        self._update_stats()

    def define_selected_event_plate(self):
        eid = self._selected_event_id()
        if not eid:
            QMessageBox.warning(self, _tr("err_title"), _tr("warn_select_row"))
            return
        ev = self._get_event_by_id(eid)
        if not ev:
            return
        if ev.get("is_defined"):
            QMessageBox.information(self, _tr("info_title"), _tr("msg_already_defined"))
            return
        snap = None
        sp = ev.get("snapshot_path", "")
        if sp and os.path.isfile(sp):
            snap = cv2.imread(sp)
        dlg = PlateFormDialog(
            self, prefill_text=ev.get("plate_text", ""),
            prefill_snapshot=snap, get_frame_callback=self.get_frame_callback)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            ok, result = plate_store.add_plate(**data)
            if not ok:
                QMessageBox.warning(self, _tr("err_title"), result)
                return
            plate = plate_store.get_plate(result)
            plate_store.attach_event_to_plate(eid, plate)
            QMessageBox.information(
                self, _tr("done_title"),
                _tr("msg_plate_added_event", plate=data['plate_display']))
            self.refresh_plates_table()
            self.run_report_search()

    def delete_selected_event(self):
        eid = self._selected_event_id()
        if not eid:
            QMessageBox.warning(self, _tr("err_title"), _tr("warn_select_row"))
            return
        confirm = QMessageBox.question(
            self, _tr("confirm_delete_title"), _tr("confirm_delete_event"))
        if confirm == QMessageBox.StandardButton.Yes:
            plate_store.delete_event(eid)
            self.run_report_search()
            self._update_stats()

    def export_report_csv(self):
        from datetime import datetime as _dt
        default = f"plate_report_{_dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, _tr("export_title"), default, "CSV (*.csv)")
        if not path:
            return
        f = self._current_report_filters()
        try:
            n = plate_store.export_events_csv(path, **f)
        except Exception as e:
            QMessageBox.warning(self, _tr("err_title"), _tr("export_fail", err=e))
            return
        QMessageBox.information(self, _tr("done_title"),
                                _tr("export_done", n=n, path=path))


# نام قدیمی برای سازگاری
PlateLibraryDialog = PlateLibraryPage
