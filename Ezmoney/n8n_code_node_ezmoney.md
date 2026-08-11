# n8n Code Node：ezmoney ETF 每日自動下載

## 流程

```
Schedule Trigger (每日 08:00)
    ↓
Code Node（JavaScript，一次跑完三步驟）
    ↓
（Optional）後續處理：存 Google Drive、寄 Email 等
```

---

## Schedule Trigger

| 設定 | 值 |
|---|---|
| Rule Type | Cron |
| Cron Expression | `0 8 * * *` |

---

## Code Node 設定

| 設定 | 值 |
|---|---|
| Mode | `Run Once for All Items` |

### 程式碼

```javascript
// ====== 設定區 ======
const FUND_CODES = ['49YTW', '63YTW']; // 要下載的 ETF 代碼，可自行增減
const BASE_URL = 'https://www.ezmoney.com.tw';
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36';

// ====== 工具函數 ======

// 從 response headers 收集 Set-Cookie
function collectCookies(headers, cookieJar) {
  const setCookies = headers['set-cookie'] || [];
  for (const c of (Array.isArray(setCookies) ? setCookies : [setCookies])) {
    const [nameValue] = c.split(';');
    const eqIdx = nameValue.indexOf('=');
    if (eqIdx > 0) {
      const name = nameValue.substring(0, eqIdx).trim();
      const value = nameValue.substring(eqIdx + 1).trim();
      cookieJar[name] = value;
    }
  }
}

// 組合 Cookie 字串
function getCookieString(cookieJar) {
  return Object.entries(cookieJar).map(([k, v]) => `${k}=${v}`).join('; ');
}

// 通用 HTTPS GET 請求
async function httpsGet(url, extraHeaders = {}) {
  const options = {
    method: 'GET',
    uri: url,
    headers: {
      'User-Agent': USER_AGENT,
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
      ...extraHeaders,
    },
    rejectUnauthorized: false, // 公司 OA Proxy 需要
    resolveWithFullResponse: true,
    maxRedirects: 5,
  };
  return await this.helpers.httpRequest(options);
}

// 通用 HTTPS GET 下載（binary）
async function httpsDownload(url, extraHeaders = {}) {
  const options = {
    method: 'GET',
    uri: url,
    headers: {
      'User-Agent': USER_AGENT,
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
      ...extraHeaders,
    },
    rejectUnauthorized: false,
    resolveWithFullResponse: true,
    encoding: null, // 拿 binary
    maxRedirects: 5,
  };
  return await this.helpers.httpRequest(options);
}

// ====== 主流程 ======

const results = [];

for (const fundCode of FUND_CODES) {
  const cookieJar = {};

  // Step 1：造訪首頁，建立 session
  const resp1 = await httpsGet.call(this, `${BASE_URL}/`);
  collectCookies(resp1.headers, cookieJar);

  // Step 2：造訪投資組合頁面，拿 Token
  const resp2 = await httpsGet.call(this,
    `${BASE_URL}/ETF/Fund/Info?fundCode=${fundCode}&tabName=asset`,
    { 'Cookie': getCookieString(cookieJar), 'Referer': `${BASE_URL}/` }
  );
  collectCookies(resp2.headers, cookieJar);

  // 提取 __RequestVerificationToken
  const html = typeof resp2.body === 'string' ? resp2.body : resp2.body.toString('utf-8');
  let requestToken = '';
  const tokenMatch = html.match(/name="__RequestVerificationToken"\s+value="([^"]+)"/);
  if (tokenMatch) {
    requestToken = tokenMatch[1];
  }
  if (!requestToken && cookieJar['__RequestVerificationToken']) {
    requestToken = cookieJar['__RequestVerificationToken'];
  }

  // Step 3：帶 Cookie 下載檔案
  const resp3 = await httpsDownload.call(this,
    `${BASE_URL}/ETF/Fund/AssetExcelNPOI?fundCode=${fundCode}`,
    {
      'Cookie': getCookieString(cookieJar),
      'Referer': `${BASE_URL}/ETF/Fund/Info?fundCode=${fundCode}&tabName=asset`,
    }
  );

  // 處理檔名
  const contentDisp = resp3.headers['content-disposition'] || '';
  let filename = '';
  const fnameMatch = contentDisp.match(/filename[^;=\n]*=([^\n]*)/);
  if (fnameMatch) {
    filename = fnameMatch[1].trim().replace(/"/g, '');
  }
  if (!filename) {
    const today = new Date().toISOString().slice(0, 10);
    filename = `ezmoney_${fundCode}_${today}.xlsx`;
  }

  // 加上 fundCode 前綴避免不同 ETF 檔名衝突
  filename = `${fundCode}_${filename}`;

  // 取得 binary data
  const content = resp3.body;
  const contentType = resp3.headers['content-type'] || 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

  results.push({
    json: {
      fundCode: fundCode,
      filename: filename,
      contentType: contentType,
      fileSize: content.length,
      requestToken: requestToken ? 'found' : 'not found',
    },
    binary: {
      data: await this.helpers.prepareBinaryData(
        Buffer.isBuffer(content) ? content : Buffer.from(content),
        filename,
        contentType,
      ),
    },
  });
}

return results;
```

