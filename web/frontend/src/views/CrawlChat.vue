<template>
  <div style="display: flex; gap: 16px; height: calc(100vh - 100px)">
    <!-- 左：指令执行 -->
    <el-card style="flex: 1; display: flex; flex-direction: column">
      <template #header>
        <span>爬取指令</span>
        <el-tag v-if="taskStatus.running" type="warning" size="small" style="margin-left: 8px">运行中：{{ taskStatus.label }}</el-tag>
        <el-tag v-else type="info" size="small" style="margin-left: 8px">空闲</el-tag>
        <el-button v-if="taskStatus.running" type="danger" size="small" style="float: right" @click="stopTask">停止</el-button>
      </template>
      <!-- 预设按钮 -->
      <div style="margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap">
        <el-button v-for="p in presets" :key="p.label" size="small" type="primary" @click="usePreset(p)" :disabled="taskStatus.running">
          {{ p.label }}
        </el-button>
      </div>
      <!-- 执行日志 -->
      <div ref="logBox" style="flex: 1; overflow: auto; background: var(--terminal-bg); color: var(--terminal-fg); padding: 12px; border-radius: 4px; font-family: monospace; font-size: 13px; margin-bottom: 12px">
        <div v-if="logs.length === 0" style="color: var(--terminal-muted)">点击预设按钮或在下方输入指令开始</div>
        <div v-for="(line, i) in logs" :key="i" style="white-space: pre-wrap; margin-bottom: 2px">{{ line }}</div>
        <div v-if="taskStatus.running" style="color: var(--terminal-running)">● 运行中...</div>
      </div>
      <!-- 输入框 -->
      <div style="display: flex; gap: 8px">
        <el-input v-model="prompt" placeholder="输入指令，如：增量爬取前50家" @keyup.enter="runPrompt" :disabled="taskStatus.running" />
        <el-button type="primary" @click="runPrompt" :disabled="taskStatus.running || !prompt.trim()">执行</el-button>
        <el-button @click="askHermes" :loading="hermesLoading" :disabled="!prompt.trim()">Hermes建议</el-button>
      </div>
    </el-card>
    <!-- 右：任务清单+记录 -->
    <el-card style="width: 360px; overflow: auto">
      <template #header>任务与记录</template>
      <h5 style="margin: 0 0 8px">可执行任务</h5>
      <el-table :data="taskList" size="small" style="width: 100%; margin-bottom: 16px">
        <el-table-column prop="label" label="名称" width="90" />
        <el-table-column prop="desc" label="说明" />
      </el-table>
      <h5 style="margin: 0 0 8px">历史记录</h5>
      <el-table :data="records" size="small" style="width: 100%">
        <el-table-column prop="task_type" label="类型" width="80" />
        <el-table-column prop="status" label="状态" width="70">
          <template #default="{ row }"><el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="140">
          <template #default="{ row }">{{ (row.created_at || '').slice(0, 16) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, nextTick } from 'vue'
import api from '../api'

const prompt = ref('')
const logs = ref([])
const presets = ref([])
const taskList = ref([])
const records = ref([])
const taskStatus = ref({ running: false })
const hermesLoading = ref(false)
const logBox = ref(null)

let sseController = null
let statusTimer = null

onMounted(async () => {
  const [p, t, r] = await Promise.all([
    api.getPresets(),
    api.getTasksList(),
    api.getCrawlRecords({ page_size: 20 }),
  ])
  presets.value = p.items || []
  taskList.value = t.items || []
  records.value = r.items || []

  // 进页面先查状态：若后端有任务在跑，自动重连订阅（补看历史日志）
  const st = await api.getCrawlStatus()
  taskStatus.value = st
  if (st.running) {
    logs.value = []
    connectStream()
  }
  startStatusPoll()
})

onUnmounted(() => {
  // 切页面只断开 SSE 订阅，不断后端任务
  if (sseController) { sseController.abort(); sseController = null }
  if (statusTimer) { clearInterval(statusTimer); statusTimer = null }
})

function usePreset(p) { prompt.value = p.prompt }
function statusType(s) { return { completed: 'success', failed: 'danger', running: 'warning' }[s] || 'info' }

function addLog(line) {
  logs.value.push(line)
  nextTick(() => { if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight })
}

function startStatusPoll() {
  statusTimer = setInterval(async () => {
    try {
      const st = await api.getCrawlStatus()
      taskStatus.value = st
      if (!st.running && sseController) {
        // 任务结束后停止轮询
        if (statusTimer) { clearInterval(statusTimer); statusTimer = null }
      }
    } catch (e) {}
  }, 3000)
}

async function connectStream() {
  if (sseController) sseController.abort()
  sseController = new AbortController()
  try {
    const resp = await fetch('/api/crawl/stream', { signal: sseController.signal })
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      for (const line of decoder.decode(value).split('\n')) {
        if (line.startsWith('data:')) {
          const data = line.slice(5).trim()
          if (data) addLog(data)
        }
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') addLog(`[error] ${e.message}`)
  }
}

async function runPrompt() {
  if (!prompt.value.trim() || taskStatus.value.running) return
  const current = prompt.value
  logs.value = [`> ${current}`, '']
  // 1. 启动后台任务（立即返回）
  const r = await api.startCrawl(current)
  if (r.error) { addLog(r.error); return }
  taskStatus.value = { running: true, label: r.label }
  // 2. 订阅日志流
  connectStream()
  startStatusPoll()
  // 刷新历史记录
  const rec = await api.getCrawlRecords({ page_size: 20 })
  records.value = rec.items || []
}

async function askHermes() {
  if (!prompt.value.trim() || hermesLoading.value) return
  hermesLoading.value = true
  addLog(`[hermes] 理解意图：${prompt.value}`)
  addLog('')
  try {
    const resp = await fetch('/api/crawl/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: prompt.value }),
    })
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      for (const line of decoder.decode(value).split('\n')) {
        if (line.startsWith('data:')) {
          const data = line.slice(5).trim()
          if (data) addLog(data)
        }
      }
    }
  } catch (e) {
    addLog(`[error] ${e.message}`)
  } finally {
    hermesLoading.value = false
  }
}

async function stopTask() {
  const r = await api.stopCrawl()
  if (r.error) { addLog(r.error); return }
  addLog('[runner] 已请求停止')
}
</script>
