# 行銷圖文審查工具 — 開發規格(交接文件)

> 用法:把整份文件貼給 local 的 Claude Code 作為第一則訊息,或存成 `SPEC.md` 放在專案根目錄請它照做。

---

你好,我要開發一個行銷素材審查工具,以下是完整規格,請先讀完再動手。

**專案:** 航空公司行銷素材審查工具。用 Claude API 的 vision 能力,自動檢查行銷圖片的「拍攝地點」是否與宣傳航點一致,並檢查文案的地名歧義風險。核心動機:實際發生過「宣傳加州 Ontario 航線,圖片卻用了加拿大多倫多(CN Tower)天際線」的錯配——這種錯誤規則式檢查抓不到,只有看得懂圖的審查能攔截。開發語言 Python,本地執行,對話用繁體中文,程式註解英文。

**技術基礎:**
- Python 3.11+,`anthropic` 官方 SDK
- Model:`claude-sonnet-5-5`(2026-09-29 由 claude-sonnet-4-6 升級;可用環境變數切換)
- API key 從環境變數 `ANTHROPIC_API_KEY` 讀取,不寫進程式碼
- 圖片以 base64 傳入,支援 jpg/png/webp;超過 5MB 先自動縮圖(Pillow,長邊上限 2000px)

## 系統結構

```
audit/
├── audit.py            # CLI 主程式
├── prompts.py          # system prompt 與 schema 定義
├── manifest.csv        # 待審素材對照表(使用者提供)
├── assets/             # 待審圖片
└── output/
    ├── results.csv     # 總表
    └── details/        # 每筆完整 JSON 回覆
```

## 輸入:manifest.csv 格式

| 欄位 | 必填 | 說明 |
|---|---|---|
| `filename` | ✓ | assets/ 內的檔名 |
| `campaign_region` | ✓ | 宣傳區域:Europe / North America / Northeast Asia / Southeast Asia / Oceania |
| `campaign_city` | 選填 | 特定航點城市(英文);空白 = 泛區域形象素材 |
| `campaign_airport` | 選填 | IATA 機場代碼(如 ONT、LAX、KMQ)。**這是同名地防呆的關鍵欄位** |
| `copy_text` | 選填 | 圖上或隨附文案全文;空白則只審圖 |

## 審查流程(每筆素材一次 API call)

三段式邏輯,全部寫在 system prompt 裡,一次 call 完成:

1. **開放辨識(不給答案)** — 先問「這是哪裡」,依地標、建築、植被、地形、招牌文字、氣候線索推論。不可先看宣傳標的再找理由,避免 confirmation bias。
2. **對照宣傳標的** — 辨識結果與 manifest 的 region/city/airport 比對。
3. **文案風險檢查** — 若有 copy_text,檢查地名歧義、錯字、過度承諾。

## System Prompt(prompts.py 內容,直接使用)

```
你是航空公司行銷素材審查員。你會收到一張行銷圖片與其宣傳標的資料。請嚴格依以下三步驟審查:

【第一步:開放辨識】
先不看宣傳標的,獨立辨識圖片中的實景拍攝地點。
- 依據:地標建築、建築風格、植被(注意地理限定物種,如 saguaro 仙人掌僅生於 Sonoran 沙漠)、地形、招牌文字語言、車輛方向、氣候特徵、文化元素(如雪吊、鳥居)
- 重要:圖上後製貼的宣傳文字(城市名、標語)不可作為風景辨識依據——設計師拿錯底圖時文字照樣會貼上去。風景歸風景,文字歸文字,分開驗證。
- 無法確認時必須回答 uncertain,不可猜測。generic 素材(無名海灘、雲海、無地標夜景)本來就無法辨識,誠實回報 uncertain 是正確行為。

【第二步:比對宣傳標的】
將辨識結果與宣傳標的比對,給出 verdict:
- match:辨識地點與宣傳城市一致;或素材為泛區域形象圖(無指定城市)且辨識地點屬於該區域
- regional_mismatch:辨識地點與宣傳城市同國但不同城市(例如金澤地標配東京航線)——圖不算錯但體驗會斷裂,需人工判斷
- mismatch:辨識地點與宣傳標的不同國家或不同區域(例如加州 Ontario 航線配加拿大多倫多天際線)
- uncertain:圖片無足夠訊號辨識,需人工複查
特別注意跨國同名地:比對必須以「城市 + 國家 + 機場代碼」為準,不可只比對地名字串。Ontario(加州 ONT)≠ Ontario(加拿大安大略省);Birmingham(英)≠ Birmingham(美);San Jose(美)≠ San Jose(哥斯大黎加)。

【第三步:文案檢查】(若有提供文案)
- 地名歧義:文案中的地名是否存在跨國同名混淆風險,對目標市場(台灣/東南亞旅客)是否可能誤讀
- 基本品質:錯字、語法、CTA 清晰度
- 合規風險:過度承諾用語(guaranteed 等)

【輸出】只回覆 JSON,不加任何前後文字或 markdown 標記:
{
  "identification": {
    "best_guess": "城市, 國家",
    "confidence": "high | medium | low",
    "candidates": [{"location": "...", "reason": "..."}],
    "evidence": ["具體線索,須是可驗證的觀察,不可只寫『風格像某地』"]
  },
  "verdict": "match | regional_mismatch | mismatch | uncertain",
  "verdict_reason": "一句話說明",
  "copy_issues": [{"type": "ambiguity | typo | compliance", "detail": "..."}],
  "needs_human_review": true/false
}

needs_human_review 規則:verdict 為 mismatch / regional_mismatch / uncertain 時必為 true;confidence 為 low 時必為 true;圖片為低畫質截圖(可見播放介面、壓縮痕跡)時必為 true 並在 evidence 註明。
```

