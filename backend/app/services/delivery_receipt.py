"""交付回执写入领域服务：记录字幕包交付接收方的行数与签收结论，产出回执记录数据集供归档架位与审计核查消费。"""
from __future__ import annotations

from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.signoff import signoff_registry
from app.store import store


class DeliveryReceiptService(BaseModuleService):
    MODULE = "delivery_receipt"
    ENTITY = "回执记录"
    OPERATION_TERM = "写入"
    LIST_FIELDS = ["回执编号", "关联字幕包", "接收方标识", "交付行数", "交付格式", "交付结论", "重交次数", "交付时间"]
    REQUIRED_FIELDS = ["回执编号", "关联字幕包", "接收方标识"]
    STATUS_ORDER = ["待交付", "交付中", "已签收", "部分签收"]
    ACTION_RULES = {"启动交付": "交付中", "确认签收": "已签收", "登记部分签收": "部分签收"}
    NEGATIVE_ACTIONS = []

    def verification_result(self, receipt_id: str) -> dict[str, Any] | None:
        """按回执找到关联字幕包，读取与字幕包同一份服务端核验结论。"""
        for row in store.rows(self.MODULE):
            if row.get("回执编号") == receipt_id:
                return signoff_registry.shared_result(str(row.get("关联字幕包", "")))
        return None

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """确认签收必须先有与字幕包共用的服务端核验结论。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or action != "确认签收":
            return super().run_action(entry_id, action)
        result = self.verification_result(str(entry.get("回执编号", "")))
        if result is None:
            return None, "尚无与字幕包共用的服务端核验结论，不能单独确认签收"
        if not result.get("ok"):
            return None, (
                f"核验结论为「{result.get('verdict')}」，"
                "交付回执不能早于字幕包清单确认签收"
            )
        updated, message = super().run_action(entry_id, action)
        if updated is not None:
            updated["交付结论"] = f"签收（核验 {result.get('result_id')}）"
            updated["pending"] = False
        return updated, message

    def confirm_signoff(self, receipt_id: str, actor: str) -> tuple[bool, str]:
        """确认签收前必须已有可核验的服务端结论，避免回执与清单各说一套。"""
        result = self.verification_result(receipt_id)
        if result is None:
            return False, "尚无与字幕包共用的服务端核验结论，不能确认签收"
        if not result.get("ok"):
            return False, f"核验结论为「{result.get('verdict')}」，不能确认签收"
        for row in store.rows(self.MODULE):
            if row.get("回执编号") == receipt_id:
                row["status"] = "已签收"
                row["pending"] = False
                row["abnormal"] = False
                row["交付结论"] = f"签收（核验 {result.get('result_id')}）"
                return True, f"回执 {receipt_id} 已按服务端核验结论签收"
        return False, f"回执 {receipt_id} 不存在"
