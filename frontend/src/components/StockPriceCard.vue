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
    <div class="chart-container" ref="chartRef"></div>
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

function renderChart() {
  if (!chartInstance.value) return
  const pts = props.priceSeries
  if (!pts.length) {
    chartInstance.value.clear()
    return
  }
  const values = pts.map((p) => p[1])
  const win = Math.max(3, Math.floor(values.length / 15))
  const ma = movingAverage(values, win)
  const upColor = '#fb7185'
  const downColor = '#34d399'
  const markerColor = (props.changePct ?? 0) >= 0 ? upColor : downColor

  const option = {
    backgroundColor: 'transparent',
    grid: { left: 2, right: 2, top: 8, bottom: 2 },
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
      {
        type: 'scatter',
        data: [[values.length - 1, values[values.length - 1]]],
        symbolSize: 6,
        itemStyle: { color: markerColor },
        silent: true,
      },
    ],
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
  font-size: 0.85rem;
  color: #e2e8f0;
  font-weight: 600;
}

.price-block {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.price {
  font-size: 1.05rem;
  font-weight: 700;
  color: #f1f5f9;
}

.change {
  font-size: 0.8rem;
  font-weight: 600;
}

.change.up {
  color: #fb7185;
}

.change.down {
  color: #34d399;
}

.chart-container {
  width: 100%;
  height: 120px;
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
