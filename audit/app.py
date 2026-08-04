# Streamlit web UI for the marketing image audit tool.
# Each visitor supplies their own Anthropic API key; the key lives only in
# st.session_state for the current browser session and is never persisted.

import csv
import io
import json
import time

import anthropic
import streamlit as st

from audit import DEFAULT_MODEL, call_api, parse_response, prep_image_bytes, row_from_result
from prompts import build_user_text

REGIONS = ["Europe", "North America", "Northeast Asia", "Southeast Asia", "Oceania"]

VERDICT_LABELS = {
    "match": ("✅ match — 圖與宣傳標的一致", st.success),
    "regional_mismatch": ("🟠 regional_mismatch — 同國不同城市,需人工判斷", st.warning),
    "mismatch": ("🔴 mismatch — 圖與宣傳標的不符!", st.error),
    "uncertain": ("⚪ uncertain — 無足夠訊號辨識,需人工複查", st.info),
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
    st.markdown("---")
    st.markdown(
        "**API key 怎麼取得?**\n\n"
        "到 [console.anthropic.com](https://console.anthropic.com/) "
        "註冊後,在 API Keys 頁面建立。每張圖審查成本約 0.01 美元。"
    )


def get_client() -> anthropic.Anthropic | None:
    if not api_key:
        st.warning("請先在左側輸入你的 Anthropic API Key。")
        return None
    return anthropic.Anthropic(api_key=api_key)


def audit_bytes(client, data: bytes, ext: str, region, city, airport, copy_text):
    """Audit one image given raw bytes; returns (parsed_or_None, raw_text)."""
    img_b64, media_type = prep_image_bytes(data, ext)
    user_text = build_user_text(region, city, airport, copy_text)
    raw = call_api(client, model, user_text, img_b64, media_type)
    return parse_response(raw), raw


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

    if parsed.get("needs_human_review"):
        st.markdown("⚠️ **此素材需要人工複查**")


def handle_api_error(e: Exception):
    if isinstance(e, anthropic.AuthenticationError):
        st.error("API key 無效,請檢查左側輸入的 key。")
    else:
        st.error(f"審查失敗:{e}")


tab_single, tab_batch = st.tabs(["單張審查", "批次審查"])

with tab_single:
    col_input, col_result = st.columns([1, 1])
    with col_input:
        uploaded = st.file_uploader("上傳圖片(jpg / png / webp)", type=["jpg", "jpeg", "png", "webp"])
        region = st.selectbox("宣傳區域", REGIONS)
        city = st.text_input("宣傳城市(英文,選填)", placeholder="例如 Ontario;空白 = 泛區域形象素材")
        airport = st.text_input("IATA 機場代碼(選填)", placeholder="例如 ONT — 同名地防呆的關鍵欄位")
        copy_text = st.text_area("文案全文(選填)", placeholder="空白則只審圖")
        run = st.button("開始審查", type="primary", disabled=uploaded is None)
        if uploaded is not None:
            st.image(uploaded, caption=uploaded.name, use_container_width=True)

    with col_result:
        if run and uploaded is not None:
            client = get_client()
            if client:
                ext = uploaded.name.rsplit(".", 1)[-1]
                with st.spinner("審查中..."):
                    try:
                        parsed, raw = audit_bytes(
                            client, uploaded.getvalue(), ext,
                            region, city.strip(), airport.strip(), copy_text.strip(),
                        )
                    except Exception as e:
                        handle_api_error(e)
                    else:
                        if parsed is None:
                            st.error("模型回覆無法解析為 JSON,原始回覆如下:")
                            st.code(raw)
                        else:
                            render_verdict(parsed)
                            with st.expander("完整 JSON 回覆"):
                                st.json(parsed)

with tab_batch:
    st.markdown(
        "上傳 **manifest.csv**(欄位:`filename, campaign_region, campaign_city, "
        "campaign_airport, copy_text`)與對應的圖片檔,一次審查整批素材。"
    )
    manifest_file = st.file_uploader("上傳 manifest.csv", type=["csv"])
    image_files = st.file_uploader(
        "上傳圖片(可多選)", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True,
    )
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
                for i, m in enumerate(rows, 1):
                    filename = (m.get("filename") or "").strip()
                    progress.progress(i / len(rows), text=f"[{i}/{len(rows)}] {filename}")
                    if filename not in images:
                        results.append({"filename": filename, "best_guess": "", "confidence": "",
                                        "verdict": "file_not_found", "verdict_reason": "未上傳此圖片",
                                        "copy_issue_count": "", "needs_human_review": True})
                        continue
                    try:
                        f = images[filename]
                        parsed, raw = audit_bytes(
                            client, f.getvalue(), filename.rsplit(".", 1)[-1],
                            (m.get("campaign_region") or "").strip(),
                            (m.get("campaign_city") or "").strip(),
                            (m.get("campaign_airport") or "").strip(),
                            (m.get("copy_text") or "").strip(),
                        )
                    except Exception as e:  # one bad asset must not kill the batch
                        results.append({"filename": filename, "best_guess": "", "confidence": "",
                                        "verdict": "api_error", "verdict_reason": str(e)[:200],
                                        "copy_issue_count": "", "needs_human_review": True})
                        continue
                    details[filename] = parsed if parsed is not None else {"parse_error": True, "raw": raw}
                    results.append(row_from_result(filename, parsed))
                    if i < len(rows):
                        time.sleep(0.5)
                progress.empty()

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
                    "verdict_reason", "copy_issue_count", "needs_human_review"])
                writer.writeheader()
                writer.writerows(results)
                st.download_button("下載 results.csv", buf.getvalue().encode("utf-8-sig"),
                                   file_name="results.csv", mime="text/csv")

                if details:
                    st.markdown("### 完整回覆(供人工複查)")
                    for filename, detail in details.items():
                        with st.expander(filename):
                            st.json(detail)
