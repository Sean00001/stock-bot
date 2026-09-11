// 個股／族群的顏色配色邏輯，跟 LineChart.vue「累積淨流入走勢」圖例共用同一套
// 判斷方式：依最終累積淨流入由大到小排序，正的(流入)由大排到小在前面、負的
// (流出)由最負排到最不負接在後面，再依這個順序輪流套色盤——這樣同一檔股票
// 不管出現在圖表圖例還是個股卡片背景，拿到的顏色都會是同一個。
//
// 跟 LineChart 內部那份 highlighted/colorMap 邏輯的差別只有一點：LineChart
// 為了畫面清爽，只挑「流入前 N/2、流出前 N/2」上色，其餘的線不畫；這裡則是
// 對「這個族群全部的股票」都配到顏色(色盤循環使用)，因為個股卡片欄要讓每
// 一檔都看得到自己的顏色，不能有漏配的。兩邊排序規則一致，所以至少排在前
// 面、會被 LineChart 選進 highlighted 的那些股票，顏色一定對得上。

export const PALETTE = ['#f97316', '#f59e0b', '#facc15', '#fb7185', '#38bdf8', '#818cf8', '#a3e635', '#94a3b8']

export const FALLBACK_COLOR = '#94a3b8'

/**
 * seriesObj: { [name]: number[] } —— 例如 cumulativeSeries.series，
 * 每個 name 對應一串隨時間累積的數值，取最後一個當作「最終淨流入」。
 * 回傳 { [name]: '#hex' }。
 */
export function buildColorMap(seriesObj) {
  const entries = Object.entries(seriesObj || {}).map(([name, arr]) => ({
    name,
    value: Array.isArray(arr) && arr.length ? arr[arr.length - 1] : 0,
  }))

  const positives = entries.filter((e) => e.value > 0).sort((a, b) => b.value - a.value)
  const negatives = entries.filter((e) => e.value < 0).sort((a, b) => a.value - b.value) // 最負在前
  const zeros = entries.filter((e) => e.value === 0)
  const ordered = [...positives, ...negatives, ...zeros]

  const map = {}
  ordered.forEach((e, idx) => {
    map[e.name] = PALETTE[idx % PALETTE.length]
  })
  return map
}
