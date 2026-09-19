<template>
  <Login v-if="!auth.state.checked || !auth.state.authenticated" @logged-in="onLoggedIn" />

  <!-- 下鑽後左右加開個股卡片欄，是「外掛」在整個原本畫面(.dashboard)的左右
       兩側，讓整頁變寬；.dashboard 內部(標題、控制列、統計方塊、兩張主圖、
       比例圖)維持跟未下鑽時一模一樣的寬度，不會因為多了卡片而被壓縮。 -->
  <div v-else class="page" :class="{ 'page--drilled': !!controls.sector }">
    <!-- 切換族群/回到大盤/換日期都會觸發一次同步的全量重新聚合計算，資料量大
         時這段計算會佔用主執行緒讓畫面「凍住」一下；用這層遮罩+轉圈圈在計算
         開始前先畫出來，讓使用者知道系統還活著，不是當機，算完才收掉。 -->
    <div v-if="switching" class="loading-overlay">
      <div class="spinner"></div>
      <div class="loading-text">{{ switchingLabel }}</div>
    </div>

    <StockPricePanel v-if="controls.sector" :stocks="stockPanels" :sector-label="controls.sector" side="left" />

    <div class="dashboard">
      <header class="top-bar glass-panel">
        <div class="logo">
          <h1 v-if="!controls.sector">盤中資金流向・逐筆 (全市場)</h1>
          <h1 v-else @click="returnToMarket" style="cursor: pointer; color: #0ea5e9">
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

      <PlaybackBar
        :model-value="controls.playhead"
        @update:model-value="(val) => (controls.playhead = val)"
        :max-offset="liveMaxOffset"
        :market-open-ts="snap.snapshot.value?.marketOpenTs || 0"
      />

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

      <div class="main-content">
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
          <div class="sankey-flex">
            <SankeyChart :flow-data="rows" @node-click="handleDrillDown" />
          </div>
          <OffMarketFlowChart :series="offMarketFlowSeries" :market-open-ts="snap.snapshot.value?.marketOpenTs || 0" />
        </div>
      </div>

      <div class="ranking-row">
        <RankingTable
          :sector-rows="sectorRanking"
          :stock-rows="stockRanking"
          :active-sector="controls.sector"
          :date="selectedDate"
          :final-dates="snap.availableDates.value.final"
          :last-price-map="snap.snapshot.value?.lastPrice || {}"
          @drill-down="handleRankingDrill"
        />
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

    <StockPricePanel v-if="controls.sector" :stocks="stockPanels" :sector-label="controls.sector" side="right" />
  </div>
</template>

<script setup>
import { reactive, ref, computed, onMounted, watch, nextTick } from 'vue'
import Login from './components/Login.vue'
import ControlsBar from './components/ControlsBar.vue'
import PlaybackBar from './components/PlaybackBar.vue'
import SankeyChart from './components/SankeyChart.vue'
import OffMarketFlowChart from './components/OffMarketFlowChart.vue'
import LineChart from './components/LineChart.vue'
import RatioChart from './components/RatioChart.vue'
import StockPricePanel from './components/StockPricePanel.vue'
import RankingTable from './components/RankingTable.vue'
import { useAuth } from './composables/useAuth'
import { useFlowSnapshot } from './composables/useFlowSnapshot'
import { useAggregation } from './composables/useAggregation'

const auth = useAuth()
const snap = useFlowSnapshot()

const controls = reactive({
  interval: 'open', // 統計區間
  resolution: '1m', // 解析度
  sector: null, // 下鑽後的族群名稱，null = 全市場(族群層)
  playhead: null, // 播放進度條目前停在的開盤後秒數；null = 跟著即時資料走(不在回放)
})

const {
  liveMaxOffset,
  rows,
  totals,
  cumulativeSeries,
  ratioSeries,
  stockPanels,
  sectorRanking,
  stockRanking,
  offMarketFlowSeries,
} = useAggregation(
  computed(() => snap.snapshot.value),
  computed(() => controls)
)

// ---- 切換讀取遮罩 ----
// 「回大盤/下鑽族群/換日期」都會讓下面那幾個 computed(rows/cumulativeSeries/
// ratioSeries/stockPanels...) 重新跑一次全量聚合，資料量大時這段是同步的
// (瀏覽器主執行緒)，跑的當下畫面完全不會更新、看起來像當機。
//
// 因為 computed 是「懶算」的——改 controls.sector 那一刻本身不會馬上觸發計算，
// 是等 Vue 接下來 re-render、樣板真的去讀這些 computed 的值時才會算——所以沒辦
// 法直接包在改值那一行前後計時。這裡用「先讓遮罩真的畫到螢幕上，再讓重算發
// 生」的方式：
//   1. switching = true，等 nextTick() 讓這個變更 flush 進 DOM
//   2. 用兩層 requestAnimationFrame 確保瀏覽器真的畫出這一幀(不只是排進 DOM，
//      是實際 paint 到螢幕上)
//   3. 這時候才真的去改 controls.sector 等值——重算就是在接下來這次 flush
//      裡同步發生，但遮罩已經在畫面上了
//   4. 再 nextTick() 等這次(會卡頓的)重新渲染完成，才把遮罩收掉
const switching = ref(false)
const switchingLabel = ref('')

