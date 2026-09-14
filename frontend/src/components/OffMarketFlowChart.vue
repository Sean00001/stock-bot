<template>
  <div class="offmarket-wrap">
    <h4 class="section-title">場外資金進出</h4>
    <p class="note">
      與資金流向圖同口徑：各單量級距(特大單/大單/中單/小單)的全市場淨買賣，正的加總＝場外新資金；負的加總＝離場/轉現金；
      兩者相減＝全市場淨額(理論上等同淨流入總計)。淨額線同時出現過正負，即視為穿越 0 軸。
    </p>
    <div class="chart-container" ref="chartRef"></div>
    <p class="summary" v-if="hasData">
      目前 {{ fmt(latestNewMoney) }} vs {{ fmt(latestCashOut) }} 億，淨額 {{ fmtSigned(latestNet) }} 億，{{
        crossed ? '已穿越 0 軸' : '尚未穿越 0 軸'
      }}
    </p>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, shallowRef, computed } from 'vue'
import * as echarts from 'echarts'

// series: { times: [秒offset,...], newMoney: [...], cashOut: [...], net: [...] }
// (來自 useAggregation 的 offMarketFlowSeries；同一套口徑見 SankeyChart.vue
// 的「場外新資金/離場轉現金」節點)
const props = defineProps({
  series: {
    type: Object,
    default: () => ({ times: [], newMoney: [], cashOut: [], net: [] }),
  },
  marketOpenTs: {
    type: Number,
    default: 0,
  },
})

const chartRef = ref(null)
const chartInstance = shallowRef(null)

function onResize() {
  chartInstance.value?.resize()
}

onMounted(() => {
  chartInstance.value = echarts.init(chartRef.value, 'dark')
  window.addEventListener('resize', onResize)
  renderChart()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
})

function formatClock(offsetSec) {
  const base = props.marketOpenTs ? props.marketOpenTs * 1000 : 0
  const d = new Date(base + offsetSec * 1000)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

function lastOf(arr) {
  return arr && arr.length ? arr[arr.length - 1] : 0
}

function fmt(v) {
  return (Math.abs(v || 0) / 1e8).toFixed(1)
}
function fmtSigned(v) {
  const val = v || 0
  const sign = val > 0 ? '+' : val < 0 ? '-' : ''
  return sign + (Math.abs(val) / 1e8).toFixed(1)
}

const hasData = computed(() => (props.series.times || []).length > 0)
const latestNewMoney = computed(() => lastOf(props.series.newMoney))
const latestCashOut = computed(() => lastOf(props.series.cashOut))
const latestNet = computed(() => lastOf(props.series.net))

// 淨額線只要曾經出現過「正」也出現過「負」，就算「已穿越 0 軸」；開盤前
// 還沒開始交易那段全部是 0，不算正也不算負，不會誤判成已穿越。
const crossed = computed(() => {
  const net = props.series.net || []
  let sawPos = false
  let sawNeg = false
  for (const v of net) {
    if (v > 0) sawPos = true
    else if (v < 0) sawNeg = true
  }
  return sawPos && sawNeg
})

watch(() => props.series, renderChart, { deep: true })

function renderChart() {
  if (!chartInstance.value) return
  // 保險：容器剛掛載時如果還沒排版完成，echarts.init() 量到的寬高可能是 0，
  // 之後就一直卡在 0x0 畫不出東西。這裡在真的要畫圖之前主動 resize() 一次。
  chartInstance.value.resize()
  const times = props.series.times || []
  if (!times.length) {
    chartInstance.value.clear()
    return
  }

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      valueFormatter: (val) => {
        const sign = val > 0 ? '+' : val < 0 ? '-' : ''
        return sign + (Math.abs(val) / 1e8).toFixed(2) + ' 億'
      },
    },
    legend: {
      data: ['場外新資金', '離場/轉現金', '淨額'],
      textStyle: { color: '#94a3b8', fontSize: 11 },
      top: 0,
      itemWidth: 14,
      itemHeight: 8,
    },
    grid: { left: '5%', right: '4%', bottom: '8%', top: '22%', containLabel: true },
    xAxis: {
      type: 'category',
      data: times.map(formatClock),
      axisLabel: { color: '#888' },
      boundaryGap: false,
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#2a2e39' } },
      axisLabel: {
        color: '#888',
        formatter: (val) => (val >= 0 ? '' : '-') + (Math.abs(val) / 1e8).toFixed(0) + '億',
      },
    },
    series: [
      {
        name: '場外新資金',
        type: 'line',
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 2, color: '#f97316' },
        itemStyle: { color: '#f97316' },
        data: props.series.newMoney || [],
      },
      {
        name: '離場/轉現金',
        type: 'line',
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 2, color: '#38bdf8' },
        itemStyle: { color: '#38bdf8' },
        data: props.series.cashOut || [],
      },
      {
        name: '淨額',
        type: 'line',
        showSymbol: false,
        smooth: true,
        lineStyle: { width: 1.5, color: '#cbd5e1', type: 'dashed' },
        itemStyle: { color: '#cbd5e1' },
        data: props.series.net || [],
      },
    ],
  }

  chartInstance.value.setOption(option, true)
}
</script>

<style scoped>
.offmarket-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  flex: 0 0 auto;
}

.section-title {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 500;
  color: #cbd5e1;
}

.note {
  margin: 0;
  font-size: 0.72rem;
  color: #64748b;
  line-height: 1.5;
}

.chart-container {
  width: 100%;
  height: 170px;
}

.summary {
  margin: 0;
  font-size: 0.78rem;
  color: #94a3b8;
}
</style>
