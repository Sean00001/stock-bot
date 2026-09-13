<template>
  <div class="ranking-panel glass-panel">
    <div class="head-row">
      <div class="title-block">
        <h3 class="panel-title">{{ viewMode === 'sector' ? '族群' : '個股' }}淨流入排行（億元）</h3>
        <p class="sub-note">
          {{ viewMode === 'sector' ? `全部 ${sectorRows.length} 個族群` : `共 ${stockRows.length} 檔股票` }}・
          點任一列可鑽看{{ viewMode === 'sector' ? '成分股' : '所屬族群' }}・點欄標題可換排序・
          淨流入% 固定全單口徑(不隨級距選擇改變)
          <template v-if="hasNextDay">・回看歷史日多出「隔天開/收/最高%」三欄</template>
        </p>
      </div>

      <div class="controls">
        <div class="btn-group">
          <button
            v-for="opt in viewOptions"
            :key="opt.key"
            class="toggle-btn"
            :class="{ active: opt.view === viewMode && opt.value === valueMode }"
            @click="selectView(opt.view, opt.value)"
          >
            {{ opt.label }}
          </button>
        </div>
        <div class="btn-group">
          <button
            v-for="opt in filterOptions"
            :key="opt.key"
            class="toggle-btn small"
            :class="{ active: amountFilter === opt.value }"
            @click="amountFilter = opt.value"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>
    </div>

    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th class="col-name" @click="toggleSort('label')">
              {{ viewMode === 'sector' ? '族群' : '個股' }}
              <span v-if="sortKey === 'label'" class="sort-arrow">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
            </th>
            <th @click="toggleSort('net')">
              淨流入
              <span v-if="sortKey === 'net'" class="sort-arrow">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
            </th>
            <th @click="toggleSort('netPct')">
              淨流入%
              <span v-if="sortKey === 'netPct'" class="sort-arrow">{{ sortDir === 'asc' ? '▲' : '▼' }}</span>
            </th>
            <th @click="toggleSort('net_xl')">特大單</th>
            <th @click="toggleSort('net_l')">大單</th>
            <th @click="toggleSort('net_m')">中單</th>
            <th @click="toggleSort('net_s')">小單</th>
            <th @click="toggleSort('amount')">成交值</th>
            <template v-if="hasNextDay">
              <th class="col-next" @click="toggleSort('nextOpenPct')">隔天開%</th>
              <th class="col-next" @click="toggleSort('nextClosePct')">隔天收%</th>
              <th class="col-next" @click="toggleSort('nextHighPct')">隔天最高%</th>
            </template>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in sortedRows" :key="row.label" class="data-row" @click="onRowClick(row)">
            <td class="col-name">{{ row.label }}</td>
            <td :class="signClass(row.net)">{{ formatSigned(row.net) }}</td>
            <td :class="signClass(row.netPct)">{{ formatSigned(row.netPct, false) }}</td>
            <td :class="signClass(row.net_xl)">{{ formatSigned(row.net_xl) }}</td>
            <td :class="signClass(row.net_l)">{{ formatSigned(row.net_l) }}</td>
            <td :class="signClass(row.net_m)">{{ formatSigned(row.net_m) }}</td>
            <td :class="signClass(row.net_s)">{{ formatSigned(row.net_s) }}</td>
            <td class="amount-cell">{{ Math.round(row.amount / 1e8) }}</td>
            <template v-if="hasNextDay">
              <td class="col-next" :class="signClass(row.nextOpenPct)">{{ formatNext(row.nextOpenPct) }}</td>
              <td class="col-next" :class="signClass(row.nextClosePct)">{{ formatNext(row.nextClosePct) }}</td>
              <td class="col-next" :class="signClass(row.nextHighPct)">{{ formatNext(row.nextHighPct) }}</td>
            </template>
          </tr>
          <tr v-if="!sortedRows.length">
            <td :colspan="hasNextDay ? 11 : 8" class="empty-cell">這個篩選條件下沒有資料。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useNextDaySummary } from '../composables/useNextDaySummary'

