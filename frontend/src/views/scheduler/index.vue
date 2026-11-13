<template>
  <section class="page" data-module="scheduler">
    <header class="page-head">
      <div>
        <h2>调度计划管理</h2>
        <p class="page-desc">管理采集任务的周期性调度安排，配置执行时间、优先级和任务间依赖关系。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记调度计划</button>
        <button class="btn" type="button" @click="exportRows">导出调度计划清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ formatCell(row[column]) }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="selectRow(row)">查看模板版本判断</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无调度计划数据，可先登记调度计划</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条调度计划记录</span>
      <span v-if="errorMessage" class="text-line">{{ errorMessage }}</span>
    </footer>

    <section class="detail-panel">
      <header class="detail-head">
        <div>
          <h3>排期视图</h3>
          <p class="page-desc">
            每个计划按关联任务所绑定模板的当前版本判断能否排期；模板配置变化后旧结论失效，原因与模板详情页一致。
          </p>
        </div>
        <span class="error-text">受影响计划 {{ schedule?.受影响计划数 ?? 0 }} 个</span>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>计划编号</th>
            <th>关联任务</th>
            <th>计划状态</th>
            <th>模板 / 版本</th>
            <th>可排期</th>
            <th>受影响原因</th>
            <th>历史运行配置</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in schedule?.计划 ?? []" :key="item.计划编号">
            <td>{{ item.计划编号 }}</td>
            <td>{{ item.任务编号 ?? '—' }}</td>
            <td>{{ item.计划状态 }}</td>
            <td>{{ item.模板编号 ?? '—' }} / {{ item.版本标识 ?? '—' }}</td>
            <td :class="item.可排期 ? 'ok-text' : 'error-text'">{{ item.可排期 ? '可排期' : '不可排期' }}</td>
            <td class="status-cell">{{ item.受影响原因 }}</td>
            <td class="status-cell">
              <template v-if="item.历史运行配置">
                版本 {{ item.历史运行配置.版本标识 }}（{{ item.历史运行配置.字段映射.length }} 个字段，已固化）
              </template>
              <template v-else>尚未运行</template>
            </td>
          </tr>
          <tr v-if="!(schedule?.计划 ?? []).length">
            <td colspan="7" class="empty-state">暂无调度计划</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="planDetail" class="detail-panel">
      <header class="detail-head">
        <div>
          <h3>{{ planDetail.计划编号 }} · 模板版本 {{ planDetail.版本标识 ?? '—' }}</h3>
          <p class="page-desc">
            {{ planDetail.可排期 ? '当前版本可排期' : '当前版本不可排期' }}：{{ planDetail.受影响原因 }}
          </p>
        </div>
        <button class="btn ghost" type="button" @click="planDetail = null">收起</button>
      </header>
      <div v-if="planDetail.完整配置" class="detail-grid">
        <article class="detail-card">
          <h4>当前版本整套配置</h4>
          <ul class="detail-list">
            <li>模板：{{ planDetail.完整配置.配置.模板编号 }}（{{ planDetail.模板状态 ?? '—' }}）</li>
            <li>分页配置：{{ planDetail.完整配置.配置.分页配置 ?? '—' }}</li>
            <li>字段映射（{{ planDetail.完整配置.配置.字段映射.length }} 个）：{{ planDetail.完整配置.配置.字段映射.join('、') }}</li>
            <li>解析规则：{{ planDetail.完整配置.解析规则.map((rule) => rule.规则编号).join('、') }}</li>
          </ul>
        </article>
        <article class="detail-card">
          <h4>历史运行配置（只读，不随模板改写）</h4>
          <ul v-if="planDetail.历史运行配置" class="detail-list">
            <li>固化版本：{{ planDetail.历史运行配置.版本标识 }}</li>
            <li>固化分页：{{ planDetail.历史运行配置.分页配置 ?? '—' }}</li>
            <li>固化字段（{{ planDetail.历史运行配置.字段映射.length }} 个）：{{ planDetail.历史运行配置.字段映射.join('、') }}</li>
          </ul>
          <p v-else class="page-desc">关联任务尚未运行过，没有固化的历史配置。</p>
        </article>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'
import {
  fetchPlanImpact,
  fetchPlanSchedule,
  type PlanScheduleView,
  type PlanVerdict,
} from '@/api/versioning'

type Row = Record<string, string | number | string[] | null>

const ENDPOINT = '/api/scheduler'
const columns = ["计划编号", "关联任务", "cron表达式", "生效日期", "失效日期", "优先级", "超时分钟", "依赖计划"]
const actions = ["提交审批", "确认排期", "暂停计划"]
const statuses = ["草稿", "计划审批中", "已排期", "已暂停"]
const stats = [{"label": "调度计划总数", "value": 0}, {"label": "已排期计划", "value": 0}, {"label": "计划审批中", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const schedule = ref<PlanScheduleView | null>(null)
const planDetail = ref<PlanVerdict | null>(null)

function formatCell(value: Row[string]) {
  if (Array.isArray(value)) return value.join('、')
  return value ?? '—'
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '调度计划登记入口尚未接入审批流'
}

async function loadSchedule() {
  try {
    schedule.value = await fetchPlanSchedule()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '排期视图读取失败'
  }
}

async function selectRow(row: Row) {
  errorMessage.value = ''
  try {
    planDetail.value = await fetchPlanImpact(Number(row.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '计划明细读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('调度计划动作未生效，请稍后重试')
    }
    const payload = await response.json()
    errorMessage.value = payload.ok ? (payload.message ?? '') : (payload.message || '调度计划动作未生效')
    await reload()
    await loadSchedule()
    if (planDetail.value?.计划编号 === row.计划编号) {
      await selectRow(row)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '调度计划操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('调度计划列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '调度计划列表读取失败'
  }
}

onMounted(async () => {
  await reload()
  await loadSchedule()
})
</script>
