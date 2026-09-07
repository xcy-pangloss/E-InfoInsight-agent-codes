<template>
  <div>
    <!-- KPI 卡片 -->
    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="6" v-for="kpi in kpis" :key="kpi.label">
        <el-card shadow="hover">
          <el-statistic :title="kpi.label" :value="kpi.value" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 图表行 -->
    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="12">
        <el-card><template #header>等级分布</template><div ref="distChart" style="height: 280px"></div></el-card>
      </el-col>
      <el-col :span="12">
        <el-card><template #header>数据质量</template>
          <div style="padding: 8px">
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="模板scope企业">{{ quality.template_scope }}</el-descriptions-item>
              <el-descriptions-item label="真实scope企业">{{ quality.real_scope }}</el-descriptions-item>
              <el-descriptions-item label="新闻覆盖">{{ quality.coverage?.news || 0 }}</el-descriptions-item>
              <el-descriptions-item label="技术画像覆盖">{{ quality.coverage?.tech || 0 }}</el-descriptions-item>
              <el-descriptions-item label="招聘覆盖">{{ quality.coverage?.recruit || 0 }}</el-descriptions-item>
            </el-descriptions>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- S/A线索 + 评分差异 -->
    <el-row :gutter="16">
      <el-col :span="14">
        <el-card>
          <template #header>S/A 级线索榜</template>
          <el-table :data="leads" size="small" border>
            <el-table-column prop="company_name" label="企业名称" min-width="200" />
            <el-table-column prop="rating_level" label="等级" width="60">
              <template #default="{ row }"><el-tag :type="levelType(row.rating_level)" size="small">{{ row.rating_level }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="total_score" label="总分" width="60" />
            <el-table-column prop="demand_tags" label="需求标签" min-width="200">
              <template #default="{ row }">{{ (row.demand_tags || []).join('、') }}</template>
            </el-table-column>
            <el-table-column prop="sales_pitch+reasoning" label="销售话术与评分依据" min-width="400">
              <template #default="{ row }">
                <div v-if="row.sales_pitch" style="margin-bottom: 4px"><strong>话术：</strong>{{ row.sales_pitch }}</div>
                <div v-if="row.reasoning" style="color: var(--el-text-color-secondary); font-size: 13px"><strong>依据：</strong>{{ row.reasoning }}</div>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card>
          <template #header>规则 vs DeepSeek 评分差异 (Top20)</template>
          <el-table :data="quality.score_diff || []" size="small" border>
            <el-table-column prop="company_name" label="企业" min-width="220" />
            <el-table-column prop="rules_score" label="规则分" width="70" />
            <el-table-column prop="ds_score" label="DS分" width="70" />
            <el-table-column label="差值" width="70">
              <template #default="{ row }">{{ row.rules_score - row.ds_score }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import * as echarts from 'echarts'
import 'echarts/theme/dark.js' // 注册 'dark' 主题
import api from '../api'
import { useTheme } from '../theme'

const { isDark } = useTheme()
const distChart = ref(null)
const summary = ref({})
const distribution = ref([])
const leads = ref([])
const quality = ref({})
const kpis = ref([])

// 缓存图表实例，主题切换时 dispose 后重建
let distInstance = null

onMounted(async () => {
  const [s, d, l, q] = await Promise.all([
    api.getSummary(),
    api.getDistribution(),
    api.getLeads({ limit: 50 }),
    api.getDataQuality(),
  ])
  summary.value = s
  distribution.value = d.items || []
  leads.value = l.items || []
  quality.value = q

  kpis.value = [
    { label: '总企业数', value: s.total_companies },
    { label: '已评级', value: s.rated },
    { label: 'S/A级线索', value: s.sa_leads },
    { label: '待处理', value: s.pending },
  ]

  await nextTick()
  renderDist()
})

onUnmounted(() => {
  distInstance?.dispose(); distInstance = null
})

// 主题切换：重建图表以应用暗色主题
watch(isDark, async () => {
  distInstance?.dispose(); distInstance = null
  await nextTick()
  renderDist()
})

function renderDist() {
  if (!distChart.value) return
  distInstance = echarts.init(distChart.value, isDark.value ? 'dark' : null)
  const levelOrder = ['S', 'A', 'B', 'C', 'D']
  const data = levelOrder.map(l => {
    const found = distribution.value.find(d => d.rating_level === l)
    return { name: l, value: found ? found.n : 0 }
  })
  distInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item' },
    series: [{
      type: 'pie', radius: ['40%', '70%'],
      data,
      label: { formatter: '{b}: {c}' },
      itemStyle: {
        color: (p) => ({ S: '#f56c6a', A: '#e6a23c', B: '#67c23a', C: '#409eff', D: '#909399' })[p.data.name]
      },
    }],
  })
}

function levelType(l) { return { S: 'danger', A: 'warning', B: 'success', C: 'info', D: '' }[l] || '' }
</script>
