# 更新日誌

## 2026-08-04(晚間更新:版權風險審查模組)

新功能
- 依第二份規格書加入版權風險審查模組,雙層架構:第一層程式端檢查(audit/rights.py,不經 API),第二層 Claude vision 掃描(併入主 prompt 第四步)
- 第一層:exiftool 讀取 EXIF/IPTC/XMP metadata(未安裝時自動降級用 Pillow)、與 manifest 的 license_source 交叉比對(矛盾標 conflict)、解析度過低標 low_res_suspect、C2PA 驗證介面(裝 c2patool 才啟用)
- 第二層:掃描浮水印、第三方 IP、可辨識人臉、受限地標、AI 生成痕跡、editorial-only 素材六類風險訊號,依 usage_scope 調整嚴重度
- manifest.csv 新增 license_source、usage_scope 欄位;results.csv 新增 rights_flag_count、max_severity、license_status 欄位
- 終端摘要與網頁版把 critical 旗標和 license conflict 置頂顯示;critical 或 conflict 一律強制 needs_human_review
- 反向圖搜留介面(--reverse-search 開關,尚未實作)
- Streamlit 網頁版同步支援:單張審查加授權來源與用途欄位,批次讀取新欄位;新增 packages.txt 讓雲端環境也裝 exiftool

驗收
- 帶 Shutterstock 浮水印的測試圖正確觸發 watermark/critical,連圖庫編號都讀出並給出購買授權的建議動作;圖乾淨但 license_source 空白時維持 unverified(兩層 AND 關係);metadata 寫 Getty 配 inhouse 正確標 conflict;低解析度圖正確標 low_res_suspect

設計原則(規格書共識)
- 本模組不判定侵權,只輸出證據與旗標;視覺乾淨不等於授權乾淨;浮水印偵測寧可誤報;地標限制只提示並導向法務

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
