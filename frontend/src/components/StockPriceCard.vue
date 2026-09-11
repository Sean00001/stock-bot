<template>
  <div class="price-card glass-panel">
    <div class="card-header">
      <div class="name">{{ code }} {{ name }}</div>
      <div class="price-block">
        <span class="price">{{ lastPrice != null ? lastPrice.toFixed(2) : '—' }}</span>
        <span v-if="changePct != null" class="change" :class="changePct >= 0 ? 'up' : 'down'">
          {{ changePct >= 0 ? '+' : '' }}{{ changePct.toFixed(2) }}%
        </span>
      </div>
    </div>
    <div class="chart-wrap">
      <div class="chart-container" ref="chartRef"></div>
      <button class="mode-btn" @click="toggleMode">{{ mode === 'line' ? 'K線' : '走勢' }}</button>
    </div>
    <div class="card-footer">
      <div class="foot-row">
        <span class="foot-label">大單以上淨流入</span>
        <span class="foot-value" :class="netAboveL >= 0 ? 'text-blue' : 'text-red'">{{ formatMoney(netAboveL) }}</span>
      </div>
      <div class="foot-row small">
        <span>特大單 {{ xlCount.toLocaleString() }} 筆</span>
        <span class="sep-dot">·</span>
        <span>大單 {{ lCount.toLocaleString() }} 筆</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, shallowRef, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  code: { type: String, required: true },
  name: { type: String, default: '' },
  // 股價序列：[[t_offset_sec, price], ...]（已還原成元，未分桶）
  priceSeries: { type: Array, default: () => [] },
  lastPrice: { type: Number, default: null },
  prevClose: { type: Number, default: null },
  changePct: { type: Number, default: null },
  netAboveL: { type: Number, default: 0 },
  xlCount: { type: Number, default: 0 },
  lCount: { type: Number, default: 0 },
})

const chartRef = ref(null)
const chartInstance = shallowRef(null)
let resizeHandler = null

// 走勢(細線+均線) / K線(蠟燭圖) 兩種顯示模式，按右下角那顆按鈕切換。
const mode = ref('line') // line | candle
function toggleMode() {
  mode.value = mode.value === 'line' ? 'candle' : 'line'
  renderChart()
}

const upColor = '#fb7185' // 跟台股慣例一致：紅漲綠跌
const downColor = '#34d399'

// 簡單移動平均，視資料點數自動抓一個約 1/15 長度的窗，太短就至少 3 個點。
function movingAverage(values, win) {
  const out = new Array(values.length)
  let sum = 0
  for (let idx = 0; idx < values.length; idx++) {
    sum += values[idx]
    if (idx >= win) sum -= values[idx - win]
    const count = Math.min(win, idx + 1)
    out[idx] = sum / count
  }
  return out
}

// 把逐筆股價(未分桶)壓成固定根數的 K 棒，不管實際 tick 有幾筆，卡片這麼小
// 的畫布固定抓一個看得清楚的根數就好(太多根會擠成一片黑)。
function buildCandles(pts, targetBars = 40) {
  if (!pts.length) return []
  const barCount = Math.max(1, Math.min(targetBars, pts.length))
  const size = Math.ceil(pts.length / barCount)
  const out = []
  for (let i = 0; i < pts.length; i += size) {
    const chunk = pts.slice(i, i + size)
    const vals = chunk.map((p) => p[1])
    out.push({
      t: chunk[0][0],
      open: vals[0],
      close: vals[vals.length - 1],
      low: Math.min(...vals),
      high: Math.max(...vals),
    })
  }
  return out
}

// 現價旁邊那個小標籤(深底白字)＋一條淺藍色的參考線，標出目前線畫到哪個
// 位置，兩種模式共用同一套設定，只是 xAxis 的最後一個索引值不同。
function currentMarkerSeries(lastIndex, lastVal, color) {
  return {
    type: 'scatter',
    data: [[lastIndex, lastVal]],
    symbolSize: 6,
    itemStyle: { color },
    silent: true,
    z: 5,
    label: {
      show: true,
      formatter: () => lastVal.toFixed(2),
      position: 'right',
      distance: 6,
      color: '#fff',
      fontSize: 10,
      padding: [2, 5],
      borderRadius: 3,
      backgroundColor: 'rgba(15, 23, 42, 0.9)',
    },
    markLine: {
      symbol: 'none',
      silent: true,
      label: { show: false },
      lineStyle: { color: '#38bdf8', width: 1, type: 'solid', opacity: 0.6 },
      data: [{ xAxis: lastIndex }],
    },
  }
}

