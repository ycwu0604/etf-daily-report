# n8n Workflow：ezmoney 每日自動下載投資組合檔案

## 流程概覽

```
Schedule Trigger (每日 08:00)
    ↓
HTTP Request #1 ─── GET 投資組合頁面，拿 Cookie + HTML
    ↓
Code Node ───────── 提取 Cookie + __RequestVerificationToken
    ↓
HTTP Request #2 ─── 帶 Cookie + Token 下載檔案
    ↓
（Optional）Write Node / Google Drive / Email
```

---

## 節點 1：Schedule Trigger

| 設定 | 值 |
|---|---|
| Rule Type | Cron |
| Cron Expression | `0 8 * * *` |

> 每天早上 8 點執行，時間可自行調整

---

## 節點 2：HTTP Request — 造訪頁面拿 Cookie + Token

### 基本設定

| 設定 | 值 |
|---|---|
| Method | `GET` |
| URL | `https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=49YTW&tabName=asset` |

### Headers

| Name | Value |
|---|---|
| `User-Agent` | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36` |

### Options

| 設定 | 值 |
|---|---|
| Include Response Headers | ✅ true |
| Include Response Status | ✅ true |

> 注意：一定要帶 User-Agent，否則網站可能拒絕請求

---

## 節點 3：Code Node — 提取 Cookie + Token

### 基本設定

| 設定 | 值 |
|---|---|
| Mode | `Run Once for All Items` |

### 程式碼

```javascript
// ====== 提取 Response Headers 裡的 Cookie ======
const headers = $input.first().json.headers;

let setCookies = headers['set-cookie'] || headers['Set-Cookie'] || [];
if (typeof setCookies === 'string') setCookies = [setCookies];

const cookieMap = {};
for (const c of setCookies) {
  const [nameValue] = c.split(';');
  const [name, ...rest] = nameValue.split('=');
  cookieMap[name.trim()] = rest.join('=').trim();
}

// ====== 從 HTML 提取 __RequestVerificationToken ======
const html = $input.first().json.data || $input.first().json.body;

// ASP.NET 常見寫法：<input name="__RequestVerificationToken" value="xxx">
let requestToken = '';
const tokenMatch = html.match(/name="__RequestVerificationToken"\s+value="([^"]+)"/);
if (tokenMatch) {
  requestToken = tokenMatch[1];
}

