import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/dashboard' },
  {
    path: '/crawl',
    name: 'CrawlChat',
    component: () => import('../views/CrawlChat.vue'),
    meta: { title: '爬取指令' },
  },
  {
    path: '/data',
    name: 'DataBrowser',
    component: () => import('../views/DataBrowser.vue'),
    meta: { title: '数据检索' },
  },
  {
    path: '/dashboard',
    name: 'RatingDashboard',
    component: () => import('../views/RatingDashboard.vue'),
    meta: { title: '评级看板' },
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
