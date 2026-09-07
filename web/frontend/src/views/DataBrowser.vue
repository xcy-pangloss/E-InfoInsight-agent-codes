<template>
  <div>
    <el-tabs v-model="activeTab">
      <!-- 企业数据 -->
      <el-tab-pane label="企业数据" name="companies">
        <div style="display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap">
          <el-input v-model="filters.keyword" placeholder="企业名搜索" clearable style="width: 180px" @keyup.enter="search" />
          <el-input v-model="filters.credit_code" placeholder="信用代码" clearable style="width: 160px" @keyup.enter="search" />
          <el-select v-model="filters.status" placeholder="状态" clearable style="width: 110px">
            <el-option v-for="s in ['raw','scored','rated','filtered']" :key="s" :label="s" :value="s" />
          </el-select>
          <el-select v-model="filters.rating_level" placeholder="等级" clearable style="width: 90px">
            <el-option v-for="l in ['S','A','B','C','D']" :key="l" :label="l" :value="l" />
          </el-select>
          <el-input v-model="filters.industry_tag" placeholder="行业标签" clearable style="width: 130px" @keyup.enter="search" />
          <el-button type="primary" @click="search">搜索</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </div>
        <el-table :data="companies.items" border size="small" @row-click="openDetail" highlight-current-row>
          <el-table-column prop="company_name" label="企业名称" min-width="220" />
          <el-table-column prop="credit_code" label="信用代码" width="180" />
          <el-table-column prop="rating_level" label="等级" width="60">
            <template #default="{ row }">
              <el-tag v-if="row.rating_level" :type="levelType(row.rating_level)" size="small">{{ row.rating_level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="total_score" label="总分" width="60" />
          <el-table-column prop="status" label="状态" width="80" />
          <el-table-column prop="funding_stage" label="融资" width="60" />
          <el-table-column prop="rated_by" label="评级来源" width="110" />
        </el-table>
        <el-pagination style="margin-top: 12px" v-model:current-page="page" :page-size="20" :total="companies.total" layout="total, prev, pager, next" @current-change="loadCompanies" />
      </el-tab-pane>

      <!-- 历史爬取记录 -->
      <el-tab-pane label="爬取记录" name="records">
        <div style="display: flex; gap: 8px; margin-bottom: 12px">
          <el-select v-model="recFilters.status" placeholder="状态" clearable style="width: 130px">
            <el-option v-for="s in ['pending','running','completed','failed']" :key="s" :label="s" :value="s" />
          </el-select>
          <el-input v-model="recFilters.task_type" placeholder="任务类型" clearable style="width: 160px" />
          <el-button type="primary" @click="loadRecords">筛选</el-button>
        </div>
        <el-table :data="records.items" border size="small">
          <el-table-column prop="task_type" label="类型" width="100" />
          <el-table-column prop="source_name" label="来源" width="120" />
          <el-table-column prop="status" label="状态" width="90">
            <template #default="{ row }"><el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="result_summary" label="结果摘要" min-width="200" show-overflow-tooltip />
          <el-table-column prop="created_at" label="创建时间" width="160">
            <template #default="{ row }">{{ (row.created_at || '').slice(0, 16) }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 企业详情抽屉 -->
    <el-drawer v-model="detailVisible" size="60%" :title="detail?.company?.company_name || '详情'">
      <div v-if="detail" style="padding: 0 16px">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="信用代码">{{ detail.company.credit_code }}</el-descriptions-item>
          <el-descriptions-item label="注册资本">{{ detail.company.registered_capital }}</el-descriptions-item>
          <el-descriptions-item label="成立日期">{{ detail.company.established_date }}</el-descriptions-item>
          <el-descriptions-item label="法人">{{ detail.company.legal_representative }}</el-descriptions-item>
          <el-descriptions-item label="行业标签">{{ (detail.company.industry_tags || []).join('、') }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ detail.company.status }}</el-descriptions-item>
          <el-descriptions-item label="经营范围" :span="2">{{ detail.company.business_scope }}</el-descriptions-item>
        </el-descriptions>

        <h4 style="margin-top: 16px">评级结果</h4>
        <el-table :data="detail.ratings" size="small" border>
          <el-table-column prop="rating_level" label="等级" width="60" />
          <el-table-column prop="total_score" label="总分" width="60" />
          <el-table-column prop="tech_score" label="技术" width="50" />
          <el-table-column prop="funding_score" label="资金" width="50" />
          <el-table-column prop="intent_score" label="意向" width="50" />
          <el-table-column prop="team_score" label="团队" width="50" />
          <el-table-column prop="industry_score" label="行业" width="50" />
          <el-table-column prop="rated_by" label="来源" width="110" />
        </el-table>
        <div v-if="detail.ratings?.[0]?.reasoning" style="margin-top: 8px; padding: 8px; background: var(--el-fill-color-light); font-size: 13px">
          <strong>评分依据：</strong>{{ detail.ratings[0].reasoning }}
        </div>

        <el-tabs style="margin-top: 16px">
          <el-tab-pane :label="`新闻(${detail.news.length})`">
            <el-table :data="detail.news" size="small" max-height="300">
              <el-table-column prop="title" label="标题" min-width="300" show-overflow-tooltip />
              <el-table-column prop="source_name" label="来源" width="100" />
              <el-table-column prop="published_at" label="时间" width="120" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`招聘(${detail.recruitments.length})`">
            <el-table :data="detail.recruitments" size="small" max-height="300">
              <el-table-column prop="position_title" label="岗位" min-width="200" />
              <el-table-column prop="salary_range" label="薪资" width="120" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="`招投标(${detail.biddings.length})`">
            <el-table :data="detail.biddings" size="small" max-height="300">
              <el-table-column prop="project_name" label="项目" min-width="300" show-overflow-tooltip />
              <el-table-column prop="budget_amount" label="预算" width="100" />
              <el-table-column prop="bid_date" label="日期" width="120" />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import api from '../api'
import { ElMessage } from 'element-plus'

const activeTab = ref('companies')
const page = ref(1)
const filters = reactive({ keyword: '', credit_code: '', status: '', rating_level: '', industry_tag: '' })
const recFilters = reactive({ status: '', task_type: '' })
const companies = ref({ items: [], total: 0 })
const records = ref({ items: [], total: 0 })
const detailVisible = ref(false)
const detail = ref(null)

onMounted(() => { loadCompanies(); loadRecords() })

async function loadCompanies() {
  const r = await api.getCompanies({ ...filters, page: page.value, page_size: 20 })
  companies.value = r
}
function search() { page.value = 1; loadCompanies() }
function resetFilters() { Object.keys(filters).forEach(k => filters[k] = ''); page.value = 1; loadCompanies() }

async function loadRecords() {
  const r = await api.getCrawlRecords({ ...recFilters, page_size: 20 })
  records.value = r
}

async function openDetail(row) {
  const r = await api.getCompany(row.id)
  detail.value = r
  detailVisible.value = true
}

function levelType(l) { return { S: 'danger', A: 'warning', B: 'success', C: 'info', D: '' }[l] || '' }
function statusType(s) { return { completed: 'success', failed: 'danger', running: 'warning' }[s] || 'info' }
</script>
