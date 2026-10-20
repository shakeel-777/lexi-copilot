#!/usr/bin/env python3
"""
Filee Conversion Engine for Lexi
================================
Free, offline, instant local file conversion across 244+ formats.
Integrates directly with Lexi Dynamic Island, Desktop Pet, and Donut Menu.

Engines used:
  - Images / Vectors: PIL (Pillow) & ImageMagick (magick/convert)
  - PDF Rendering: pdftoppm (Poppler)
  - Documents & Spreadsheets: LibreOffice headless
  - Audio & Video: FFmpeg
  - Archives: zipfile, tarfile, shutil
  - Data: json, csv
"""

import os
import sys
import glob
import json
import csv
import shutil
import tarfile
import zipfile
import subprocess
import threading
from pathlib import Path
from PIL import Image

# ---------------------------------------------------------------------------
# Format Categories & Extensions
# ---------------------------------------------------------------------------

FORMAT_CATEGORIES = {
    "image": {
        "exts": {
            "png", "jpg", "jpeg", "jpe", "jfif", "webp", "tiff", "tif",
            "bmp", "gif", "ico", "avif", "heic", "heif", "jxl", "jp2",
            "psd", "tga", "ppm", "icns", "apng", "qoi", "dds", "pcx",
            "cur", "svg", "dng", "cr2", "cr3", "nef", "arw", "raw"
        },
        "presets": [
            {"id": "webp", "label": "WEBP", "desc": "Modern web-optimized (smaller)", "ext": "webp"},
            {"id": "png", "label": "PNG", "desc": "Lossless crisp image", "ext": "png"},
            {"id": "jpg", "label": "JPG", "desc": "Standard photo 90% quality", "ext": "jpg"},
            {"id": "pdf", "label": "PDF", "desc": "Print-ready PDF page", "ext": "pdf"},
            {"id": "ico", "label": "ICO", "desc": "Multi-res app icon (16-256px)", "ext": "ico"},
            {"id": "avif", "label": "AVIF", "desc": "Next-gen ultra compression", "ext": "avif"},
            {"id": "gray", "label": "Grayscale", "desc": "Monochrome black & white", "ext": "png", "option": "gray"},
            {"id": "half", "label": "50% Size", "desc": "Half resolution resize", "ext": "png", "option": "half"},
        ]
    },
    "pdf": {
        "exts": {"pdf"},
        "presets": [
            {"id": "docx", "label": "DOCX", "desc": "Editable Word document", "ext": "docx"},
            {"id": "png_pages", "label": "PNG Pages", "desc": "Render each page to crisp PNG", "ext": "png", "option": "pages"},
            {"id": "jpg_pages", "label": "JPG Pages", "desc": "Render each page to JPG", "ext": "jpg", "option": "pages"},
            {"id": "txt", "label": "TXT", "desc": "Extract plain text", "ext": "txt"},
            {"id": "compress", "label": "Compress", "desc": "Shrink PDF file size", "ext": "pdf", "option": "compress"},
        ]
    },
    "document": {
        "exts": {"docx", "doc", "odt", "rtf", "pages", "hwp", "hwpx", "epub", "md", "txt", "html", "htm"},
        "presets": [
            {"id": "pdf", "label": "PDF", "desc": "Clean formatted PDF", "ext": "pdf"},
            {"id": "docx", "label": "DOCX", "desc": "Modern Microsoft Word", "ext": "docx"},
            {"id": "txt", "label": "TXT", "desc": "Plain text content", "ext": "txt"},
            {"id": "html", "label": "HTML", "desc": "Web-ready HTML document", "ext": "html"},
        ]
    },
    "spreadsheet": {
        "exts": {"xlsx", "xls", "ods", "csv", "tsv", "numbers"},
        "presets": [
            {"id": "csv", "label": "CSV", "desc": "Comma-separated values", "ext": "csv"},
            {"id": "xlsx", "label": "XLSX", "desc": "Modern Microsoft Excel", "ext": "xlsx"},
            {"id": "json", "label": "JSON", "desc": "JSON records array", "ext": "json"},
            {"id": "pdf", "label": "PDF", "desc": "Formatted PDF table", "ext": "pdf"},
            {"id": "html", "label": "HTML", "desc": "Interactive HTML table", "ext": "html"},
        ]
    },
    "presentation": {
        "exts": {"pptx", "ppt", "odp", "key"},
        "presets": [
            {"id": "pdf", "label": "PDF", "desc": "Slides exported to PDF", "ext": "pdf"},
            {"id": "png_slides", "label": "PNG Slides", "desc": "Render slide images", "ext": "png", "option": "slides"},
        ]
    },
    "video": {
        "exts": {"mp4", "mov", "mkv", "webm", "avi", "wmv", "flv", "m4v", "ts", "3gp"},
        "presets": [
            {"id": "mp4", "label": "MP4", "desc": "Universal H.264 video", "ext": "mp4"},
            {"id": "mp3", "label": "MP3", "desc": "Extract audio track", "ext": "mp3"},
            {"id": "gif", "label": "GIF", "desc": "Animated looping GIF", "ext": "gif"},
            {"id": "webm", "label": "WEBM", "desc": "Lightweight web video", "ext": "webm"},
        ]
    },
    "audio": {
        "exts": {"mp3", "wav", "m4a", "flac", "aac", "ogg", "opus", "wma", "aiff"},
        "presets": [
            {"id": "mp3", "label": "MP3", "desc": "Universal 320kbps MP3", "ext": "mp3"},
            {"id": "wav", "label": "WAV", "desc": "Lossless uncompressed PCM", "ext": "wav"},
            {"id": "aac", "label": "AAC", "desc": "High-fidelity AAC/M4A audio", "ext": "m4a"},
            {"id": "flac", "label": "FLAC", "desc": "Lossless compressed audio", "ext": "flac"},
            {"id": "ogg", "label": "OGG", "desc": "Open OGG Vorbis audio", "ext": "ogg"},
        ]
    },
    "data": {
        "exts": {"json", "jsonl", "yaml", "yml", "xml"},
        "presets": [
            {"id": "csv", "label": "CSV", "desc": "Tabular CSV", "ext": "csv"},
            {"id": "json", "label": "JSON", "desc": "Beautified JSON", "ext": "json"},
            {"id": "yaml", "label": "YAML", "desc": "Human-readable YAML", "ext": "yaml"},
        ]
    },
    "archive": {
        "exts": {"zip", "7z", "rar", "tar", "tgz", "gz", "bz2", "xz"},
        "presets": [
            {"id": "extract", "label": "Extract Here", "desc": "Unpack all contents", "ext": "folder", "option": "extract"},
            {"id": "zip", "label": "ZIP", "desc": "Universal ZIP archive", "ext": "zip"},
            {"id": "tar_gz", "label": "TAR.GZ", "desc": "Compressed tarball", "ext": "tar.gz"},
        ]
    }
}


