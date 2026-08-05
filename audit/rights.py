# Program-side (non-AI) copyright risk checks: metadata, resolution,
# license-source cross-check. This layer never judges ownership — it only
# collects evidence for human verification.

import base64
import io
import json
import shutil
import subprocess
import urllib.error
import urllib.request
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
        return "double_check", ("manifest 未提供 license_source,不代表圖有問題,"
                                "請人工 double check 採購紀錄後補上來源")
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


# Stock agency domains: a reverse-search hit on these is a strong signal the
# image is licensed stock and the purchase record must be verified
STOCK_DOMAINS = [
    "shutterstock.com", "gettyimages.com", "istockphoto.com", "stock.adobe.com",
    "alamy.com", "dreamstime.com", "depositphotos.com", "123rf.com",
    "stocksy.com", "westend61.de", "unsplash.com", "pexels.com", "pixabay.com",
]

VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
VISION_MAX_BYTES = 3_500_000  # downscale before upload above this size


def _shrink_for_vision(data: bytes) -> bytes:
    if len(data) <= VISION_MAX_BYTES:
        return data
    img = Image.open(io.BytesIO(data))
    img.thumbnail((1600, 1600))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _stock_hit(url: str) -> bool:
    return any(domain in url for domain in STOCK_DOMAINS)


def reverse_search(data: bytes, api_key: str) -> dict:
    """Reverse image search via Google Cloud Vision WEB_DETECTION.

    Queries where else on the web this image (or near-copies) appears.
    Callers should only query flagged images in batch runs to stay within
    the free tier (1000 requests/month, ~USD 3.5 per extra 1000).
    """
    if not api_key:
        return {"implemented": True, "status": "no_key",
                "note": "未提供 Google Vision API key,略過反向圖搜"}
    body = json.dumps({"requests": [{
        "image": {"content": base64.b64encode(_shrink_for_vision(data)).decode("ascii")},
        "features": [{"type": "WEB_DETECTION", "maxResults": 15}],
    }]}).encode("utf-8")
    req = urllib.request.Request(
        f"{VISION_ENDPOINT}?key={api_key}", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        return {"implemented": True, "status": "error",
                "note": f"Google Vision API 回應 {e.code}:{detail}"}
    except Exception as e:
        return {"implemented": True, "status": "error",
                "note": f"反向圖搜失敗:{e}"}

    web = (payload.get("responses") or [{}])[0].get("webDetection", {})
    full = [m.get("url", "") for m in web.get("fullMatchingImages", [])]
    partial = [m.get("url", "") for m in web.get("partialMatchingImages", [])]
    pages = [{"url": p.get("url", ""), "title": p.get("pageTitle", "")}
             for p in web.get("pagesWithMatchingImages", [])]
    labels = [g.get("label", "") for g in web.get("webEntities", [])
              if g.get("label")]
    stock_hits = sorted({u for u in full + partial + [p["url"] for p in pages]
                         if _stock_hit(u)})
    notes = []
    if stock_hits:
        notes.append("圖片出現在圖庫網站上,極可能是需授權的圖庫素材,請核對採購紀錄")
    if full and not stock_hits:
        notes.append("網路上找到完全相同的圖,請確認來源與授權")
    if partial and not full:
        notes.append("找到部分相符的圖(可能是裁切或改製版本),建議人工比對")
    if not (full or partial or pages):
        notes.append("網路上未找到相符圖片(不代表沒有版權,僅供參考)")
    return {
        "implemented": True,
        "status": "ok",
        "full_match_count": len(full),
        "partial_match_count": len(partial),
        "stock_site_hit": bool(stock_hits),
        "stock_site_urls": stock_hits[:10],
        "full_matches": full[:10],
        "partial_matches": partial[:10],
        "pages": pages[:10],
        "web_labels": labels[:8],
        "notes": notes,
    }
