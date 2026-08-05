#!/usr/bin/env python3
"""Marketing image audit CLI.

Checks whether the shooting location of a marketing image matches the
advertised destination, using Claude's vision capability. See SPEC:
marketing-image-audit-spec.md.

Usage:
  python audit.py --manifest manifest.csv --assets ./assets --out ./output
  python audit.py --single image.jpg --region "North America" --city "Los Angeles" --airport LAX
"""

import argparse
import base64
import csv
import io
import json
import os
import sys
import time
from pathlib import Path

import anthropic
from PIL import Image

import rights
from prompts import SYSTEM_PROMPT, build_user_text

DEFAULT_MODEL = os.environ.get("AUDIT_MODEL", "claude-sonnet-4-6")
MAX_BYTES = 5 * 1024 * 1024  # resize images above this size
MAX_LONG_EDGE = 2000         # px, long-edge cap when resizing
MAX_RETRIES = 3

MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".png": "image/png", ".webp": "image/webp"}


def prep_image_bytes(data: bytes, ext: str) -> tuple[str, str]:
    """Return (base64_data, media_type) from raw bytes; downscale above 5MB."""
    ext = ext.lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext not in MEDIA_TYPES:
        raise ValueError(f"unsupported image format: {ext} (need jpg/png/webp)")
    media_type = MEDIA_TYPES[ext]
    if len(data) > MAX_BYTES:
        img = Image.open(io.BytesIO(data))
        img.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE))
        buf = io.BytesIO()
        # Re-encode as JPEG for size; drop alpha if present
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=85)
        data = buf.getvalue()
        media_type = "image/jpeg"
    return base64.b64encode(data).decode("ascii"), media_type


def load_image_b64(path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type) for an image file on disk."""
    return prep_image_bytes(path.read_bytes(), path.suffix)


def call_api(client: anthropic.Anthropic, model: str, user_text: str,
             img_b64: str, media_type: str) -> str:
    """One API call with backoff retry on rate-limit/overload errors."""
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_b64,
                        }},
                    ],
                }],
            )
            return resp.content[0].text
        except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
            status = getattr(e, "status_code", None)
            if status not in (429, 529) or attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt * 5
            print(f"  API {status},{wait} 秒後重試({attempt + 1}/{MAX_RETRIES})...")
            time.sleep(wait)
            last_err = e
    raise last_err


def parse_response(raw: str) -> dict | None:
    """Defensive JSON parsing: strip code fences, return None on failure."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def audit_one(client, model, image_path: Path, region, city, airport, copy_text,
              usage_scope=""):
    """Audit a single asset; returns (parsed_or_None, raw_text)."""
    img_b64, media_type = load_image_b64(image_path)
    user_text = build_user_text(region, city, airport, copy_text, usage_scope)
    raw = call_api(client, model, user_text, img_b64, media_type)
    return parse_response(raw), raw


EMPTY_ROW = {"filename": "", "best_guess": "", "confidence": "", "verdict": "",
             "verdict_reason": "", "copy_issue_count": "", "rights_flag_count": "",
             "max_severity": "", "license_status": "", "reverse_hits": "",
             "reverse_stock_hit": "", "needs_human_review": True}


def is_flagged(row: dict) -> bool:
    """Batch reverse search only queries flagged images, to control cost."""
    return bool(row.get("rights_flag_count") or 0) or \
        row.get("license_status") in ("double_check", "conflict")


def apply_reverse_result(row: dict, rs: dict | None):
    """Fold a reverse-search report into a results row."""
    if not rs or rs.get("status") != "ok":
        return
    row["reverse_hits"] = rs["full_match_count"] + rs["partial_match_count"]
    row["reverse_stock_hit"] = rs["stock_site_hit"]
    if rs["stock_site_hit"]:  # stock-site hit always forces human review
        row["needs_human_review"] = True


