# 更新日誌

## 2026-08-08(新增畫面合理性檢查)

新功能
- 審查指令新增第五步「圖像合理性檢查」,專抓 AI 生成與合成圖的內容錯誤,分四類回報:
  - landmark_error 地標張冠李戴:標示城市名的貼紙或分格,地標必須屬於該城市;背景不可混入其他城市的建築;地標輪廓神似但細節錯誤也要抓
  - vehicle_error 交通工具不符當地:重點是「顏色對但車型錯」,例如墨爾本電車塗裝對但畫成歐洲流線車體;另查駕駛側與街景樣式
  - physics_error 透視與物理矛盾:仰拍配平視背景、從下往上看飛機卻露出機背、光影或風向互相矛盾
  - brand_error 品牌細節錯誤:航空公司商標位置、機型、標準色
- 結果新增 integrity_issues 欄位;含 warning 以上等級時強制人工複查(模型端與程式端雙重保險)
- CLI 的 results.csv 新增 integrity_issue_count 欄位,單張輸出加印「畫面合理性」筆數;網頁版結果新增「畫面合理性問題」區塊,格式與版權訊號一致
- 使用說明同步更新:工具功能由兩件事改為三件事

緣起與實測
- 起因是一張華航紐澳增班的宣傳圖:朋友指出墨爾本貼紙的電車「看起來像歐洲的」,人工放大確認塗裝是墨爾本綠加奶油色、車體卻是歐洲 Tatra/PCC 式流線造型(真實 W 級電車是方正車頭、中央單燈);同圖另有仰拍卻見飛機機背、前景仰角配平視背景、頭髮與裙襬風向相反、背景疑似混入奧克蘭天空塔等問題
- 舊版指令完全沒叫模型檢查這些,所以看不出來——結論是補指令而不是換模型
- 驗收:以模擬回覆單測 row_from_result,integrity_issues 計數與強制人工複查邏輯正確;三個檔案語法檢查通過(本機 Python 3.9 無法跑完整 import,部署環境不受影響)

部署狀態(待確認)
- push 後線上實測同一張華航圖,結果沒有「畫面合理性問題」區塊,且光影矛盾仍被歸在 ai_generated——判斷線上仍是舊版,Streamlit Cloud 沒有自動更新(與 8/5 的部署經驗一致)
- 待辦:到 share.streamlit.io 的 Manage app 按 Reboot app,再用同一張圖重測;判斷標準是結果出現「畫面合理性問題」區塊,至少應抓到電車車型(vehicle_error)與飛機視角(physics_error)
- 若 Reboot 後仍分不清版本,考慮在側欄加一行審查規則版本標記

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
- 真實金鑰連線實測通過:浮水印測試圖查出真實出處為 Wikimedia Commons(Toronto_Skyline_2017.jpg),證明圖上的 Shutterstock 浮水印為測試時後製的假浮水印──第二層與第三層證據矛盾時反向圖搜勝出,正是此模組的價值所在
- 下載比對驗證 Google 判定品質:8 個「相符」網址與原圖相關度皆 0.993 以上,確認為同一張照片的轉載,Google 未誤判;使用者看到「角度不同」的來源是「出現此圖的網頁」清單(頁面封面非被比中的圖)

當日後續調整(實測後的修正)
- 修正金鑰含非英文字元時的編碼錯誤:改為中文提示,說明如何從 Google 主控台重新複製完整金鑰
- 修正複雜圖片回覆被截斷:模型回覆上限 1500 token 調高至 4000(多地標合成圖的分析較長,原上限會切斷 JSON 導致解析失敗)
- 宣傳區域改為選填:未指定時 verdict 回 not_checked,略過地點比對,只做文案檢查與版權掃描(支援純版權審查的用法)
- 反向圖搜結果措辭調整:「找到完全相同的圖」改為「找到類似圖片」,數字統計註明沿用 Google 分類、實際請人工比對
- 版權旗標定位調整(Aria 拍板):版權審查重點是「照片本身」的授權而非被拍到的建築物──公共建築外觀不再因知名就標記,只提示已知例外(艾菲爾夜間燈光、藝術裝置為主體、場所內部);logo/商標偵測維持不變
- action 用語調整:不再寫「請洽法務確認」(不預設使用者有法務部門),改寫具體可執行的下一步
- 使用說明新增「結果看起來怪怪的?」章節:網頁清單點開圖不一樣、浮水印與反向圖搜矛盾、相似不等於相符等常見情況的原因與判讀

部署記錄(完整版上線,一波三折)
- 完整版(版權模組+反向圖搜)force push 上線,取代線上舊版(舊版只有地點審查+精簡說明)
- 坑一:force push 改寫歷史,Streamlit Cloud 增量拉取拉出新舊混雜的程式碼(app.py 新、audit.py 舊)造成 ImportError,推新 commit 也修不好;解法是 Manage app → Reboot app 強制重新 clone
- 坑二:Reboot 重裝套件抓到當天剛發布的 streamlit 1.61.0,與 starlette 1.4.0 介面不相容,伺服器啟動即 500;解法是 requirements.txt 釘死已知正常組合(streamlit==1.60.0 + starlette==1.3.1)
- 經驗:此專案日後避免用 force push 部署;套件版本一律釘死,升級前先本地驗證
- 待辦:雲端 Streamlit 對 use_container_width 參數有棄用警告(2025-12-31 後移除),日後改用 width 參數

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
