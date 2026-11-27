"""字幕包导出领域服务：按生效转写版本导出字幕包并记录绑定版本，产出字幕包数据集供交付回执与归档架位消费。"""
from __future__ import annotations

from typing import Any
import hashlib

from app.services.base import BaseModuleService
from app.services.signoff import signoff_registry
from app.store import store


class SubtitlePackService(BaseModuleService):
    MODULE = "subtitle_pack"
    ENTITY = "字幕包"
    OPERATION_TERM = "导出"
    LIST_FIELDS = ["字幕包编号", "关联媒体源", "绑定版本", "字幕行数", "覆盖时长秒", "导出格式", "下载次数", "生成时间"]
    REQUIRED_FIELDS = ["字幕包编号", "关联媒体源", "绑定版本"]
    STATUS_ORDER = ["待生成", "生成中", "可下载", "已失效"]
    ACTION_RULES = {"启动生成": "生成中", "标记可下载": "可下载", "强制失效": "已失效"}
    NEGATIVE_ACTIONS = []

    def create_pack(
        self, pack_id: str, media_id: str, version_id: str,
        line_count: int, duration: int, export_format: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """创建字幕包并绑定版本。"""
        rows = store.rows(self.MODULE)
        content = f"PACK-CONTENT-{pack_id}-{version_id}"
        checksum = hashlib.md5(content.encode()).hexdigest()[:8]
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "字幕包编号": pack_id, "关联媒体源": media_id,
            "绑定版本": version_id, "字幕行数": str(line_count),
            "覆盖时长秒": str(duration), "导出格式": export_format,
            "下载次数": "0", "生成时间": "2026-11-01",
            "status": "可下载", "pending": False, "abnormal": False,
            "_checksum": checksum,
        }
        rows.append(entry)
        return entry, f"字幕包 {pack_id} 已创建，绑定 {version_id}"

    def get_packs_by_version(self, version_id: str) -> list[dict[str, Any]]:
        """获取绑定到指定版本的所有字幕包。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows if r.get("绑定版本") == version_id]

    def get_packs_by_media(self, media_id: str) -> list[dict[str, Any]]:
        """获取指定媒体源的所有字幕包。"""
        rows = store.rows(self.MODULE)
        return [r for r in rows if r.get("关联媒体源") == media_id]

    def verification_result(self, pack_id: str) -> dict[str, Any] | None:
        """读取服务端统一的签收核验结论，与交付回执共用同一份。"""
        return signoff_registry.shared_result(pack_id)

    def mark_invalid(self, pack_id: str) -> tuple[bool, str]:
        """标记字幕包为失效（版本被覆盖后）。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("字幕包编号") == pack_id:
                r["status"] = "已失效"
                r["abnormal"] = True
                return True, f"字幕包 {pack_id} 已标记失效"
        return False, f"字幕包 {pack_id} 不存在"

    def increment_download(self, pack_id: str) -> tuple[bool, int]:
        """增加下载次数。"""
        rows = store.rows(self.MODULE)
        for r in rows:
            if r.get("字幕包编号") == pack_id:
                count = int(r.get("下载次数", "0")) + 1
                r["下载次数"] = str(count)
                return True, count
        return False, 0