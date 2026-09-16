"""CI helper: prepare plate-reader assets (model + OCR models).

Runs on the GitHub Actions Windows runner BEFORE PyInstaller, so the exe
bundles everything and no download happens on the user's PC.

Steps:
  1) Download plate_detector.pt (HuggingFace -> hf-mirror fallback), >= 1MB.
  2) Verify the vendored OCR models in plate_ocr_models/ (hezar CRNN ONNX +
     RapidOCR Arabic) — they live in the repo, no download needed.
  3) Smoke test: really load the model with plate_detector.PlateDetector.

Fails loudly (exit 1, Persian message) if anything is missing or broken.

NOTE: this lives in its own .py file on purpose. The workflow used to embed
these snippets as bash heredocs inside build.yml, but checkout on the Windows
runner uses CRLF line endings and bash heredocs break with CRLF
("syntax error: unexpected end of file"). A plain .py file is immune to that.
"""

import os
import sys
import urllib.request

# خروجی کنسول رانر ویندوز cp1252 است و چاپ متن فارسی در آن
# UnicodeEncodeError می‌دهد؛ پس stdout را از اول UTF-8 می‌کنیم تا
# پیام‌های خطای فارسی‌ی fail() واقعاً دیده شوند (نه اینکه خودِ چاپ
# پیام کرش کند و خطای اصلی پنهان بماند).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO)


def fail(msg):
    print("FAIL: " + msg, flush=True)
    sys.exit(1)


# ---------------------------------------------------------------- 1) model
MODEL = os.path.join(REPO, "plate_detector.pt")
if not os.path.isfile(MODEL):
    repo = "joker5914/yolov8n-license-plate"
    urls = [
        "https://huggingface.co/%s/resolve/main/best.pt" % repo,
        "https://hf-mirror.com/%s/resolve/main/best.pt" % repo,
    ]
    tmp = MODEL + ".downloading"
    ok = False
    for url in urls:
        try:
            print("downloading " + url, flush=True)
            req = urllib.request.Request(url, headers={"User-Agent": "IAS-CMS"})
            with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
            if os.path.getsize(tmp) < 1024 * 1024:
                raise IOError("model file too small")
            os.replace(tmp, MODEL)
            ok = True
            break
        except Exception as e:  # noqa: BLE001 - try next mirror
            print("failed (%s): %s" % (url, e), flush=True)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    if not ok:
        fail("دانلود plate_detector.pt با همه‌ی منابع شکست خورد.")
    print("plate_detector.pt OK: %d bytes" % os.path.getsize(MODEL), flush=True)
else:
    print("plate_detector.pt exists (%d bytes), skip download"
          % os.path.getsize(MODEL), flush=True)

if os.path.getsize(MODEL) < 1024 * 1024:
    fail("plate_detector.pt ناقص است (%d بایت)." % os.path.getsize(MODEL))
print("plate_detector.pt ready: %d bytes" % os.path.getsize(MODEL), flush=True)

# ------------------------------------------- 2) vendored OCR models check
# موتور خوانش پلاک: hezarai/crnn-fa-license-plate-recognition-v2 به‌صورت ONNX
# (موتور اصلی) + RapidOCR عربی (fallback). هر دو داخل ریپو و پوشه‌ی
# plate_ocr_models هستند؛ دانلودی لازم نیست — فقط راستی‌آزمایی حضورشان.
# (EasyOCR قبلاً حذف شده و دیگر نه نصب می‌شود نه باندل.)
_OCR_MODELS = (
    "hezar_plate_v2.onnx",               # موتور OCR پلاک: CRNN مخصوص پلاک فارسی
)
_ocr_dir = os.path.join(REPO, "plate_ocr_models")
for _name in _OCR_MODELS:
    _p = os.path.join(_ocr_dir, _name)
    if not os.path.isfile(_p):
        fail("مدل OCR «%s» در plate_ocr_models نیست؛ فایل را به ریپو اضافه کنید."
             % _name)
    print("ocr model OK: %s (%d bytes)" % (_name, os.path.getsize(_p)),
          flush=True)
print("plate_ocr_models ready.", flush=True)

# ------------------------------------------------------------ 3) smoke test
os.environ["IAS_PLATE_MODEL"] = os.path.abspath(MODEL)
sys.path.insert(0, REPO)
try:
    import plate_detector as pd  # noqa: E402

    d = pd.PlateDetector()
    print("PlateDetector.available = %s | source = %s"
          % (d.available, d.model_source), flush=True)
    if not d.available:
        fail("PlateDetector could not load plate_detector.pt: "
             + str(getattr(d, "load_error", "?")))
    print("smoke test OK", flush=True)
except SystemExit:
    raise
except Exception as e:  # noqa: BLE001
    fail("smoke test exception: %s" % e)

print("ALL PLATE ASSETS READY", flush=True)
