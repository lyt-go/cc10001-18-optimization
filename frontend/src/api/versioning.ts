/** 模板版本 / 排期判断接口的类型与请求封装，供模板库、采集任务与调度计划页面共用。 */
import { fetchJson, request } from '@/api/client'

export type TemplateConfig = {
  模板编号: string
  站点分类: string | null
  模板名称: string | null
  解析规则集: string[]
  请求模板: string | null
  分页配置: string | null
  适用域名: string | null
  字符编码: string | null
  字段映射: string[]
}

export type RuleSnapshotView = {
  规则编号: string
  状态: string
  规则指纹: string
}

export type FullConfig = {
  模板指纹: string
  规则集指纹: string
  校验指纹: string
  版本标识: string
  配置: TemplateConfig
  解析规则: RuleSnapshotView[]
  缺失规则: string[]
  已废弃规则: string[]
}

export type ConclusionView = {
  结果: string
  校验指纹: string
  原因: string
  验证时间: string
  来源: string
  可复用: boolean
}

export type VersionHistoryItem = {
  版本标识: string
  模板指纹: string
  规则集指纹: string
  校验指纹: string
  站点分类: string | null
  分页配置: string | null
  字段映射: string[]
  解析规则: string[]
  当前版本: boolean
  曾生效: boolean
  校验有效期至: string | null
  说明: string
}

export type TemplateVersionDetail = {
  模板编号: string
  模板状态: string
  版本标识: string
  生效版本: string | null
  配置与生效版本一致: boolean
  校验指纹: string
  模板指纹: string
  规则集指纹: string
  完整配置: FullConfig
  校验结论: ConclusionView | null
  结论可复用: boolean
  结论来源: string | null
  验证时间: string | null
  有效期至: string | null
  提交次数: number
  版本历史: VersionHistoryItem[]
}

export type HistoricalRunConfig = {
  任务编号: string
  模板编号: string
  版本标识: string
  校验指纹: string
  字段映射: string[]
  分页配置: string | null
  请求模板: string | null
  字符编码: string | null
  适用域名: string | null
  固化时间: string
}

export type TaskVerdict = {
  任务编号: string
  任务状态?: string
  模板编号: string
  模板状态: string
  版本标识: string
  校验指纹: string
  模板指纹: string
  规则集指纹: string
  校验结论: string
  结论来源: string
  结论可复用: boolean
  校验有效期至: string | null
  可启动: boolean
  受影响原因: string
  完整配置: FullConfig
  历史运行配置: HistoricalRunConfig | null
}

export type TemplateImpact = {
  模板编号: string
  模板名称: string | null
  模板状态: string
  版本标识: string
  校验指纹: string
  校验结论: string | null
  任务: TaskVerdict[]
  排期计划: { 计划编号: string; 关联任务: string; 状态: string }[]
  受影响任务数: number
}

export type ScheduleView = {
  任务: TaskVerdict[]
  受影响任务数: number
}

export type PlanVerdict = {
  计划编号: string
  任务编号?: string
  任务状态?: string
  计划状态: string
  模板编号: string | null
  模板状态?: string
  版本标识?: string
  校验指纹?: string
  可排期: boolean
  受影响原因: string
  完整配置?: FullConfig
  历史运行配置?: HistoricalRunConfig | null
}

export type PlanScheduleView = {
  计划: PlanVerdict[]
  受影响计划数: number
}

export function fetchTemplateVersions(entryId: number): Promise<TemplateVersionDetail> {
  return fetchJson<TemplateVersionDetail>(`/api/templatelib/${entryId}/versions`)
}

export function fetchTemplateImpact(entryId: number): Promise<TemplateImpact> {
  return fetchJson<TemplateImpact>(`/api/templatelib/${entryId}/impact`)
}

export function fetchTaskImpact(entryId: number): Promise<TaskVerdict> {
  return fetchJson<TaskVerdict>(`/api/taskcrawl/${entryId}/impact`)
}

export function fetchTaskSchedule(templateCode?: string): Promise<ScheduleView> {
  const query = templateCode ? `?template_code=${encodeURIComponent(templateCode)}` : ''
  return fetchJson<ScheduleView>(`/api/taskcrawl/schedule${query}`)
}

export function fetchPlanSchedule(): Promise<PlanScheduleView> {
  return fetchJson<PlanScheduleView>('/api/scheduler/schedule')
}

export function fetchPlanImpact(entryId: number): Promise<PlanVerdict> {
  return fetchJson<PlanVerdict>(`/api/scheduler/${entryId}/impact`)
}

export async function patchTemplate(entryId: number, values: Record<string, unknown>) {
  const response = await request(`/api/templatelib/${entryId}`, {
    method: 'PATCH',
    body: JSON.stringify({ values }),
  })
  return response.json() as Promise<{ ok: boolean; message: string; entry?: Record<string, unknown> }>
}

export async function rollbackTemplate(entryId: number, versionId: string) {
  const response = await request(`/api/templatelib/${entryId}/rollback`, {
    method: 'POST',
    body: JSON.stringify({ values: { version_id: versionId } }),
  })
  return response.json() as Promise<{ ok: boolean; message: string; entry?: Record<string, unknown> }>
}

export function impactSourceLabel(source: string): string {
  if (source === 'cache') return '有效期内复用'
  if (source === 'stale') return '旧结论已失效'
  return '本次按当前版本判定'
}
