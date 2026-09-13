import { computed } from 'vue'
import { sumWindow, INTERVAL_SECONDS, RESOLUTION_SECONDS } from '../lib/flowCodec'

/**
 * 把 useFlowSnapshot() 解碼出來的 tick 級資料，依目前選的
 * 統計區間(interval：開盤累積 / 最近30秒 / ... / 60分) 與
 * 解析度(resolution：累積走勢圖取樣顆粒) 現場聚合成畫面要的形狀。
 *
 * decodedRef: useFlowSnapshot().snapshot（ref）
 * controlsRef: ref({ interval, resolution, sector })  sector=null 代表在看全市場(族群層)，
 *              有值代表已下鑽到某個族群，改看該族群底下的個股
 */

// ---- 記憶化 ----
// 「返回大盤/下鑽族群/切換日期」在市場層跟族群層之間來回切換時，只要底下的
// tick 資料沒有真的變(同一份 decoded、同一個 d.to 版本)，重複回到同一個
// sector/interval/resolution 組合，答案必然完全一樣——沒必要每次都重新掃過
// 一次全市場/整天的 tick 資料。用一個簡單的 Map 快取：key 含 d.date + d.to
// (資料版本)，資料一有新 tick 進來(d.to 變了)或換了別的日期，舊 key 自然對
// 不上、不會被誤用，不需要額外手動清快取。
//
// 這樣「返回大盤」幾乎會變成瞬間——因為一開始下鑽進某個族群之前，就已經在
// 市場層算過一次了，直接命中快取；「切換日期」也會有感改善，因為換日期後
// 通常也會馬上又切回市場層看總覽，一樣吃得到快取。
//
// 快取存在 useAggregation() 這個 closure 裡，跟著 App.vue 整個生命週期活著；
// 用 Map 的插入順序做簡單的 FIFO 上限，避免長時間掛著、切過很多天的資料後
// 無限長大。
const MAX_CACHE_ENTRIES = 300

function memoize(cache, key, compute) {
  if (cache.has(key)) return cache.get(key)
  const value = compute()
  cache.set(key, value)
  if (cache.size > MAX_CACHE_ENTRIES) {
    cache.delete(cache.keys().next().value)
  }
  return value
}

