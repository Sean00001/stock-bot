<template>
  <div class="line-chart-wrap">
    <div class="local-controls">
      <button class="chip" :class="{ active: mode === 'both' }" @click="mode = 'both'">流入＋流出</button>
      <button class="chip" :class="{ active: mode === 'inflow' }" @click="mode = 'inflow'">只看流入</button>
      <span class="sep" />
      <button class="chip" :class="{ active: count === 8 }" @click="count = 8">8 條</button>
      <button class="chip" :class="{ active: count === 4 }" @click="count = 4">4 條</button>
    </div>
    <div class="chart-container" ref="chartRef"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, shallowRef, computed } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  // { times: [秒offset,...], series: { name: [累積值,...] } }
  cumulative: {
    type: Object,
    default: () => ({ times: [], series: {} }),
  },
  marketOpenTs: {
    type: Number,
    default: 0,
  },
})

const emit = defineEmits(['line-click'])

const chartRef = ref(null)
const chartInstance = shallowRef(null)
const mode = ref('both') // both | inflow
const count = ref(8)

onMounted(() => {
  chartInstance.value = echarts.init(chartRef.value, 'dark')
  chartInstance.value.on('click', (params) => {
    if (params.seriesName) emit('line-click', params.seriesName)
  })
  window.addEventListener('resize', () => chartInstance.value?.resize())
  renderChart()
})

// 依目前最終累積值排序，挑出流入前 N/2、流出前 N/2 (只看流入模式則全部給流入)
const highlighted = computed(() => {
  const names = Object.keys(props.cumulative.series || {})
  const finalValueOf = (name) => {
    const arr = props.cumulative.series[name]
    return arr && arr.length ? arr[arr.length - 1] : 0
  }
  const sorted = names.slice().sort((a, b) => finalValueOf(b) - finalValueOf(a))
  const half = Math.max(1, Math.floor(count.value / 2))

  if (mode.value === 'inflow') {
    return sorted.filter((n) => finalValueOf(n) > 0).slice(0, count.value)
  }
  const top = sorted.filter((n) => finalValueOf(n) > 0).slice(0, half)
  const bottom = sorted
    .filter((n) => finalValueOf(n) < 0)
    .slice(-half)
    .reverse()
  return [...top, ...bottom]
})

function formatClock(offsetSec) {
  const base = props.marketOpenTs ? props.marketOpenTs * 1000 : 0
  const d = new Date(base + offsetSec * 1000)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

watch([() => props.cumulative, mode, count], renderChart, { deep: true })

function renderChart() {
  if (!chartInstance.value) return
  // 保險：容器剛掛載時如果還沒排版完成，echarts.init() 量到的寬高可能是 0，
  // 之後就一直卡在 0x0 畫不出東西(主控台會看到 "Can't get DOM width or
  // height" 這個警告)。這裡在真的要畫圖之前主動 resize() 一次，強制它
  // 重新量測目前容器的實際大小，避免這個問題。
  chartInstance.value.resize()
  const times = props.cumulative.times || []
  if (!times.length) return

  const names = highlighted.value
  const palette = ['#f97316', '#f59e0b', '#facc15', '#fb7185', '#38bdf8', '#818cf8', '#a3e635', '#94a3b8']

  // 端點標籤直接標在每條線自己的終點旁邊（名稱+數值），取代原本另一個獨立的
  // 圖例清單——這樣標籤位置天生對齊那條線實際畫到哪裡，不用在圖例跟線之間
  // 來回對照。滑鼠移到線本身或這個端點標籤上都會觸發 emphasis，把其他線
  // 變暗(blur)，方便在很多條線擠在一起時找到特定一條，跟參考站同樣的互動。
  const fmtMoney = (val) =>
    (val > 0 ? '+' : '') +
    (Math.abs(val) >= 100000000 ? (val / 100000000).toFixed(1) + '億' : (val / 10000).toFixed(0) + '萬')

  const series = names.map((name, idx) => ({
    name,
    type: 'line',
    showSymbol: false,
    smooth: true,
    lineStyle: { width: 2 },
    itemStyle: { color: palette[idx % palette.length] },
    data: props.cumulative.series[name],
    endLabel: {
      show: true,
      formatter: (params) => `${params.seriesName}\n${fmtMoney(params.value)}`,
      color: 'inherit',
      fontSize: 11,
      lineHeight: 14,
    },
    emphasis: {
      focus: 'series',
      lineStyle: { width: 3 },
    },
    blur: {
      lineStyle: { opacity: 0.12 },
      endLabel: { opacity: 0.25 },
    },
  }))

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      valueFormatter: (val) =>
        Math.abs(val) >= 100000000 ? (val / 100000000).toFixed(2) + ' 億' : (val / 10000).toFixed(2) + ' 萬',
    },
    grid: { left: '5%', right: '19%', bottom: '10%', top: '5%', containLabel: true },
    xAxis: {
      type: 'category',
      data: times.map(formatClock),
      axisLabel: { color: '#888' },
      boundaryGap: false,
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#2a2e39' } },
      axisLabel: { color: '#888', formatter: (val) => (val / 100000000).toFixed(0) + '億' },
    },
    series,
  }

  chartInstance.value.setOption(option, true)
}
</script>

<style scoped>
.line-chart-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.local-controls {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.sep {
  width: 1px;
  height: 14px;
  background: rgba(255, 255, 255, 0.1);
  margin: 0 4px;
}

.chip {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  color: #cbd5e1;
  font-size: 0.72rem;
  padding: 3px 10px;
  cursor: pointer;
}

.chip.active {
  background: #0ea5e9;
  border-color: #0ea5e9;
  color: #041018;
  font-weight: 600;
}

.chart-container {
  width: 100%;
  flex: 1;
  min-height: 0;
}
</style>
