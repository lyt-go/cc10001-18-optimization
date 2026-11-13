<template>
  <section class="page" data-module="taskcrawl">
    <header class="page-head">
      <div>
        <h2>采集任务管理</h2>
        <p class="page-desc">定义单次或周期性采集任务，关联采集源与解析规则，记录增量水位与批次边界。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记采集任务</button>
        <button class="btn" type="button" @click="exportRows">导出采集任务清单</button>
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
            <button class="link" type="button" @click="selectRow(row)">按模板当前版本判断</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采集任务数据，可先登记采集任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集任务记录</span>
      <span v-if="errorMessage" class="text-line">{{ errorMessage }}</span>
    </footer>

    <section class="detail-panel">
      <header class="detail-head">
        <div>
          <h3>任务排期视图</h3>
          <p class="page-desc">
            只按模板当前版本判断能否启动；配置变化后旧结论失效，受影响原因与模板详情页保持同一口径。
          </p>
        </div>
        <span class="error-text">受影响任务 {{ schedule?.受影响任务数 ?? 0 }} 个</span>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>任务编号</th>
            <th>任务状态</th>
            <th>模板 / 版本</th>
            <th>校验指纹</th>
            <th>结论来源</th>
            <th>可启动</th>
            <th>受影响原因</th>
            <th>历史运行配置</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in schedule?.任务 ?? []" :key="item.任务编号">
            <td>{{ item.任务编号 }}</td>
            <td>{{ item.任务状态 ?? '—' }}</td>
            <td>{{ item.模板编号 ?? '—' }} / {{ item.版本标识 ?? '—' }}</td>
            <td>{{ item.校验指纹 ?? '—' }}</td>
            <td>{{ impactSourceLabel(item.结论来源) }}</td>
            <td :class="item.可启动 ? 'ok-text' : 'error-text'">{{ item.可启动 ? '可启动' : '不可启动' }}</td>
            <td class="status-cell">{{ item.受影响原因 }}</td>
            <td class="status-cell">
              <template v-if="item.历史运行配置">
                版本 {{ item.历史运行配置.版本标识 }}（{{ item.历史运行配置.字段映射.length }} 个字段，已固化）
              </template>
              <template v-else>尚未运行</template>
            </td>
          </tr>
          <tr v-if="!(schedule?.任务 ?? []).length">
            <td colspan="8" class="empty-state">暂无任务</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="taskDetail" class="detail-panel">
      <header class="detail-head">
        <div>
          <h3>{{ taskDetail.任务编号 }} · 模板版本 {{ taskDetail.版本标识 }}</h3>
          <p class="page-desc">
            {{ taskDetail.可启动 ? '当前版本可启动' : '当前版本不可启动' }}：
            {{ taskDetail.受影响原因 }}
          </p>
        </div>
        <button class="btn ghost" type="button" @click="taskDetail = null">收起</button>
      </header>
      <div class="detail-grid">
        <article class="detail-card">
          <h4>当前版本整套配置</h4>
          <ul class="detail-list">
            <li>模板：{{ taskDetail.完整配置.配置.模板编号 }}（{{ taskDetail.模板状态 }}）</li>
            <li>分页配置：{{ taskDetail.完整配置.配置.分页配置 ?? '—' }}</li>
            <li>字段映射（{{ taskDetail.完整配置.配置.字段映射.length }} 个）：{{ taskDetail.完整配置.配置.字段映射.join('、') }}</li>
            <li>解析规则：{{ taskDetail.完整配置.解析规则.map((rule) => rule.规则编号).join('、') }}</li>
            <li>字符编码 / 适用域名：{{ taskDetail.完整配置.配置.字符编码 ?? '—' }} / {{ taskDetail.完整配置.配置.适用域名 ?? '—' }}</li>
          </ul>
        </article>
        <article class="detail-card">
          <h4>历史运行配置（只读，不随模板改写）</h4>
          <ul v-if="taskDetail.历史运行配置" class="detail-list">
            <li>固化版本：{{ taskDetail.历史运行配置.版本标识 }}</li>
            <li>固化分页：{{ taskDetail.历史运行配置.分页配置 ?? '—' }}</li>
            <li>固化字段（{{ taskDetail.历史运行配置.字段映射.length }} 个）：{{ taskDetail.历史运行配置.字段映射.join('、') }}</li>
            <li>固化时间：{{ taskDetail.历史运行配置.固化时间 }}</li>
          </ul>
          <p v-else class="page-desc">该任务尚未运行过，没有固化的历史配置。</p>
        </article>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'
import {
  fetchTaskImpact,
  fetchTaskSchedule,
  impactSourceLabel,
  type ScheduleView,
  type TaskVerdict,
} from '@/api/versioning'

type Row = Record<string, string | number | string[] | null>

const ENDPOINT = '/api/taskcrawl'
const columns = ["任务编号", "采集源名称", "规则编号", "调度周期", "增量字段", "批次大小", "起始水位", "截止水位"]
const actions = ["提交调度", "启动任务", "终止任务"]
const statuses = ["草稿", "待调度", "执行中", "已完成", "已终止"]
const stats = [{"label": "采集任务总数", "value": 0}, {"label": "执行中任务", "value": 0}, {"label": "已完成任务", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const schedule = ref<ScheduleView | null>(null)
const taskDetail = ref<TaskVerdict | null>(null)

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
  errorMessage.value = '采集任务登记入口尚未接入审批流'
}

async function loadSchedule() {
  try {
    schedule.value = await fetchTaskSchedule()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '任务排期视图读取失败'
  }
}

async function selectRow(row: Row) {
  errorMessage.value = ''
  try {
    taskDetail.value = await fetchTaskImpact(Number(row.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '任务明细读取失败'
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
      throw new Error('采集任务动作未生效，请稍后重试')
    }
    const payload = await response.json()
    errorMessage.value = payload.ok ? (payload.message ?? '') : (payload.message || '采集任务动作未生效')
    await reload()
    await loadSchedule()
    if (taskDetail.value?.任务编号 === row.任务编号) {
      await selectRow(row)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集任务操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('采集任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集任务列表读取失败'
  }
}

onMounted(async () => {
  await reload()
  await loadSchedule()
})
</script>
