<template>
  <div class="playback-bar glass-panel">
    <button class="play-btn" @click="togglePlay" :disabled="!hasData" :title="isPlaying ? '暫停' : '播放'">
      <span v-if="isPlaying">❚❚</span>
      <span v-else>▶</span>
    </button>

    <input
      class="scrubber"
      type="range"
      min="0"
      :max="Math.max(maxOffset, 1)"
      step="1"
      :value="displayOffset"
      :disabled="!hasData"
      @input="onScrub($event.target.value)"
      @change="onScrubEnd"
    />

    <span class="clock">{{ clockLabel }}</span>

    <div class="speed-group">
      <button
        v-for="s in speedOptions"
        :key="s"
        class="chip"
        :class="{ active: speed === s }"
        @click="speed = s"
      >
        {{ s }}×
      </button>
    </div>

    <button class="live-btn" v-if="modelValue != null" @click="backToLive" title="回到即時/最新資料">
      回到即時 ↦
    </button>
  </div>
</template>

<script setup>
import { ref, computed, onBeforeUnmount, watch } from 'vue'

// modelValue: 目前的播放時間點(開盤後秒數)，null = 跟著即時資料走(不在回放)。
// maxOffset: 目前這份快照實際有資料到的秒數(useAggregation 的 liveMaxOffset)，
//            回放/拖動的上限；直播中的日期這個值會一直變大。
// marketOpenTs: 用來把「開盤後秒數」換算成時鐘時間顯示。
const props = defineProps({
  modelValue: { type: Number, default: null },
  maxOffset: { type: Number, default: 0 },
  marketOpenTs: { type: Number, default: 0 },
})
const emit = defineEmits(['update:modelValue'])

// 播放速度倍率。這裡的「倍率」不是嚴格的「1 秒真實時間 = 1 秒盤中時間」，
// 那樣播一整天(開盤到 13:30，約 16200 秒)要 4.5 小時，對回放來說太慢沒有
// 意義。改成 1× = 每 1 秒真實時間前進 BASE_SIM_SEC_PER_SEC 秒盤中時間，
// 這樣 1× 大約 9 分鐘可以看完整天，4×/8× 更快、0.5× 更慢，跟參考網站
// 「回放」的體感速度數量級接近。不滿意這個節奏可以只改這一個常數調整。
const BASE_SIM_SEC_PER_SEC = 30
const speedOptions = [0.5, 1, 2, 4, 8]
const speed = ref(1)

const isPlaying = ref(false)
const hasData = computed(() => props.maxOffset > 0)

// 拖動進度條的當下，先用本地暫存值即時反應 UI(滑桿位置、時鐘文字)，放開
// 才真的 emit 出去——避免每移動 1px 就觸發一次外面 App.vue 的重新聚合
// (那是同步、資料量大時會讓畫面卡頓的重運算)。播放中則直接用 modelValue，
// 因為播放本來就是每個 tick 都要重算一次。
const scrubValue = ref(null)
const displayOffset = computed(() => {
  if (scrubValue.value != null) return scrubValue.value
  return props.modelValue == null ? props.maxOffset : props.modelValue
})

// 拖動滑桿時想要「邊拖邊動」(圖表即時跟著滑桿位置更新)，但滑桿原生的
// @input 事件在拖曳時可能一秒觸發幾十次；如果每次都直接 emit 出去讓
// App.vue 整套聚合(全市場 rows/累積走勢/比例走勢/排行榜)重算一次、再讓
// Sankey/LineChart/RatioChart 這三張 ECharts 圖都重新 setOption 一次，
// 全市場規模下這一整套本來就不便宜，用 requestAnimationFrame(最多每秒
// 60 次)去觸發，實測會把瀏覽器主執行緒塞爆、感覺整頁卡死。
//
// 改成用時間節流：拖曳中最多每 SCRUB_THROTTLE_MS 毫秒才真的觸發一次重算，
// 不是每次滑鼠移動、也不是每一幀都算，用比較低的更新頻率換取拖曳時畫面
// 還能正常反應，不會卡死。放開滑桿那一刻一定會補發最後位置，確保停下來
// 時看到的一定是滑桿實際停的地方，不會因為節流漏掉最後一次更新。
//
// 如果 300ms 這個節奏還是感覺卡，可以調大這個數字(例如 500ms)，用「不那
// 麼即時」換取更順；调到很大(例如 99999)幾乎就等於「放開才更新」。
const SCRUB_THROTTLE_MS = 300
let scrubTimer = null

function onScrub(val) {
  if (isPlaying.value) stopLoop()
  scrubValue.value = Number(val)
  if (scrubTimer == null) {
    scrubTimer = setTimeout(() => {
      scrubTimer = null
      if (scrubValue.value != null) emit('update:modelValue', scrubValue.value)
    }, SCRUB_THROTTLE_MS)
  }
}

function onScrubEnd() {
  if (scrubTimer != null) {
    clearTimeout(scrubTimer)
    scrubTimer = null
  }
  if (scrubValue.value != null) {
    emit('update:modelValue', scrubValue.value)
    scrubValue.value = null
  }
}

