# Streamlit web UI for the marketing image audit tool.
# Each visitor supplies their own Anthropic API key; the key lives only in
# st.session_state for the current browser session and is never persisted.

import csv
import io
import json
import tempfile
import time
from pathlib import Path

import anthropic
import streamlit as st

import rights
from audit import (DEFAULT_MODEL, apply_reverse_result, call_api, is_flagged,
                   parse_response, prep_image_bytes, row_from_result)
from prompts import build_user_text

REGIONS = ["Europe", "North America", "Northeast Asia", "Southeast Asia", "Oceania"]
SCOPES = ["(未指定)", "social", "paid_ad", "ooh", "print"]
SEVERITY_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
LICENSE_LABELS = {
    "verified": ("✅ verified — 授權來源已登錄", st.success),
    "double_check": ("🔍 double check — 未登錄授權來源,請人工確認採購紀錄後補上", st.warning),
    "conflict": ("🔴 conflict — metadata 與授權來源矛盾,需人工核對", st.error),
}

VERDICT_LABELS = {
    "match": ("✅ match — 圖與宣傳標的一致", st.success),
    "regional_mismatch": ("🟠 regional_mismatch — 同國不同城市,需人工判斷", st.warning),
    "mismatch": ("🔴 mismatch — 圖與宣傳標的不符!", st.error),
    "uncertain": ("⚪ uncertain — 無足夠訊號辨識,需人工複查", st.info),
    "not_checked": ("⚪ not_checked — 未指定宣傳標的,略過地點比對", st.info),
}

st.set_page_config(page_title="行銷素材審查工具", page_icon="🛫", layout="wide")
st.title("🛫 行銷素材審查工具")
st.caption("用 Claude vision 檢查行銷圖片的拍攝地點是否與宣傳航點一致,並檢查文案地名歧義風險。")

with st.sidebar:
    st.header("設定")
    api_key = st.text_input(
        "Anthropic API Key", type="password",
        help="只保存在你目前的瀏覽器工作階段,不會被儲存或上傳到任何地方。",
    )
    model = st.text_input("模型", value=DEFAULT_MODEL)
    vision_key = st.text_input(
        "Google Vision API Key(選填)", type="password",
        help="反向圖搜用。同樣只保存在目前的瀏覽器工作階段。取得方式見「使用說明」分頁。",
    )
    st.markdown("---")
    st.markdown(
        "**API key 怎麼取得?**\n\n"
        "到 [console.anthropic.com](https://console.anthropic.com/) "
        "註冊後,在 API Keys 頁面建立。每張圖審查成本約 0.03 美元。\n\n"
        "反向圖搜另需 Google Vision API key(每月前 1000 次免費),"
        "取得步驟見「使用說明」分頁。"
    )


def get_client() -> anthropic.Anthropic | None:
    if not api_key:
        st.warning("請先在左側輸入你的 Anthropic API Key。")
        return None
    return anthropic.Anthropic(api_key=api_key)


def audit_bytes(client, data: bytes, ext: str, region, city, airport, copy_text,
                usage_scope=""):
    """Audit one image given raw bytes; returns (parsed_or_None, raw_text)."""
    img_b64, media_type = prep_image_bytes(data, ext)
    user_text = build_user_text(region, city, airport, copy_text, usage_scope)
    raw = call_api(client, model, user_text, img_b64, media_type)
    return parse_response(raw), raw


def local_checks_bytes(data: bytes, ext: str, license_source: str) -> dict:
    """Run layer-1 rights checks on uploaded bytes via a temp file."""
    suffix = "." + ext.lstrip(".")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        return rights.local_checks(tmp_path, license_source)
    finally:
        tmp_path.unlink(missing_ok=True)


def norm_scope(selected: str) -> str:
    return "" if selected == "(未指定)" else selected


def render_verdict(parsed: dict):
    verdict = parsed.get("verdict", "")
    label, box = VERDICT_LABELS.get(verdict, (verdict, st.info))
    box(f"{label}\n\n{parsed.get('verdict_reason', '')}")

    ident = parsed.get("identification", {})
    st.markdown(
        f"**辨識結果:** {ident.get('best_guess', '(無)')} "
        f"(confidence: {ident.get('confidence', '-')})"
    )
    evidence = ident.get("evidence") or []
    if evidence:
        st.markdown("**辨識依據:**")
        for e in evidence:
            st.markdown(f"- {e}")

    copy_issues = parsed.get("copy_issues") or []
    if copy_issues:
        st.markdown(f"**文案問題({len(copy_issues)} 筆):**")
        for issue in copy_issues:
            st.markdown(f"- `{issue.get('type', '?')}` {issue.get('detail', '')}")

    flags = parsed.get("rights_flags") or []
    if flags:
        st.markdown(f"**版權風險訊號({len(flags)} 筆):**")
        for f in flags:
            icon = SEVERITY_ICON.get(f.get("severity", ""), "▫️")
            st.markdown(f"- {icon} `{f.get('type', '?')}`({f.get('severity', '')})"
                        f" {f.get('detail', '')}")
            if f.get("action"):
                st.caption(f"　建議動作:{f['action']}")

    integrity = parsed.get("integrity_issues") or []
    if integrity:
        st.markdown(f"**畫面合理性問題({len(integrity)} 筆):**")
        for issue in integrity:
            icon = SEVERITY_ICON.get(issue.get("severity", ""), "▫️")
            st.markdown(f"- {icon} `{issue.get('type', '?')}`({issue.get('severity', '')})"
                        f" {issue.get('detail', '')}")
            if issue.get("action"):
                st.caption(f"　建議動作:{issue['action']}")

    if parsed.get("needs_human_review"):
        st.markdown("⚠️ **此素材需要人工複查**")


