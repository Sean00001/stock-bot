import { reactive } from 'vue'

const state = reactive({
  authenticated: false,
  username: null,
  checked: false,
})

async function checkSession() {
  try {
    const res = await fetch('/api/session/', { credentials: 'include' })
    const json = await res.json()
    state.authenticated = !!json.authenticated
    state.username = json.username || null
  } catch (e) {
    state.authenticated = false
  } finally {
    state.checked = true
  }
  return state.authenticated
}

async function login(username, password) {
  const res = await fetch('/api/login/', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  let json = {}
  try {
    json = await res.json()
  } catch (e) {
    // ignore
  }
  if (res.ok && json.status === 'ok') {
    state.authenticated = true
    state.username = json.username || username
    return { ok: true }
  }
  return { ok: false, error: json.error || '登入失敗' }
}

async function logout() {
  try {
    await fetch('/api/logout/', { method: 'POST', credentials: 'include' })
  } finally {
    state.authenticated = false
    state.username = null
  }
}

export function useAuth() {
  return { state, checkSession, login, logout }
}
