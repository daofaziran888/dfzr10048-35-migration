"""历史签收迁移与 V2 交付清单：领域服务与接口测试。"""
from __future__ import annotations

import pytest

from app.services.signoff import (
    MIGRATION_BLOCKED,
    MANIFEST_ACTIVE,
    VERDICT_MISMATCH,
    VERDICT_PARTIAL,
    VERDICT_PENDING,
    VERDICT_UNVERIFIABLE,
    VERDICT_VERIFIED,
    SignoffRegistry,
    legacy8,
    line_digest,
    parse_subtitle_lines,
    sha256_hex,
)
from app.seed_signoff import SBPK_0003_V1_SRT


V2_CORRECTION = """1
00:00:00,000 --> 00:00:02,500
欢迎收看年度技术峰会

2
00:00:02,500 --> 00:00:06,000
今天我们讨论数据采集流水线

3
00:00:06,000 --> 00:00:09,500
以及端到端设计理念（V2 更正）
"""


@pytest.fixture()
def registry() -> SignoffRegistry:
    return SignoffRegistry()


def v1_bytes() -> bytes:
    return SBPK_0003_V1_SRT.encode("utf-8")


def corrected_bytes() -> bytes:
    return V2_CORRECTION.encode("utf-8")


class TestLegacyRecord:
    def test_legacy_signoff_keeps_pack_number_checksum_and_source(self, registry):
        legacy = registry.read_legacy_signoff("SBPK-0003")
        assert legacy is not None
        assert legacy["原包号"] == "SBPK-0003"
        assert legacy["旧校验算法"] == "md5-8"
        assert len(legacy["旧校验"]) == 8
        assert legacy["实际来源"].startswith("跨平台内容分发渠道")
        assert legacy["逐文件指纹"][0]["整包指纹"].startswith("sha256:")

    def test_legacy_checksum_matches_existing_eight_char_assembly(self, registry):
        assert registry.read_legacy_signoff("SBPK-0003")["旧校验"] == legacy8(v1_bytes())

    def test_legacy_record_survives_migration_and_rollback(self, registry):
        before = registry.read_legacy_signoff("SBPK-0003")
        registry.start_migration("SBPK-0003", "值班员")
        registry.confirm_migration("SBPK-0003", "值班员")
        registry.rollback_migration("SBPK-0003", "值班员")
        after = registry.read_legacy_signoff("SBPK-0003")
        assert after == before


class TestManifest:
    def test_manifest_verifies_actual_subtitle_lines(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        result = registry.confirm_migration("SBPK-0003", "值班员")
        assert result["ok"]
        manifest = result["manifest"]
        assert manifest["清单版本"] == "V2"
        assert manifest["status"] == MANIFEST_ACTIVE
        entry = manifest["files"][0]
        assert entry["核验状态"] == "可逐项复验"
        assert entry["已核验行数"] == entry["字幕行数"] == 3
        assert [row["实际字幕"] for row in entry["逐行"]] == parse_subtitle_lines(v1_bytes())
        assert all(row["核验"] for row in entry["逐行"])

    def test_confirm_reports_who_confirmed_new_version(self, registry):
        registry.start_migration("SBPK-0003", "值班员甲")
        result = registry.confirm_migration("SBPK-0003", "值班长乙")
        assert result["manifest"]["confirmed_by"] == "值班长乙"
        verify = registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})
        assert verify["confirmed_by"] == "值班长乙"
        detail = registry.detail("SBPK-0003")
        assert detail["migration"]["confirmed_by"] == "值班长乙"

    def test_manifest_blocked_when_original_missing(self, registry):
        registry.start_migration("SBPK-0002", "值班员")
        result = registry.confirm_migration("SBPK-0002", "值班员")
        assert not result["ok"]
        assert "无法复验" in result["message"]
        assert "缺少签收方归档 V1 原件" in result["manifest"]["blocked_reason"]
        assert registry.detail("SBPK-0002")["migration"]["status"] == MIGRATION_BLOCKED

    def test_missing_original_then_receiver_supplies_it(self, registry):
        registry.start_migration("SBPK-0002", "值班员")
        blocked = registry.confirm_migration("SBPK-0002", "值班员")
        assert not blocked["ok"]
        from app.seed_signoff import SBPK_0002_V1_SRT
        result = registry.verify_pack(
            "SBPK-0002", "视频归档系统", {"SBPK-0002.v1.srt": SBPK_0002_V1_SRT.encode()},
        )
        # 迁移确认还没成功，提交原件只能先留存排队，缺失环节要指出还缺哪一环
        assert result["verdict"] in (VERDICT_UNVERIFIABLE, VERDICT_PENDING)
        assert any("排队" in link for link in result["missing_links"])
        registry.start_migration("SBPK-0002", "值班员")
        confirmed = registry.confirm_migration("SBPK-0002", "值班员")
        assert confirmed["ok"]
        assert confirmed["manifest"]["files"][0]["纳入来源"] == "接收方提交的签收原件"