def row_from_result(filename: str, parsed: dict | None,
                    local: dict | None = None) -> dict:
    license_status = (local or {}).get("license_status", "")
    if parsed is None:
        return {**EMPTY_ROW, "filename": filename, "verdict": "parse_error",
                "verdict_reason": "回覆非合法 JSON,原文見 details",
                "license_status": license_status}
    ident = parsed.get("identification", {})
    flags = parsed.get("rights_flags") or []
    max_sev = rights.max_severity(flags)
    # Critical flags and license conflicts always force human review
    needs_review = (parsed.get("needs_human_review", True)
                    or max_sev in ("critical", "warning")
                    or license_status == "conflict")
    return {
        "filename": filename,
        "best_guess": ident.get("best_guess", ""),
        "confidence": ident.get("confidence", ""),
        "verdict": parsed.get("verdict", ""),
        "verdict_reason": parsed.get("verdict_reason", ""),
        "copy_issue_count": len(parsed.get("copy_issues") or []),
        "rights_flag_count": len(flags),
        "max_severity": max_sev,
        "license_status": license_status,
        "needs_human_review": needs_review,
    }


def save_detail(details_dir: Path, filename: str, parsed: dict | None, raw: str,
                local: dict | None = None, reverse: dict | None = None):
    out = details_dir / (Path(filename).stem + ".json")
    detail = parsed if parsed is not None else {"parse_error": True, "raw": raw}
    if local is not None:
        detail = {**detail, "local_checks": local}
    if reverse is not None:
        detail = {**detail, "reverse_search": reverse}
    out.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")


def print_result(row: dict):
    print(f"  辨識:{row['best_guess'] or '(無)'}(confidence: {row['confidence'] or '-'})")
    print(f"  verdict:{row['verdict']} — {row['verdict_reason']}")
    print(f"  文案問題:{row['copy_issue_count']} 筆|版權旗標:{row['rights_flag_count']} 筆"
          f"(最高 {row['max_severity'] or '-'})|授權:{row['license_status'] or '-'}"
          f"|需人工複查:{row['needs_human_review']}")
    if row.get("reverse_hits") != "":
        stock = ",且出現在圖庫網站!" if row.get("reverse_stock_hit") else ""
        print(f"  反向圖搜:{row['reverse_hits']} 筆相符{stock}")


def get_vision_key(args) -> str:
    key = os.environ.get("GOOGLE_VISION_API_KEY", "")
    if args.reverse_search and not key:
        sys.exit("--reverse-search 需要環境變數 GOOGLE_VISION_API_KEY"
                 "(Google Cloud Vision API 金鑰,取得方式見使用說明.md)。")
    return key


def run_batch(args, client):
    vision_key = get_vision_key(args)
    manifest = Path(args.manifest)
    assets_dir = Path(args.assets)
    out_dir = Path(args.out)
    details_dir = out_dir / "details"
    details_dir.mkdir(parents=True, exist_ok=True)

    with manifest.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("manifest 是空的,沒有素材可審。")

    results = []
    for i, m in enumerate(rows, 1):
        filename = (m.get("filename") or "").strip()
        print(f"[{i}/{len(rows)}] {filename}")
        image_path = assets_dir / filename
        if not image_path.exists():
            results.append({**EMPTY_ROW, "filename": filename,
                            "verdict": "file_not_found",
                            "verdict_reason": "assets 內找不到檔案"})
            print("  找不到檔案,略過。")
            continue
        license_source = (m.get("license_source") or "").strip()
        local = rights.local_checks(image_path, license_source)
        try:
            parsed, raw = audit_one(
                client, args.model, image_path,
                (m.get("campaign_region") or "").strip(),
                (m.get("campaign_city") or "").strip(),
                (m.get("campaign_airport") or "").strip(),
                (m.get("copy_text") or "").strip(),
                (m.get("usage_scope") or "").strip(),
            )
        except Exception as e:  # one bad asset must not kill the batch
            results.append({**EMPTY_ROW, "filename": filename,
                            "verdict": "api_error", "verdict_reason": str(e)[:200],
                            "license_status": local["license_status"]})
            print(f"  API 失敗:{e}")
            continue
        row = row_from_result(filename, parsed, local)
        reverse = None
        if args.reverse_search and is_flagged(row):
            print("  已被標記,執行反向圖搜...")
            reverse = rights.reverse_search(image_path.read_bytes(), vision_key)
            if reverse.get("status") == "error":
                print(f"  反向圖搜失敗:{reverse['note']}")
            apply_reverse_result(row, reverse)
        save_detail(details_dir, filename, parsed, raw, local, reverse)
        results.append(row)
        print_result(row)
        if args.delay > 0 and i < len(rows):
            time.sleep(args.delay)

    fieldnames = ["filename", "best_guess", "confidence", "verdict",
                  "verdict_reason", "copy_issue_count", "rights_flag_count",
                  "max_severity", "license_status", "reverse_hits",
                  "reverse_stock_hit", "needs_human_review"]
    results_csv = out_dir / "results.csv"
    with results_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    counts = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    flagged = [r["filename"] for r in results if r["needs_human_review"] is True]
    criticals = [r["filename"] for r in results if r["max_severity"] == "critical"]
    conflicts = [r["filename"] for r in results if r["license_status"] == "conflict"]
    stock_hits = [r["filename"] for r in results if r.get("reverse_stock_hit") is True]

    print("\n===== 審查摘要 =====")
    if criticals:
        print(f"!! 版權 critical 旗標({len(criticals)} 筆),最優先處理:")
        for name in criticals:
            print(f"  - {name}")
    if stock_hits:
        print(f"!! 反向圖搜命中圖庫網站({len(stock_hits)} 筆),請核對採購紀錄:")
        for name in stock_hits:
            print(f"  - {name}")
    if conflicts:
        print(f"!! 授權來源矛盾 license conflict({len(conflicts)} 筆):")
        for name in conflicts:
            print(f"  - {name}")
    for verdict in ("match", "regional_mismatch", "mismatch", "uncertain"):
        if verdict in counts:
            print(f"  {verdict}:{counts.pop(verdict)} 筆")
    for verdict, n in counts.items():  # parse_error / api_error / file_not_found
        print(f"  {verdict}:{n} 筆")
    if flagged:
        print(f"\n需人工複查({len(flagged)} 筆):")
        for name in flagged:
            print(f"  - {name}")
    print(f"\n結果已寫入 {results_csv}")


