<template>
  <div class="login-page">
    <form class="login-card glass-panel" @submit.prevent="onSubmit">
      <h1>盤中資金流向</h1>
      <p class="sub">請登入以繼續</p>

      <label>
        帳號
        <input v-model="username" type="text" autocomplete="username" required />
      </label>
      <label>
        密碼
        <input v-model="password" type="password" autocomplete="current-password" required />
      </label>

      <p v-if="error" class="error">{{ error }}</p>

      <button type="submit" :disabled="loading">{{ loading ? '登入中...' : '登 入' }}</button>
    </form>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useAuth } from '../composables/useAuth'

const emit = defineEmits(['logged-in'])

const { login } = useAuth()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

const onSubmit = async () => {
  error.value = ''
  loading.value = true
  try {
    const result = await login(username.value, password.value)
    if (result.ok) {
      emit('logged-in')
    } else {
      error.value = result.error
    }
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #0f111a;
}

.login-card {
  width: 320px;
  padding: 32px 28px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.login-card h1 {
  margin: 0;
  font-size: 1.3rem;
  color: #f8fafc;
  text-align: center;
}

.sub {
  margin: 0 0 6px 0;
  text-align: center;
  color: #94a3b8;
  font-size: 0.85rem;
}

label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #cbd5e1;
  font-size: 0.85rem;
}

input {
  background: rgba(15, 17, 26, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  padding: 10px 12px;
  color: #f1f5f9;
  font-size: 0.95rem;
}

input:focus {
  outline: none;
  border-color: #0ea5e9;
}

button {
  margin-top: 8px;
  background: linear-gradient(135deg, #0ea5e9, #38bdf8);
  border: none;
  border-radius: 8px;
  padding: 11px;
  color: #041018;
  font-weight: 700;
  font-size: 0.95rem;
  cursor: pointer;
}

button:disabled {
  opacity: 0.6;
  cursor: default;
}

.error {
  margin: 0;
  color: #fb7185;
  font-size: 0.85rem;
  text-align: center;
}
</style>
