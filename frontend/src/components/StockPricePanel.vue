<template>
  <div class="price-panel glass-panel">
    <h3 class="panel-title">{{ titleText }}</h3>
    <div v-if="filtered.length" class="cards">
      <StockPriceCard v-for="s in filtered" :key="s.code" v-bind="s" />
    </div>
    <p v-else class="empty">{{ side === 'out' ? '這個族群目前沒有資金流出的個股。' : '這個族群目前沒有資金流入的個股。' }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import StockPriceCard from './StockPriceCard.vue'

const props = defineProps({
  stocks: { type: Array, default: () => [] },
  sectorLabel: { type: String, default: '' },
  // out = 資金流出(淨流入為負，畫在主圖左邊，對齊資金流向圖「資金被抽走」
  // 那一側) / in = 資金流入(淨流入為正，畫在主圖右邊，對齊「資金流進去」)
  side: { type: String, default: 'in' },
})

// stockPanels 本來就已經依 |大單以上淨流入| 由大到小排過序，這裡只是照方向
// 篩選、不重新排序，維持「流向越明顯排越前面」的順序。
const filtered = computed(() => props.stocks.filter((s) => (props.side === 'out' ? s.netAboveL < 0 : s.netAboveL >= 0)))

const titleText = computed(() => `${props.sectorLabel} ${props.side === 'out' ? '資金流出' : '資金流入'}`)
</script>

<style scoped>
.price-panel {
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

.cards {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-right: 4px;
}

.empty {
  color: #64748b;
  font-size: 0.85rem;
}
</style>
