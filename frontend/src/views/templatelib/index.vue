<template>
  <section class="page" data-module="templatelib">
    <header class="page-head">
      <div>
        <h2>模板库管理</h2>
        <p class="page-desc">维护常见网站的采集模板库，支持快速复用解析规则、请求配置和分页策略。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记采集模板</button>
        <button class="btn" type="button" @click="exportRows">导出模板库清单</button>
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
            <button class="link" type="button" @click="selectRow(row)">查看版本与受影响任务</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无模板库数据，可先登记采集模板</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条模板库记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section v-if="detail" class="detail-panel">
      <header class="detail-head">
        <div>
          <h3>{{ detail.模板编号 }} · 版本 {{ detail.版本标识 }}</h3>
          <p class="page-desc">
            状态 {{ detail.模板状态 }}；校验指纹 {{ detail.校验指纹 }}；
            {{ conclusionLabel }}
          </p>
          <p v-if="detail.生效版本 && !detail.配置与生效版本一致" class="error-text">
            当前配置为 {{ detail.版本标识 }}，已发布版本仍是 {{ detail.生效版本 }}，需重新验证并发布后才能启动任务。
          </p>
        </div>
        <button class="btn ghost" type="button" @click="detail = null">收起</button>
      </header>

      <div class="detail-grid">
        <article class="detail-card">
          <h4>当前版本整套配置</h4>
          <ul class="detail-list">
            <li>站点分类：{{ detail.完整配置.配置.站点分类 ?? '—' }}</li>
            <li>分页配置：{{ detail.完整配置.配置.分页配置 ?? '—' }}</li>
            <li>请求模板：{{ detail.完整配置.配置.请求模板 ?? '—' }}</li>
            <li>适用域名 / 字符编码：{{ detail.完整配置.配置.适用域名 ?? '—' }} / {{ detail.完整配置.配置.字符编码 ?? '—' }}</li>
            <li>解析规则：{{ detail.完整配置.解析规则.map((rule) => `${rule.规则编号}(${rule.状态})`).join('、') || '—' }}</li>
            <li>输出字段（{{ detail.完整配置.配置.字段映射.length }} 个）：{{ detail.完整配置.配置.字段映射.join('、') || '—' }}</li>
            <li v-if="detail.完整配置.缺失规则.length">缺失规则：{{ detail.完整配置.缺失规则.join('、') }}</li>
            <li v-if="detail.完整配置.已废弃规则.length">已废弃规则：{{ detail.完整配置.已废弃规则.join('、') }}</li>
          </ul>
        </article>

        <article class="detail-card">
          <h4>校验结论</h4>
          <ul class="detail-list">
            <li>结果：{{ detail.校验结论?.结果 ?? '尚未提交验证' }}</li>
            <li>来源：{{ detail.校验结论 ? impactSourceLabel(detail.校验结论.来源) : '—' }}</li>
            <li>可复用：{{ detail.结论可复用 ? '是（同一指纹有效期内）' : '否（配置或有效期已变化）' }}</li>
            <li>验证时间：{{ detail.验证时间 ?? '—' }}</li>
            <li>有效期至：{{ detail.有效期至 ?? '—' }}</li>
            <li>原因：{{ detail.校验结论?.原因 ?? '—' }}</li>
          </ul>
          <p v-if="detail.校验结论 && !detail.结论可复用" class="error-text">
            旧结论已失效，引用该模板的任务需按当前版本重新验证。
          </p>
        </article>
      </div>

      <div class="detail-grid">
        <article class="detail-card">
          <h4>版本历史（回滚按整套字段读取）</h4>
          <ul class="detail-list">
            <li v-for="version in detail.版本历史" :key="version.版本标识">
              <span :class="version.当前版本 ? 'tag-current' : ''">{{ version.版本标识 }}</span>
              · 分页「{{ version.分页配置 ?? '—' }}」
              · 字段 {{ version.字段映射.length }} 个
              · {{ version.曾生效 ? '曾生效' : '未生效' }}
              <button
                v-if="version.曾生效 && !version.当前版本"
                class="link"
                type="button"
                @click="rollback(version.版本标识)"
              >
                回滚到此版本
              </button>
            </li>
            <li v-if="!detail.版本历史.length">暂无版本记录</li>
          </ul>
        </article>

        <article class="detail-card">
          <h4>受影响任务（{{ impact?.受影响任务数 ?? 0 }} 个不可启动）</h4>
          <ul class="detail-list">
            <li v-for="task in impact?.任务 ?? []" :key="task.任务编号">
              {{ task.任务编号 }} · 版本 {{ task.版本标识 }} ·
              <strong :class="task.可启动 ? 'ok-text' : 'error-text'">
                {{ task.可启动 ? '可启动' : '不可启动' }}
              </strong>
              <p class="detail-note">{{ task.受影响原因 }}</p>
              <p v-if="task.历史运行配置" class="detail-note">
                历史运行版本 {{ task.历史运行配置.版本标识 }}（{{ task.历史运行配置.字段映射.length }} 个字段），不回写。
              </p>
            </li>
            <li v-if="!impact?.任务.length">暂无任务引用该模板</li>
          </ul>
          <ul class="detail-list">
            <li v-for="plan in impact?.排期计划 ?? []" :key="plan.计划编号">
              排期计划 {{ plan.计划编号 }} · {{ plan.关联任务 }} · {{ plan.状态 }}
            </li>
          </ul>
        </article>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'