// 如果上面找不到，試另一種寫法
if (!requestToken) {
  const tokenMatch2 = html.match(/__RequestVerificationToken[^=]*=\s*([^;&\s"]+)/);
  if (tokenMatch2) requestToken = tokenMatch2[1];
}

// ====== 組合 Cookie 字串 ======
// 只保留必要的 Cookie（_ga, _gcl_au 等 Google 追蹤 Cookie 可忽略）
const neededCookies = [];

if (cookieMap['ASP.NET_SessionId']) {
  neededCookies.push(`ASP.NET_SessionId=${cookieMap['ASP.NET_SessionId']}`);
}
if (cookieMap['__RequestVerificationToken']) {
  neededCookies.push(`__RequestVerificationToken=${cookieMap['__RequestVerificationToken']}`);
}
if (cookieMap['_nxqsession_asp.net_sessionid']) {
  neededCookies.push(`_nxqsession_asp.net_sessionid=${cookieMap['_nxqsession_asp.net_sessionid']}`);
}
if (cookieMap['__nxquid']) {
  neededCookies.push(`__nxquid=${cookieMap['__nxquid']}`);
}

const cookieString = neededCookies.join('; ');

// ====== 下載 URL ======
const downloadUrl = 'https://www.ezmoney.com.tw/ETF/Fund/AssetExcelNPOI?fundCode=49YTW';

return [{
  json: {
    cookie: cookieString,
    requestToken: requestToken,
    downloadUrl: downloadUrl
  }
}];
```

---

## 節點 4：HTTP Request — 帶 Cookie + Token 下載檔案

### 基本設定

| 設定 | 值 |
|---|---|
| Method | `GET` |
| URL | `{{ $json.downloadUrl }}` |

### Headers

| Name | Value |
|---|---|
| `Cookie` | `{{ $json.cookie }}` |
| `Referer` | `https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=49YTW&tabName=asset` |
| `User-Agent` | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36` |

### Options

| 設定 | 值 |
|---|---|
| Response Format | **File** ← 關鍵！選 File 才會拿到二進位檔案 |

> 注意：Response Format 必須選 File，否則會拿到文字而非檔案

---

## 節點 5（Optional）：Write File — 存檔到本機

| 設定 | 值 |
|---|---|
| File Name | `/path/to/save/ezmoney_49YTW_{{ $now.format('yyyy-MM-dd') }}.xlsx` |
| Data | `data` |

> 路徑請依實際需求修改

---

## 重要 Cookie 說明

| Cookie 名稱 | 用途 | 來源 |
|---|---|---|
| `ASP.NET_SessionId` | ASP.NET Session 識別 | 造訪頁面時 Server 自動發 |
| `__RequestVerificationToken` | 防偽造驗證 Token ⚠️ 重要 | 同時出現在 Cookie 和 HTML `<input>` 裡 |
| `_nxqsession_asp.net_sessionid` | 網站自訂 Session | 造訪頁面時取得 |
| `__nxquid` | 使用者識別 | 造訪頁面時取得 |

### 可忽略的 Cookie（Google 追蹤用）

| Cookie 名稱 | 用途 |
|---|---|
| `_ga` | Google Analytics |
| `_gcl_au` | Google 廣告追蹤 |
| `_ga_3MMYCX29JS` | Google Analytics |

---

## 常見問題排解

| 問題 | 可能原因 | 解法 |
|---|---|---|
| 下載回來是空白或 302 轉址 | Cookie 或 Token 缺漏 | 檢查 Code Node 輸出，確認 cookie 和 requestToken 有值 |
| 回傳登入頁面 | 網站需要登入 | 需加登入步驟（HTTP Request POST 登入 API） |
| `__RequestVerificationToken` 從 HTML 抓不到 | Token 寫法不同 | 把 HTTP Request #1 回傳的 HTML 貼出來分析 |
| 回傳 403 Forbidden | 缺少 Referer 或 User-Agent | 確認 Headers 都有帶 |
| 回傳 500 Server Error | ASP.NET 驗證 Token 不符 | 確認 Cookie 裡的 Token 和 HTML 裡的 Token 一致 |

---

## 原始 cURL 參考

下載請求的原始 cURL（從 Chrome DevTools 複製）：

```bash
curl 'https://www.ezmoney.com.tw/ETF/Fund/AssetExcelNPOI?fundCode=49YTW' \
  -H 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7' \
  -H 'Accept-Language: zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7' \
  -H 'Connection: keep-alive' \
  -b 'ASP.NET_SessionId=lknxyhyi1fdnombkkoyhon1p; _nxqsession_asp.net_sessionid=ve+NBeBD6JE/nbhKVux9PHTsEg81PUhi; _ga=GA1.1.2103714999.1783582547; _gcl_au=1.1.2098192128.1783582547; __RequestVerificationToken=vF2k19fyW7Tyx8KmLNXVQ_7LDNLDlVTRuRd2JAPWLiWxnN-_Ar7OtXSM-gPWz5a2fBo5nWKTsLAU7CeN_Kk8Dqcj0WnzGN8cFCtRjj-tsBs1; __nxquid=JJpttuYzkTdlc+Z2F3iwvOJ9QhBctw==0018; _ga_3MMYCX29JS=GS2.1.s1783582546$o1$g1$t1783583225$j60$l0$h2059750447' \
  -H 'Referer: https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=49YTW&tabName=asset' \
  -H 'Sec-Fetch-Dest: document' \
  -H 'Sec-Fetch-Mode: navigate' \
  -H 'Sec-Fetch-Site: same-origin' \
  -H 'Sec-Fetch-User: ?1' \
  -H 'Upgrade-Insecure-Requests: 1' \
  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36' \
  -H 'sec-ch-ua: "Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Windows"'
```
