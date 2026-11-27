"""历史签收示例数据：旧签收只带八位校验，但签收时另行留存了逐文件指纹。

这里的 content 就是签收方当时交付、接收方仍保有的 V1 原文件字节。
`originals` 标记哪些文件仍能在签收方归档原件库中读到；没有列出的包只能靠
接收方提交原件复验，页面必须显示“无法复验”并指出缺哪一环。
"""
from __future__ import annotations

from typing import Any

SBPK_0003_V1_SRT = """1
00:00:00,000 --> 00:00:02,500
欢迎收看年度技术峰会

2
00:00:02,500 --> 00:00:06,000
今天我们讨论数据采集流水线

3
00:00:06,000 --> 00:00:09,500
以及端到端设计理念
"""

SBPK_0002_V1_SRT = """1
00:00:00,000 --> 00:00:03,000
产品发布会现在开始

2
00:00:03,000 --> 00:00:07,000
本次发布覆盖三条产品线
"""

SIGNOFF_SEEDS: list[dict[str, Any]] = [
    {
        "pack_no": "SBPK-0003",
        "media_id": "MEDI-0001",
        "receiver": "媒体检索平台",
        "signer_party": "内容交付值班组",
        "signed_at": "2026-09-03 15:20",
        "actual_source": "跨平台内容分发渠道（2026-09-03 历史签收批次）",
        "migration_status": "未开始",
        "files": [
            {"filename": "SBPK-0003.v1.srt", "content": SBPK_0003_V1_SRT},
        ],
        "originals": ["SBPK-0003.v1.srt"],
    },
    {
        "pack_no": "SBPK-0002",
        "media_id": "MEDI-0001",
        "receiver": "视频归档系统",
        "signer_party": "内容交付值班组",
        "signed_at": "2026-09-02 11:05",
        "actual_source": "内部采集网关（2026-09-02 历史签收批次）",
        "migration_status": "未开始",
        "files": [
            {"filename": "SBPK-0002.v1.srt", "content": SBPK_0002_V1_SRT},
        ],
        # 签收方归档原件库中已找不到这个文件，只能等接收方提交原件复验。
        "originals": [],
    },
]