class TestOriginalDownload:
    def test_download_returns_signed_bytes(self, registry):
        data, source, reason = registry.get_original("SBPK-0003", "SBPK-0003.v1.srt")
        assert data == v1_bytes()
        assert reason is None
        assert source == "签收方归档原件"

    def test_missing_original_reports_missing_link(self, registry):
        data, source, reason = registry.get_original("SBPK-0002", "SBPK-0002.v1.srt")
        assert data is None
        assert "缺少签收方归档 V1 原件" in reason

    def test_correction_never_overwrites_original(self, registry):
        registry.write_correction("SBPK-0003", "SBPK-0003.v1.srt", V2_CORRECTION, "更正人")
        data, source, _ = registry.get_original("SBPK-0003", "SBPK-0003.v1.srt")
        assert data == v1_bytes()
        assert source == "签收方归档原件"
        detail = registry.detail("SBPK-0003")
        assert detail["workspace"][0]["与签收一致"] is False

    def test_correction_does_not_change_frozen_migration_snapshot(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        registry.write_correction("SBPK-0003", "SBPK-0003.v1.srt", V2_CORRECTION, "更正人")
        result = registry.confirm_migration("SBPK-0003", "值班员")
        assert result["ok"]
        entry = result["manifest"]["files"][0]
        assert entry["纳入来源"] == "签收方归档原件"
        assert entry["整包指纹"] == "sha256:" + sha256_hex(v1_bytes())

    def test_workspace_file_with_identical_bytes_may_stand_in(self, registry):
        # 同名文件且字节与签收一致时才可纳入
        registry.write_correction("SBPK-0002", "SBPK-0002.v1.srt", "", "占位")
        from app.seed_signoff import SBPK_0002_V1_SRT
        registry._workspace[("SBPK-0002", "SBPK-0002.v1.srt")]["data"] = SBPK_0002_V1_SRT.encode()
        data, source, _ = registry.get_original("SBPK-0002", "SBPK-0002.v1.srt")
        assert data is not None and source == "同位置工作区文件"


class TestConcurrentVerify:
    def test_verify_before_migration_starts_is_queued(self, registry):
        result = registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})
        assert result["verdict"] == "迁移未开始"
        assert result["request_id"]
        registry.start_migration("SBPK-0003", "值班员")
        confirmed = registry.confirm_migration("SBPK-0003", "值班员")
        assert confirmed["ok"]
        assert confirmed["resolved_requests"][0]["status"] == "已完成"
        assert confirmed["resolved_requests"][0]["verdict"] == VERDICT_VERIFIED

    def test_verify_arrives_while_migration_running(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        result = registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})
        assert result["verdict"] == VERDICT_PENDING
        assert "排队" in result["message"]
        confirmed = registry.confirm_migration("SBPK-0003", "值班员")
        resolved = confirmed["resolved_requests"][0]
        assert resolved["confirmed_by"] == "值班员"
        shared = registry.shared_result("SBPK-0003")
        assert shared["ok"] and shared["verdict"] == VERDICT_VERIFIED

    def test_verify_with_correct_bytes_after_confirmation(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        registry.confirm_migration("SBPK-0003", "值班员")
        result = registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})
        assert result["ok"]
        assert result["verdict"] == VERDICT_VERIFIED
        assert result["files"][0]["已核验行数"] == 3
        assert all(row["核验"] for row in result["files"][0]["逐行"])
        assert registry.shared_result("SBPK-0003")["result_id"] == result["result_id"]