def run_single(args, client):
    image_path = Path(args.single)
    if not image_path.exists():
        sys.exit(f"找不到圖片:{image_path}")
    vision_key = get_vision_key(args)
    print(f"審查 {image_path.name} ...")
    local = rights.local_checks(image_path, args.license_source)
    parsed, raw = audit_one(client, args.model, image_path,
                            args.region, args.city, args.airport, args.copy,
                            args.scope)
    row = row_from_result(image_path.name, parsed, local)
    reverse = None
    if args.reverse_search:  # single mode: explicit request, always run
        print("  反向圖搜中...")
        reverse = rights.reverse_search(image_path.read_bytes(), vision_key)
        apply_reverse_result(row, reverse)
    print_result(row)
    print("\n本地檢查(第一層):")
    print(json.dumps(local, ensure_ascii=False, indent=2))
    if reverse is not None:
        print("\n反向圖搜(第三層,Google Vision WEB_DETECTION):")
        print(json.dumps(reverse, ensure_ascii=False, indent=2))
    if parsed is not None:
        print("\n完整回覆:")
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    else:
        print("\nJSON 解析失敗,原始回覆:")
        print(raw)


def main():
    p = argparse.ArgumentParser(description="航空公司行銷素材審查工具")
    p.add_argument("--manifest", default="manifest.csv", help="待審素材對照表 CSV")
    p.add_argument("--assets", default="./assets", help="圖片資料夾")
    p.add_argument("--out", default="./output", help="輸出資料夾")
    p.add_argument("--single", help="單張快速測試:圖片路徑")
    p.add_argument("--region", default="", help="宣傳區域(--single 用)")
    p.add_argument("--city", default="", help="宣傳城市(--single 用)")
    p.add_argument("--airport", default="", help="IATA 機場代碼(--single 用)")
    p.add_argument("--copy", default="", help="文案全文(--single 用)")
    p.add_argument("--license-source", default="", dest="license_source",
                   help="授權來源,如 stock:12345 / inhouse / agency(--single 用)")
    p.add_argument("--scope", default="", help="用途:social / paid_ad / ooh / print(--single 用)")
    p.add_argument("--reverse-search", action="store_true",
                   help="反向圖搜(Google Vision WEB_DETECTION,需 GOOGLE_VISION_API_KEY;"
                        "批次模式只查被標記的圖以控制費用)")
    p.add_argument("--delay", type=float, default=0.5, help="每筆之間的延遲秒數")
    p.add_argument("--model", default=DEFAULT_MODEL, help="模型 ID")
    args = p.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("請先設定環境變數 ANTHROPIC_API_KEY 再執行。")
    client = anthropic.Anthropic()

    if args.single:
        run_single(args, client)
    else:
        run_batch(args, client)


if __name__ == "__main__":
    main()
