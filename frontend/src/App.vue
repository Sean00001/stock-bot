<template>
  <Login v-if="!auth.state.checked || !auth.state.authenticated" @logged-in="onLoggedIn" />

  <div v-else class="dashboard">
    <header class="top-bar glass-panel">
      <div class="logo">
        <h1 v-if="!controls.sector">盤中資金流向・逐筆 (全市場)</h1>
        <h1 v-else @click="controls.sector = null" style="cursor: pointer; color: #0ea5e9">
          &larr; 返回大盤 | {{ controls.sector }} 族群資金流向
        </h1>
      </div>

      <div class="date-control">
        <select v-model="selectedDate" @change="onDateChange">
          <option v-for="d in dateOptions" :key="d" :value="d">{{ d }}</option>
        </select>
        <span class="status-tag" :class="snap.status.value">{{ statusLabel }}</span>
        <span class="asof" v-if="snap.snapshot.value">更新於 {{ formatAsof(snap.snapshot.value.asof) }}</span>
        <button class="logout" @click="onLogout">登出</button>
      </div>
    </header>

    <ControlsBar :model-value="controls" @update:model-value="onControlsUpdate" />

    <div class="stats-bar">
      <div class="stat-box glass-panel">
        <div class="label">族群/個股成交值</div>
        <div class="value">{{ formatMoney(totals.amount) }}</div>
      </div>
      <div class="stat-box glass-panel">
        <div class="label">淨流入總計</div>
        <div class="value" :class="totals.net >= 0 ? 'text-blue' : 'text-red'">{{ formatMoney(totals.net) }}</div>
      </div>
      <div class="stat-box glass-panel">
        <div class="label">特大單</div>
        <div class="value" :class="totals.net_xl >= 0 ? 'text-blue' : 'text-red'">{{ formatMoney(totals.net_xl) }}</div>
      </div>
      <div class="stat-box glass-panel">
        <div class="label">大單</div>
        <div class="value" :class="totals.net_l >= 0 ? 'text-blue' : 'text-red'">{{ formatMoney(totals.net_l) }}</div>
      </div>
      <div class="stat-box glass-panel">
        <div class="label">中單</div>
        <div class="value" :class="totals.net_m >= 0 ? 'text-blue' : 'text-red'">{{ formatMoney(totals.net_m) }}</div>
      </div>
      <div class="stat-box glass-panel">
        <div class="label">小單</div>
        <div class="value" :class="totals.net_s >= 0 ? 'text-blue' : 'text-red'">{{ formatMoney(totals.net_s) }}</div>
      </div>
    </div>

    <div class="main-content" :class="{ 'main-content--drilled': !!controls.sector }">
      <div class="left-panel glass-panel">
        <h3 class="panel-title">累積淨流入走勢 (可點擊線條下鑽)</h3>
        <LineChart
          :cumulative="cumulativeSeries"
          :market-open-ts="snap.snapshot.value?.marketOpenTs || 0"
          @line-click="handleDrillDown"
        />
      </div>

      <div class="right-panel glass-panel">
        <h3 class="panel-title">資金流向圖 (可點擊區塊與線條下鑽)</h3>
        <SankeyChart :flow-data="rows" @node-click="handleDrillDown" />
      </div>

      <StockPricePanel v-if="controls.sector" :stocks="stockPanels" :sector-label="controls.sector" />
    </div>

    <div class="ratio-row">
      <div class="ratio-panel glass-panel">
        <h3 class="panel-title">淨流入占成交值比走勢 (可點擊線條下鑽)</h3>
        <RatioChart
          :ratio="ratioSeries"
          :market-open-ts="snap.snapshot.value?.marketOpenTs || 0"
          @line-click="handleDrillDown"
        />
      </div>
    </div>

    <p v-if="snap.status.value === 'error'" class="error-banner">
      資料讀取失敗：{{ snap.errorMsg.value }}
    </p>
    <p v-else-if="snap.status.value === 'not_found'" class="error-banner">
      {{ selectedDate }} 還沒有資金流向資料（worker 可能還沒跑，或這天沒開盤）。
    </p>
  </div>
</template>

<script setup>
import { reactive, ref, computed, onMounted, watch } from 'vue'
import Login from './components/Login.vue'
import ControlsBar from './components/ControlsBar.vue'
import SankeyChart from './components/SankeyChart.vue'
import LineChart from './components/LineChart.vue'
import RatioChart from './components/RatioChart.vue'
import StockPricePanel from './components/StockPricePanel.vue'
import { useAuth } from './composables/useAuth'
import { useFlowSnapshot } from './composables/useFlowSnapshot'
import { useAggregation } from './composables/useAggregation'

const auth = useAuth()
const snap = useFlowSnapshot()

const controls = reactive({
  interval: 'open', // 統計區間
  resolution: '1m', // 解析度
  sector: null, // 下鑽後的族群名稱，null = 全市場(族群層)
})

const { rows, totals, cumulativeSeries, ratioSeries, stockPanels } = useAggregation(
  computed(() => snap.snapshot.value),
  computed(() => controls)
)

const selectedDate = ref('')
const dateOptions = computed(() => {
  const d = snap.availableDates.value
  return [...d.live, ...d.final].sort().reverse()
})

const statusLabel = computed(() => {
  switch (snap.status.value) {
    case 'loading':
      return '載入中...'
    case 'live':
      return '盤中即時'
    case 'final':
      return '整日回放'
    case 'error':
      return '讀取失敗'
    case 'not_found':
      return '無資料'
    default:
      return ''
  }
})

