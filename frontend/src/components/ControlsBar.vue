<template>
  <div class="controls-bar glass-panel">
    <div class="group">
      <span class="group-label">統計區間</span>
      <button
        v-for="opt in intervalOptions"
        :key="opt.value"
        class="chip"
        :class="{ active: modelValue.interval === opt.value }"
        @click="update('interval', opt.value)"
      >
        {{ opt.label }}
      </button>
    </div>

    <div class="group">
      <span class="group-label">解析度</span>
      <button
        v-for="opt in resolutionOptions"
        :key="opt.value"
        class="chip"
        :class="{ active: modelValue.resolution === opt.value }"
        @click="update('resolution', opt.value)"
      >
        {{ opt.label }}
      </button>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  modelValue: {
    type: Object,
    required: true, // { interval, resolution, sector }
  },
})
const emit = defineEmits(['update:modelValue'])

const intervalOptions = [
  { value: 'open', label: '開盤累積' },
  { value: '30s', label: '最近30秒' },
  { value: '1m', label: '1分' },
  { value: '5m', label: '5分' },
  { value: '15m', label: '15分' },
  { value: '30m', label: '30分' },
  { value: '60m', label: '60分' },
]

const resolutionOptions = [
  { value: '1s', label: '1秒' },
  { value: '5s', label: '5秒' },
  { value: '15s', label: '15秒' },
  { value: '1m', label: '1分' },
  { value: '5m', label: '5分' },
]

function update(key, value) {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}
</script>

<style scoped>
.controls-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  padding: 12px 20px;
  margin-bottom: 20px;
}

.group {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.group-label {
  color: #94a3b8;
  font-size: 0.8rem;
  margin-right: 4px;
}

.chip {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  color: #cbd5e1;
  font-size: 0.78rem;
  padding: 5px 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.chip:hover {
  border-color: rgba(14, 165, 233, 0.5);
}

.chip.active {
  background: #0ea5e9;
  border-color: #0ea5e9;
  color: #041018;
  font-weight: 600;
}
</style>
