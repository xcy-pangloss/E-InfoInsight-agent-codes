import { ref } from 'vue'

// 全局主题状态：isDark + 持久化 + 切换 html.dark
const isDark = ref(false)

function applyClass() {
  document.documentElement.classList.toggle('dark', isDark.value)
}

// 从 localStorage 恢复，默认跟随系统偏好
const saved = localStorage.getItem('theme')
if (saved) {
  isDark.value = saved === 'dark'
} else {
  isDark.value = window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}
applyClass()

export function toggleTheme() {
  isDark.value = !isDark.value
  localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
  applyClass()
}

export function useTheme() {
  return { isDark, toggleTheme }
}