function renderChart() {
  if (!chartInstance.value) return
  const pts = props.priceSeries
  if (!pts.length) {
    chartInstance.value.clear()
    return
  }
  const markerColor = (props.changePct ?? 0) >= 0 ? upColor : downColor

  let option
  // 右邊要留給現價標籤的位置，不然標籤會被裁掉(跟累積淨流入走勢圖那次
  // 端點標籤被截斷是同一種問題)。
  const grid = { left: 2, right: 46, top: 8, bottom: 2 }

  if (mode.value === 'candle') {
    const candles = buildCandles(pts)
    const closes = candles.map((c) => c.close)
    const maWin = Math.max(3, Math.floor(candles.length / 6))
    const ma = movingAverage(closes, maWin)
    const lastIdx = candles.length - 1

    option = {
      backgroundColor: 'transparent',
      grid,
      xAxis: { type: 'category', show: false, data: candles.map((c) => c.t), boundaryGap: true },
      yAxis: { type: 'value', show: false, scale: true },
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          const raw = params.find((p) => p.seriesType === 'candlestick')
          if (!raw || !raw.data) return ''
          const [o, c, l, h] = raw.data
          return `開 ${o.toFixed(2)}　收 ${c.toFixed(2)}<br/>低 ${l.toFixed(2)}　高 ${h.toFixed(2)}`
        },
      },
      series: [
        {
          type: 'candlestick',
          data: candles.map((c) => [c.open, c.close, c.low, c.high]),
          itemStyle: {
            color: upColor,
            color0: downColor,
            borderColor: upColor,
            borderColor0: downColor,
          },
        },
        {
          type: 'line',
          data: ma,
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.5, color: '#f59e0b' },
          z: 4,
        },
        currentMarkerSeries(lastIdx, candles[lastIdx].close, markerColor),
      ],
    }
  } else {
    const values = pts.map((p) => p[1])
    const win = Math.max(3, Math.floor(values.length / 15))
    const ma = movingAverage(values, win)
    const lastIdx = values.length - 1

    option = {
      backgroundColor: 'transparent',
      grid,
      xAxis: { type: 'category', show: false, data: pts.map((p) => p[0]), boundaryGap: false },
      yAxis: { type: 'value', show: false, scale: true },
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          const raw = params.find((p) => p.seriesIndex === 0)
          return raw && raw.value != null ? raw.value.toFixed(2) : ''
        },
      },
      series: [
        {
          type: 'line',
          data: values,
          showSymbol: false,
          lineStyle: { width: 1, color: '#cbd5e1' },
        },
        {
          type: 'line',
          data: ma,
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.5, color: '#f59e0b' },
        },
        currentMarkerSeries(lastIdx, values[lastIdx], markerColor),
      ],
    }
  }

  chartInstance.value.setOption(option, true)
}

onMounted(() => {
  chartInstance.value = echarts.init(chartRef.value, 'dark')
  renderChart()
  resizeHandler = () => chartInstance.value?.resize()
  window.addEventListener('resize', resizeHandler)
})

onBeforeUnmount(() => {
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
  chartInstance.value?.dispose()
})

watch(() => props.priceSeries, renderChart)

function formatMoney(val) {
  if (val === undefined || val === null) return '0 億'
  const sign = val > 0 ? '+' : ''
  const abs = Math.abs(val)
  if (abs >= 100000000) return `${sign}${(val / 100000000).toFixed(2)} 億`
  return `${sign}${(val / 10000).toFixed(2)} 萬`
}
</script>

<style scoped>
.price-card {
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-shrink: 0;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.name {
  font-size: 0.9rem;
  color: #e2e8f0;
  font-weight: 600;
}

.price-block {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.price {
  font-size: 1.35rem;
  font-weight: 700;
  color: #f1f5f9;
}

.change {
  font-size: 0.85rem;
  font-weight: 600;
}

.change.up {
  color: #fb7185;
}

.change.down {
  color: #34d399;
}

.chart-wrap {
  position: relative;
}

.chart-container {
  width: 100%;
  height: 120px;
}

.mode-btn {
  position: absolute;
  right: 2px;
  bottom: 2px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #94a3b8;
  border-radius: 5px;
  padding: 1px 7px;
  font-size: 0.68rem;
  cursor: pointer;
  line-height: 1.6;
}

.mode-btn:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #e2e8f0;
}

.card-footer {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.foot-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 0.78rem;
  color: #94a3b8;
}

/* 之前欄位太窄時，「大單以上淨流入」這種中文標籤會被硬拆成一個字一行
   (瀏覽器對中文的預設斷行規則是逐字斷)，看起來像亂碼。改成不允許在
   文字中間斷行，欄位夠寬的話就是一行；就算哪天欄位又變窄，也只會整串
   一起換到下一行，不會再拆得亂七八糟。 */
.foot-label,
.foot-value {
  white-space: nowrap;
}

.foot-row.small {
  font-size: 0.72rem;
  justify-content: flex-start;
  gap: 6px;
  color: #64748b;
}

.sep-dot {
  color: #475569;
}

.foot-value {
  font-weight: 600;
}

.text-blue {
  color: #38bdf8;
}

.text-red {
  color: #fb7185;
}
</style>