class TestV2CannotStandInForV1:
    def test_v2_same_name_rejected_before_migration(self, registry):
        result = registry.verify_pack(
            "SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": corrected_bytes()},
        )
        assert result["verdict"] == VERDICT_MISMATCH
        assert not result["ok"]
        assert any("不可代验 V1" in link for link in result["missing_links"])

    def test_v2_rejected_before_confirmation_is_not_queued_as_valid(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        rejected = registry.verify_pack(
            "SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": corrected_bytes()},
        )
        assert rejected["verdict"] == VERDICT_MISMATCH
        assert registry.detail("SBPK-0003")["pending"] == []
        confirmed = registry.confirm_migration("SBPK-0003", "值班员")
        assert confirmed["ok"]
        assert confirmed["resolved_requests"] == []
        # 清单自身的字节来自签收原件，不会因为有人递过 V2 文件而被动过
        assert confirmed["manifest"]["files"][0]["整包指纹"] == "sha256:" + sha256_hex(v1_bytes())

    def test_v2_same_name_rejected_after_reactivation(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        registry.confirm_migration("SBPK-0003", "值班员")
        registry.rollback_migration("SBPK-0003", "值班员")
        registry.reactivate_migration("SBPK-0003", "值班员")
        result = registry.verify_pack(
            "SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": corrected_bytes()},
        )
        assert result["verdict"] == VERDICT_MISMATCH
        assert registry.shared_result("SBPK-0003")["ok"] is False

    def test_v2_same_name_rejected_after_migration(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        registry.confirm_migration("SBPK-0003", "值班员")
        result = registry.verify_pack(
            "SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": corrected_bytes()},
        )
        assert result["verdict"] == VERDICT_MISMATCH
        assert registry.shared_result("SBPK-0003")["ok"] is False

    def test_v2_workspace_file_cannot_be_downloaded_as_original(self, registry):
        registry.write_correction("SBPK-0003", "SBPK-0003.v1.srt", V2_CORRECTION, "更正人")
        # 原件库仍在，仍返回 V1 字节
        data, _, _ = registry.get_original("SBPK-0003", "SBPK-0003.v1.srt")
        assert data == v1_bytes()
        # 若原件库也缺失，只剩 V2 工作区字节，则必须报缺环而不是拿 V2 顶替
        registry._originals.pop(("SBPK-0003", "SBPK-0003.v1.srt"))
        data, source, reason = registry.get_original("SBPK-0003", "SBPK-0003.v1.srt")
        assert data is None
        assert "不可代验 V1" in reason

    def test_manifest_never_includes_mismatched_bytes(self, registry):
        registry.write_correction("SBPK-0002", "SBPK-0002.v1.srt", "完全不同", "更正人")
        registry.start_migration("SBPK-0002", "值班员")
        result = registry.confirm_migration("SBPK-0002", "值班员")
        assert not result["ok"]
        assert result["manifest"]["status"] != MANIFEST_ACTIVE


class TestRollbackAndReactivation:
    def test_rollback_then_reactivate_reuses_single_manifest(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        first = registry.confirm_migration("SBPK-0003", "值班员")
        manifest_id = first["manifest"]["manifest_id"]
        registry.rollback_migration("SBPK-0003", "值班员")
        reactivated = registry.reactivate_migration("SBPK-0003", "值班员")
        assert reactivated[0]
        assert registry.detail("SBPK-0003")["manifest"]["manifest_id"] == manifest_id
        assert len(registry._manifests) == 1

    def test_rollback_stops_verification_from_being_valid(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        registry.confirm_migration("SBPK-0003", "值班员")
        registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})
        registry.rollback_migration("SBPK-0003", "值班员")
        shared = registry.shared_result("SBPK-0003")
        assert shared is not None
        assert not shared["ok"]
        assert shared["verdict"] == "清单已回滚"
        result = registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})
        assert not result["ok"]
        assert result["verdict"] == "清单已回滚"

    def test_shared_consumers_see_invalidated_result_after_rollback(self):
        from app.services.delivery_receipt import DeliveryReceiptService
        from app.services.signoff import signoff_registry
        from app.services.subtitle_pack import SubtitlePackService

        signoff_registry.reset()
        signoff_registry.start_migration("SBPK-0003", "值班员")
        signoff_registry.confirm_migration("SBPK-0003", "值班员")
        signoff_registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})
        signoff_registry.rollback_migration("SBPK-0003", "值班员")
        assert not SubtitlePackService().verification_result("SBPK-0003")["ok"]
        assert not DeliveryReceiptService().verification_result("DLRC-0003")["ok"]

    def test_reactivate_clears_invalidated_result(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        registry.confirm_migration("SBPK-0003", "值班员")
        registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})
        registry.rollback_migration("SBPK-0003", "值班员")
        registry.reactivate_migration("SBPK-0003", "值班员")
        assert registry.shared_result("SBPK-0003") is None

    def test_rollback_does_not_verify_v1(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        registry.confirm_migration("SBPK-0003", "值班员")
        registry.rollback_migration("SBPK-0003", "值班员")
        assert registry.detail("SBPK-0003")["manifest"]["status"] == "已回滚"
        assert not registry.verify_pack(
            "SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()},
        )["ok"]

    def test_cannot_create_second_manifest_after_rollback(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        registry.confirm_migration("SBPK-0003", "值班员")
        registry.rollback_migration("SBPK-0003", "值班员")
        ok, message = registry.start_migration("SBPK-0003", "值班员")
        assert not ok
        assert "切回" in message
        assert len(registry._manifests) == 1

    def test_reactivate_after_rollback_verifies_again(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        registry.confirm_migration("SBPK-0003", "值班员")
        registry.rollback_migration("SBPK-0003", "值班员")
        registry.reactivate_migration("SBPK-0003", "值班长丙")
        result = registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})
        assert result["ok"]
        assert result["confirmed_by"] == "值班长丙"


class TestSharedResult:
    def test_shared_result_flagged_for_both_consumers(self, registry):
        registry.start_migration("SBPK-0003", "值班员")
        registry.confirm_migration("SBPK-0003", "值班员")
        registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})
        shared = registry.shared_result("SBPK-0003")
        assert shared["shared"] is True
        assert shared["consumers"] == ["subtitle_pack", "delivery_receipt"]

    def test_subtitle_pack_and_receipt_share_result(self):
        from app.services.delivery_receipt import DeliveryReceiptService
        from app.services.subtitle_pack import SubtitlePackService
        from app.services.signoff import signoff_registry

        signoff_registry.reset()
        signoff_registry.start_migration("SBPK-0003", "值班员")
        signoff_registry.confirm_migration("SBPK-0003", "值班员")
        signoff_registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})
        pack_view = SubtitlePackService().verification_result("SBPK-0003")
        receipt_view = DeliveryReceiptService().verification_result("DLRC-0003")
        assert pack_view is not None and receipt_view is not None
        assert pack_view["result_id"] == receipt_view["result_id"]
        assert pack_view["verdict"] == receipt_view["verdict"]


