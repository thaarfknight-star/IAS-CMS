# updater.ps1 — اعمال «فایل آپدیت» ایمن آرا سورنا
# این اسکریپت توسط خود برنامه (updater.py) بعد از خروج کامل برنامه اجرا می‌شود:
#   powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File updater.ps1 `
#       -InstallDir "C:\...\IAS-CMS" -PendingDir "C:\...\IAS-CMS\pending_update" -ExeName "CCTV_CMS"
#
# مراحل: انتظار برای بسته‌شدن برنامه → بکاپ فایل‌های قدیمی → کپی فایل‌های جدید →
#        راستی‌آزمایی sha256 → ثبت version.txt/manifest.json → پاک‌سازی → اجرای مجدد برنامه

param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][string]$PendingDir,
    [string]$ExeName = "CCTV_CMS"
)

$logFile = Join-Path $InstallDir "update.log"
function Log([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    try { Add-Content -Path $logFile -Value $line -Encoding UTF8 } catch {}
}

Log("=== updater started (target v?) ===")

# ۱) انتظار برای خروج کامل برنامه (حداکثر ۱۲۰ ثانیه)
$waited = 0
while ($waited -lt 120) {
    $p = Get-Process -Name $ExeName -ErrorAction SilentlyContinue
    if (-not $p) { break }
    Start-Sleep -Seconds 1
    $waited++
}
if (Get-Process -Name $ExeName -ErrorAction SilentlyContinue) {
    Log("ERROR: app still running after 120s, aborting update")
    exit 2
}

# ۲) خواندن مشخصات آپدیت
$infoPath = Join-Path $PendingDir "update_info.json"
if (-not (Test-Path $infoPath)) {
    Log("ERROR: update_info.json not found in $PendingDir")
    exit 3
}
try {
    $info = Get-Content -Path $infoPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    Log("ERROR: cannot parse update_info.json : $_")
    exit 4
}
Log(("update v{0} (prev v{1}): {2} files, {3} removed" -f $info.version, $info.prev_version, $info.files.Count, $info.removed.Count))

# ۳) بکاپ فایل‌های قدیمی
$backupRoot = Join-Path $InstallDir ("backup\v" + $info.prev_version)
$backupCount = 0
foreach ($f in $info.files) {
    $dst = Join-Path $InstallDir $f.path
    if (Test-Path -LiteralPath $dst) {
        $b = Join-Path $backupRoot $f.path
        try {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $b) | Out-Null
            Copy-Item -LiteralPath $dst -Destination $b -Force
            $backupCount++
        } catch {
            Log(("WARN backup failed: {0} : {1}" -f $f.path, $_))
        }
    }
}
Log("backed up $backupCount files to $backupRoot")

# ۴) کپی فایل‌های جدید
$copyFail = 0
foreach ($f in $info.files) {
    $src = Join-Path $PendingDir ("files\" + $f.path)
    $dst = Join-Path $InstallDir $f.path
    try {
        $parent = Split-Path -Parent $dst
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        Copy-Item -LiteralPath $src -Destination $dst -Force
    } catch {
        Log(("ERROR copy failed: {0} : {1}" -f $f.path, $_))
        $copyFail++
    }
}

# ۵) حذف فایل‌های منسوخ‌شده
foreach ($r in $info.removed) {
    $t = Join-Path $InstallDir $r
    if (Test-Path -LiteralPath $t) {
        try { Remove-Item -LiteralPath $t -Force } catch { Log(("WARN remove failed: {0}" -f $r)) }
    }
}

# ۶) راستی‌آزمایی sha256
$badHash = 0
foreach ($f in $info.files) {
    $dst = Join-Path $InstallDir $f.path
    try {
        $h = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLower()
        if ($h -ne $f.sha256.ToLower()) {
            Log(("HASH MISMATCH: {0}" -f $f.path))
            $badHash++
        }
    } catch {
        Log(("HASH CHECK FAILED (missing?): {0}" -f $f.path))
        $badHash++
    }
}

# ۷) ثبت نسخه و مانیفست جدید + پاک‌سازی pending
try {
    Set-Content -Path (Join-Path $InstallDir "version.txt") -Value $info.version -Encoding ASCII -NoNewline
    Copy-Item -LiteralPath (Join-Path $PendingDir "manifest.json") -Destination (Join-Path $InstallDir "manifest.json") -Force
} catch {
    Log(("ERROR writing version/manifest: {0}" -f $_))
}
try { Remove-Item -LiteralPath $PendingDir -Recurse -Force } catch {}

if ($copyFail -gt 0 -or $badHash -gt 0) {
    Log(("update FINISHED WITH ERRORS: copyFail={0} badHash={1}" -f $copyFail, $badHash))
    exit 5
}

Log(("update to v{0} OK" -f $info.version))

# ۸) اجرای مجدد برنامه
try {
    Start-Process -FilePath (Join-Path $InstallDir ($ExeName + ".exe")) -WorkingDirectory $InstallDir
    Log("app relaunched")
} catch {
    Log(("WARN relaunch failed: {0}" -f $_))
}
exit 0