import {
  fetchTemplateImpact,
  fetchTemplateVersions,
  impactSourceLabel,
  rollbackTemplate,
  type TemplateImpact,
  type TemplateVersionDetail,
} from '@/api/versioning'

type Row = Record<string, string | number | string[] | null>

const ENDPOINT = '/api/templatelib'
const columns = ["模板编号", "站点分类", "模板名称", "解析规则集", "请求模板", "分页配置", "适用域名", "生效版本"]
const actions = ["提交模板验证", "发布模板", "废弃模板"]
const statuses = ["草稿", "模板验证中", "已发布", "已废弃"]
const stats = [{"label": "采集模板总数", "value": 0}, {"label": "已发布模板", "value": 0}, {"label": "模板验证中", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const detail = ref<TemplateVersionDetail | null>(null)
const impact = ref<TemplateImpact | null>(null)

const conclusionLabel = computed(() => {
  if (!detail.value?.校验结论) return '尚未提交模板验证'
  return detail.value.结论可复用
    ? `结论有效期内可复用（${detail.value.校验结论.结果}）`
    : `旧结论已失效（${detail.value.校验结论.结果}）`
})

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
  errorMessage.value = '采集模板登记入口尚未接入审批流'
}

async function selectRow(row: Row) {
  errorMessage.value = ''
  try {
    const [versionDetail, templateImpact] = await Promise.all([
      fetchTemplateVersions(Number(row.id)),
      fetchTemplateImpact(Number(row.id)),
    ])
    detail.value = versionDetail
    impact.value = templateImpact
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '模板版本读取失败'
  }
}

async function refreshDetail(entryId: number) {
  const [versionDetail, templateImpact] = await Promise.all([
    fetchTemplateVersions(entryId),
    fetchTemplateImpact(entryId),
  ])
  detail.value = versionDetail
  impact.value = templateImpact
}

async function rollback(versionId: string) {
  if (!detail.value) return
  const row = rows.value.find((item) => item.模板编号 === detail.value?.模板编号)
  if (!row) {
    errorMessage.value = '找不到当前模板记录，请刷新列表后重试'
    return
  }
  errorMessage.value = ''
  try {
    const payload = await rollbackTemplate(Number(row.id), versionId)
    if (!payload.ok) {
      throw new Error(payload.message || '模板回滚失败')
    }
    errorMessage.value = payload.message
    await reload()
    await refreshDetail(Number(row.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '模板回滚失败'
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
      throw new Error('模板库动作未生效，请稍后重试')
    }
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '模板库动作未生效')
    }
    errorMessage.value = payload.message ?? ''
    await reload()
    if (detail.value?.模板编号 === row.模板编号) {
      await refreshDetail(Number(row.id))
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '模板库操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('采集模板列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '模板库列表读取失败'
  }
}

onMounted(reload)
</script>