function paintFrame() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve))
  })
}

async function runHeavyChange(label, mutate) {
  switching.value = true
  switchingLabel.value = label
  await nextTick()
  await paintFrame()
  await mutate()
  await nextTick()
  switching.value = false
}

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
  await runHeavyChange('切換日期中...', async () => {
    controls.sector = null
    controls.playhead = null // 換日期時重設播放進度，避免帶著舊日期的時間點造成混淆
    await snap.open(selectedDate.value)
  })
}

function returnToMarket() {
  runHeavyChange('返回大盤中...', () => {
    controls.sector = null
  })
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
    runHeavyChange(`載入 ${name} 族群資料中...`, () => {
      controls.sector = name
    })
  }
}

// 排行表(RankingTable)的列點擊：跟上面主圖表的 handleDrillDown 不一樣的地方
// 是排行表本身不受目前下鑽狀態影響(永遠顯示全市場排行)，所以不管現在有沒有
// 已經下鑽在某個族群，點排行表的列都要能直接切過去(包含「已下鑽族群 A，又
// 點排行表裡族群 B」這種跨族群切換)；只有點到「目前就已經在看的那個族群」
// 時才不用重算。個股列的 row.sector 是該股票所屬族群，一樣可以直接下鑽進去。
function handleRankingDrill(sectorName) {
  if (!sectorName || sectorName === controls.sector) return
  runHeavyChange(`載入 ${sectorName} 族群資料中...`, () => {
    controls.sector = sectorName
  })
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
/* .page 是最外層：平常(未下鑽)就只有 .dashboard 一個小孩，跟以前效果一樣；
   下鑽後左右多兩個 StockPricePanel 當手足，用 flex 排成一列，.dashboard
   本身寬度、內部版面完全不受影響，整頁自然變寬。 */
.page {
  display: flex;
  justify-content: center;
  align-items: flex-start;
  gap: 20px;
  padding: 20px;
}

.page--drilled {
  align-items: stretch; /* 左右股票欄跟中間欄拉齊到一樣高 */
}

/* 固定滿版，不管 .page 目前多寬(下鑽後會變寬)都蓋得住整個畫面；z-index
   拉高確保蓋過 glass-panel 那些卡片。 */
.loading-overlay {
  position: fixed;
  inset: 0;
  z-index: 999;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  background: rgba(15, 17, 26, 0.72);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
}

.spinner {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: 4px solid rgba(255, 255, 255, 0.15);
  border-top-color: #0ea5e9;
  animation: spin 0.8s linear infinite;
}

.loading-text {
  color: #cbd5e1;
  font-size: 0.95rem;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.dashboard {
  flex: 1 1 auto;
  min-width: 0;
  max-width: 1600px;
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
  /* 不管有沒有下鑽，這一排永遠是固定的 2fr : 1fr，不會因為左右多了個股
     卡片欄就被壓縮——個股卡片是外掛在 .dashboard 外面，跟這裡無關。
     右側面板多加了「場外資金進出」小圖之後高度比較擠，整排高度從 600px
     加到 760px，讓 Sankey 圖跟新的小圖都有足夠空間，左側 LineChart 也
     跟著變高一點，不算浪費。 */
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 20px;
  height: 760px;
}

.left-panel,
.right-panel {
  padding: 20px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* SankeyChart 元件本身用 height:100% 撐滿容器，這裡包一層 flex:1 的 wrapper，
   讓它只吃「扣掉下面場外資金進出小圖」之後剩下的空間，而不是硬撐滿整個
   .right-panel 把新加的小圖擠出去。 */
.sankey-flex {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* StockPricePanel 是 .page 底下跟 .dashboard 平行的手足欄位，這裡用它元件
   根節點自帶的 .price-panel class 直接定寬(Vue scoped CSS 對「模板裡直接
   寫的子元件」也會把父層的 scope 屬性一起蓋上去，所以這樣寫得到)。固定寬
   度 + 跟 .dashboard 一樣高，個股卡片才有足夠空間不會被壓扁。 */
.price-panel {
  flex: 0 0 320px;
  max-width: 320px;
}

.panel-title {
  margin: 0 0 15px 0;
  font-size: 1.1rem;
  font-weight: 500;
  color: #cbd5e1;
}

.ranking-row {
  margin-top: 20px;
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
  .page {
    flex-direction: column;
    align-items: stretch;
  }
  .price-panel {
    flex: 0 0 auto;
    max-width: none;
  }
  .main-content {
    grid-template-columns: 1fr;
    height: auto;
  }
  .left-panel,
  .right-panel {
    height: 640px;
  }
  .ratio-panel {
    height: 320px;
  }
}
</style>
