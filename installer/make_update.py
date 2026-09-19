# -*- coding: utf-8 -*-
"""ساخت مانیفست کامل و «فایل آپدیت» تفاضلی ایمن آرا سورنا.

دستورات:
    manifest --dist dist/CCTV_CMS --version 2.0.0 --out dist/manifest.json
    package  --dist dist/CCTV_CMS --version 2.0.0 --prev prev_manifest.json
             --out dist/IAS-CMS-Update-v2.0.0.zip --stagedir update_staging

ساختار فایل آپدیت (zip):
    update_info.json   نسخه، فایل‌های تغییریافته (مسیر+sha256+حجم)، فایل‌های حذف‌شده
    manifest.json      مانیفست کامل نسخه‌ی جدید
    files/<path>       فقط فایل‌های جدید/تغییریافته (نسبت به prev)

اگر prev داده نشود (اولین نسخه)، همه‌ی فایل‌ها داخل آپدیت می‌آیند (full payload).
"""

import argparse
import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def sha256_of(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(block), b""):
            h.update(blk)
    return h.hexdigest()


def build_manifest(dist: Path):
    files = []
    for root, _dirs, names in os.walk(dist):
        for n in names:
            p = Path(root) / n
            rel = p.relative_to(dist).as_posix()
            files.append({
                "path": rel,
                "size": p.stat().st_size,
                "sha256": sha256_of(p),
            })
    files.sort(key=lambda x: x["path"])
    return files


def cmd_manifest(args):
    dist = Path(args.dist)
    files = build_manifest(dist)
    manifest = {
        "version": args.version,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
    }
    Path(args.out).write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    total = sum(f["size"] for f in files)
    print(f"manifest v{args.version}: {len(files)} files, {total / 1e6:.1f} MB -> {args.out}")


def cmd_package(args):
    dist = Path(args.dist)
    new_files = build_manifest(dist)
    new_map = {f["path"]: f for f in new_files}

    prev_version, prev_map = None, {}
    if args.prev and Path(args.prev).exists():
        prev = json.loads(Path(args.prev).read_text(encoding="utf-8"))
        prev_version = prev.get("version")
        prev_map = {f["path"]: f for f in prev.get("files", [])}
        print(f"prev manifest: v{prev_version} ({len(prev_map)} files)")
    else:
        print("no prev manifest -> full payload update")

    changed = [f for p, f in new_map.items()
               if p not in prev_map or prev_map[p]["sha256"] != f["sha256"]]
    removed = sorted(p for p in prev_map if p not in new_map)

    stage = Path(args.stagedir)
    if stage.exists():
        shutil.rmtree(stage)
    files_dir = stage / "files"
    files_dir.mkdir(parents=True)
    for f in changed:
        src = dist / f["path"]
        dst = files_dir / f["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    manifest = {
        "version": args.version,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": new_files,
    }
    (stage / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    info = {
        "app": "IAS-CMS",
        "version": args.version,
        "prev_version": prev_version,
        "created": manifest["created"],
        "files": [{"path": f["path"], "size": f["size"], "sha256": f["sha256"]}
                  for f in changed],
        "removed": removed,
        "total_size": sum(f["size"] for f in changed),
    }
    (stage / "update_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")

    out = Path(args.out)
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _d, names in os.walk(stage):
            for n in names:
                p = Path(root) / n
                z.write(p, p.relative_to(stage).as_posix())
    print(f"update package v{args.version}: {len(changed)} changed, "
          f"{len(removed)} removed, {info['total_size'] / 1e6:.1f} MB -> {out}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest")
    m.add_argument("--dist", required=True)
    m.add_argument("--version", required=True)
    m.add_argument("--out", required=True)

    p = sub.add_parser("package")
    p.add_argument("--dist", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--prev", default=None)
    p.add_argument("--out", required=True)
    p.add_argument("--stagedir", default="update_staging")

    args = ap.parse_args()
    if args.cmd == "manifest":
        cmd_manifest(args)
    else:
        cmd_package(args)


if __name__ == "__main__":
    main()
