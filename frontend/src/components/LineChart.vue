<template>
  <div class="line-chart-wrap">
    <div class="local-controls">
      <button class="chip" :class="{ active: mode === 'both' }" @click="mode = 'both'">流入＋流出</button>
      <button class="chip" :class="{ active: mode === 'inflow' }" @click="mode = 'inflow'">只看流入</button>
      <span class="sep" />
      <button class="chip" :class="{ active: count === 8 }" @click="count = 8">8 條</button>
      <button class="chip" :class="{ active: count === 4 }" @click="count = 4">4 條</button>
    </div>
    <div class="chart-row">
      <div class="chart-container" ref="chartRef"></div>
      <!-- 排行清單：跟線本身脫鉤，不管線畫在哪、擠不擠，每一列永遠是固定
           高度、永遠讀得到、永遠是好點的一大塊。滑鼠移過去會連動圖表把
           那條線點亮(跟以前滑鼠移到線/端點標籤上一樣的效果)，直接點下去
           跟點線本身一樣會下鑽。 -->
      <div class="legend-list">
        <div
          v-for="name in legendNames"
          :key="name"
          class="legend-row"
          @mouseenter="onLegendHover(name)"
          @mouseleave="onLegendLeave"
          @click="onLegendClick(name)"
        >
          <span class="dot" :style="{ background: colorOf(name) }" />
          <span class="legend-name" :title="name">{{ name }}</span>
          <span class="legend-value" :style="{ color: colorOf(name) }">{{ fmtMoney(finalValueOf(name)) }}</span>
        </div>
      </div>
    </div>
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

const palette = ['#f97316', '#f59e0b', '#facc15', '#fb7185', '#38bdf8', '#818cf8', '#a3e635', '#94a3b8']

function finalValueOf(name) {
  const arr = props.cumulative.series[name]
  return arr && arr.length ? arr[arr.length - 1] : 0
}

function fmtMoney(val) {
  return (
    (val > 0 ? '+' : '') +
    (Math.abs(val) >= 100000000 ? (val / 100000000).toFixed(1) + '億' : (val / 10000).toFixed(0) + '萬')
  )
}

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

// 圖表裡每條線的顏色跟排行清單裡色點/數字的顏色，共用同一份對照表，兩邊
// 永遠對得起來。
const colorMap = computed(() => {
  const map = {}
  highlighted.value.forEach((name, idx) => {
    map[name] = palette[idx % palette.length]
  })
  return map
})
function colorOf(name) {
  return colorMap.value[name] || '#94a3b8'
}

// 排行清單固定用「由高到低」單一排序(流入最多排最上面、流出最多排最下
// 面)，不管線在圖上實際畫到哪個高度，清單本身永遠是這個順序、永遠等高。
const legendNames = computed(() => highlighted.value.slice().sort((a, b) => finalValueOf(b) - finalValueOf(a)))

function onLegendHover(name) {
  if (!chartInstance.value) return
  chartInstance.value.dispatchAction({ type: 'downplay' })
  chartInstance.value.dispatchAction({ type: 'highlight', seriesName: name })
}
function onLegendLeave() {
  chartInstance.value?.dispatchAction({ type: 'downplay' })
}
function onLegendClick(name) {
  emit('line-click', name)
}

function formatClock(offsetSec) {
  const base = props.marketOpenTs ? props.marketOpenTs * 1000 : 0
  const d = new Date(base + offsetSec * 1000)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

// 這張圖常常同時有一條爆量的族群(例如晶圓代工 -2000多億)跟一群擠在 0 附近
// 的族群(其他大部分族群通常在 ±500 億內)。如果 Y 軸照原始金額線性畫，
// 那條爆量的線會把軸拉得很長，其餘那一群線全部被壓縮在軸的一小段裡。
// 這裡改用「symlog」(對稱對數)座標：數值越大，被壓縮得越多；越接近 0，
// 越接近原始線性比例。SYMLOG_C 是「多大金額以內大致還算線性」的門檻，
// 數字越小壓縮越激烈。
const SYMLOG_C = 5e9 // 50 億
function symlogForward(v) {
  return Math.sign(v) * Math.log10(1 + Math.abs(v) / SYMLOG_C)
}
function symlogInverse(t) {
  return Math.sign(t) * (Math.pow(10, Math.abs(t)) - 1) * SYMLOG_C
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

  // 名稱＋數值改到圖表外面的排行清單顯示(見上方 legendNames)，這裡的線
  // 本身不再需要端點文字標籤——不管幾條線擠在哪個位置，都不會再互相疊字。
  // 滑鼠移到線本身或排行清單的那一列都會觸發 emphasis，把其他線變暗
  // (blur)，方便在很多條線擠在一起時找到特定一條。
  const series = names.map((name, idx) => ({
    name,
    type: 'line',
    showSymbol: false,
    smooth: true,
    cursor: 'pointer',
    // 預設 echarts 的折線只有資料點(節點)本身可以觸發滑鼠事件，線段中間
    // 完全不會反應點擊。開這個之後整條線(包含兩個資料點之間的線段)都算
    // 在點擊/hover 的命中範圍內，才能做到「點線上任何一點都能下鑽」。
    triggerLineEvent: true,
    lineStyle: { width: 2 },
    itemStyle: { color: palette[idx % palette.length] },
    // 畫在圖上的是 symlog 壓縮過的座標，不是原始金額——tooltip、Y 軸刻度
    // 顯示的時候都要用 symlogInverse() 換算回真正的金額，不然使用者看到
    // 的數字會是錯的。排行清單則是直接用原始金額，跟這裡的轉換無關。
    data: (props.cumulative.series[name] || []).map(symlogForward),
    emphasis: {
      focus: 'series',
      lineStyle: { width: 3 },
    },
    blur: {
      lineStyle: { opacity: 0.12 },
    },
  }))

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      valueFormatter: (val) => {
        const real = symlogInverse(val)
        return Math.abs(real) >= 100000000 ? (real / 100000000).toFixed(2) + ' 億' : (real / 10000).toFixed(2) + ' 萬'
      },
    },
    grid: { left: '5%', right: '4%', bottom: '10%', top: '5%', containLabel: true },
    xAxis: {
      type: 'category',
      data: times.map(formatClock),
      axisLabel: { color: '#888' },
      boundaryGap: false,
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#2a2e39' } },
      // 軸上的刻度位置是 symlog 座標，要換算回真正金額才能顯示；換算完
      // 之後相鄰刻度之間代表的金額不會是等差的(這是對數座標本來就有的
      // 特性，越靠近 0 刻度越密、越遠越疏)，是正常現象。
      axisLabel: {
        color: '#888',
        formatter: (val) => {
          const real = symlogInverse(val)
          const sign = real > 0 ? '+' : real < 0 ? '-' : ''
          return sign + (Math.abs(real) / 100000000).toFixed(0) + '億'
        },
      },
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

.chart-row {
  display: flex;
  flex: 1;
  min-height: 0;
  gap: 10px;
}

.chart-container {
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.legend-list {
  width: 152px;
  flex-shrink: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-top: 4px;
}

.legend-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border-radius: 4px;
  cursor: pointer;
}

.legend-row:hover {
  background: rgba(255, 255, 255, 0.06);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #cbd5e1;
  font-size: 0.72rem;
}

.legend-value {
  font-size: 0.72rem;
  font-weight: 600;
  white-space: nowrap;
}
</style>