export function useAggregation(decodedRef, controlsRef) {
  const rowsCache = new Map()
  const cumulativeCache = new Map()
  const ratioCache = new Map()
  const stockPanelsCache = new Map()
  const sectorRankingCache = new Map()
  const stockRankingCache = new Map()

  const nowOffset = computed(() => {
    const d = decodedRef.value
    if (!d) return 0
    let maxT = 0
    for (const sector of d.sectors) {
      for (const stockBuckets of d.net[sector] || []) {
        for (const pts of stockBuckets) {
          if (pts.length) maxT = Math.max(maxT, pts[pts.length - 1][0])
        }
      }
    }
    return maxT
  })

  const windowRange = computed(() => {
    const secs = INTERVAL_SECONDS[controlsRef.value.interval]
    const tTo = nowOffset.value + 1 // sumWindow 是 [from,to) 半開區間，+1 才含最後一筆
    const tFrom = secs == null ? null : Math.max(0, tTo - secs)
    return { tFrom, tTo }
  })

  function sum4(bucketsOfOneStock, tFrom, tTo) {
    return [0, 1, 2, 3].map((b) => sumWindow(bucketsOfOneStock[b], tFrom, tTo))
  }

  function buildRow(name, net4, amt4) {
    const net = net4[0] + net4[1] + net4[2] + net4[3]
    const amount = amt4[0] + amt4[1] + amt4[2] + amt4[3]
    return {
      name,
      net_xl: net4[0],
      net_l: net4[1],
      net_m: net4[2],
      net_s: net4[3],
      net,
      amount,
    }
  }

  // 目前這個統計區間下，族群層(或下鑽後的個股層)各自的淨流入/成交值
  const rows = computed(() => {
    const d = decodedRef.value
    if (!d) return []
    const sectorFilter = controlsRef.value.sector
    const key = `${d.date}|${d.to}|${sectorFilter || ''}|${controlsRef.value.interval}`

    return memoize(rowsCache, key, () => {
      const { tFrom, tTo } = windowRange.value

      if (sectorFilter) {
        const stockList = d.stocks[sectorFilter] || []
        const netList = d.net[sectorFilter] || []
        const amtList = d.amt[sectorFilter] || []
        return stockList
          .map((s, idx) => {
            const net4 = sum4(netList[idx] || [[], [], [], []], tFrom, tTo)
            const amt4 = sum4(amtList[idx] || [[], [], [], []], tFrom, tTo)
            return buildRow(`${s.code} ${s.name}`, net4, amt4)
          })
          .filter((r) => r.amount > 0)
      }

      return d.sectors
        .map((sector) => {
          const netList = d.net[sector] || []
          const amtList = d.amt[sector] || []
          const net4 = [0, 0, 0, 0]
          const amt4 = [0, 0, 0, 0]
          for (let i = 0; i < netList.length; i++) {
            const n = sum4(netList[i], tFrom, tTo)
            const a = sum4(amtList[i], tFrom, tTo)
            for (let b = 0; b < 4; b++) {
              net4[b] += n[b]
              amt4[b] += a[b]
            }
          }
          return buildRow(sector, net4, amt4)
        })
        .filter((r) => r.amount > 0)
    })
  })

  const totals = computed(() => {
    const acc = { amount: 0, net: 0, net_xl: 0, net_l: 0, net_m: 0, net_s: 0 }
    for (const r of rows.value) {
      acc.amount += r.amount
      acc.net += r.net
      acc.net_xl += r.net_xl
      acc.net_l += r.net_l
      acc.net_m += r.net_m
      acc.net_s += r.net_s
    }
    return acc
  })

  // ---- 累積淨流入走勢：不受「統計區間」影響，永遠是開盤到現在；
  //      只有「解析度」決定取樣顆粒。用一次排序 + 單向指標掃描，
  //      避免每個時間點都重新做一次區間加總（那樣在 1 秒解析度下會很慢）。
  function flattenPoints(bucketsList) {
    const all = []
    for (const bucket4 of bucketsList) {
      for (const pts of bucket4) {
        for (const p of pts) all.push(p)
      }
    }
    all.sort((a, b) => a[0] - b[0])
    return all
  }

  function toCumulativeSteps(points, stepSec, steps) {
    const cum = new Array(steps)
    let idx = 0
    let running = 0
    for (let k = 0; k < steps; k++) {
      const upTo = (k + 1) * stepSec
      while (idx < points.length && points[idx][0] < upTo) {
        running += points[idx][1]
        idx++
      }
      cum[k] = running
    }
    return cum
  }

  const cumulativeSeries = computed(() => {
    const d = decodedRef.value
    if (!d) return { times: [], series: {} }
    const sectorFilter = controlsRef.value.sector
    const key = `${d.date}|${d.to}|${sectorFilter || ''}|${controlsRef.value.resolution}`

    return memoize(cumulativeCache, key, () => {
      const stepSec = RESOLUTION_SECONDS[controlsRef.value.resolution] || 60
      const steps = Math.max(1, Math.floor(nowOffset.value / stepSec) + 1)
      const times = Array.from({ length: steps }, (_, k) => (k + 1) * stepSec)

      const series = {}
      if (sectorFilter) {
        const stockList = d.stocks[sectorFilter] || []
        const netList = d.net[sectorFilter] || []
        stockList.forEach((s, idx) => {
          const pts = flattenPoints([netList[idx] || [[], [], [], []]])
          series[`${s.code} ${s.name}`] = toCumulativeSteps(pts, stepSec, steps)
        })
      } else {
        d.sectors.forEach((sector) => {
          const pts = flattenPoints(d.net[sector] || [])
          series[sector] = toCumulativeSteps(pts, stepSec, steps)
        })
      }
      return { times, series }
    })
  })

  // ---- 淨流入占成交值比走勢：跟累積淨流入走勢圖同一份 X 軸(開盤到現在、
  //      同一個解析度)，只是把「累積淨流入」換算成「累積淨流入 ÷ 累積成交值」
  //      的百分比。固定用全單口徑(不受單量級距篩選影響，永遠是 4 桶加總)，
  //      跟族群/個股層的切換方式跟累積淨流入走勢圖一致。
  //      分母(累積成交值)還沒到 RATIO_MIN_AMT 的時間點不算比例(容易因為分母
  //      太小而亂跳)，該點回傳 null，畫圖時會自然斷開，不會硬畫一條假線。
  const RATIO_MIN_AMT = 300_000_000 // 3 億元

  const ratioSeries = computed(() => {
    const d = decodedRef.value
    if (!d) return { times: [], series: {} }
    const sectorFilter = controlsRef.value.sector
    const key = `${d.date}|${d.to}|${sectorFilter || ''}|${controlsRef.value.resolution}`

    return memoize(ratioCache, key, () => {
      const stepSec = RESOLUTION_SECONDS[controlsRef.value.resolution] || 60
      const steps = Math.max(1, Math.floor(nowOffset.value / stepSec) + 1)
      const times = Array.from({ length: steps }, (_, k) => (k + 1) * stepSec)

      function ratioOf(netPts, amtPts) {
        const netCum = toCumulativeSteps(netPts, stepSec, steps)
        const amtCum = toCumulativeSteps(amtPts, stepSec, steps)
        return netCum.map((n, idx) => {
          const a = amtCum[idx]
          return a >= RATIO_MIN_AMT ? (n / a) * 100 : null
        })
      }

      const series = {}
      if (sectorFilter) {
        const stockList = d.stocks[sectorFilter] || []
        const netList = d.net[sectorFilter] || []
        const amtList = d.amt[sectorFilter] || []
        stockList.forEach((s, idx) => {
          const netPts = flattenPoints([netList[idx] || [[], [], [], []]])
          const amtPts = flattenPoints([amtList[idx] || [[], [], [], []]])
          series[`${s.code} ${s.name}`] = ratioOf(netPts, amtPts)
        })
      } else {
        d.sectors.forEach((sector) => {
          const netPts = flattenPoints(d.net[sector] || [])
          const amtPts = flattenPoints(d.amt[sector] || [])
          series[sector] = ratioOf(netPts, amtPts)
        })
      }
      return { times, series }
    })
  })

  // ---- 下鑽到某族群後，右側「個股股價走勢」卡片要用的資料：
  //      每檔股票的股價序列(未分桶) + 現價/漲跌幅 + 目前統計區間下的
  //      大單以上淨流入金額 + 特大單/大單筆數。依大單以上淨流入絕對值排序，
  //      流向最明顯的股票排在最前面。
  const stockPanels = computed(() => {
    const d = decodedRef.value
    const sectorFilter = controlsRef.value.sector
    if (!d || !sectorFilter) return []
    const key = `${d.date}|${d.to}|${sectorFilter}|${controlsRef.value.interval}`

    return memoize(stockPanelsCache, key, () => {
      const { tFrom, tTo } = windowRange.value
      const stockList = d.stocks[sectorFilter] || []
      const netList = d.net[sectorFilter] || []
      const priceList = d.price[sectorFilter] || []

      return stockList
        .map((s, idx) => {
          const priceSeries = priceList[idx] || []
          const prevClose = d.prevClose[s.code] ?? null
          const lastPrice = priceSeries.length ? priceSeries[priceSeries.length - 1][1] : prevClose
          const changePct =
            prevClose != null && prevClose > 0 && lastPrice != null
              ? ((lastPrice - prevClose) / prevClose) * 100
              : null
          const buckets = netList[idx] || [[], [], [], []]
          const net4 = sum4(buckets, tFrom, tTo)
          return {
            code: s.code,
            name: s.name,
            priceSeries,
            lastPrice,
            prevClose,
            changePct,
            netAboveL: net4[0] + net4[1], // 大單以上(特大單+大單)淨流入
            xlCount: buckets[0].length,
            lCount: buckets[1].length,
          }
        })
        .filter((s) => s.priceSeries.length > 0)
        .sort((a, b) => Math.abs(b.netAboveL) - Math.abs(a.netAboveL))
    })
  })

  // ---- 族群/個股淨流入排行表用的資料 ----
  // 跟上面的 rows 不一樣的地方：rows 會依「目前有沒有下鑽到某個族群」切換
  // 顯示族群層或該族群底下的個股層；下面這兩個排行永遠是「全市場」口徑
  // (不受 controls.sector 影響)，因為排行表是獨立於下鑽狀態、隨時都能看
  // 全市場總覽的區塊——即使目前正下鑽在某個族群裡，排行表也還是看得到
  // 全部 44 個族群/171 檔股票的排名，用排行表自己的列點擊來下鑽，跟上面
  // 主圖表的下鑽狀態互不影響對方的計算基礎。

  // 全市場「族群」排行：跟 rows 的「else 分支」邏輯一樣，只是不受 sectorFilter
  // 影響、永遠算全部族群。
  const sectorRanking = computed(() => {
    const d = decodedRef.value
    if (!d) return []
    const key = `${d.date}|${d.to}|${controlsRef.value.interval}`

    return memoize(sectorRankingCache, key, () => {
      const { tFrom, tTo } = windowRange.value
      return d.sectors
        .map((sector) => {
          const netList = d.net[sector] || []
          const amtList = d.amt[sector] || []
          const net4 = [0, 0, 0, 0]
          const amt4 = [0, 0, 0, 0]
          for (let i = 0; i < netList.length; i++) {
            const n = sum4(netList[i], tFrom, tTo)
            const a = sum4(amtList[i], tFrom, tTo)
            for (let b = 0; b < 4; b++) {
              net4[b] += n[b]
              amt4[b] += a[b]
            }
          }
          const row = buildRow(sector, net4, amt4)
          row.sector = sector
          row.netPct = row.amount > 0 ? (row.net / row.amount) * 100 : 0
          return row
        })
        .filter((r) => r.amount > 0)
    })
  })

  // 全市場「個股」排行：把每個族群底下的股票攤平成同一個陣列，不分族群
  // 混在一起排名(跟 rows 下鑽某族群時「只看那個族群底下個股」不一樣)。
  const stockRanking = computed(() => {
    const d = decodedRef.value
    if (!d) return []
    const key = `${d.date}|${d.to}|${controlsRef.value.interval}`

    return memoize(stockRankingCache, key, () => {
      const { tFrom, tTo } = windowRange.value
      const out = []
      for (const sector of d.sectors) {
        const stockList = d.stocks[sector] || []
        const netList = d.net[sector] || []
        const amtList = d.amt[sector] || []
        stockList.forEach((s, idx) => {
          const net4 = sum4(netList[idx] || [[], [], [], []], tFrom, tTo)
          const amt4 = sum4(amtList[idx] || [[], [], [], []], tFrom, tTo)
          const row = buildRow(`${s.code} ${s.name}`, net4, amt4)
          row.code = s.code
          row.stockName = s.name
          row.sector = sector
          row.netPct = row.amount > 0 ? (row.net / row.amount) * 100 : 0
          out.push(row)
        })
      }
      return out.filter((r) => r.amount > 0)
    })
  })

  return {
    nowOffset,
    windowRange,
    rows,
    totals,
    cumulativeSeries,
    ratioSeries,
    stockPanels,
    sectorRanking,
    stockRanking,
  }
}
