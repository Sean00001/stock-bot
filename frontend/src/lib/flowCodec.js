// 對應 worker/flow_codec.py 的解碼邏輯（差分編碼 -> 還原成 [[t, v], ...]）。
// 格式細節請見該檔案開頭的說明；這裡只做「讀」，不做「寫」。

/**
 * 把 {"i":[...], "v":[...]} 差分編碼還原成 [[t_offset_sec, value], ...]
 * i[0] 是絕對秒數 offset，i[k>0] 是跟前一筆的差。
 */
export function decodeBucket(bucket) {
  const i = (bucket && bucket.i) || []
  const v = (bucket && bucket.v) || []
  const out = new Array(i.length)
  let running = 0
  for (let k = 0; k < i.length; k++) {
    running = k === 0 ? i[k] : running + i[k]
    out[k] = [running, v[k]]
  }
  return out
}

/**
 * 對一個已解碼的點陣列 [[t,v],...]（t 遞增），加總落在 [tFrom, tTo) 內的 v。
 * tFrom=null 代表不設下限，tTo=null 代表不設上限。points 必須已按 t 排序。
 */
export function sumWindow(points, tFrom, tTo) {
  if (!points || points.length === 0) return 0
  let lo = 0
  let hi = points.length
  if (tFrom != null) {
    lo = lowerBound(points, tFrom)
  }
  if (tTo != null) {
    hi = lowerBound(points, tTo)
  }
  let sum = 0
  for (let k = lo; k < hi; k++) sum += points[k][1]
  return sum
}

function lowerBound(points, t) {
  let lo = 0
  let hi = points.length
  while (lo < hi) {
    const mid = (lo + hi) >>> 1
    if (points[mid][0] < t) lo = mid + 1
    else hi = mid
  }
  return lo
}

/**
 * 找出整份快照裡最新的 tick 秒數 offset（across 所有 sector/stock/bucket）。
 * 拿來當「現在」的游標，因為 mock/worker 都不見得每個 stock 都有 tick 到最新一秒。
 */
export function findLatestOffset(decodedNet) {
  let maxT = 0
  for (const sector in decodedNet) {
    for (const stock of decodedNet[sector]) {
      for (const bucket of stock) {
        if (bucket.length) {
          const last = bucket[bucket.length - 1][0]
          if (last > maxT) maxT = last
        }
      }
    }
  }
  return maxT
}

/**
 * 把後端回傳的完整快照（net/amt 為 {i,v} 形式）整份解碼成
 * {sector: [ [ [[t,v],...] x4桶 ] 每檔股票 ]} 的結構，之後聚合用 sumWindow 現場算。
 * price 是每檔股票「單一」一條序列（不分桶，每筆 tick 都記），解碼後另外除以
 * price_scale 還原成元 —— 給下鑽到某族群後、右側個股股價走勢小圖用。
 */
export function decodeSnapshot(raw) {
  const decode4 = (buckets) => buckets.map(decodeBucket)
  const decodeGroup = (group) => {
    const out = {}
    for (const sector in group) {
      out[sector] = group[sector].map(decode4)
    }
    return out
  }
  const priceScale = raw.price_scale || 100
  const decodePriceGroup = (group) => {
    const out = {}
    for (const sector in group) {
      out[sector] = group[sector].map((bucket) => {
        const pts = decodeBucket(bucket)
        return pts.map(([t, v]) => [t, v / priceScale])
      })
    }
    return out
  }
  return {
    date: raw.date,
    asof: raw.asof,
    to: raw.to,
    final: !!raw.final,
    marketOpenTs: raw.market_open_ts,
    sectors: raw.sectors || [],
    stocks: raw.stocks || {},
    lastPrice: raw.last_price || {},
    prevClose: raw.prev_close || {},
    net: decodeGroup(raw.net || {}),
    amt: decodeGroup(raw.amt || {}),
    price: decodePriceGroup(raw.price || {}),
  }
}

/**
 * 依目前已解碼的 snapshot，算出每檔股票的「游標」：4 個 net 桶各自目前有幾筆
 * + price 序列目前有幾筆。格式跟 worker 寫 .t.json 時附的 "from" 欄位一致
 * ([xl_len, l_len, m_len, s_len, price_len])，輪詢 .t.json 時要拿這份跟
 * "from" 核對，兩邊對得起來才能直接把增量疊加上去，對不起來就要整包重抓
 * .b.json——不然增量會接錯位置，資料就亂掉了。
 */
