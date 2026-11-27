<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">拾影工坊 · 音视频采集与转写结构化平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向音视频媒体源接入、分片切分、转码作业、转写任务、语音分段、说话人标注、字幕对齐、时间轴标注、术语抽取、转写版本、置信复核、纠错回路、字幕包交付、分片水位与失败重投的采集结构化平台。</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "历史签收迁移", path: "/signoff_migration" }, { label: "媒体源", path: "/media_source" }, { label: "采集通道", path: "/capture_channel" }, { label: "媒体分片", path: "/media_shard" }, { label: "转码作业", path: "/transcode_job" }, { label: "转写任务", path: "/transcribe_task" }, { label: "语音分段", path: "/speech_segment" }, { label: "说话人标注", path: "/speaker_label" }, { label: "字幕对齐", path: "/subtitle_align" }, { label: "时间轴标注", path: "/timeline_mark" }, { label: "术语抽取", path: "/term_extract" }, { label: "转写版本", path: "/transcript_version" }, { label: "置信复核", path: "/confidence_review" }, { label: "纠错回路", path: "/correction_loop" }, { label: "字幕包", path: "/subtitle_pack" }, { label: "媒体索引", path: "/media_index" }, { label: "分片水位", path: "/shard_watermark" }, { label: "失败重投", path: "/fail_redeliver" }, { label: "转写配额", path: "/transcribe_quota" }, { label: "抽样质检", path: "/sample_qc" }, { label: "交付回执", path: "/delivery_receipt" }, { label: "归档架位", path: "/archive_shelf" }, { label: "采集审计", path: "/media_audit" }]
</script>