def render_local_checks(local: dict):
    label, box = LICENSE_LABELS.get(local["license_status"],
                                    (local["license_status"], st.info))
    box(label)
    with st.expander("本地檢查詳情(metadata / 解析度)"):
        st.json(local)


def render_reverse(rs: dict):
    st.markdown("### 反向圖搜結果")
    if rs.get("status") == "no_key":
        st.warning("未輸入 Google Vision API Key,已略過反向圖搜。")
        return
    if rs.get("status") == "error":
        st.error(rs.get("note", "反向圖搜失敗"))
        return
    if rs.get("stock_site_hit"):
        st.error("🔴 圖片出現在圖庫網站上,極可能是需授權的圖庫素材,請核對採購紀錄:" +
                 "".join(f"\n- {u}" for u in rs.get("stock_site_urls", [])))
    for note in rs.get("notes", []):
        st.markdown(f"- {note}")
    st.markdown(f"Google 判定完全相符 {rs.get('full_match_count', 0)} 筆|"
                f"部分相符 {rs.get('partial_match_count', 0)} 筆(實際請點開連結人工比對)")
    pages = rs.get("pages", [])
    if pages:
        with st.expander(f"出現此圖的網頁({len(pages)} 筆)"):
            for p in pages:
                title = p.get("title") or p.get("url", "")
                st.markdown(f"- [{title}]({p.get('url', '')})")
    if rs.get("web_labels"):
        st.caption("網路上對這張圖的常見描述:" + "、".join(rs["web_labels"]))


def handle_api_error(e: Exception):
    if isinstance(e, anthropic.AuthenticationError):
        st.error("API key 無效,請檢查左側輸入的 key。")
    else:
        st.error(f"審查失敗:{e}")


tab_single, tab_batch, tab_help = st.tabs(["單張審查", "批次審查", "使用說明"])

with tab_help:
    manual = Path(__file__).resolve().parent.parent / "使用說明.md"
    if manual.exists():
        st.markdown(manual.read_text(encoding="utf-8"))
    else:
        st.info("使用說明文件不存在。")

with tab_single:
    col_input, col_result = st.columns([1, 1])
    with col_input:
        uploaded = st.file_uploader("上傳圖片(jpg / png / webp)", type=["jpg", "jpeg", "png", "webp"])
        region = st.selectbox("宣傳區域(選填)", ["(未指定)"] + REGIONS,
                              help="不指定就略過地點比對,只做文案檢查與版權掃描")
        city = st.text_input("宣傳城市(英文,選填)", placeholder="例如 Ontario;空白 = 泛區域形象素材")
        airport = st.text_input("IATA 機場代碼(選填)", placeholder="例如 ONT — 同名地防呆的關鍵欄位")
        copy_text = st.text_area("文案全文(選填)", placeholder="空白則只審圖")
        col_a, col_b = st.columns(2)
        license_source = col_a.text_input(
            "授權來源(選填)", placeholder="stock:訂單號 / inhouse / agency",
            help="空白會標 double_check(請人工確認採購紀錄),不因圖看起來乾淨就跳過")
        scope = col_b.selectbox("用途 usage_scope", SCOPES,
                                help="影響肖像權與地標限制的嚴格度")
        do_reverse = st.checkbox(
            "反向圖搜(查這張圖在網路上的出處)",
            help="用 Google Vision 查這張圖出現在哪些網站,抓圖庫素材與轉存圖。"
                 "需在左側輸入 Google Vision API Key。")
        run = st.button("開始審查", type="primary", disabled=uploaded is None)
        if uploaded is not None:
            st.image(uploaded, caption=uploaded.name, use_container_width=True)

    with col_result:
        if run and uploaded is not None:
            client = get_client()
            if client:
                ext = uploaded.name.rsplit(".", 1)[-1]
                local = local_checks_bytes(uploaded.getvalue(), ext,
                                           license_source.strip())
                with st.spinner("審查中..."):
                    try:
                        parsed, raw = audit_bytes(
                            client, uploaded.getvalue(), ext,
                            norm_scope(region), city.strip(), airport.strip(),
                            copy_text.strip(), norm_scope(scope),
                        )
                    except Exception as e:
                        handle_api_error(e)
                    else:
                        if parsed is None:
                            st.error("模型回覆無法解析為 JSON,原始回覆如下:")
                            st.code(raw)
                        else:
                            render_local_checks(local)
                            render_verdict(parsed)
                            reverse = None
                            if do_reverse:
                                with st.spinner("反向圖搜中..."):
                                    reverse = rights.reverse_search(
                                        uploaded.getvalue(), vision_key)
                                render_reverse(reverse)
                            detail = {**parsed, "local_checks": local}
                            if reverse is not None:
                                detail["reverse_search"] = reverse
                            with st.expander("完整 JSON 回覆"):
                                st.json(detail)