const props = defineProps({
  sectorRows: { type: Array, default: () => [] }, // useAggregation().sectorRanking
  stockRows: { type: Array, default: () => [] }, // useAggregation().stockRanking
  date: { type: String, default: '' },
  finalDates: { type: Array, default: () => [] },
  // 目前查看的這一天、每檔股票的收盤價(decoded.lastPrice)——已收盤日期才有值，
  // 拿來當「隔天%」的計算基準(隔天開/收/最高 相對「這天收盤」的漲跌幅)。
  lastPriceMap: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['drill-down'])

const viewMode = ref('sector') // sector | stock
const valueMode = ref('abs') // abs | pct  (只影響預設排序欄位，兩種數字表格上都看得到)
const amountFilter = ref(0) // 門檻(元)：0 = 全部
const sortKey = ref('net')
const sortDir = ref('desc')

const viewOptions = [
  { key: 'sector-abs', view: 'sector', value: 'abs', label: '族群・億' },
  { key: 'sector-pct', view: 'sector', value: 'pct', label: '族群・%' },
  { key: 'stock-abs', view: 'stock', value: 'abs', label: '個股・億' },
  { key: 'stock-pct', view: 'stock', value: 'pct', label: '個股・%' },
]

const filterOptions = [
  { key: 'all', value: 0, label: '全部' },
  { key: 'ge10', value: 1_000_000_000, label: '≥10億' },
  { key: 'ge39', value: 3_900_000_000, label: '≥39億' },
  { key: 'ge100', value: 10_000_000_000, label: '≥100億' },
]

function toggleSort(key) {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortDir.value = 'desc'
  }
}

// 切換「族群・%」「個股・%」時，預設排序欄位跟著換成淨流入%，體感比較直覺
// (「億」模式預設看淨流入絕對金額排序，「%」模式預設看淨流入%排序)；使用者
// 之後自己點別的欄位排序，就照使用者的選擇，不會被這裡蓋掉。
function selectView(view, value) {
  const changed = viewMode.value !== view || valueMode.value !== value
  viewMode.value = view
  valueMode.value = value
  if (changed) {
    sortKey.value = value === 'pct' ? 'netPct' : 'net'
    sortDir.value = 'desc'
  }
}

const dateRef = computed(() => props.date)
const finalDatesRef = computed(() => props.finalDates)
const { summary: nextSummary } = useNextDaySummary(dateRef, finalDatesRef)

const hasNextDay = computed(() => !!nextSummary.value)

// 單一股票的「隔天開/收/最高%」：都是相對「這天收盤價」(lastPriceMap[code])算的。
function nextDayPctFor(code) {
  if (!nextSummary.value) return { nextOpenPct: null, nextClosePct: null, nextHighPct: null }
  const base = props.lastPriceMap[code]
  if (!(base > 0)) return { nextOpenPct: null, nextClosePct: null, nextHighPct: null }
  const openP = nextSummary.value.open_price?.[code]
  const closeP = nextSummary.value.last_price?.[code]
  const highP = nextSummary.value.high_price?.[code]
  const pct = (p) => (p != null ? ((p - base) / base) * 100 : null)
  return { nextOpenPct: pct(openP), nextClosePct: pct(closeP), nextHighPct: pct(highP) }
}

const enrichedStockRows = computed(() => {
  return props.stockRows.map((r) => ({
    ...r,
    label: `${r.code} ${r.stockName}`,
    ...nextDayPctFor(r.code),
  }))
})

// 族群列的「隔天%」：依「這天」該族群底下每檔股票的成交值加權平均個股的
// 隔天%——成交值愈大的股票，對這個族群代表數字的影響愈大，比單純平均更
// 不容易被一檔冷門小型股的暴漲暴跌誤導。
const enrichedSectorRows = computed(() => {
  const stocksBySector = {}
  for (const r of enrichedStockRows.value) {
    ;(stocksBySector[r.sector] ||= []).push(r)
  }

  function weightedAvg(list, key) {
    let wsum = 0
    let vsum = 0
    for (const s of list) {
      if (s[key] == null) continue
      const w = s.amount || 0
      wsum += w
      vsum += w * s[key]
    }
    return wsum > 0 ? vsum / wsum : null
  }

  return props.sectorRows.map((r) => {
    const stocks = stocksBySector[r.sector] || []
    return {
      ...r,
      label: r.name,
      nextOpenPct: hasNextDay.value ? weightedAvg(stocks, 'nextOpenPct') : null,
      nextClosePct: hasNextDay.value ? weightedAvg(stocks, 'nextClosePct') : null,
      nextHighPct: hasNextDay.value ? weightedAvg(stocks, 'nextHighPct') : null,
    }
  })
})

