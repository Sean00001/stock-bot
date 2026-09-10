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