export function buildCursor(decoded) {
  const cursor = {}
  for (const sector in decoded.stocks) {
    const stockList = decoded.stocks[sector] || []
    const netList = decoded.net[sector] || []
    const priceList = decoded.price[sector] || []
    stockList.forEach((s, i) => {
      const buckets = netList[i] || [[], [], [], []]
      const priceArr = priceList[i] || []
      cursor[s.code] = [buckets[0].length, buckets[1].length, buckets[2].length, buckets[3].length, priceArr.length]
    })
  }
  return cursor
}

function cursorEqual(a, b) {
  if (!a || !b) return false
  for (let k = 0; k < 5; k++) {
    if ((a[k] || 0) !== (b[k] || 0)) return false
  }
  return true
}

/**
 * 把 .t.json 的增量原始資料，疊加進 decodeSnapshot() 已經解碼好的結構——用
 * push() 原地變動陣列，不整包换掉 decoded 物件，Vue 深層響應式才抓得到「哪些
 * 陣列真的變長了」，只有真的受影響的圖表/計算會重新跑，不是每次輪詢都整個
 * 重算一次。
 *
 * cursor 是呼叫端目前記著的每檔股票游標(buildCursor() 算出來的那份物件，會
 * 在這裡就地更新)；raw 是 .t.json 解析出來的原始 JSON(還沒解碼)。
 *
 * 回傳 true 表示整批套用成功；false 表示套不上去(可能是某個族群的股票數量
 * 跟本地記的不一樣——盤中新股票開始有成交會讓後面股票的陣列位置整個偏移，
 * 或是某檔股票的 cursor 跟這批增量附的 "from" 對不起來——可能漏接過某次快照、
 * 或 worker 中途重啟過)，這種情況完全不會動 decoded，呼叫端要改成整包重抓
 * .b.json 才安全。
 */
export function applyIncremental(decoded, cursor, raw) {
  const priceScale = raw.price_scale || 100
  const incrFrom = raw.from || {}
  const netIn = raw.net || {}
  const amtIn = raw.amt || {}
  const priceIn = raw.price || {}

  // 先整批核對過一輪，任何一項對不起來就整批放棄——不要套到一半才發現不
  // 一致，留下部分套用、部分沒套的髒狀態。
  for (const sector in netIn) {
    const stockList = decoded.stocks[sector] || []
    const netRow = netIn[sector] || []
    if (netRow.length !== stockList.length) return false
    for (let i = 0; i < stockList.length; i++) {
      const expected = incrFrom[stockList[i].code] || [0, 0, 0, 0, 0]
      if (!cursorEqual(cursor[stockList[i].code], expected)) return false
    }
  }

  // 核對通過，才真的把新資料 push 進已解碼的陣列。
  for (const sector in netIn) {
    const stockList = decoded.stocks[sector] || []
    const netRow = netIn[sector] || []
    const amtRow = amtIn[sector] || []
    const priceRow = priceIn[sector] || []
    for (let i = 0; i < stockList.length; i++) {
      const code = stockList[i].code
      for (let b = 0; b < 4; b++) {
        const newNet = decodeBucket((netRow[i] || [])[b])
        if (newNet.length) decoded.net[sector][i][b].push(...newNet)
        const newAmt = decodeBucket((amtRow[i] || [])[b])
        if (newAmt.length) decoded.amt[sector][i][b].push(...newAmt)
      }
      const newPrice = decodeBucket(priceRow[i]).map(([t, v]) => [t, v / priceScale])
      if (newPrice.length) decoded.price[sector][i].push(...newPrice)

      cursor[code] = [
        decoded.net[sector][i][0].length,
        decoded.net[sector][i][1].length,
        decoded.net[sector][i][2].length,
        decoded.net[sector][i][3].length,
        decoded.price[sector][i].length,
      ]
    }
  }

  decoded.to = raw.to
  decoded.asof = raw.asof
  return true
}

export const BUCKET_LABELS = ['特大單', '大單', '中單', '小單']
export const BUCKET_KEYS = ['xl', 'l', 'm', 's']

// 統計區間 -> 秒數（null 代表「開盤累積」，不設下限）
export const INTERVAL_SECONDS = {
  open: null,
  '30s': 30,
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '30m': 1800,
  '60m': 3600,
}

// 解析度 -> 秒數，用來決定累積走勢圖取樣的顆粒度
export const RESOLUTION_SECONDS = {
  '1s': 1,
  '5s': 5,
  '15s': 15,
  '1m': 60,
  '5m': 300,
}
