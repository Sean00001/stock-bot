<template>
  <div class="sankey-wrap">
    <div class="local-controls">
      <div class="group">
        <button
          v-for="opt in bucketOptions"
          :key="opt.value"
          class="chip"
          :class="{ active: bucketFilter === opt.value }"
          @click="bucketFilter = opt.value"
        >
          {{ opt.label }}
        </button>
      </div>
      <span class="sep" />
      <div class="group">
        <span class="group-label">顯示</span>
        <button class="chip" :class="{ active: showLimit === 8 }" @click="showLimit = 8">前8</button>
        <button class="chip" :class="{ active: showLimit === 12 }" @click="showLimit = 12">前12</button>
        <button class="chip" :class="{ active: showLimit === Infinity }" @click="showLimit = Infinity">全部</button>
      </div>
      <span class="sep" />
      <div class="group">
        <span class="group-label">排序</span>
        <button class="chip" :class="{ active: sortMode === 'value' }" @click="sortMode = 'value'">依金額</button>
        <button class="chip" :class="{ active: sortMode === 'link' }" @click="sortMode = 'link'">依連線</button>
      </div>
    </div>
    <div class="chart-container" ref="chartRef"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, shallowRef, computed } from 'vue'
import * as echarts from 'echarts'

// flowData: [{ name, net_xl, net_l, net_m, net_s, net, amount }, ...]（來自 useAggregation 的 rows）
const props = defineProps({
  flowData: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits(['node-click'])

const chartRef = ref(null)
const chartInstance = shallowRef(null)

const bucketFilter = ref('all') // all | xl | l | m | s
const showLimit = ref(8)
const sortMode = ref('value') // value | link

const bucketOptions = [
  { value: 'all', label: '全部' },
  { value: 'xl', label: '特大單' },
  { value: 'l', label: '大單' },
  { value: 'm', label: '中單' },
  { value: 's', label: '小單' },
]

const BUCKET_KEYS = ['net_xl', 'net_l', 'net_m', 'net_s']
const BUCKET_LABELS = ['特大單', '大單', '中單', '小單']
const BUCKET_COLORS = ['#8b5cf6', '#3b82f6', '#f59e0b', '#10b981']

onMounted(() => {
  chartInstance.value = echarts.init(chartRef.value, 'dark')
  chartInstance.value.on('click', (params) => {
    if (params.dataType === 'node' && !BUCKET_LABELS.includes(params.name) && !params.name.startsWith('其餘')) {
      emit('node-click', params.name)
    } else if (params.dataType === 'edge') {
      const target = params.data.target
      if (!BUCKET_LABELS.includes(target) && !String(target).startsWith('其餘')) {
        emit('node-click', target)
      }
    }
  })
  window.addEventListener('resize', () => chartInstance.value?.resize())
  renderChart()
})

// 依目前 單量級距 篩選，算出每個 entity 要用哪些桶、方向怎麼判斷
const buckets = computed(() => {
  if (bucketFilter.value === 'all') return [0, 1, 2, 3]
  return [bucketOptions.findIndex((o) => o.value === bucketFilter.value) - 1]
})

function buildGraph() {
  const activeBuckets = buckets.value
  // 每個 entity 的「總淨額」只看目前有效的桶；用來決定它是左邊(流出)還是右邊(流入)
  const withNet = props.flowData
    .map((row) => {
      const net = activeBuckets.reduce((s, b) => s + row[BUCKET_KEYS[b]], 0)
      return { name: row.name, net, row }
    })
    .filter((e) => Math.abs(e.net) > 0)

  const sourceSide = withNet.filter((e) => e.net < 0).sort((a, b) => a.net - b.net) // 越負越前面
  const targetSide = withNet.filter((e) => e.net > 0).sort((a, b) => b.net - a.net) // 越大越前面

  function splitTop(list) {
    const limit = showLimit.value
    if (list.length <= limit) return { top: list, restCount: 0, restNet: 0 }
    const top = list.slice(0, limit)
    const rest = list.slice(limit)
    const restNet = rest.reduce((s, e) => s + e.net, 0)
    return { top, restCount: rest.length, restNet }
  }

  const src = splitTop(sourceSide)
  const tgt = splitTop(targetSide)

  const nodes = []
  const links = []
  const bucketNodeNames = activeBuckets.map((b) => BUCKET_LABELS[b])
  bucketNodeNames.forEach((name, i) => {
    nodes.push({ name, itemStyle: { color: BUCKET_COLORS[activeBuckets[i]] }, rank: 1 })
  })

  function addEntityLinks(entity, side) {
    // side: 'out'(entity -> bucket，entity 在左邊/資金被抽走) | 'in'(bucket -> entity，entity 在右邊/資金流進去)
    activeBuckets.forEach((b) => {
      const v = entity.row[BUCKET_KEYS[b]]
      const mag = side === 'out' ? Math.max(0, -v) : Math.max(0, v)
      if (mag <= 0) return
      const bucketName = BUCKET_LABELS[b]
      if (side === 'out') links.push({ source: entity.name, target: bucketName, value: mag })
      else links.push({ source: bucketName, target: entity.name, value: mag })
    })
  }

  src.top.forEach((e) => {
    nodes.push({ name: e.name, rank: 0 })
    addEntityLinks(e, 'out')
  })
  if (src.restCount > 0) {
    // 流出側跟流入側各自的「其餘 N 項」有可能剛好項數相同(例如都是 9 項)，
    // 這樣兩邊會產生同名節點，echarts sankey 不允許節點名稱重複，會直接丟
    // Exception 把整張圖弄壞(之後所有 setOption/resize 都會失敗)。這裡在
    // 名稱後面加上方向字樣，確保流出側跟流入側的「其餘」節點永遠不會同名。
    const restName = `其餘 ${src.restCount} 項(流出)`
    nodes.push({ name: restName, rank: 0, itemStyle: { color: '#475569' } })
    activeBuckets.forEach((b) => {
      const mag = src.top.length
        ? Math.max(0, -(src.restNet)) / activeBuckets.length // 粗略平均攤到各桶(僅供視覺呈現)
        : 0
      if (mag > 0) links.push({ source: restName, target: BUCKET_LABELS[b], value: mag })
    })
  }

  tgt.top.forEach((e) => {
    nodes.push({ name: e.name, rank: 2 })
    addEntityLinks(e, 'in')
  })
  if (tgt.restCount > 0) {
    const restName = `其餘 ${tgt.restCount} 項(流入)`
    nodes.push({ name: restName, rank: 2, itemStyle: { color: '#475569' } })
    activeBuckets.forEach((b) => {
      const mag = tgt.top.length ? Math.max(0, tgt.restNet) / activeBuckets.length : 0
      if (mag > 0) links.push({ source: BUCKET_LABELS[b], target: restName, value: mag })
    })
  }

  if (sortMode.value === 'link') {
    // 簡化版「依連線」：把每個 entity 依它連到哪個桶(以主要金額判斷)分組排在一起，
    // 減少視覺上的線條交錯；不是嚴格的交叉數最小化演算法。
    const bucketOf = (name) => {
      let best = -1
      let bestVal = -1
      for (const l of links) {
        if (l.source === name || l.target === name) {
          const bName = BUCKET_LABELS.includes(l.source) ? l.source : l.target
          const idx = BUCKET_LABELS.indexOf(bName)
          if (l.value > bestVal) {
            bestVal = l.value
            best = idx
          }
        }
      }
      return best
    }
    nodes.sort((a, b) => {
      if (a.rank !== b.rank) return a.rank - b.rank
      if (a.rank === 1) return 0
      return bucketOf(a.name) - bucketOf(b.name)
    })
  }

  return { nodes, links }
}

watch(() => [props.flowData, bucketFilter.value, showLimit.value, sortMode.value], renderChart, { deep: true })

function renderChart() {
  if (!chartInstance.value || !props.flowData.length) return
  // 保險：容器剛掛載時如果還沒排版完成，echarts.init() 量到的寬高可能是 0，
  // 之後就一直卡在 0x0 畫不出東西(主控台會看到 "Can't get DOM width or
  // height" 這個警告)。這裡在真的要畫圖之前主動 resize() 一次，強制它
  // 重新量測目前容器的實際大小，避免這個問題。
  chartInstance.value.resize()
  const { nodes, links } = buildGraph()
  if (!links.length) {
    chartInstance.value.clear()
    return
  }

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: (info) => {
        const val = info.data.value
        const fmt = val >= 100000000 ? (val / 100000000).toFixed(2) + ' 億' : (val / 10000).toFixed(2) + ' 萬'
        return info.dataType === 'edge' ? `${info.data.source} → ${info.data.target}: ${fmt}` : `${info.name}: ${fmt}`
      },
    },
    series: [
      {
        type: 'sankey',
        emphasis: { focus: 'adjacency' },
        data: nodes,
        links,
        lineStyle: { color: 'source', curveness: 0.5, opacity: 0.4 },
        label: { color: '#fff', formatter: '{b}', fontSize: 11 },
      },
    ],
  }

  try {
    chartInstance.value.setOption(option, true)
  } catch (e) {
    // 保險：萬一未來又出現其他 echarts 不接受的資料形狀(例如又有沒想到的
    // 同名節點)，setOption 丟出的例外如果沒接住，這個 echarts instance
    // 之後每次 setOption/resize 都會繼續失敗、整張圖永遠卡死。這裡接住
    // 例外、印出來方便除錯，並且清空畫布，至少不會卡成一直壞掉的狀態。
    console.error('[SankeyChart] setOption 失敗，資料可能有問題:', e, { nodes, links })
    chartInstance.value.clear()
  }
}
</script>

<style scoped>
.sankey-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.local-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.group {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.group-label {
  color: #94a3b8;
  font-size: 0.72rem;
  margin-right: 2px;
}

.sep {
  width: 1px;
  height: 14px;
  background: rgba(255, 255, 255, 0.1);
  margin: 0 2px;
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
