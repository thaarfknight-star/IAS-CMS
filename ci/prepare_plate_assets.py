"""CI helper: prepare plate-reader assets (model + EasyOCR models).

Runs on the GitHub Actions Windows runner BEFORE PyInstaller, so the exe
bundles everything and no download happens on the user's PC.

Steps:
  1) Download plate_detector.pt (HuggingFace -> hf-mirror fallback), >= 1MB.
  2) Pre-download EasyOCR fa/en models into easyocr_models/.
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

# ------------------------------------------------------- 2) EasyOCR models
DET = os.path.join(REPO, "easyocr_models", "model", "text_detection.pt")
if not os.path.isfile(DET):
    try:
        import easyocr  # noqa: E402

        easyocr.Reader(
            ["fa", "en"],
            gpu=False,
            model_storage_directory=os.path.join(REPO, "easyocr_models"),
            verbose=False,
        )
        print("EasyOCR models pre-downloaded OK", flush=True)
    except Exception as e:  # noqa: BLE001
        fail("پیش‌دانلود مدل‌های EasyOCR شکست خورد: %s" % e)
else:
    print("easyocr_models exists, skip pre-download", flush=True)
print("easyocr_models ready.", flush=True)

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