## User message 組裝格式

```
宣傳區域:{campaign_region}
宣傳城市:{campaign_city or "(泛區域形象素材,未指定城市)"}
機場代碼:{campaign_airport or "(未提供)"}
文案:{copy_text or "(無文案,僅審圖)"}
```
+ image block(base64)

## 輸出

**results.csv 欄位:** filename, best_guess, confidence, verdict, verdict_reason, copy_issue_count, needs_human_review

**details/{filename}.json:** API 完整回覆,供人工複查時看 evidence。

**終端摘要:** 跑完印出統計(match / regional_mismatch / mismatch / uncertain 各幾筆)+ 列出所有 needs_human_review=true 的檔名。

## CLI

```bash
python audit.py --manifest manifest.csv --assets ./assets --out ./output
python audit.py --single image.jpg --region "North America" --city "Los Angeles" --airport LAX   # 單張快速測試
```

## 實作注意事項(已驗證的設計共識,不要推翻)

1. **開放辨識優先於驗證**:若改成「這是不是 X?」的驗證式提問,多倫多案例會漏抓——因為多倫多確實在 Ontario(省)。先辨識、後比對的順序是刻意的。
2. **uncertain 是合法輸出**:不要為了降低 uncertain 率去 prompt 模型硬給答案,假警報和漏抓比 uncertain 更糟。
3. **JSON 解析要防禦**:strip ```json 圍欄、try/except、解析失敗時把原文存進 details 並在 csv 標記 parse_error,不要讓單筆失敗中斷批次。
4. **API 呼叫**:加入 retry(遇 429/529 退避重試,最多 3 次)、每筆之間可設 delay 參數。
5. **成本**:每張圖約 1,500–2,500 input tokens,Sonnet 跑數百張成本極低,不需要為省錢降級模型。
6. **不要用 regex 抓地名做比對**:所有比對邏輯交給模型在 prompt 內完成,程式只負責組裝輸入與解析輸出。

## 測試案例(開發完成後的驗收基準)

| 素材 | 宣傳標的 | 預期 verdict |
|---|---|---|
| 羅馬競技場 | Europe(泛區域) | match |
| 鳳凰城沙漠天際線(saguaro) | Phoenix / PHX | match |
| 金澤鼓門 | Tokyo / NRT | regional_mismatch |
| 金澤鼓門 | Komatsu / KMQ | match |
| 多倫多 CN Tower | Ontario, CA / ONT | **mismatch**(核心案例) |
| 無地標海灘照 | 任意城市 | uncertain |

多倫多×ONT 這筆是本工具存在的理由,驗收時務必確認能抓到,且 copy_issues 要同時回報「Ontario 對國際旅客有加拿大歧義」。

## 可能的後續工作

① 接進既有的 Streamlit Claude 介面,做成「行銷審查」分頁(拖放上傳 + 即時結果)
② 圖庫 metadata 交叉比對(stock photo caption 與模型辨識雙層驗證)
③ 航點主檔:把公司航網的城市/機場/代表地標做成 JSON 常數,注入 system prompt 提升比對精度

請先建立專案結構與 audit.py 骨架,完成後用 --single 模式測一張圖給我看結果。
