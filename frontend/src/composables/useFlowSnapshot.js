import { ref } from 'vue'
import { decodeSnapshot } from '../lib/flowCodec'

const POLL_MS = 3000

/**
 * 負責跟 backend 的 /api/flow/* 拿快照檔並輪詢更新。
 *
 * 目前先用「每次都整包重抓 .b.json 全量快照」的簡單做法，還沒接 worker/backend
 * 已經準備好的 .t.json 增量端點 —— 這是刻意先求正確、容易除錯，之後如果盤中
 * 股票檔數一多、快照檔變大，可以再把這裡改成用 .t.json 疊加更新，不用動別的地方。
 */
export function useFlowSnapshot() {
  const snapshot = ref(null) // decodeSnapshot() 的結果
  const status = ref('idle') // idle | loading | live | final | error | not_found
  const errorMsg = ref('')
  const availableDates = ref({ final: [], live: [] })

  let pollTimer = null
  let currentDate = null

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

  async function pollOnce(date, kind) {
    const raw = await fetchJson(`/api/flow/snapshot/${date}/${kind}/`)
    snapshot.value = decodeSnapshot(raw)
    return snapshot.value
  }

  /** 開啟某一天的資料。已 finalize 的日期只抓一次；今天/還在更新的日期會固定輪詢。 */
  async function open(date) {
    stop()
    currentDate = date
    status.value = 'loading'
    errorMsg.value = ''

    try {
      await loadDates()
      const isFinal = availableDates.value.final.includes(date)

      if (isFinal) {
        await pollOnce(date, 'full')
        status.value = 'final'
        return
      }

      await pollOnce(date, 'b')
      status.value = 'live'

      pollTimer = setInterval(async () => {
        if (currentDate !== date) return // 使用者已經切到別的日期
        try {
          await pollOnce(date, 'b')
          status.value = 'live'
        } catch (e) {
          if (e.status === 404) {
            // .b.json 可能因為收盤 finalize 而被換成 {date}.json
            try {
              await loadDates()
              if (availableDates.value.final.includes(date)) {
                stop()
                await pollOnce(date, 'full')
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
