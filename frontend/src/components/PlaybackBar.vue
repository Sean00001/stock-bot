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
// @input 事件在拖曳時可能一秒觸發幾十次，如果每次都直接 emit 出去讓
// App.vue 整套聚合重算一次，會多做很多用不到的中間結果(使用者根本沒看到
// 那些中間畫面就滑過去了)，等於白算。這裡用 requestAnimationFrame 節流：
// 同一畫面更新週期內(約 1/60 秒)不管收到幾次 @input，只在畫面真的要畫下
// 一幀之前 emit 最新的一個值，讓瀏覽器可以正常渲染的速度為準，而不是被
// 滑鼠事件的觸發頻率牽著跑。
let scrubRafId = null

function onScrub(val) {
  if (isPlaying.value) stopLoop()
  scrubValue.value = Number(val)
  if (scrubRafId == null) {
    scrubRafId = requestAnimationFrame(() => {
      scrubRafId = null
      if (scrubValue.value != null) emit('update:modelValue', scrubValue.value)
    })
  }
}

function onScrubEnd() {
  // 保險：萬一放開滑桿那一刻，前一個 rAF 還沒觸發，這裡直接補發一次，確保
  // 放開當下畫面一定跟滑桿停下來的位置同步，不會因為節流漏掉最後一次更新。
  if (scrubRafId != null) {
    cancelAnimationFrame(scrubRafId)
    scrubRafId = null
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

// ---- 播放迴圈：用 requestAnimationFrame 量測真實經過時間，換算成盤中秒數
//      前進多少，而不是用固定 setInterval 累加(分頁切到背景、掉幀時容易跟
//      真實時間對不起來)。每一幀都會讓 App.vue 整套聚合重新算一次，資料量
//      大(全市場、逐筆)時可能會感覺到頓——如果覺得播放不夠順，可以把下面
//      這個 rAF 迴圈改成用 setInterval(200ms 左右)代替，用較低的更新頻率
//      換取比較穩定的畫面。
let rafId = null
let lastTs = null

function loopStep(ts) {
  if (!isPlaying.value) return
  if (lastTs == null) lastTs = ts
  const dtSec = (ts - lastTs) / 1000
  lastTs = ts

  const cur = props.modelValue == null ? props.maxOffset : props.modelValue
  const next = cur + dtSec * BASE_SIM_SEC_PER_SEC * speed.value

  if (next >= props.maxOffset) {
    // 播到最新資料了。如果這份資料還在直播中(maxOffset 之後可能還會繼續長
    // 大)，直接切回「跟著即時走」模式，比停在一個很快就會過期的固定點更合理；
    // 已經收盤的日期播到底就單純停在最後一秒。
    emit('update:modelValue', props.maxOffset)
    stopLoop()
    return
  }

  emit('update:modelValue', next)
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
}

function togglePlay() {
  if (isPlaying.value) stopLoop()
  else startLoop()
}

onBeforeUnmount(() => {
  stopLoop()
  if (scrubRafId != null) cancelAnimationFrame(scrubRafId)
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
