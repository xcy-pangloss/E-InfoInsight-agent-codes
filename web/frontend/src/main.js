import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './theme.js' // 初始化主题（应用 html.dark）
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'
import App from './App.vue'
import router from './router'
import './style.css'

createApp(App).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')
