<template>
  <div class="price-panel glass-panel">
    <h3 class="panel-title">{{ titleText }}</h3>
    <div v-if="filtered.length" class="cards">
      <StockPriceCard v-for="s in filtered" :key="s.code" v-bind="s" />
    </div>
    <p v-else class="empty">這個族群目前沒有個股資料。</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import StockPriceCard from './StockPriceCard.vue'

const props = defineProps({
  stocks: { type: Array, default: () => [] },
  sectorLabel: { type: String, default: '' },
  // left / right：純粹拿來把同一份股票清單平均分成兩半，跟資金流入/流出
  // 方向無關——不然像「電信」這種族群常常只有一兩檔在流出，硬要依方向分
  // 左右的話，某一邊會只剩一檔、看起來很空。
  side: { type: String, default: 'left' },
})

// stockPanels 本來就已經依 |大單以上淨流入| 由大到小排過序；用交錯(下標
// 奇偶)分配到左右兩側，讓兩邊都混得到排名靠前跟靠後的股票，數量也最多只
// 差一檔，比單純切前半/後半更平均、不會有一邊都是大咖一邊都是小咖。
const filtered = computed(() => {
  const offset = props.side === 'right' ? 1 : 0
  return props.stocks.filter((_, idx) => idx % 2 === offset)
})

const titleText = computed(() => `${props.sectorLabel} 個股股價走勢`)
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
  gap: 16px;
  padding-right: 4px;
}

.empty {
  color: #64748b;
  font-size: 0.85rem;
}
</style>