class TestFingerprints:
    def test_line_digest_matches_subtitle_text(self):
        lines = parse_subtitle_lines(v1_bytes())
        assert line_digest(lines[0]) == "sha256:" + __import__("hashlib").sha256(
            "欢迎收看年度技术峰会".encode(),
        ).hexdigest()

    def test_parse_ignores_index_and_timeline_rows(self):
        lines = parse_subtitle_lines(v1_bytes())
        assert len(lines) == 3
        assert not any("-->" in line or line.isdigit() for line in lines)

    def test_unknown_pack(self, registry):
        assert registry.detail("NOPE") is None
        result = registry.verify_pack("NOPE", "接收方", {})
        assert result["verdict"] == VERDICT_UNVERIFIABLE


class TestDeliveryReceiptGate:
    def _prepare_verified(self):
        from app.services.signoff import signoff_registry

        signoff_registry.reset()
        signoff_registry.start_migration("SBPK-0003", "值班员")
        signoff_registry.confirm_migration("SBPK-0003", "值班员")
        signoff_registry.verify_pack("SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": v1_bytes()})

    def test_receipt_cannot_be_signed_without_shared_result(self):
        from app.services.delivery_receipt import DeliveryReceiptService
        from app.services.signoff import signoff_registry

        signoff_registry.reset()
        service = DeliveryReceiptService()
        entry, message = service.run_action(3, "确认签收")
        assert entry is None
        assert "核验结论" in message

    def test_receipt_blocks_when_shared_conclusion_is_mismatch(self):
        from app.services.delivery_receipt import DeliveryReceiptService
        from app.services.signoff import signoff_registry

        signoff_registry.reset()
        signoff_registry.start_migration("SBPK-0003", "值班员")
        signoff_registry.confirm_migration("SBPK-0003", "值班员")
        signoff_registry.verify_pack(
            "SBPK-0003", "媒体检索平台", {"SBPK-0003.v1.srt": corrected_bytes()},
        )
        entry, message = DeliveryReceiptService().run_action(3, "确认签收")
        assert entry is None
        assert "核验不符" in message

    def test_receipt_signs_on_shared_verified_result(self):
        from app.services.delivery_receipt import DeliveryReceiptService

        self._prepare_verified()
        entry, _ = DeliveryReceiptService().run_action(3, "确认签收")
        assert entry is not None
        assert entry["status"] == "已签收"
        assert entry["交付结论"].startswith("签收（核验 SVR-")


class TestApi:
    def test_endpoints(self):
        from app.routers.signoff import router

        paths = {route.path for route in router.routes}
        assert "/api/signoff/packs" in paths
        assert "/api/signoff/packs/{pack_no}/verify" in paths
        assert "/api/signoff/packs/{pack_no}/legacy" in paths
        assert "/api/signoff/packs/{pack_no}/originals/{filename}" in paths
        assert "/api/signoff/packs/{pack_no}/migration/confirm" in paths
        assert "/api/signoff/packs/{pack_no}/shared-result" in paths