with tab_batch:
    st.markdown(
        "上傳 **manifest.csv**(欄位:`filename, campaign_region, campaign_city, "
        "campaign_airport, copy_text, license_source, usage_scope`)與對應的圖片檔,"
        "一次審查整批素材。"
    )
    manifest_file = st.file_uploader("上傳 manifest.csv", type=["csv"])
    image_files = st.file_uploader(
        "上傳圖片(可多選)", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True,
    )
    batch_reverse = st.checkbox(
        "對被標記的圖做反向圖搜",
        help="只查有版權旗標或授權待確認的圖,控制 Google Vision 費用"
             "(每月前 1000 次免費)。需在左側輸入 Google Vision API Key。")
    run_batch = st.button(
        "開始批次審查", type="primary",
        disabled=manifest_file is None or not image_files,
    )

    if run_batch:
        client = get_client()
        if client:
            rows = list(csv.DictReader(io.StringIO(manifest_file.getvalue().decode("utf-8-sig"))))
            images = {f.name: f for f in image_files}
            if not rows:
                st.error("manifest 是空的,沒有素材可審。")
            else:
                results, details = [], {}
                progress = st.progress(0.0, text="開始審查...")
                from audit import EMPTY_ROW
                for i, m in enumerate(rows, 1):
                    filename = (m.get("filename") or "").strip()
                    progress.progress(i / len(rows), text=f"[{i}/{len(rows)}] {filename}")
                    if filename not in images:
                        results.append({**EMPTY_ROW, "filename": filename,
                                        "verdict": "file_not_found",
                                        "verdict_reason": "未上傳此圖片"})
                        continue
                    f = images[filename]
                    ext = filename.rsplit(".", 1)[-1]
                    local = local_checks_bytes(
                        f.getvalue(), ext, (m.get("license_source") or "").strip())
                    try:
                        parsed, raw = audit_bytes(
                            client, f.getvalue(), ext,
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
                        continue
                    detail = parsed if parsed is not None else {"parse_error": True, "raw": raw}
                    row = row_from_result(filename, parsed, local)
                    if batch_reverse and is_flagged(row):
                        rs = rights.reverse_search(f.getvalue(), vision_key)
                        apply_reverse_result(row, rs)
                        detail = {**detail, "reverse_search": rs}
                    details[filename] = {**detail, "local_checks": local}
                    results.append(row)
                    if i < len(rows):
                        time.sleep(0.5)
                progress.empty()

                criticals = [r["filename"] for r in results if r["max_severity"] == "critical"]
                conflicts = [r["filename"] for r in results if r["license_status"] == "conflict"]
                stock_hits = [r["filename"] for r in results
                              if r.get("reverse_stock_hit") is True]
                if stock_hits:
                    st.error("🔴 反向圖搜命中圖庫網站,請核對採購紀錄:" +
                             "".join(f"\n- {n}" for n in stock_hits))
                if criticals:
                    st.error("🔴 版權 critical 旗標,最優先處理:" +
                             "".join(f"\n- {n}" for n in criticals))
                if conflicts:
                    st.error("🔴 授權來源矛盾(license conflict):" +
                             "".join(f"\n- {n}" for n in conflicts))

                counts = {}
                for r in results:
                    counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
                st.markdown("### 審查摘要")
                cols = st.columns(4)
                for col, verdict in zip(cols, ("match", "regional_mismatch", "mismatch", "uncertain")):
                    col.metric(verdict, counts.get(verdict, 0))
                other = {k: v for k, v in counts.items()
                         if k not in ("match", "regional_mismatch", "mismatch", "uncertain")}
                if other:
                    st.warning("另有:" + "、".join(f"{k} {v} 筆" for k, v in other.items()))

                flagged = [r["filename"] for r in results if r["needs_human_review"] is True]
                if flagged:
                    st.markdown(f"**需人工複查({len(flagged)} 筆):**" +
                                "".join(f"\n- {n}" for n in flagged))

                st.dataframe(results, use_container_width=True)

                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=[
                    "filename", "best_guess", "confidence", "verdict",
                    "verdict_reason", "copy_issue_count", "rights_flag_count",
                    "max_severity", "license_status", "reverse_hits",
                    "reverse_stock_hit", "needs_human_review"])
                writer.writeheader()
                writer.writerows(results)
                st.download_button("下載 results.csv", buf.getvalue().encode("utf-8-sig"),
                                   file_name="results.csv", mime="text/csv")

                if details:
                    st.markdown("### 完整回覆(供人工複查)")
                    for filename, detail in details.items():
                        with st.expander(filename):
                            st.json(detail)
