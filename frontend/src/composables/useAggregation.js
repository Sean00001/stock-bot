import { computed } from 'vue'
import { sumWindow, INTERVAL_SECONDS, RESOLUTION_SECONDS } from '../lib/flowCodec'

/**
 * 把 useFlowSnapshot() 解碼出來的 tick 級資料，依目前選的
 * 統計區間(interval：開盤累積 / 最近30秒 / ... / 60分) 與
 * 解析度(resolution：累積走勢圖取樣顆粒) 現場聚合成畫面要的形狀。
 *
 * decodedRef: useFlowSnapshot().snapshot（ref）
 * controlsRef: ref({ interval, resolution, sector, playhead })
 *              sector=null 代表在看全市場(族群層)，有值代表已下鑽到某個族群，改看該族群底下的個股
 *              playhead=null 代表跟著「現在」走(直播模式，跟改版前行為一致)；
 *              有數字代表使用者正在用播放進度條回放，把整份聚合的「現在」凍結在這個
 *              開盤後秒數，讓下面所有計算都當作「現在只看得到這個時間點以前的 tick」。
 */

// ---- 記憶化 ----
// 「返回大盤/下鑽族群/切換日期/拖動播放進度條」在市場層跟族群層之間來回切換、
// 或反覆回到同一個播放時間點時，只要底下的 tick 資料版本(見 nowOffset 說明)
// 沒有真的變，重複回到同一個 sector/interval/resolution/nowOffset 組合，答案
// 必然完全一樣——沒必要每次都重新掃過一次全市場/整天的 tick 資料。用一個簡單
// 的 Map 快取：key 含 d.date + nowOffset(見下方)，資料一有新 tick 進來或換了
// 別的日期、或播放進度條移動到新的時間點，舊 key 自然對不上、不會被誤用，不
// 需要額外手動清快取。
//
// 快取存在 useAggregation() 這個 closure 裡，跟著 App.vue 整個生命週期活著；
// 用 Map 的插入順序做簡單的 FIFO 上限，避免長時間掛著、切過很多天/拖過很多
// 時間點的資料後無限長大。
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
  const offMarketCache = new Map()

  // 全市場擴到 5～10 條連線、近千到近兩千檔股票之後，flattenPoints() 的
  // 排序(把某個範圍內所有股票 x 4 桶的 tick 攤平、依時間排序合併成一條)變成
  // 明顯的效能瓶頸：如果每次「現在」(nowOffset)一變——也就是每拖一下播放
  // 進度條、播放中的每一幀——就重新攤平+排序一次全市場的 tick，使用者會
  // 感覺拖曳/播放很頓、每動一次都要等好一陣子。
  //
  // 但其實「攤平+排序」這件事本身完全不受 nowOffset 影響——同一份資料(同
  // 一個 d.to)不管現在把「現在」定在哪個時間點，排序結果都一樣，會變的只有
  // 後面 toCumulativeSteps() 要累加到第幾步。所以把「攤平+排序」單獨快取，
  // key 只含資料版本(d.date + d.to)+ 範圍(族群/個股/桶別)，不含 nowOffset，
  // 這樣同一份資料只要排序一次，之後拖幾百次進度條、播放幾百幀，都只需要
  // 重跑後面那段線性掃描(toCumulativeSteps)，不用再重新排序——這是真正拖慢
  // 的地方，這樣快取之後應該會快一個數量級以上。
  const flattenCache = new Map()

  function getFlattened(versionKey, scopeKey, bucketsList) {
    return memoize(flattenCache, `${versionKey}|${scopeKey}`, () => flattenPoints(bucketsList))
  }

  // offMarketFlowSeries 專用：要攤平的不是「某個範圍(族群/個股)的全部 4 桶」，
  // 而是「全市場所有族群、所有股票，但只挑其中一個桶別 b」，跟 getFlattened()
  // 的資料形狀不一樣，所以另外寫一個攤平函式，快取邏輯(不含 nowOffset，只
  // 含資料版本)是一樣的。
  function getFlattenedTier(versionKey, b, d) {
    return memoize(flattenCache, `${versionKey}|tier${b}`, () => {
      const all = []
      for (const sector of d.sectors) {
        for (const stockBuckets of d.net[sector] || []) {
          const bucketPts = stockBuckets[b]
          if (bucketPts) for (const p of bucketPts) all.push(p)
        }
      }
      all.sort((a, c) => a[0] - c[0])
      return all
    })
  }

  // 整份快照裡實際存在的最新 tick 秒數 offset——不受播放進度條影響，永遠是
  // 「這份資料目前真正跑到哪裡」，直播中的日期會隨著新 tick 進來持續變大，
  // 已收盤 finalize 的日期則固定在當天最後一筆成交。播放進度條的可拖動範圍
  // 就是 [0, liveMaxOffset]，也是「回到即時」要跳回的位置。
  const liveMaxOffset = computed(() => {
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

  // 下面所有聚合計算實際使用的「現在」：
  //   - controls.playhead 是 null(沒在播放/沒拖過進度條) -> 跟 liveMaxOffset 一樣，
  //     就是改版前的直播行為，直播中的日期會自動跟著新 tick 往前走。
  //   - controls.playhead 是數字 -> 使用者正在回放，把「現在」凍結在這個時間點
  //     (夾在 [0, liveMaxOffset] 內，避免拖到還沒有資料的未來)。
  const nowOffset = computed(() => {
    const ph = controlsRef.value.playhead
    if (ph == null) return liveMaxOffset.value
    return Math.min(Math.max(0, ph), liveMaxOffset.value)
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

  // 目前這個統計區間下，族群層(或下鑽後的個股層)各自的淨流入/成交值。
  // 這裡是用 sumWindow()(對已排序好的單一桶陣列做二分搜尋)而不是攤平+排序，
  // 本來就很快，全市場規模下也不太需要另外快取排序結果。
  const rows = computed(() => {
    const d = decodedRef.value
    if (!d) return []
    const sectorFilter = controlsRef.value.sector
    const key = `${d.date}|${nowOffset.value}|${sectorFilter || ''}|${controlsRef.value.interval}`

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

  // ---- 累積淨流入走勢：不受「統計區間」影響，永遠是開盤到「現在」(直播模式
  //      是真的現在，回放模式是播放進度條停在的那個時間點)；只有「解析度」
  //      決定取樣顆粒。用一次排序 + 單向指標掃描，避免每個時間點都重新做一次
  //      區間加總（那樣在 1 秒解析度下會很慢）。
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
    const key = `${d.date}|${nowOffset.value}|${sectorFilter || ''}|${controlsRef.value.resolution}`
    const versionKey = `${d.date}|${d.to}`

    return memoize(cumulativeCache, key, () => {
      const stepSec = RESOLUTION_SECONDS[controlsRef.value.resolution] || 60
      const steps = Math.max(1, Math.floor(nowOffset.value / stepSec) + 1)
      const times = Array.from({ length: steps }, (_, k) => (k + 1) * stepSec)

      const series = {}
      if (sectorFilter) {
        const stockList = d.stocks[sectorFilter] || []
        const netList = d.net[sectorFilter] || []
        stockList.forEach((s, idx) => {
          const pts = getFlattened(versionKey, `net|${sectorFilter}|${s.code}`, [netList[idx] || [[], [], [], []]])
          series[`${s.code} ${s.name}`] = toCumulativeSteps(pts, stepSec, steps)
        })
      } else {
        d.sectors.forEach((sector) => {
          const pts = getFlattened(versionKey, `net|${sector}`, d.net[sector] || [])
          series[sector] = toCumulativeSteps(pts, stepSec, steps)
        })
      }
      return { times, series }
    })
  })

  // ---- 淨流入占成交值比走勢：跟累積淨流入走勢圖同一份 X 軸(開盤到「現在」、
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
    const key = `${d.date}|${nowOffset.value}|${sectorFilter || ''}|${controlsRef.value.resolution}`
    const versionKey = `${d.date}|${d.to}`

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
          const netPts = getFlattened(versionKey, `net|${sectorFilter}|${s.code}`, [netList[idx] || [[], [], [], []]])
          const amtPts = getFlattened(versionKey, `amt|${sectorFilter}|${s.code}`, [amtList[idx] || [[], [], [], []]])
          series[`${s.code} ${s.name}`] = ratioOf(netPts, amtPts)
        })
      } else {
        d.sectors.forEach((sector) => {
          const netPts = getFlattened(versionKey, `net|${sector}`, d.net[sector] || [])
          const amtPts = getFlattened(versionKey, `amt|${sector}`, d.amt[sector] || [])
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
    const key = `${d.date}|${nowOffset.value}|${sectorFilter}|${controlsRef.value.interval}`

    return memoize(stockPanelsCache, key, () => {
      const { tFrom, tTo } = windowRange.value
      const stockList = d.stocks[sectorFilter] || []
      const netList = d.net[sectorFilter] || []
      const priceList = d.price[sectorFilter] || []

      return stockList
        .map((s, idx) => {
          const fullPriceSeries = priceList[idx] || []
          // 回放模式下股價走勢小圖也要凍結在播放時間點，不能露出「未來」的股
          // 價，不然使用者一邊拖進度條、一邊卻在卡片上看到比現在更後面的價位。
          const priceSeries =
            controlsRef.value.playhead == null
              ? fullPriceSeries
              : fullPriceSeries.filter((p) => p[0] <= nowOffset.value)
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
    const key = `${d.date}|${nowOffset.value}|${controlsRef.value.interval}`

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
    const key = `${d.date}|${nowOffset.value}|${controlsRef.value.interval}`

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

  // ---- 場外資金進出：跟 SankeyChart 裡「場外新資金/離場轉現金」節點同一套
  //      口徑(見 SankeyChart.vue 的 tierNetTotal)。某個「單量級距」(特大單/
  //      大單/中單/小單)在某個時間點，全市場(不分族群、不受下鑽狀態影響，
  //      永遠全市場口徑)的淨買賣是正是負：正的部分視為「場外新資金」流進
  //      這個級距；負的部分視為「離場/轉現金」——這個級距淨賣超，賣出去的
  //      錢沒有留在其他被追蹤的族群裡，等於離開這批觀察範圍、可能轉成現金。
  //      四個級距的正負部分各自加總成兩條時間序列(彼此獨立，不會互相抵銷，
  //      才看得出「某個級距淨買、另一個級距淨賣」這種各級距方向不一致的
  //      情況)，兩條相減就是全市場淨額，理論上會等於同一時間點的「淨流入
  //      總計」，這裡分開算兩條只是為了畫成走勢圖用。
  const offMarketFlowSeries = computed(() => {
    const d = decodedRef.value
    if (!d) return { times: [], newMoney: [], cashOut: [], net: [] }
    const key = `${d.date}|${nowOffset.value}|${controlsRef.value.resolution}`
    const versionKey = `${d.date}|${d.to}`

    return memoize(offMarketCache, key, () => {
      const stepSec = RESOLUTION_SECONDS[controlsRef.value.resolution] || 60
      const steps = Math.max(1, Math.floor(nowOffset.value / stepSec) + 1)
      const times = Array.from({ length: steps }, (_, k) => (k + 1) * stepSec)

      const tierCum = [0, 1, 2, 3].map((b) => {
        const pts = getFlattenedTier(versionKey, b, d)
        return toCumulativeSteps(pts, stepSec, steps)
      })

      const newMoney = new Array(steps)
      const cashOut = new Array(steps)
      const net = new Array(steps)
      for (let k = 0; k < steps; k++) {
        let pos = 0
        let neg = 0
        for (let b = 0; b < 4; b++) {
          const v = tierCum[b][k]
          if (v > 0) pos += v
          else neg += -v
        }
        newMoney[k] = pos
        cashOut[k] = neg
        net[k] = pos - neg
      }
      return { times, newMoney, cashOut, net }
    })
  })

  return {
    liveMaxOffset,
    nowOffset,
    windowRange,
    rows,
    totals,
    cumulativeSeries,
    ratioSeries,
    stockPanels,
    sectorRanking,
    stockRanking,
    offMarketFlowSeries,
  }
}
