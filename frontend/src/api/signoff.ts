import { request } from './client'

export type SignoffPackRow = {
  '原包号': string
  '关联媒体源': string
  '接收方标识': string
  '签收时间': string
  '旧校验': string
  '实际来源': string
  '迁移状态': string
  '清单状态': string
  '清单编号': string | null
  '原件可下载': string[]
  '待确认复验数': number
}

export type ManifestLine = {
  '行号': number
  '指纹': string
  '实际字幕': string | null
  '核验': boolean
  '来源': string | null
}

export type ManifestFile = {
  '文件名': string
  '整包指纹': string
  '八位旧校验': string
  '实际来源': string
  '核验状态': string
  '纳入来源': string | null
  '字节数': number
  '字幕行数': number
  '已核验行数': number
  '缺失环节': string | null
  '逐行': ManifestLine[]
}

export type Manifest = {
  'manifest_id': string
  '原包号': string
  '清单版本': string
  '关联媒体源': string
  '接收方标识': string
  '实际来源': string
  '旧校验': string
  status: string
  blocked_reason: string | null
  confirmed_at: string | null
  confirmed_by: string | null
  files: ManifestFile[]
  '字幕行数': number
  '已核验行数': number
}

export type PendingRequest = {
  request_id: string
  receiver: string
  files: string[]
  status: string
  submitted_at: string
  resolved_at: string | null
  confirmed_by: string | null
  result_id: string | null
  verdict?: string
}

export type SignoffDetail = {
  signoff: {
    '原包号': string
    '关联媒体源': string
    '接收方标识': string
    '签收方': string
    '签收时间': string
    '实际来源': string
    '旧校验算法': string
    '旧校验': string
    '逐文件指纹': {
      '文件名': string
      '字节数': number
      '整包指纹': string
      '八位旧校验': string
      '字幕行数': number
      '逐行指纹': string[]
    }[]
  }
  migration: {
    status: string
    started_at: string | null
    started_by: string | null
    confirmed_at: string | null
    confirmed_by: string | null
    rolled_back_at: string | null
    rolled_back_by: string | null
    read_files: string[]
    missing_files: string[]
    blocked_reason: string | null
  }
  snapshot_files: string[]
  manifest: Manifest | null
  originals: {
    '文件名': string
    '可下载': boolean
    '来源': string | null
    '缺失原因': string | null
    '字节数': number
    '指纹': string
  }[]
  workspace: {
    '文件名': string
    '写入者': string
    '写入时间': string
    '指纹': string
    '与签收一致': boolean
    '备注': string
  }[]
  pending: PendingRequest[]
  last_result: VerificationResult | null
  audit: { action: string; actor: string; detail: string; at: string }[]
}

export type VerificationFile = {
  '文件名': string
  '期望指纹'?: string | null
  '提交指纹'?: string | null
  '状态': string
  '来源'?: string | null
  '字幕行数'?: number
  '已核验行数'?: number
  '说明'?: string | null
  '逐行'?: ManifestLine[]
}

export type VerificationResult = {
  result_id: string
  '原包号': string
  '接收方标识': string
  verdict: string
  ok: boolean
  message: string
  files: VerificationFile[]
  missing_links: string[]
  manifest_id: string | null
  manifest_status: string | null
  confirmed_by: string | null
  confirmed_at: string | null
  request_id: string | null
  consumers?: string[]
  shared?: boolean
  share_note?: string
}

async function readJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  const payload = await response.json()
  if (!response.ok) {
    const detail = payload?.detail
    const message = typeof detail === 'string' ? detail : detail?.message
    throw new Error(message ?? '请求未完成')
  }
  return payload as T
}

export async function listSignoffPacks(): Promise<SignoffPackRow[]> {
  const payload = await readJson<{ items: SignoffPackRow[] }>('/api/signoff/packs')
  return payload.items
}

export function getSignoffPack(packNo: string): Promise<SignoffDetail> {
  return readJson<SignoffDetail>(`/api/signoff/packs/${encodeURIComponent(packNo)}`)
}

function migrationAction(packNo: string, action: string, actor: string) {
  return readJson<{ ok: boolean; message: string }>(
    `/api/signoff/packs/${encodeURIComponent(packNo)}/migration/${action}`,
    { method: 'POST', body: JSON.stringify({ actor }) },
  )
}

export const startMigration = (packNo: string, actor: string) =>
  migrationAction(packNo, 'start', actor)
export const confirmMigration = (packNo: string, actor: string) =>
  migrationAction(packNo, 'confirm', actor)
export const rollbackMigration = (packNo: string, actor: string) =>
  migrationAction(packNo, 'rollback', actor)
export const reactivateMigration = (packNo: string, actor: string) =>
  migrationAction(packNo, 'reactivate', actor)

export function writeCorrection(
  packNo: string,
  filename: string,
  content: string,
  actor: string,
): Promise<{ ok: boolean; message: string }> {
  return readJson(`/api/signoff/packs/${encodeURIComponent(packNo)}/corrections`, {
    method: 'POST',
    body: JSON.stringify({ filename, content, actor }),
  })
}

export function verifyPack(
  packNo: string,
  receiver: string,
  files: { filename: string; content: string }[],
): Promise<VerificationResult> {
  return readJson(`/api/signoff/packs/${encodeURIComponent(packNo)}/verify`, {
    method: 'POST',
    body: JSON.stringify({ receiver, files }),
  })
}

export function readLegacySignoff(packNo: string): Promise<unknown> {
  return readJson(`/api/signoff/packs/${encodeURIComponent(packNo)}/legacy`)
}

export function readSharedResult(packNo: string): Promise<VerificationResult> {
  return readJson(`/api/signoff/packs/${encodeURIComponent(packNo)}/shared-result`)
}

export async function downloadOriginal(packNo: string, filename: string): Promise<string> {
  const response = await request(
    `/api/signoff/packs/${encodeURIComponent(packNo)}/originals/${encodeURIComponent(filename)}`,
  )
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail = payload?.detail
    throw new Error(detail?.message ?? '该原件不可下载')
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
  return response.headers.get('X-Original-Source') ?? ''
}
