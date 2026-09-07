import axios from 'axios'

const http = axios.create({ baseURL: '/api' })

export default {
  // 页面二：数据检索
  getCompanies: (params) => http.get('/companies', { params }).then(r => r.data),
  getCompany: (id) => http.get(`/companies/${id}`).then(r => r.data),
  getCrawlRecords: (params) => http.get('/crawl-records', { params }).then(r => r.data),
  getNews: (params) => http.get('/news', { params }).then(r => r.data),
  getRecruitments: (params) => http.get('/recruitments', { params }).then(r => r.data),
  getBiddings: (params) => http.get('/biddings', { params }).then(r => r.data),

  // 页面三：评级看板
  getSummary: () => http.get('/dashboard/summary').then(r => r.data),
  getDistribution: () => http.get('/dashboard/distribution').then(r => r.data),
  getDimensionRadar: () => http.get('/dashboard/dimension-radar').then(r => r.data),
  getScoreTrend: () => http.get('/dashboard/score-trend').then(r => r.data),
  getLeads: (params) => http.get('/dashboard/leads', { params }).then(r => r.data),
  getDataQuality: () => http.get('/dashboard/data-quality').then(r => r.data),

  // 页面一：爬取指令
  getPresets: () => http.get('/crawl/presets').then(r => r.data),
  getTasksList: () => http.get('/crawl/tasks-list').then(r => r.data),
  getCrawlRecords: (params) => http.get('/crawl/records', { params }).then(r => r.data),
  startCrawl: (prompt) => http.post('/crawl/run', { prompt }).then(r => r.data),
  getCrawlStatus: () => http.get('/crawl/status').then(r => r.data),
  stopCrawl: () => http.post('/crawl/stop').then(r => r.data),
}