function backToLive() {
  stopLoop()
  scrubValue.value = null
  emit('update:modelValue', null)
}

// ---- 播放迴圈：用 requestAnimationFrame 量測真實經過時間(每一幀都算，這
//      步很便宜，純數字加法)，換算成盤中秒數該前進多少；但真正觸發 emit
//      (害 App.vue 整套聚合+三張 ECharts 圖重算一次的那個動作)跟拖曳滑桿
//      一樣，用 SCRUB_THROTTLE_MS 節流，不是每一幀都 emit。這樣播放速度
//      本身(BASE_SIM_SEC_PER_SEC x 倍率)不會因為節流而變慢——時間累積是
//      每一幀都在算的，只是畫面/圖表沒有每一幀都重畫，用比較低的重算頻率
//      換取播放時不會卡頓。
let rafId = null
let lastTs = null
let lastEmitTs = null
let pendingOffset = null

function loopStep(ts) {
  if (!isPlaying.value) return
  if (lastTs == null) {
    lastTs = ts
    lastEmitTs = ts
    pendingOffset = props.modelValue == null ? props.maxOffset : props.modelValue
  }
  const dtSec = (ts - lastTs) / 1000
  lastTs = ts

  pendingOffset += dtSec * BASE_SIM_SEC_PER_SEC * speed.value

  if (pendingOffset >= props.maxOffset) {
    // 播到最新資料了。如果這份資料還在直播中(maxOffset 之後可能還會繼續長
    // 大)，直接切回「跟著即時走」模式，比停在一個很快就會過期的固定點更合理；
    // 已經收盤的日期播到底就單純停在最後一秒。
    emit('update:modelValue', props.maxOffset)
    stopLoop()
    return
  }

  if (ts - lastEmitTs >= SCRUB_THROTTLE_MS) {
    lastEmitTs = ts
    emit('update:modelValue', pendingOffset)
  }

  rafId = requestAnimationFrame(loopStep)
}

function startLoop() {
  isPlaying.value = true
  lastTs = null
  // 如果目前是「跟著即時走」(modelValue null)，播放要從現在這個位置開始往
  // 前推進，而不是從 0 開始重播一次。
  if (props.modelValue == null) emit('update:modelValue', props.maxOffset)
  rafId = requestAnimationFrame(loopStep)
}

function stopLoop() {
  isPlaying.value = false
  if (rafId != null) cancelAnimationFrame(rafId)
  rafId = null
  lastTs = null
  lastEmitTs = null
  pendingOffset = null
}

function togglePlay() {
  if (isPlaying.value) stopLoop()
  else startLoop()
}

onBeforeUnmount(() => {
  stopLoop()
  if (scrubTimer != null) clearTimeout(scrubTimer)
})

// 換日期、或資料被整包換掉時(外面的 App.vue 會在切日期時順手把 modelValue
// 重設成 null)，播放狀態也要跟著停掉，不然會用舊日期的節奏繼續推進新日期
// 的播放時間點。
watch(
  () => props.marketOpenTs,
  () => stopLoop()
)

function formatClock(offsetSec) {
  if (!props.marketOpenTs || offsetSec == null) return '--:--:--'
  const d = new Date(props.marketOpenTs * 1000 + offsetSec * 1000)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

const clockLabel = computed(() => {
  const label = formatClock(displayOffset.value)
  return props.modelValue == null ? `${label}(即時)` : label
})
</script>

<style scoped>
.playback-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 20px;
  margin-bottom: 20px;
}

.play-btn {
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.15);
  background: #0ea5e9;
  color: #041018;
  font-size: 0.85rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.play-btn:disabled {
  background: rgba(255, 255, 255, 0.06);
  color: #64748b;
  cursor: not-allowed;
}

.scrubber {
  flex: 1 1 auto;
  min-width: 0;
  accent-color: #0ea5e9;
  cursor: pointer;
}

.scrubber:disabled {
  cursor: not-allowed;
}

.clock {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
  color: #f1f5f9;
  font-size: 0.95rem;
  min-width: 108px;
  text-align: right;
}

.speed-group {
  flex: 0 0 auto;
  display: flex;
  gap: 4px;
}

.chip {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  color: #cbd5e1;
  font-size: 0.75rem;
  padding: 4px 10px;
  cursor: pointer;
}

.chip.active {
  background: #0ea5e9;
  border-color: #0ea5e9;
  color: #041018;
  font-weight: 600;
}

.live-btn {
  flex: 0 0 auto;
  background: transparent;
  border: 1px solid rgba(56, 189, 248, 0.4);
  color: #38bdf8;
  border-radius: 6px;
  padding: 5px 10px;
  font-size: 0.78rem;
  cursor: pointer;
  white-space: nowrap;
}

@media (max-width: 700px) {
  .playback-bar {
    flex-wrap: wrap;
  }
  .scrubber {
    order: 1;
    flex: 1 1 100%;
  }
}
</style>