---

## 回傳結果

每個 fundCode 會產生一筆結果，包含：

| 欄位 | 說明 |
|---|---|
| `json.fundCode` | ETF 代碼 |
| `json.filename` | 存檔檔名 |
| `json.contentType` | 檔案類型 |
| `json.fileSize` | 檔案大小（bytes） |
| `binary.data` | 檔案二進位內容 |

---

## 如何增減 ETF

修改最上面的 `FUND_CODES` 陣列：

```javascript
// 只下載一支
const FUND_CODES = ['49YTW'];

// 下載多支
const FUND_CODES = ['49YTW', '63YTW', '0050', '0056'];

// 下載全部（自行加入代碼）
const FUND_CODES = ['49YTW', '63YTW', '78YTW', '82YTW'];
```

---

## 後續處理（Optional）

### 存到 Google Drive

在 Code Node 後面接 **Google Drive** 節點：

| 設定 | 值 |
|---|---|
| Operation | Upload |
| File Name | `{{ $json.filename }}` |
| Binary Property | `data` |
| Folder ID | 目標資料夾 ID |

### 寄 Email 通知

接 **Send Email** 節點：

| 設定 | 值 |
|---|---|
| To | your@email.com |
| Subject | `ezmoney 每日下載完成 - {{ $json.fundCode }}` |
| Attachments | `data` |

### 存到本機

接 **Write File** 節點：

| 設定 | 值 |
|---|---|
| File Name | `/path/to/save/{{ $json.filename }}` |
| Binary Property | `data` |

---

## 注意事項

1. **n8n 內建 `this.helpers.httpRequest`**，不需要安裝 axios
2. **`rejectUnauthorized: false`** 是因為公司 OA Proxy 的 SSL 憑證問題
3. **每個 fundCode 獨立跑完整三步驟**（首頁 → 頁面 → 下載），確保 Cookie 獨立不互干擾
4. 如果某支 ETF 下載失敗，不會影響其他 ETF（迴圈繼續跑）
5. 如果需要登入才能下載，在 Step 1 前加一個 POST 登入請求

---

## 常見問題

| 問題 | 解法 |
|---|---|
| `this.helpers.httpRequest is not a function` | n8n 版本太舊，改用 `this.helpers.request` |
| 回傳 302 redirect | 加 `maxRedirects: 5` 或確認 Cookie 有正確帶上 |
| 回傳登入頁面 | 網站需要登入，需加登入步驟 |
| `MODULE_NOT_FOUND` | 確認用 `this.helpers.httpRequest` 而非 `require('axios')` |