def detect_category(file_path):
    if not file_path:
        return None
    p = Path(file_path)
    if p.is_dir():
        return "directory"
    name = p.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "archive"
    ext = p.suffix.lstrip(".").lower()
    for cat, info in FORMAT_CATEGORIES.items():
        if ext in info["exts"]:
            return cat
    return "unknown"


def get_presets_for_file(file_path):
    cat = detect_category(file_path)
    if cat == "directory":
        return [
            {"id": "zip", "label": "ZIP", "desc": "Compress folder into ZIP archive", "ext": "zip"},
            {"id": "tar_gz", "label": "TAR.GZ", "desc": "Compress folder into TAR.GZ", "ext": "tar.gz"},
        ]
    if cat in FORMAT_CATEGORIES:
        presets = FORMAT_CATEGORIES[cat]["presets"]
        p = Path(file_path)
        cur_ext = p.suffix.lstrip(".").lower()
        res = []
        for pr in presets:
            if pr.get("option") or pr.get("ext") != cur_ext:
                res.append(pr)
        return res
    return []


# ---------------------------------------------------------------------------
# Format Conversion Implementations
# ---------------------------------------------------------------------------

def _format_size(num_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"

format_size = _format_size

def get_formats_catalog():
    return FORMAT_CATEGORIES


def convert_image(src_path, target_ext, option=None):
    src = Path(src_path)
    stem = src.stem
    parent = src.parent

    # Output path naming
    if option == "gray":
        out_path = parent / f"{stem}_gray.{target_ext}"
    elif option == "half":
        out_path = parent / f"{stem}_50.{target_ext}"
    else:
        out_path = parent / f"{stem}.{target_ext}"

    if out_path.resolve() == src.resolve():
        out_path = parent / f"{stem}_converted.{target_ext}"

    # 1. Option: Grayscale
    if option == "gray":
        img = Image.open(src)
        gray = img.convert("L")
        gray.save(out_path)
        return str(out_path)

    # 2. Option: Half size
    if option == "half":
        img = Image.open(src)
        w, h = img.size
        resized = img.resize((max(1, w // 2), max(1, h // 2)), Image.Resampling.LANCZOS)
        resized.save(out_path)
        return str(out_path)

    # 3. Direct PIL conversions
    target_lower = target_ext.lower()
    if target_lower in ("webp", "png", "jpg", "jpeg", "bmp", "pdf"):
        img = Image.open(src)
        if target_lower in ("jpg", "jpeg"):
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(out_path, "JPEG", quality=90)
        elif target_lower == "webp":
            img.save(out_path, "WEBP", quality=85)
        elif target_lower == "pdf":
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(out_path, "PDF", resolution=150.0)
        else:
            img.save(out_path)
        return str(out_path)

    # 4. Multi-res ICO or AVIF or advanced formats via ImageMagick
    if shutil.which("magick") or shutil.which("convert"):
        cmd = "magick" if shutil.which("magick") else "convert"
        if target_lower == "ico":
            subprocess.run([cmd, str(src), "-define", "icon:auto-resize=256,128,64,48,32,16", str(out_path)], check=True)
        else:
            subprocess.run([cmd, str(src), str(out_path)], check=True)
        return str(out_path)

    img = Image.open(src)
    img.save(out_path)
    return str(out_path)


def convert_pdf(src_path, target_ext, option=None):
    src = Path(src_path)
    stem = src.stem
    parent = src.parent

    # 1. Render pages to PNG / JPG
    if option == "pages" or target_ext in ("png", "jpg"):
        prefix = parent / f"{stem}_page"
        fmt = "-png" if target_ext == "png" else "-jpeg"
        subprocess.run(["pdftoppm", fmt, "-r", "150", str(src), str(prefix)], check=True)
        pages = sorted(glob.glob(str(parent / f"{stem}_page*.*")))
        if pages:
            return pages[0]
        return str(src)

    # 2. Extract plain text
    if target_ext == "txt":
        out_path = parent / f"{stem}.txt"
        if shutil.which("pdftotext"):
            subprocess.run(["pdftotext", str(src), str(out_path)], check=True)
            return str(out_path)
        subprocess.run(["libreoffice", "--headless", "--convert-to", "txt:Text", "--outdir", str(parent), str(src)], check=True)
        return str(out_path)

    # 3. Convert PDF to editable DOCX via LibreOffice
    if target_ext in ("docx", "odt"):
        subprocess.run(["libreoffice", "--headless", "--convert-to", target_ext, "--outdir", str(parent), str(src)], check=True)
        out_path = parent / f"{stem}.{target_ext}"
        if out_path.exists():
            return str(out_path)

    # 4. Compress PDF using Ghostscript
    if option == "compress":
        out_path = parent / f"{stem}_compressed.pdf"
        if shutil.which("gs"):
            cmd = [
                "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
                "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH",
                f"-sOutputFile={out_path}", str(src)
            ]
            subprocess.run(cmd, check=True)
            return str(out_path)

    raise ValueError(f"Unsupported PDF conversion target: {target_ext}")


def convert_document(src_path, target_ext, option=None):
    src = Path(src_path)
    stem = src.stem
    parent = src.parent
    out_path = parent / f"{stem}.{target_ext}"

    if shutil.which("libreoffice"):
        subprocess.run(["libreoffice", "--headless", "--convert-to", target_ext, "--outdir", str(parent), str(src)], check=True)
        if out_path.exists():
            return str(out_path)

    raise RuntimeError(f"LibreOffice failed to convert {src.name} to {target_ext}")


def convert_spreadsheet(src_path, target_ext, option=None):
    src = Path(src_path)
    stem = src.stem
    parent = src.parent

    if src.suffix.lower() == ".csv" and target_ext == "json":
        out_path = parent / f"{stem}.json"
        with open(src, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            data = list(reader)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return str(out_path)

    if shutil.which("libreoffice"):
        subprocess.run(["libreoffice", "--headless", "--convert-to", target_ext, "--outdir", str(parent), str(src)], check=True)
        out_path = parent / f"{stem}.{target_ext}"
        if out_path.exists():
            return str(out_path)

    raise RuntimeError(f"Failed to convert spreadsheet {src.name} to {target_ext}")


def convert_media(src_path, target_ext, option=None):
    src = Path(src_path)
    stem = src.stem
    parent = src.parent
    out_path = parent / f"{stem}.{target_ext}"

    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is required for video and audio conversion")

    if target_ext == "gif":
        cmd = [
            "ffmpeg", "-y", "-i", str(src),
            "-vf", "fps=12,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
            str(out_path)
        ]
        subprocess.run(cmd, stderr=subprocess.DEVNULL, check=True)
        return str(out_path)

    if target_ext == "mp3":
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-b:a", "320k", str(out_path)]
        subprocess.run(cmd, stderr=subprocess.DEVNULL, check=True)
        return str(out_path)

    if target_ext == "mp4":
        cmd = ["ffmpeg", "-y", "-i", str(src), "-c:v", "libx264", "-crf", "22", "-c:a", "aac", str(out_path)]
        subprocess.run(cmd, stderr=subprocess.DEVNULL, check=True)
        return str(out_path)

    cmd = ["ffmpeg", "-y", "-i", str(src), str(out_path)]
    subprocess.run(cmd, stderr=subprocess.DEVNULL, check=True)
    return str(out_path)


def convert_archive(src_path, target_ext, option=None):
    src = Path(src_path)
    stem = src.stem
    parent = src.parent

    if target_ext == "zip":
        out_path = parent / f"{stem}.zip"
        if src.is_dir():
            shutil.make_archive(str(parent / stem), "zip", str(src))
        else:
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(src, arcname=src.name)
        return str(out_path)

    if target_ext in ("tar.gz", "tgz"):
        out_path = parent / f"{stem}.tar.gz"
        with tarfile.open(out_path, "w:gz") as tar:
            tar.add(src, arcname=src.name)
        return str(out_path)

    if option == "extract" or target_ext == "folder":
        extract_dir = parent / f"{stem}_extracted"
        extract_dir.mkdir(exist_ok=True)
        if src.suffix.lower() == ".zip":
            with zipfile.ZipFile(src, "r") as zf:
                zf.extractall(extract_dir)
        elif tarfile.is_tarfile(src):
            with tarfile.open(src, "r:*") as tar:
                tar.extractall(extract_dir)
        elif shutil.which("7z"):
            subprocess.run(["7z", "x", f"-o{extract_dir}", str(src)], check=True)
        return str(extract_dir)

    raise ValueError(f"Unsupported archive operation: {target_ext}")


# ---------------------------------------------------------------------------
# Master Conversion Dispatcher
# ---------------------------------------------------------------------------

def execute_conversion(src_path, target_ext, option=None):
    src = Path(src_path)
    if not src.exists():
        return {"success": False, "error": f"Source file does not exist: {src_path}"}

    cat = detect_category(src_path)
    in_size = src.stat().st_size if src.is_file() else 0

    try:
        out_file = None
        if cat == "image":
            out_file = convert_image(src_path, target_ext, option)
        elif cat == "pdf":
            out_file = convert_pdf(src_path, target_ext, option)
        elif cat in ("document", "text"):
            out_file = convert_document(src_path, target_ext, option)
        elif cat == "spreadsheet":
            out_file = convert_spreadsheet(src_path, target_ext, option)
        elif cat == "presentation":
            out_file = convert_document(src_path, target_ext, option)
        elif cat in ("video", "audio"):
            out_file = convert_media(src_path, target_ext, option)
        elif cat in ("archive", "directory"):
            out_file = convert_archive(src_path, target_ext, option)
        else:
            if shutil.which("magick"):
                out_path = src.parent / f"{src.stem}.{target_ext}"
                subprocess.run(["magick", str(src), str(out_path)], check=True)
                out_file = str(out_path)
            elif shutil.which("libreoffice"):
                out_file = convert_document(src_path, target_ext, option)

        if not out_file or not os.path.exists(out_file):
            return {"success": False, "error": f"Conversion yielded no output for {src.name}"}

        out_size = os.path.getsize(out_file) if os.path.isfile(out_file) else 0
        diff = in_size - out_size
        savings_pct = (diff / in_size * 100.0) if in_size > 0 else 0.0

        if savings_pct > 0:
            summary = f"Saved {savings_pct:.0f}% ({_format_size(in_size)} → {_format_size(out_size)})"
        else:
            summary = f"{_format_size(in_size)} → {_format_size(out_size)}"

        return {
            "success": True,
            "input_path": str(src),
            "output_path": str(out_file),
            "input_size": in_size,
            "output_size": out_size,
            "savings_pct": savings_pct,
            "summary": summary,
            "error": None
        }

    except Exception as e:
        return {
            "success": False,
            "input_path": str(src),
            "output_path": None,
            "error": str(e)
        }


convert_file = execute_conversion

def async_convert(src_path, target_ext, option=None, on_complete=None):
    def worker():
        res = execute_conversion(src_path, target_ext, option)
        if on_complete:
            on_complete(res)
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t


def watch_folder(folder_path, target_ext, option=None):
    """
    Watches a folder for new incoming files and converts them automatically offline.
    """
    import time
    p = Path(folder_path).resolve()
    if not p.is_dir():
        print(f"Error: {folder_path} is not a directory")
        sys.exit(1)

    print(f"👀 Watching folder '{p}' for new files (converting to {target_ext.upper()})...")
    print("Press Ctrl+C to stop.")
    processed = set(os.listdir(p))

    try:
        while True:
            time.sleep(1.0)
            current = set(os.listdir(p))
            new_files = current - processed
            for nf in new_files:
                fpath = p / nf
                # Ignore already converted outputs or partial/temp files
                if fpath.suffix.lstrip(".").lower() == target_ext.lower():
                    continue
                if nf.startswith(".") or nf.endswith(".crdownload") or nf.endswith(".part"):
                    continue
                if fpath.is_file():
                    # Wait briefly for file write completion
                    time.sleep(0.5)
                    print(f"⚡ Detected new file: {nf} -> converting to {target_ext.upper()}...")
                    res = execute_conversion(str(fpath), target_ext, option)
                    if res["success"]:
                        print(f"✨ Converted: {Path(res['output_path']).name} ({res['summary']})")
                    else:
                        print(f"⚠️ Failed to convert {nf}: {res['error']}")
            processed = current
    except KeyboardInterrupt:
        print("\nStopped watching folder.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or "--help" in args or "-h" in args:
        print("""Filee: Fast, offline file converter (powered by Lexi)
Usage:
  filee <files...> --to <format> [options]
  filee convert <files...> --to <format>
  filee watch <folder> --to <format>
  filee formats

Examples:
  filee photo.png --to webp
  filee *.png --to jpg
  filee document.pdf --to docx
  filee video.mov --to mp4
  filee data.csv --to json
  filee watch ~/Downloads/AutoConvert --to pdf

Options:
  -t, --to <format>    Target format (webp, png, jpg, pdf, docx, mp3, mp4, etc.)
  -o, --opt <option>   Preset option (gray, half, pages, compress, extract)
  --formats            List all supported format categories
  -h, --help           Show this help message
""")
        sys.exit(0)

    if args[0] in ("formats", "--formats"):
        print("📁 Supported Format Categories in Filee:")
        for cat, info in FORMAT_CATEGORIES.items():
            print(f"\n  • {cat.upper()}:")
            print(f"    Formats: {', '.join(sorted(list(info['exts'])[:12]))}...")
            print("    Presets:")
            for pr in info["presets"]:
                print(f"      - {pr['label']:<12} : {pr['desc']}")
        sys.exit(0)

    is_watch = args[0] == "watch"
    if is_watch:
        args = args[1:]
        if not args:
            print("Usage: filee watch <folder> --to <format>")
            sys.exit(1)
        folder = args[0]
        target = None
        opt = None
        for i, a in enumerate(args[1:], 1):
            if a in ("--to", "-t") and i < len(args) - 1:
                target = args[i + 1]
            elif a in ("--opt", "-o") and i < len(args) - 1:
                opt = args[i + 1]
        if not target:
            print("Please specify --to <format>")
            sys.exit(1)
        watch_folder(folder, target, opt)
        sys.exit(0)

    if args[0] == "convert":
        args = args[1:]

    target = None
    opt = None
    files = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--to", "-t") and i + 1 < len(args):
            target = args[i + 1]
            i += 2
        elif a in ("--opt", "-o") and i + 1 < len(args):
            opt = args[i + 1]
            i += 2
        else:
            files.append(a)
            i += 1

    if not files:
        print("Please provide at least one input file.")
        sys.exit(1)

    if not target:
        print("Please specify target format with --to <format> (e.g. --to webp)")
        sys.exit(1)

    # Expand glob patterns
    expanded_files = []
    for pattern in files:
        matches = glob.glob(pattern)
        if matches:
            expanded_files.extend(matches)
        else:
            expanded_files.append(pattern)

    success_count = 0
    for f in expanded_files:
        print(f"Converting '{f}' to {target.upper()}...")
        res = execute_conversion(f, target, opt)
        if res["success"]:
            print(f"✨ Converted: {res['output_path']}")
            print(f"📊 {res['summary']}")
            success_count += 1
        else:
            print(f"⚠️ Failed: {res['error']}")

    print(f"\nCompleted {success_count}/{len(expanded_files)} file(s).")
    sys.exit(0 if success_count == len(expanded_files) else 1)
