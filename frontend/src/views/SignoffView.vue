<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>历史签收迁移</h2>
        <p class="page-desc">
          把只有八位校验的历史签收迁移成 V2 交付清单：逐项核验实际字幕、保留 V1 原件与逐文件指纹，
          接收方可以提交原件复验，字幕包与交付回执共用同一份服务端核验结论。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="reload-signoff" @click="loadPacks">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="signoff-error">{{ errorMessage }}</p>

    <div class="signoff-layout">
      <aside class="signoff-list">
        <button
          v-for="pack in packs"
          :key="pack['原包号']"
          class="signoff-item"
          :class="{ active: pack['原包号'] === selectedPack }"
          :data-testid="`pack-${pack['原包号']}`"
          type="button"
          @click="selectPack(pack['原包号'])"
        >
          <strong>{{ pack['原包号'] }}</strong>
          <span class="signoff-meta">
            旧校验 {{ pack['旧校验'] }} · {{ pack['迁移状态'] }} · {{ pack['清单状态'] }}
          </span>
          <span class="signoff-meta">{{ pack['实际来源'] }}</span>
          <span v-if="pack['待确认复验数']" class="badge" data-testid="pending-badge">
            待确认复验 {{ pack['待确认复验数'] }}
          </span>
        </button>
      </aside>

      <div v-if="detail" class="signoff-detail">
        <article class="panel">
          <h3>旧签收（部署切换后仍保留读取）</h3>
          <dl class="kv-grid">
            <div><dt>原包号</dt><dd data-testid="legacy-pack-no">{{ detail.signoff['原包号'] }}</dd></div>
            <div><dt>旧校验（{{ detail.signoff['旧校验算法'] }}）</dt><dd data-testid="legacy-checksum">{{ detail.signoff['旧校验'] }}</dd></div>
            <div><dt>实际来源</dt><dd data-testid="legacy-source">{{ detail.signoff['实际来源'] }}</dd></div>
            <div><dt>签收方</dt><dd>{{ detail.signoff['签收方'] }}</dd></div>
            <div><dt>接收方</dt><dd>{{ detail.signoff['接收方标识'] }}</dd></div>
            <div><dt>签收时间</dt><dd>{{ detail.signoff['签收时间'] }}</dd></div>
          </dl>
          <table class="data-table compact">
            <thead>
              <tr><th>文件名</th><th>字节数</th><th>八位旧校验</th><th>整包指纹</th><th>字幕行数</th></tr>
            </thead>
            <tbody>
              <tr v-for="file in detail.signoff['逐文件指纹']" :key="file['文件名']">
                <td>{{ file['文件名'] }}</td>
                <td>{{ file['字节数'] }}</td>
                <td>{{ file['八位旧校验'] }}</td>
                <td class="mono">{{ file['整包指纹'] }}</td>
                <td>{{ file['字幕行数'] }}</td>
              </tr>
            </tbody>
          </table>
        </article>

        <article class="panel">
          <h3>迁移与清单</h3>
          <div class="action-row">
            <span class="status-pill" data-testid="migration-status">迁移状态：{{ detail.migration.status }}</span>
            <span v-if="detail.manifest" class="status-pill" data-testid="manifest-status">
              清单：{{ detail.manifest.status }}
            </span>
            <button class="btn" type="button" data-testid="start-migration" @click="runMigration('start')">启动迁移</button>
            <button class="btn primary" type="button" data-testid="confirm-migration" @click="runMigration('confirm')">确认新版</button>
            <button class="btn" type="button" data-testid="rollback-migration" @click="runMigration('rollback')">回滚</button>
            <button class="btn" type="button" data-testid="reactivate-migration" @click="runMigration('reactivate')">切回</button>
          </div>
          <p class="hint">
            迁移已读取 V1 文件：
            <span data-testid="read-files">{{ detail.migration.read_files.join('、') || '—' }}</span>
            <span v-if="detail.migration.missing_files.length" class="error-text" data-testid="missing-files">
              ；缺原件：{{ detail.migration.missing_files.join('、') }}
            </span>
          </p>
          <p v-if="detail.migration.confirmed_by" class="hint" data-testid="confirmed-by">
            新版确认完成方：{{ detail.migration.confirmed_by }}
            <template v-if="detail.migration.confirmed_at">（{{ detail.migration.confirmed_at }}）</template>
          </p>
          <p v-if="detail.migration.blocked_reason" class="error-text" data-testid="blocked-reason">
            无法复验：{{ detail.migration.blocked_reason }}
          </p>
        </article>

        <article class="panel">
          <h3>V1 原件</h3>
          <table class="data-table compact">
            <thead>
              <tr><th>文件名</th><th>可下载</th><th>来源</th><th>缺失环节</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in detail.originals" :key="item['文件名']" :data-testid="`original-${item['文件名']}`">
                <td>{{ item['文件名'] }}</td>
                <td>{{ item['可下载'] ? '是' : '否' }}</td>
                <td>{{ item['来源'] ?? '—' }}</td>
                <td :class="{ 'error-text': !item['可下载'] }">{{ item['缺失原因'] ?? '—' }}</td>
                <td>
                  <button
                    v-if="item['可下载']"
                    class="link"
                    type="button"
                    :data-testid="`download-${item['文件名']}`"
                    @click="downloadOriginal(selectedPack, item['文件名'])"
                  >下载 V1 原件</button>
                  <span v-else>无法复验</span>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="detail.workspace.length" class="hint">
            同位置工作区（V2 更正，不覆盖签收原件）：
            <span v-for="item in detail.workspace" :key="item['文件名']" data-testid="workspace-entry">
              {{ item['文件名'] }} 由 {{ item['写入者'] }} 写入，与签收一致：{{ item['与签收一致'] ? '是' : '否' }}。
            </span>
          </p>
          <button class="btn" type="button" data-testid="toggle-correction" @click="showCorrection = !showCorrection">
            {{ showCorrection ? '收起 V2 更正写入' : '写入同位置 V2 更正' }}
          </button>
          <div v-if="showCorrection" class="inline-form" data-testid="correction-form">
            <label class="create-field">
              <span>文件名</span>
              <input v-model="correction.filename" data-testid="correction-filename" />
            </label>
            <label class="create-field">
              <span>V2 更正内容（写入同位置工作区，不覆盖签收原件）</span>
              <textarea v-model="correction.content" rows="6" data-testid="correction-content" />
            </label>
            <button class="btn" type="button" data-testid="submit-correction" @click="submitCorrection">写入更正</button>
          </div>
        </article>

        <article v-if="detail.manifest" class="panel" data-testid="manifest-panel">
          <h3>V2 交付清单 · {{ detail.manifest['manifest_id'] }}（{{ detail.manifest.status }}）</h3>
          <p class="hint">
            逐项核验实际字幕：{{ detail.manifest['已核验行数'] }} / {{ detail.manifest['字幕行数'] }} 行
            <template v-if="detail.manifest.confirmed_by">，确认方 {{ detail.manifest.confirmed_by }}</template>
          </p>
          <div v-for="file in detail.manifest.files" :key="file['文件名']" class="manifest-file" :data-testid="`manifest-file-${file['文件名']}`">
            <p>
              <strong>{{ file['文件名'] }}</strong>
              · {{ file['核验状态'] }}
              · 纳入来源：{{ file['纳入来源'] ?? '—' }}
              · 行 {{ file['已核验行数'] }}/{{ file['字幕行数'] }}
              <span class="mono">· {{ file['整包指纹'] }}</span>
            </p>
            <p v-if="file['缺失环节']" class="error-text">{{ file['缺失环节'] }}</p>
            <table class="data-table compact">
              <thead><tr><th>行号</th><th>字幕行指纹</th><th>实际字幕</th><th>核验</th></tr></thead>
              <tbody>
                <tr v-for="line in file['逐行']" :key="line['行号']">
                  <td>{{ line['行号'] }}</td>
                  <td class="mono">{{ line['指纹'] }}</td>
                  <td>{{ line['实际字幕'] ?? '—' }}</td>
                  <td>{{ line['核验'] ? '一致' : '待复验' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>

        <article class="panel">
          <h3>接收方复验</h3>
          <p class="hint">提交签收时保有的原文件与指纹；迁移确认前提交会先排队，确认后自动完成。</p>
          <div class="inline-form" data-testid="verify-form">
            <label class="create-field">
              <span>接收方标识</span>
              <input v-model="verify.receiver" data-testid="verify-receiver" />
            </label>
            <label class="create-field">
              <span>文件名</span>
              <input v-model="verify.filename" data-testid="verify-filename" />
            </label>
            <label class="create-field">
              <span>提交原件内容</span>
              <textarea v-model="verify.content" rows="8" data-testid="verify-content" />
            </label>
            <button class="btn primary" type="button" data-testid="submit-verify" @click="submitVerify">提交复验</button>
          </div>
          <div v-if="result" class="result-box" data-testid="verify-result">
            <p>
              <strong data-testid="verify-verdict">{{ result.verdict }}</strong>
              · {{ result.message }}
              <span class="mono">（{{ result.result_id }}）</span>
            </p>
            <ul v-if="result.missing_links.length">
              <li v-for="link in result.missing_links" :key="link" class="error-text">{{ link }}</li>
            </ul>
            <table v-if="result.files.length" class="data-table compact">
              <thead><tr><th>文件名</th><th>期望指纹</th><th>提交指纹</th><th>状态</th><th>已核验行数</th></tr></thead>
              <tbody>
                <tr v-for="file in result.files" :key="file['文件名']">
                  <td>{{ file['文件名'] }}</td>
                  <td class="mono">{{ file['期望指纹'] ?? '—' }}</td>
                  <td class="mono">{{ file['提交指纹'] ?? '—' }}</td>
                  <td>{{ file['状态'] }}</td>
                  <td>{{ file['已核验行数'] ?? '—' }}</td>
                </tr>
              </tbody>
            </table>
            <p v-if="result.confirmed_by" class="hint" data-testid="result-confirmed-by">
              新版确认完成方：{{ result.confirmed_by }}
            </p>
          </div>
        </article>

        <article v-if="detail.pending.length" class="panel" data-testid="pending-panel">
          <h3>待确认复验请求</h3>
          <table class="data-table compact">
            <thead><tr><th>请求号</th><th>接收方</th><th>文件</th><th>状态</th><th>到达时间</th><th>完成方</th></tr></thead>
            <tbody>
              <tr v-for="item in detail.pending" :key="item.request_id">
                <td>{{ item.request_id }}</td>
                <td>{{ item.receiver }}</td>
                <td>{{ item.files.join('、') }}</td>
                <td>{{ item.status }}</td>
                <td>{{ item.submitted_at }}</td>
                <td>{{ item.confirmed_by ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </article>

        <article class="panel">
          <h3>迁移轨迹</h3>
          <ul class="audit-list">
            <li v-for="(item, index) in detail.audit" :key="index">
              {{ item.at }} · {{ item.actor }} · {{ item.action }}：{{ item.detail }}
            </li>
            <li v-if="!detail.audit.length" class="hint">暂无迁移记录</li>
          </ul>
        </article>
      </div>
      <div v-else class="signoff-detail hint">选择左侧原包号查看签收详情</div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import {
  confirmMigration,
  downloadOriginal,
  getSignoffPack,
  listSignoffPacks,
  reactivateMigration,
  rollbackMigration,
  startMigration,
  verifyPack,
  writeCorrection,
} from '@/api/signoff'
import type { SignoffDetail, SignoffPackRow, VerificationResult } from '@/api/signoff'

const packs = ref<SignoffPackRow[]>([])
const selectedPack = ref('')
const detail = ref<SignoffDetail | null>(null)
const result = ref<VerificationResult | null>(null)
const errorMessage = ref('')
const showCorrection = ref(false)
const correction = reactive({ filename: '', content: '' })
const verify = reactive({ receiver: '', filename: '', content: '' })

const ACTORS: Record<string, (packNo: string, actor: string) => Promise<{ ok: boolean; message: string }>> = {
  start: startMigration,
  confirm: confirmMigration,
  rollback: rollbackMigration,
  reactivate: reactivateMigration,
}

function currentActor(): string {
  return '值班员'
}

async function loadPacks() {
  errorMessage.value = ''
  try {
    packs.value = await listSignoffPacks()
    if (!selectedPack.value && packs.value.length) {
      selectedPack.value = packs.value[0]['原包号']
    }
    if (selectedPack.value) await loadDetail(selectedPack.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '历史签收读取失败'
  }
}

async function loadDetail(packNo: string) {
  try {
    detail.value = await getSignoffPack(packNo)
    result.value = detail.value.last_result
    const firstFile = detail.value.signoff['逐文件指纹'][0]?.['文件名'] ?? ''
    if (!correction.filename) correction.filename = firstFile
    if (!verify.filename) verify.filename = firstFile
    if (!verify.receiver) verify.receiver = detail.value.signoff['接收方标识']
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '签收详情读取失败'
  }
}

async function selectPack(packNo: string) {
  selectedPack.value = packNo
  result.value = null
  await loadDetail(packNo)
}

async function runMigration(action: string) {
  errorMessage.value = ''
  try {
    const outcome = await ACTORS[action](selectedPack.value, currentActor())
    if (!outcome.ok) errorMessage.value = outcome.message
    await loadPacks()
    await loadDetail(selectedPack.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '迁移动作未完成'
  }
}

async function submitCorrection() {
  errorMessage.value = ''
  try {
    const outcome = await writeCorrection(
      selectedPack.value, correction.filename, correction.content, '更正执行人',
    )
    if (!outcome.ok) errorMessage.value = outcome.message
    await loadDetail(selectedPack.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '更正写入失败'
  }
}

async function submitVerify() {
  errorMessage.value = ''
  try {
    result.value = await verifyPack(selectedPack.value, verify.receiver, [
      { filename: verify.filename, content: verify.content },
    ])
    await loadPacks()
    await loadDetail(selectedPack.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复验未完成'
  }
}

async function download(packNo: string, filename: string) {
  errorMessage.value = ''
  try {
    await downloadOriginal(packNo, filename)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '原件不可下载'
  }
}

onMounted(loadPacks)
</script>