function todayStr() {
  const now = new Date()
  const tzNow = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Taipei' }))
  const yyyy = tzNow.getFullYear()
  const mm = String(tzNow.getMonth() + 1).padStart(2, '0')
  const dd = String(tzNow.getDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}

async function init() {
  await snap.loadDates()
  const today = todayStr()
  const all = [...snap.availableDates.value.live, ...snap.availableDates.value.final].sort()
  let target = today
  if (!all.includes(today)) {
    target = all.length ? all[all.length - 1] : today
  }
  selectedDate.value = target
  await snap.open(target)
}

async function onDateChange() {
  controls.sector = null
  await snap.open(selectedDate.value)
}

async function onLoggedIn() {
  // 備援：如果 watch 那條路徑因為某些理由沒觸發，這裡再補一次。
  if (!initStarted) {
    initStarted = true
    await init()
  }
}

// 主要初始化觸發點：直接盯著登入狀態，不依賴 Login 元件 emit 的事件抵達時機。
// (實測發現 Login 觸發 emit 時，auth.state.authenticated 已經在同一輪更新裡被
//  設成 true，Vue 可能已經先把 <Login> 卸載掉了，導致 emit 的 listener 沒有真的
//  被呼叫到、init() 永遠不會執行、頁面就會卡在「有登入畫面但資料是空的」。)
let initStarted = false
watch(
  () => auth.state.authenticated,
  async (isAuthed) => {
    if (isAuthed && !initStarted) {
      initStarted = true
      await init()
    }
    if (!isAuthed) {
      initStarted = false
    }
  },
  { immediate: true }
)

async function onLogout() {
  snap.stop()
  await auth.logout()
}

function onControlsUpdate(next) {
  // controls 是 reactive()，不能整包重新賦值(const 變數)，只能就地合併屬性
  Object.assign(controls, next)
}

function handleDrillDown(name) {
  if (!controls.sector) {
    // name 目前是「族群名稱」；如果是族群層點下去，就下鑽進該族群
    controls.sector = name
  }
}

function formatMoney(val) {
  if (val === undefined || val === null) return '0 億'
  const sign = val > 0 ? '+' : ''
  const abs = Math.abs(val)
  if (abs >= 100000000) return `${sign}${(val / 100000000).toFixed(2)} 億`
  return `${sign}${(val / 10000).toFixed(2)} 萬`
}

function formatAsof(iso) {
  if (!iso) return ''
  try {
    return iso.slice(11, 19)
  } catch (e) {
    return iso
  }
}

onMounted(() => {
  // 只負責確認 session 狀態；真正的初始化交給上面那個 watch(auth.state.authenticated) 觸發，
  // 避免這裡跟 watch 兩邊都各自呼叫一次 init() 造成重複輪詢。
  auth.checkSession()
})
</script>

<style>
body {
  margin: 0;
  padding: 0;
  background-color: #0f111a;
  color: #e2e8f0;
  font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
}
</style>

<style scoped>
.dashboard {
  padding: 20px;
  max-width: 1600px;
  margin: 0 auto;
}

.glass-panel {
  background: rgba(30, 41, 59, 0.7);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.05);
  border-radius: 12px;
  box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
}

.top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 15px 30px;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}

.logo h1 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
  color: #f8fafc;
}

.date-control {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #94a3b8;
  font-size: 0.85rem;
}

.date-control select {
  background: rgba(15, 17, 26, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  color: #f1f5f9;
  padding: 5px 8px;
}

.status-tag {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 0.75rem;
  background: rgba(255, 255, 255, 0.06);
}

.status-tag.live {
  color: #34d399;
  background: rgba(52, 211, 153, 0.12);
}

.status-tag.final {
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.12);
}

.status-tag.error,
.status-tag.not_found {
  color: #fb7185;
  background: rgba(251, 113, 133, 0.12);
}

.logout {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #cbd5e1;
  border-radius: 6px;
  padding: 5px 10px;
  cursor: pointer;
  font-size: 0.8rem;
}

.stats-bar {
  display: flex;
  gap: 15px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.stat-box {
  flex: 1;
  min-width: 130px;
  padding: 15px 20px;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.stat-box .label {
  font-size: 0.85rem;
  color: #94a3b8;
}

.stat-box .value {
  font-size: 1.25rem;
  font-weight: 700;
  color: #f1f5f9;
}

.text-blue {
  color: #38bdf8 !important;
}

.text-red {
  color: #fb7185 !important;
}

.main-content {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 20px;
  height: 600px;
}

.main-content--drilled {
  /* 個股卡片那一欄(最右邊)原本只分到 1fr(整個 3.7fr 裡不到三成寬度)，
     字都擠到要換行、疊字。加寬到跟左邊主圖差不多寬，卡片裡的文字才有
     空間排成一行。 */
  grid-template-columns: 1.2fr 1fr 1.3fr;
}

.left-panel,
.right-panel {
  padding: 20px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.panel-title {
  margin: 0 0 15px 0;
  font-size: 1.1rem;
  font-weight: 500;
  color: #cbd5e1;
}

.ratio-row {
  margin-top: 20px;
}

.ratio-panel {
  padding: 20px;
  display: flex;
  flex-direction: column;
  height: 340px;
}

.error-banner {
  margin-top: 16px;
  padding: 10px 16px;
  border-radius: 8px;
  background: rgba(251, 113, 133, 0.12);
  color: #fb7185;
  font-size: 0.85rem;
}

@media (max-width: 1100px) {
  .main-content,
  .main-content--drilled {
    grid-template-columns: 1fr;
    height: auto;
  }
  .left-panel,
  .right-panel {
    height: 500px;
  }
  .ratio-panel {
    height: 320px;
  }
}
</style>
