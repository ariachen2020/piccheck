# Program-side (non-AI) copyright risk checks: metadata, resolution,
# license-source cross-check. This layer never judges ownership — it only
# collects evidence for human verification.

import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image
from PIL.ExifTags import TAGS

# Stock agency keywords for metadata vs license_source cross-check
STOCK_AGENCIES = [
    "getty", "shutterstock", "adobe stock", "istock", "alamy",
    "dreamstime", "depositphotos", "123rf", "stocksy", "westend61",
]

MIN_LONG_EDGE = 1200  # below this the file may be a preview/re-saved web image

SEVERITY_ORDER = {"critical": 3, "warning": 2, "info": 1}


def exiftool_available() -> bool:
    return shutil.which("exiftool") is not None


def read_metadata(path: Path) -> dict:
    """Read Copyright/Creator/Credit metadata; exiftool preferred, Pillow fallback."""
    if exiftool_available():
        try:
            out = subprocess.run(
                ["exiftool", "-j", "-Copyright", "-Creator", "-Artist",
                 "-Credit", "-By-line", "-Source", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            data = json.loads(out.stdout)[0]
            fields = {k: str(v) for k, v in data.items()
                      if k not in ("SourceFile",) and str(v).strip()}
            return {"method": "exiftool", "fields": fields,
                    "has_metadata": bool(fields)}
        except Exception:
            pass  # fall through to Pillow
    # Simplified fallback: Pillow reads basic EXIF only (no IPTC/XMP)
    fields = {}
    try:
        exif = Image.open(path).getexif()
        for tag_id, value in exif.items():
            name = TAGS.get(tag_id, str(tag_id))
            if name in ("Copyright", "Artist") and str(value).strip():
                fields[name] = str(value).strip()
    except Exception:
        pass
    return {"method": "pillow (簡化版,僅基本 EXIF)", "fields": fields,
            "has_metadata": bool(fields)}


def check_c2pa(path: Path) -> dict | None:
    """Verify C2PA content credentials if c2patool is installed; else skip."""
    if shutil.which("c2patool") is None:
        return None
    try:
        out = subprocess.run(["c2patool", str(path)],
                             capture_output=True, text=True, timeout=30)
        return {"available": True, "output": out.stdout[:2000]}
    except Exception:
        return None


def check_resolution(path: Path) -> dict:
    try:
        w, h = Image.open(path).size
    except Exception:
        return {"width": None, "height": None, "low_res_suspect": False}
    return {"width": w, "height": h,
            "low_res_suspect": max(w, h) < MIN_LONG_EDGE}


def _metadata_mentions_agency(metadata: dict) -> str | None:
    joined = " ".join(metadata.get("fields", {}).values()).lower()
    for agency in STOCK_AGENCIES:
        if agency in joined:
            return agency
    return None


def license_status(license_source: str, metadata: dict) -> tuple[str, str]:
    """Return (status, note). Clean-looking pixels never upgrade the status."""
    source = (license_source or "").strip()
    agency = _metadata_mentions_agency(metadata)
    if not source:
        return "unverified", "manifest 未提供 license_source,無法驗證授權,需人工補齊採購紀錄"
    if agency and not source.startswith("stock:"):
        return "conflict", (f"metadata 出現圖庫名稱「{agency}」,"
                            f"但 license_source 為 {source},來源矛盾,需人工核對")
    return "verified", f"license_source: {source}"


def local_checks(path: Path, license_source: str) -> dict:
    """Run all layer-1 checks for one image; returns a report dict."""
    metadata = read_metadata(path)
    resolution = check_resolution(path)
    status, note = license_status(license_source, metadata)
    notes = [note]
    if not metadata["has_metadata"]:
        notes.append("圖片無 metadata(非問題,但社群轉存圖通常被剝除,註記供參考)")
    if resolution["low_res_suspect"]:
        notes.append(f"長邊 {max(resolution['width'], resolution['height'])}px < "
                     f"{MIN_LONG_EDGE}px,可能是圖庫預覽圖或網路轉存(low_res_suspect)")
    c2pa = check_c2pa(path)
    return {
        "metadata": metadata,
        "resolution": resolution,
        "c2pa": c2pa if c2pa else "未安裝 c2patool,跳過",
        "license_status": status,
        "notes": notes,
    }


def max_severity(rights_flags: list) -> str:
    """Highest severity among model-reported rights flags; '' if none."""
    best = ""
    best_rank = 0
    for flag in rights_flags or []:
        rank = SEVERITY_ORDER.get(flag.get("severity", ""), 0)
        if rank > best_rank:
            best, best_rank = flag["severity"], rank
    return best


def reverse_search(path: Path) -> dict:
    """Reverse image search stub (TinEye / Google Vision WEB_DETECTION).

    Interface reserved per spec; only flagged images should be queried
    when implemented, to control cost.
    """
    return {"implemented": False,
            "note": "反向圖搜尚未實作,介面保留(--reverse-search)"}
