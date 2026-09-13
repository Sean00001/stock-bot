import { ref, watch } from 'vue'

/**
 * 給「族群/個股淨流入排行」表的「隔天開/收/最高%」欄位用：找出目前查看的
 * 日期(dateRef)在「已收盤」日期清單(finalDatesRef，來自 useFlowSnapshot 的
 * availableDates.value.final，已經是排序過的)裡的下一天，抓那天的
 * /api/flow/summary/ 摘要(只有 last_price/open_price/high_price/prev_close
 * 幾個小欄位，不含整包 tick 資料)。
 *
 * 沒有「下一天」可查的情況(目前查看的日期還在盤中、還沒收盤；或已經是目前
 * 最新的已收盤日期，還沒有更晚一天的資料)，summary 就是 null，排行表那幾欄
 * 直接顯示「—」即可，不用特別報錯——這本來就是「還沒有隔天資料」的正常狀態，
 * 不是失敗。
 */
export function useNextDaySummary(dateRef, finalDatesRef) {
  const nextDate = ref(null)
  const summary = ref(null) // { last_price, open_price, high_price, prev_close } 或 null
  const loading = ref(false)

  let requestSeq = 0

  async function load() {
    const mySeq = ++requestSeq
    summary.value = null
    nextDate.value = null

    const finals = finalDatesRef.value || []
    const idx = finals.indexOf(dateRef.value)
    // 不在「已收盤」清單裡(還在盤中/查無此日期)，或已經是最後一個已收盤日期
    // (還沒有更晚一天的資料)，都沒有「隔天」可查。
    if (idx === -1 || idx === finals.length - 1) return

    const next = finals[idx + 1]
    nextDate.value = next
    loading.value = true
    try {
      const res = await fetch(`/api/flow/summary/${next}/`, { credentials: 'include' })
      if (mySeq !== requestSeq) return // 這期間使用者已經切到別的日期，這批結果作廢
      if (res.ok) {
        summary.value = await res.json()
      }
    } catch (e) {
      // 隔天欄位是錦上添花，抓不到就維持 null、顯示「—」即可，不用讓整個
      // 排行表因此報錯。
    } finally {
      if (mySeq === requestSeq) loading.value = false
    }
  }

  watch([dateRef, finalDatesRef], load, { immediate: true })

  return { nextDate, summary, loading }
}
