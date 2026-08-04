# 更新日誌

## 2026-08-04

新功能
- 依規格書建立行銷素材審查工具：用 Claude vision 檢查行銷圖片拍攝地點是否與宣傳航點一致，並檢查文案地名歧義風險
- audit/audit.py：CLI 主程式，支援批次審查（manifest.csv）與 --single 單張快速測試，含自動縮圖（超過 5MB）、429/529 退避重試、JSON 防禦解析（單筆失敗不中斷批次）
- audit/prompts.py：三段式審查 system prompt（開放辨識 → 比對宣傳標的 → 文案檢查），特別防範跨國同名地錯配與 confirmation bias
- audit/app.py：Streamlit 網頁版，側欄輸入各自的 Anthropic API key（僅存於當次瀏覽器工作階段），分「單張審查」與「批次審查」兩個分頁，批次結果可下載 results.csv

驗收
- 核心案例通過：多倫多 CN Tower 天際線配加州 Ontario（ONT）航線，正確判定 mismatch，並同步回報文案的 Ontario 跨國歧義與 guaranteed 過度承諾用語

部署
- 程式碼推上 GitHub（ariachen2020/piccheck，公開 repo），並在 Streamlit Community Cloud 部署（主檔 audit/app.py，branch master）
- GitHub 出現 Streamlit deploy key 通知屬正常現象，該金鑰僅能存取此 repo

決策記錄
- 模型維持 claude-sonnet-4-6：驗收結果辨識力足夠，地標案例換 Opus 差異不大；可用環境變數 AUDIT_MODEL 切換
- 暫不引入 GPT 雙重複查：成本與複雜度翻倍，現有 needs_human_review 人工複查機制已是防線；若日後需要，優先做「Opus 5 只複查 match 案例」的折衷方案

待辦
- 填入真實素材的 manifest.csv 跑一輪批次，觀察漏抓率後再決定是否升級模型
