import { ref } from 'vue'
import { decodeSnapshot, buildCursor, applyIncremental } from '../lib/flowCodec'

// 開盤中改用 .t.json 增量輪詢(見下方 pollIncremental)，這個間隔只是「多久跟
// 伺服器要一次新資料」——因為增量檔通常只有幾 KB，拉長一點對「資料新鮮度」
// 影響不大，卻能再少掉一些沒必要的請求/運算次數。
const POLL_MS = 8000

/**
 * 負責跟 backend 的 /api/flow/* 拿快照檔並輪詢更新。
 *
 * 開盤中的輪詢改用 worker 準備好的 .t.json 增量端點：每次只拉「上次之後新增
 * 的那一小段 tick」(通常幾 KB)，用 applyIncremental() 疊加到已經解碼好的
 * snapshot 結構上(原地 push，不整包换掉物件；Vue 深層響應式抓得到哪些陣列
 * 真的變動了，只有真的受影響的圖表/計算會重跑)。以前的做法是不管三七二十一
 * 每次都整包重抓 .b.json 全量快照(收盤前可以長到 16~17MB)，等於每 3 秒就要
 * 重新下載+解析+解碼一次全天資料，是網站整體變慢/卡頓的主因，所以改成這樣。
 *
 * 只有下面幾種情況才會整包重抓 .b.json(貴，但正確)：
 *   1. 第一次打開這個日期(還沒有基準快照可以疊加)
 *   2. 疊加時發現本地 cursor 跟 .t.json 附的 "from" 對不起來(可能漏接過某次
 *      快照、或 worker 中途重啟過)
 *   3. 某個族群底下的股票數量跟本地記的不一樣(盤中新股票開始有成交，股票
 *      清單一變，後面股票在陣列裡的位置就整個偏移，不能再用位置對應)
 */
export function useFlowSnapshot() {
  const snapshot = ref(null) // decodeSnapshot() 的結果
  const status = ref('idle') // idle | loading | live | final | error | not_found
  const errorMsg = ref('')
  const availableDates = ref({ final: [], live: [] })

  let pollTimer = null
  let currentDate = null
  let cursor = {} // {code: [xl_len, l_len, m_len, s_len, price_len]}，只在 live 輪詢時用

  async function fetchJson(url) {
    const res = await fetch(url, { credentials: 'include' })
    if (!res.ok) {
      const err = new Error('HTTP ' + res.status)
      err.status = res.status
      throw err
    }
    return res.json()
  }

  async function loadDates() {
    const json = await fetchJson('/api/flow/dates/')
    availableDates.value = { final: json.final || [], live: json.live || [] }
    return availableDates.value
  }

  function stop() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  /** 整包抓一次(.b.json 或 {date}.json)，取代目前的 snapshot，並重建本地 cursor。 */
  async function pollFull(date, kind) {
    const raw = await fetchJson(`/api/flow/snapshot/${date}/${kind}/`)
    snapshot.value = decodeSnapshot(raw)
    cursor = buildCursor(snapshot.value)
    return snapshot.value
  }

  /** 只拉 .t.json 增量疊加；疊不上去(cursor 兜不起來/股票清單變動)就改成整包重抓 .b.json。 */
  async function pollIncremental(date) {
    if (!snapshot.value) {
      await pollFull(date, 'b')
      return
    }
    const raw = await fetchJson(`/api/flow/snapshot/${date}/t/`)
    const ok = applyIncremental(snapshot.value, cursor, raw)
    if (!ok) {
      await pollFull(date, 'b')
    }
  }

  /** 開啟某一天的資料。已 finalize 的日期只抓一次；今天/還在更新的日期會固定輪詢。 */
  async function open(date) {
    stop()
    currentDate = date
    status.value = 'loading'
    errorMsg.value = ''
    snapshot.value = null
    cursor = {}

    try {
      await loadDates()
      const isFinal = availableDates.value.final.includes(date)

      if (isFinal) {
        await pollFull(date, 'full')
        status.value = 'final'
        return
      }

      await pollFull(date, 'b')
      status.value = 'live'

      pollTimer = setInterval(async () => {
        if (currentDate !== date) return // 使用者已經切到別的日期
        try {
          await pollIncremental(date)
          status.value = 'live'
        } catch (e) {
          if (e.status === 404) {
            // .t.json/.b.json 可能因為收盤 finalize 而被換成 {date}.json
            try {
              await loadDates()
              if (availableDates.value.final.includes(date)) {
                stop()
                await pollFull(date, 'full')
                status.value = 'final'
              }
            } catch (_) {
              /* 下一輪再試 */
            }
          }
        }
      }, POLL_MS)
    } catch (e) {
      status.value = e.status === 404 ? 'not_found' : 'error'
      errorMsg.value = String((e && e.message) || e)
    }
  }

  return { snapshot, status, errorMsg, availableDates, loadDates, open, stop }
}
