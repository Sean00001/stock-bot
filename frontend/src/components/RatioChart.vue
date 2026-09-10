<template>
  <div class="ratio-chart-wrap">
    <div class="local-controls">
      <button class="chip" :class="{ active: mode === 'both' }" @click="mode = 'both'">流入＋流出</button>
      <button class="chip" :class="{ active: mode === 'inflow' }" @click="mode = 'inflow'">只看流入</button>
      <span class="sep" />
      <button class="chip" :class="{ active: count === 8 }" @click="count = 8">8 條</button>
      <button class="chip" :class="{ active: count === 4 }" @click="count = 4">4 條</button>
    </div>
    <div class="chart-container" ref="chartRef"></div>
    <p class="note">
      開盤起累積淨流入 ÷ 同期累積成交值(%)，固定全單口徑(不受單量級距篩選影響)。
      累積成交值未達 3 億的時段不畫，避免分母太小時比例亂跳。
    </p>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, shallowRef, computed } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  // { times: [秒offset,...], series: { name: [比例(%)或 null, ...] } }
  ratio: {
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

// 依「目前最後一個有值的比例點」排序，挑出流入前 N/2、流出前 N/2
// (只看流入模式則全部給流入)；比例序列尾端可能是 null(分母不足)，要往前找。
function lastValueOf(arr) {
  if (!arr) return 0
  for (let k = arr.length - 1; k >= 0; k--) {
    if (arr[k] != null) return arr[k]
  }
  return 0
}

const highlighted = computed(() => {
  const names = Object.keys(props.ratio.series || {})
  const sorted = names.slice().sort((a, b) => lastValueOf(props.ratio.series[b]) - lastValueOf(props.ratio.series[a]))
  const half = Math.max(1, Math.floor(count.value / 2))

  if (mode.value === 'inflow') {
    return sorted.filter((n) => lastValueOf(props.ratio.series[n]) > 0).slice(0, count.value)
  }
  const top = sorted.filter((n) => lastValueOf(props.ratio.series[n]) > 0).slice(0, half)
  const bottom = sorted
    .filter((n) => lastValueOf(props.ratio.series[n]) < 0)
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

watch([() => props.ratio, mode, count], renderChart, { deep: true })

function renderChart() {
  if (!chartInstance.value) return
  // 保險：容器剛掛載時如果還沒排版完成，echarts.init() 量到的寬高可能是 0，
  // 之後就一直卡在 0x0 畫不出東西(主控台會看到 "Can't get DOM width or
  // height" 這個警告)。這裡在真的要畫圖之前主動 resize() 一次，強制它
  // 重新量測目前容器的實際大小，避免這個問題。
  chartInstance.value.resize()
  const times = props.ratio.times || []
  if (!times.length) return

  const names = highlighted.value
  const palette = ['#f97316', '#f59e0b', '#facc15', '#fb7185', '#38bdf8', '#818cf8', '#a3e635', '#94a3b8']

  // 端點標籤直接標在每條線自己的終點旁邊（名稱+數值），取代原本另一個獨立的
  // 圖例清單，並讓滑鼠移到線本身或標籤上時把其他線變暗(blur)，方便在很多條
  // 線擠在一起時找到特定一條，跟累積淨流入走勢圖、參考站同樣的互動方式。
  const series = names.map((name, idx) => ({
    name,
    type: 'line',
    showSymbol: false,
    smooth: true,
    connectNulls: false, // 分母不足的斷點不硬連起來
    lineStyle: { width: 2 },
    itemStyle: { color: palette[idx % palette.length] },
    data: props.ratio.series[name],
    endLabel: {
      show: true,
      formatter: (params) => {
        const v = params.value
        if (v == null) return ''
        return `${params.seriesName}\n${v > 0 ? '+' : ''}${v.toFixed(1)}%`
      },
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
      valueFormatter: (val) => (val == null ? '—' : val.toFixed(2) + '%'),
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
      axisLabel: { color: '#888', formatter: (val) => val.toFixed(0) + '%' },
    },
    series,
  }

  chartInstance.value.setOption(option, true)
}
</script>

<style scoped>
.ratio-chart-wrap {
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

.note {
  margin: 8px 0 0 0;
  font-size: 0.72rem;
  color: #64748b;
  line-height: 1.5;
}
</style>
