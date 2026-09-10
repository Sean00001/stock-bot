<template>
  <div class="price-panel glass-panel">
    <h3 class="panel-title">{{ sectorLabel }} — 個股股價走勢</h3>
    <div v-if="stocks.length" class="cards">
      <StockPriceCard v-for="s in stocks" :key="s.code" v-bind="s" />
    </div>
    <p v-else class="empty">這個族群目前還沒有可畫的股價資料。</p>
  </div>
</template>

<script setup>
import StockPriceCard from './StockPriceCard.vue'

defineProps({
  stocks: { type: Array, default: () => [] },
  sectorLabel: { type: String, default: '' },
})
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
