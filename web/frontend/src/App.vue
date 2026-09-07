<template>
  <el-container style="height: 100vh">
    <el-header style="display: flex; align-items: center; border-bottom: 1px solid var(--el-border-color); padding: 0 24px; height: 56px">
      <span style="font-size: 18px; font-weight: 600; margin-right: 24px; white-space: nowrap; color: var(--el-text-color-primary)">e-InfoInsight</span>
      <el-menu mode="horizontal" :ellipsis="false" :default-active="currentRoute" router style="border: none; flex: 1">
        <el-menu-item index="/crawl">爬取指令</el-menu-item>
        <el-menu-item index="/data">数据检索</el-menu-item>
        <el-menu-item index="/dashboard">评级看板</el-menu-item>
      </el-menu>
      <!-- 右上角主题切换 -->
      <el-tooltip :content="isDark ? '切换到亮色' : '切换到暗色'" placement="bottom">
        <el-button circle size="default" @click="toggleTheme">
          <el-icon size="18"><component :is="isDark ? 'Sunny' : 'Moon'" /></el-icon>
        </el-button>
      </el-tooltip>
    </el-header>
    <el-main style="padding: 20px; background: var(--app-bg); overflow: auto">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Sunny, Moon } from '@element-plus/icons-vue'
import { useTheme } from './theme'

const route = useRoute()
const currentRoute = computed(() => route.path)
const { isDark, toggleTheme } = useTheme()
</script>
