# 更新日誌

## 2026-08-05(反向圖搜上線)

新功能
- 反向圖搜(第三層)正式實作:接 Google Cloud Vision WEB_DETECTION,查詢圖片出現在網路上的位置,回報完全相符、部分相符、出現此圖的網頁清單與網路常見描述
- 命中圖庫網站(Shutterstock、Getty、iStock 等 13 個常見網域)時特別標紅提醒核對採購紀錄,並強制 needs_human_review
- 成本控制照既定共識:批次模式只查「已被標記」的圖(有 rights_flags、或授權狀態 double_check / conflict);單張模式勾選即查。Google 免費額度每月 1000 次,超出每千次約 3.5 美元
- CLI:--reverse-search 開關由介面保留轉為正式功能,金鑰讀環境變數 GOOGLE_VISION_API_KEY;results.csv 新增 reverse_hits、reverse_stock_hit 欄位
- 網頁版:側欄新增 Google Vision API Key 欄位(僅存當次瀏覽器工作階段),單張與批次分頁各加反向圖搜選項,批次摘要把圖庫命中置頂標紅
- 大圖自動縮至 1600px 再送 Google,避免超過 API 上限
- 使用說明同步更新:新增 Google API Key 申請步驟(10 分鐘教學)、反向圖搜結果解讀;「不能做到的事」中重製圖段落改為說明反向圖搜的能力邊界(原圖沒上過網仍查不到)

驗收
- 以模擬 Google 回應測試:圖庫命中正確標紅並強制人工複查、無相符結果給出「不代表沒有版權」的中性註記、超大圖正確縮圖、未提供金鑰時 CLI 明確報錯並指向使用說明
- 真實 API 連線測試待 Aria 申請 Google 金鑰後進行

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

後續調整(同日晚間)
- 實測第二個驗收案例:可口可樂產品圖正確觸發 third_party_ip/critical(logo 為主視覺自動升級嚴重度),並額外抓到 ai_generated 渲染痕跡
- 授權狀態 unverified 改名為 double_check,附註文字改為中性語氣(「不代表圖有問題,請人工確認採購紀錄後補上」),判定邏輯不變
- 每張圖費用估計修正:0.01 美元 → 0.03 美元(版權掃描加入後 prompt 與回覆變長)
- 新增使用說明書(使用說明.md)與網站「使用說明」分頁:含操作步驟、結果解讀、能做與不能做的事
- 部署策略:遠端只上線「舊版 app + 精簡版說明」(不含版權功能),版權模組完整版留在本地待 Aria 確認後再上線;本地與遠端已分岔,上線時需 force push 並換回完整版說明

決策記錄(晚間)
- 反向圖搜(重製圖偵測的完整解法)先記錄不實作,Aria 未確定是否需要;若做,優先用 Google Vision WEB_DETECTION 免費額度,只查已被標記的圖
- AVIF 格式支援待決:目前需手動轉檔,Aria 尚未回覆是否要內建支援

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