const activeRows = computed(() => (viewMode.value === 'sector' ? enrichedSectorRows.value : enrichedStockRows.value))

const filteredRows = computed(() => activeRows.value.filter((r) => r.amount >= amountFilter.value))

const sortedRows = computed(() => {
  const key = sortKey.value
  const dir = sortDir.value === 'asc' ? 1 : -1
  return [...filteredRows.value].sort((a, b) => {
    const av = a[key]
    const bv = b[key]
    if (av == null && bv == null) return 0
    if (av == null) return 1 // null(沒有隔天資料)排到最後，不管排序方向
    if (bv == null) return -1
    if (typeof av === 'string') return av.localeCompare(bv) * dir
    return (av - bv) * dir
  })
})

function onRowClick(row) {
  emit('drill-down', row.sector)
}

function signClass(v) {
  if (v == null) return ''
  // 這張表是「淨流入(資金流向)」的排行，紅=流入(正)、藍=流出(負)——沿用
  // 你給的參考圖配色；跟畫面最上面統計方塊「藍色=正/紅色=負」剛好相反，
  // 那邊是舊的配色選擇，這裡刻意配合參考圖，兩處目前不一致，之後想統一
  // 成同一套配色的話再說一聲。
  return v > 0 ? 'text-pos' : v < 0 ? 'text-neg' : ''
}

function formatSigned(v, isMoney = true) {
  if (v == null) return '—'
  const num = isMoney ? v / 1e8 : v
  const sign = num > 0 ? '+' : ''
  return `${sign}${num.toFixed(2)}`
}

function formatNext(v) {
  if (v == null) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}`
}

</script>

<style scoped>
.ranking-panel {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.head-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}

.panel-title {
  margin: 0 0 4px 0;
  font-size: 1.1rem;
  font-weight: 500;
  color: #cbd5e1;
}

.sub-note {
  margin: 0;
  font-size: 0.75rem;
  color: #64748b;
}

.controls {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-end;
}

.btn-group {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.toggle-btn {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #94a3b8;
  border-radius: 6px;
  padding: 5px 12px;
  font-size: 0.8rem;
  cursor: pointer;
}

.toggle-btn.small {
  padding: 3px 9px;
  font-size: 0.72rem;
}

.toggle-btn:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #e2e8f0;
}

.toggle-btn.active {
  background: rgba(56, 189, 248, 0.18);
  border-color: rgba(56, 189, 248, 0.5);
  color: #38bdf8;
}

.table-scroll {
  overflow-x: auto;
  max-height: 520px;
  overflow-y: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
  white-space: nowrap;
}

thead th {
  position: sticky;
  top: 0;
  background: #10131f;
  text-align: right;
  color: #94a3b8;
  font-weight: 500;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

thead th:hover {
  color: #e2e8f0;
}

thead th.col-name {
  text-align: left;
}

.sort-arrow {
  font-size: 0.65rem;
  margin-left: 2px;
}

tbody td {
  text-align: right;
  padding: 7px 12px;
  color: #cbd5e1;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}

td.col-name {
  text-align: left;
  color: #e2e8f0;
}

tr.data-row {
  cursor: pointer;
}

tr.data-row:hover td {
  background: rgba(255, 255, 255, 0.04);
}

.text-pos {
  color: #fb7185;
}

.text-neg {
  color: #38bdf8;
}

.amount-cell {
  color: #94a3b8;
}

.col-next {
  color: #a3a3c2;
}

.empty-cell {
  text-align: center;
  color: #64748b;
  padding: 20px;
}
</style>
