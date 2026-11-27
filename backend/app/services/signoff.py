"""字幕包历史签收迁移与 V2 交付清单领域服务。

现有装配流程只给字幕包算八位校验，旧签收因此只能证明“整包大概没变”。本服务把
历史签收迁移成 V2 交付清单：

- 旧签收记录（原包号、八位旧校验、实际来源、签收时逐文件指纹）始终可读，
  部署切换后保留 `read_legacy_signoff` 这条旧读取依赖；
- V2 清单逐文件、逐条字幕记录签收指纹与实际字节的核验结果；
- 签收方原件与同位置工作区分离：后来写入的 V2 更正只落工作区，永远不覆盖
  签收时留存的原件；
- 同名文件只有整包字节与签收指纹一致才可纳入，差异字节一律标记“核验不符”；
- 同一字幕包最多只有一份 V2 清单，回滚后再切回复用并重新激活同一份清单；
- 复验请求可以与迁移确认乱序到达：字节和指纹一致的请求先排队，确认后自动完成；
  字节不符的请求当场拒绝；
- 字幕包与交付回执都调用 `shared_result`，共用同一份服务端核验结论。
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.seed_signoff import SIGNOFF_SEEDS

SIGNOFF_ARCHIVE_SOURCE = "签收方归档原件"
RECEIVER_ORIGINAL_SOURCE = "接收方提交的签收原件"
WORKSPACE_SOURCE = "同位置工作区文件"

MIGRATION_NOT_STARTED = "未开始"
MIGRATION_RUNNING = "迁移中"
MIGRATION_CONFIRMED = "已确认"
MIGRATION_ROLLED_BACK = "已回滚"
MIGRATION_BLOCKED = "确认受阻"

MANIFEST_DRAFT = "草稿"
MANIFEST_ACTIVE = "已生效"
MANIFEST_BLOCKED = "阻断"
MANIFEST_ROLLED_BACK = "已回滚"

VERDICT_PENDING = "待迁移确认"
VERDICT_VERIFIED = "已复验"
VERDICT_PARTIAL = "部分复验"
VERDICT_UNVERIFIABLE = "无法复验"
VERDICT_MISMATCH = "核验不符"
VERDICT_NOT_STARTED = "迁移未开始"
VERDICT_ROLLED_BACK = "清单已回滚"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def legacy8(data: bytes) -> str:
    """现有装配流程只产出的八位校验，保留用于旧签收比对。"""
    return hashlib.md5(data).hexdigest()[:8]


def line_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", "replace")


def parse_subtitle_lines(data: bytes) -> list[str]:
    """抽取实际字幕行：序号行、空行与时间轴行都不是字幕条目。"""
    lines: list[str] = []
    for raw in _decode(data).splitlines():
        text = raw.strip()
        if not text or text.isdigit() or "-->" in text:
            continue
        lines.append(text)
    return lines


@dataclass
class LegacyFile:
    filename: str
    data: bytes
    sha256: str
    legacy_checksum: str
    line_texts: list[str] = field(default_factory=list)
    line_digests: list[str] = field(default_factory=list)


@dataclass
class LegacySignoff:
    pack_no: str
    media_id: str
    receiver: str
    signer_party: str
    signed_at: str
    actual_source: str
    legacy_checksum: str
    files: dict[str, LegacyFile]

    def to_dict(self, *, include_bytes: bool = False) -> dict[str, Any]:
        return {
            "原包号": self.pack_no,
            "关联媒体源": self.media_id,
            "接收方标识": self.receiver,
            "签收方": self.signer_party,
            "签收时间": self.signed_at,
            "实际来源": self.actual_source,
            "旧校验算法": "md5-8",
            "旧校验": self.legacy_checksum,
            "逐文件指纹": [
                {
                    "文件名": f.filename,
                    "字节数": len(f.data),
                    "整包指纹": "sha256:" + f.sha256,
                    "八位旧校验": f.legacy_checksum,
                    "字幕行数": len(f.line_texts),
                    "逐行指纹": f.line_digests,
                    "签收字节": _decode(f.data) if include_bytes else None,
                }
                for f in self.files.values()
            ],
        }


def _seed_fingerprint(content: str) -> LegacyFile:
    data = content.encode("utf-8")
    lines = parse_subtitle_lines(data)
    return LegacyFile(
        filename="",
        data=data,
        sha256=sha256_hex(data),
        legacy_checksum=legacy8(data),
        line_texts=lines,
        line_digests=[line_digest(text) for text in lines],
    )


class SignoffRegistry:
    """历史签收与 V2 清单的唯一状态机。"""

    def __init__(self, seeds: list[dict[str, Any]] | None = None) -> None:
        self._lock = threading.RLock()
        self.reset(seeds)

    # ------------------------------------------------------------------ 状态
    def reset(self, seeds: list[dict[str, Any]] | None = None) -> None:
        with self._lock:
            self._signoffs: dict[str, LegacySignoff] = {}
            self._originals: dict[tuple[str, str], bytes] = {}
            self._receiver_originals: dict[tuple[str, str, str], bytes] = {}
            self._workspace: dict[tuple[str, str], dict[str, Any]] = {}
            self._migrations: dict[str, dict[str, Any]] = {}
            self._manifests: dict[str, dict[str, Any]] = {}
            self._pending: dict[str, list[dict[str, Any]]] = {}
            self._results: dict[str, dict[str, Any]] = {}
            self._audit: dict[str, list[dict[str, Any]]] = {}
            self._result_seq = 0
            for seed in seeds if seeds is not None else SIGNOFF_SEEDS:
                self._load_seed(seed)

    def _load_seed(self, seed: dict[str, Any]) -> None:
        pack_no = seed["pack_no"]
        files: dict[str, LegacyFile] = {}
        for item in seed["files"]:
            fp = _seed_fingerprint(item["content"])
            fp.filename = item["filename"]
            files[fp.filename] = fp
            if item["filename"] in set(seed.get("originals", [])):
                self._originals[(pack_no, item["filename"])] = fp.data
        joined = b"".join(fp.data for fp in files.values())
        self._signoffs[pack_no] = LegacySignoff(
            pack_no=pack_no,
            media_id=seed["media_id"],
            receiver=seed["receiver"],
            signer_party=seed["signer_party"],
            signed_at=seed["signed_at"],
            actual_source=seed["actual_source"],
            legacy_checksum=legacy8(joined),
            files=files,
        )
        self._migrations[pack_no] = {
            "pack_no": pack_no,
            "status": MIGRATION_NOT_STARTED,
            "started_at": None,
            "started_by": None,
            "confirmed_at": None,
            "confirmed_by": None,
            "rolled_back_at": None,
            "rolled_back_by": None,
            "read_files": [],
            "missing_files": [],
            "blocked_reason": None,
            "snapshot": {},
            "snapshot_sources": {},
        }
        self._audit[pack_no] = []

    # ------------------------------------------------------------------ 工具
    def _signoff(self, pack_no: str) -> LegacySignoff | None:
        return self._signoffs.get(pack_no)

    def _log(self, pack_no: str, action: str, actor: str, detail: str) -> None:
        self._audit.setdefault(pack_no, []).append({
            "pack_no": pack_no,
            "action": action,
            "actor": actor,
            "detail": detail,
            "at": _now(),
        })

    def _next_result_id(self) -> str:
        self._result_seq += 1
        return f"SVR-{self._result_seq:04d}"

    def _workspace_bytes(self, pack_no: str, filename: str) -> bytes | None:
        item = self._workspace.get((pack_no, filename))
        return item["data"] if item else None

    # -------------------------------------------------- 旧签收读取依赖（保留）
    def read_legacy_signoff(self, pack_no: str) -> dict[str, Any] | None:
        """旧签收读取依赖：部署切换、迁移和回滚之后都保持可用。"""
        signoff = self._signoff(pack_no)
        return signoff.to_dict(include_bytes=False) if signoff else None

    # ------------------------------------------------------------- 列表/详情
    def list_packs(self) -> list[dict[str, Any]]:
        with self._lock:
            packs: list[dict[str, Any]] = []
            for pack_no, signoff in self._signoffs.items():
                migration = self._migrations[pack_no]
                manifest = self._manifests.get(pack_no)
                packs.append({
                    "原包号": pack_no,
                    "关联媒体源": signoff.media_id,
                    "接收方标识": signoff.receiver,
                    "签收时间": signoff.signed_at,
                    "旧校验": signoff.legacy_checksum,
                    "实际来源": signoff.actual_source,
                    "迁移状态": migration["status"],
                    "清单状态": manifest["status"] if manifest else "未迁移",
                    "清单编号": manifest["manifest_id"] if manifest else None,
                    "原件可下载": [
                        name for name in signoff.files
                        if self.get_original(pack_no, name)[0] is not None
                    ],
                    "待确认复验数": sum(
                        1 for p in self._pending.get(pack_no, [])
                        if p["status"] == "排队中"
                    ),
                })
            return packs

    def detail(self, pack_no: str) -> dict[str, Any] | None:
        with self._lock:
            signoff = self._signoff(pack_no)
            if signoff is None:
                return None
            migration = self._migrations[pack_no]
            manifest = self._manifests.get(pack_no)
            originals = []
            for filename in signoff.files:
                data, source, reason = self.get_original(pack_no, filename)
                originals.append({
                    "文件名": filename,
                    "可下载": data is not None,
                    "来源": source,
                    "缺失原因": reason,
                    "字节数": len(data) if data is not None else 0,
                    "指纹": "sha256:" + signoff.files[filename].sha256,
                })
            workspace = [
                {
                    "文件名": key[1],
                    "写入者": item["actor"],
                    "写入时间": item["at"],
                    "指纹": "sha256:" + sha256_hex(item["data"]),
                    "与签收一致": sha256_hex(item["data"]) == signoff.files[key[1]].sha256,
                    "备注": "仅工作区，签收原件库未被覆盖",
                }
                for key, item in self._workspace.items()
                if key[0] == pack_no and key[1] in signoff.files
            ]
            return {
                "signoff": signoff.to_dict(include_bytes=False),
                "migration": {
                    key: value
                    for key, value in migration.items()
                    if key != "snapshot"
                },
                "snapshot_files": sorted(migration["snapshot"]),
                "manifest": manifest,
                "originals": originals,
                "workspace": workspace,
                "pending": self._pending.get(pack_no, []),
                "last_result": self._shared_or_invalidated(pack_no),
                "audit": self._audit.get(pack_no, []),
            }

    # --------------------------------------------------------- 原件与工作区
    def expected_fingerprint(self, pack_no: str, filename: str) -> str | None:
        signoff = self._signoff(pack_no)
        if signoff is None or filename not in signoff.files:
            return None
        return "sha256:" + signoff.files[filename].sha256

    def get_original(
        self, pack_no: str, filename: str,
    ) -> tuple[bytes | None, str | None, str | None]:
        """返回真正能下载、能证明的 V1 原件；绝不返回同位置工作区字节。"""
        with self._lock:
            signoff = self._signoff(pack_no)
            if signoff is None or filename not in signoff.files:
                return None, None, "签收记录中没有该文件"
            expected = signoff.files[filename].sha256
            archived = self._originals.get((pack_no, filename))
            if archived is not None and sha256_hex(archived) == expected:
                return archived, SIGNOFF_ARCHIVE_SOURCE, None
            receiver = self._receiver_originals.get((pack_no, filename, expected))
            if receiver is not None:
                return receiver, RECEIVER_ORIGINAL_SOURCE, None
            workspace = self._workspace_bytes(pack_no, filename)
            if workspace is not None and sha256_hex(workspace) == expected:
                # 只有同名字节与签收指纹完全相符时，工作区文件才可以充当原件。
                return workspace, WORKSPACE_SOURCE, None
            if workspace is not None:
                return None, None, (
                    "同位置仅有 V2 更正字节（sha256:" + sha256_hex(workspace)[:12] +
                    "…），与签收指纹不符，不可代验 V1；缺少签收方归档 V1 原件"
                )
            return None, None, "缺少签收方归档 V1 原件，且接收方尚未提交签收时原件"

    def write_correction(
        self, pack_no: str, filename: str, content: str, actor: str,
    ) -> tuple[bool, str]:
        """V2 更正只写入同位置工作区，签收原件库与迁移快照都不受影响。"""
        with self._lock:
            signoff = self._signoff(pack_no)
            if signoff is None:
                return False, f"原包号 {pack_no} 没有历史签收"
            if filename not in signoff.files:
                return False, f"{pack_no} 的签收清单里没有 {filename}"
            data = content.encode("utf-8")
            before = self._workspace_bytes(pack_no, filename)
            self._workspace[(pack_no, filename)] = {
                "data": data,
                "actor": actor,
                "at": _now(),
            }
            same = sha256_hex(data) == signoff.files[filename].sha256
            self._log(
                pack_no, "写入 V2 更正", actor,
                f"{filename} 工作区指纹 sha256:{sha256_hex(data)[:12]}…，"
                + ("与签收字节一致" if same else "与签收字节存在差异")
                + "；签收原件库未覆盖",
            )
            if before is not None:
                return True, f"{filename} 工作区已更新（不覆盖签收原件）"
            return True, f"{filename} 更正已写入同位置工作区（不覆盖签收原件）"

    def _remember_receiver_originals(
        self, pack_no: str, submitted: dict[str, bytes],
    ) -> list[str]:
        """只留存与签收指纹完全一致的接收方原件，用于补齐缺失环节。"""
        signoff = self._signoffs[pack_no]
        remembered = []
        for filename, data in submitted.items():
            legacy_file = signoff.files.get(filename)
            if legacy_file is None:
                continue
            if sha256_hex(data) == legacy_file.sha256:
                self._receiver_originals[(pack_no, filename, legacy_file.sha256)] = data
                remembered.append(filename)
        return remembered

    # -------------------------------------------------------------- 迁移流程
    def start_migration(
        self, pack_no: str, actor: str,
    ) -> tuple[bool, str]:
        """读取 V1 原件并冻结快照；此后的同位置更正不会改变迁移读到的字节。"""
        with self._lock:
            signoff = self._signoff(pack_no)
            if signoff is None:
                return False, f"原包号 {pack_no} 没有历史签收"
            migration = self._migrations[pack_no]
            if migration["status"] == MIGRATION_RUNNING:
                return False, f"{pack_no} 迁移正在进行，先确认或回滚"
            if migration["status"] == MIGRATION_CONFIRMED:
                return False, f"{pack_no} 迁移已确认，清单已生效"
            if migration["status"] == MIGRATION_ROLLED_BACK:
                # 回滚后再切回必须复用原清单，不能借重新迁移造出第二份有效清单。
                return False, f"{pack_no} 已回滚，请使用“切回”复用同一份 V2 清单"
            snapshot: dict[str, bytes] = {}
            sources: dict[str, str] = {}
            missing: list[str] = []
            for filename in signoff.files:
                data, source, _ = self.get_original(pack_no, filename)
                if data is None:
                    missing.append(filename)
                else:
                    snapshot[filename] = data
                    sources[filename] = source or ""
            migration.update({
                "status": MIGRATION_RUNNING,
                "started_at": _now(),
                "started_by": actor,
                "confirmed_at": None,
                "confirmed_by": None,
                "rolled_back_at": None,
                "rolled_back_by": None,
                "read_files": sorted(snapshot),
                "missing_files": missing,
                "blocked_reason": None,
                "snapshot": snapshot,
                "snapshot_sources": sources,
            })
            self._log(
                pack_no, "启动迁移", actor,
                f"读取 V1 文件 {len(snapshot)} 个" +
                (f"，缺原件 {', '.join(missing)}" if missing else ""),
            )
            if missing:
                return True, (
                    f"已读取 {len(snapshot)} 个 V1 文件，仍缺 {', '.join(missing)}；"
                    "需接收方提交签收时原件后才能确认清单"
                )
            return True, f"已读取 {len(snapshot)} 个 V1 文件并冻结迁移快照"

    def confirm_migration(self, pack_no: str, actor: str) -> dict[str, Any]:
        """生成 V2 交付清单并处理排队的复验请求。"""
        with self._lock:
            signoff = self._signoff(pack_no)
            if signoff is None:
                return {"ok": False, "message": f"原包号 {pack_no} 没有历史签收"}
            migration = self._migrations[pack_no]
            if migration["status"] == MIGRATION_CONFIRMED:
                return {"ok": False, "message": f"{pack_no} 迁移已确认，清单已生效"}
            if migration["status"] == MIGRATION_ROLLED_BACK:
                return {"ok": False, "message": f"{pack_no} 迁移已回滚，请先重新启用清单"}
            if migration["status"] not in (MIGRATION_RUNNING, MIGRATION_BLOCKED):
                return {"ok": False, "message": f"{pack_no} 尚未启动迁移，不能确认"}

            manifest = self._build_manifest(pack_no, migration)
            self._manifests[pack_no] = manifest
            if manifest["status"] == MANIFEST_BLOCKED:
                migration["status"] = MIGRATION_BLOCKED
                migration["blocked_reason"] = manifest["blocked_reason"]
                self._log(pack_no, "确认迁移", actor, "清单阻断：" + str(manifest["blocked_reason"]))
                return {
                    "ok": False,
                    "message": "无法复验：" + str(manifest["blocked_reason"]),
                    "manifest": manifest,
                }

            migration.update({
                "status": MIGRATION_CONFIRMED,
                "confirmed_at": _now(),
                "confirmed_by": actor,
                "blocked_reason": None,
            })
            manifest.update({
                "status": MANIFEST_ACTIVE,
                "confirmed_at": migration["confirmed_at"],
                "confirmed_by": actor,
            })
            self._log(pack_no, "确认迁移", actor, f"V2 清单 {manifest['manifest_id']} 生效")

            resolved = self._resolve_pending(pack_no)
            return {
                "ok": True,
                "message": f"V2 交付清单已生效，确认方：{actor}",
                "manifest": manifest,
                "resolved_requests": resolved,
            }

    def rollback_migration(self, pack_no: str, actor: str) -> tuple[bool, str]:
        """回滚只停用清单，旧签收读取依赖与原件库都保留。"""
        with self._lock:
            signoff = self._signoff(pack_no)
            if signoff is None:
                return False, f"原包号 {pack_no} 没有历史签收"
            migration = self._migrations[pack_no]
            manifest = self._manifests.get(pack_no)
            if migration["status"] != MIGRATION_CONFIRMED or manifest is None:
                return False, f"{pack_no} 没有可回滚的生效清单"
            migration.update({
                "status": MIGRATION_ROLLED_BACK,
                "rolled_back_at": _now(),
                "rolled_back_by": actor,
                "confirmed_by": None,
                "confirmed_at": None,
            })
            manifest["status"] = MANIFEST_ROLLED_BACK
            self._log(pack_no, "回滚迁移", actor, "清单停用，旧签收读取依赖保留")
            return True, f"{pack_no} 已回滚，旧签收与原件仍可读取"

    def reactivate_migration(self, pack_no: str, actor: str) -> tuple[bool, str]:
        """切回复用同一份 V2 清单，不重建、不产生第二份有效清单。"""
        with self._lock:
            signoff = self._signoff(pack_no)
            if signoff is None:
                return False, f"原包号 {pack_no} 没有历史签收"
            migration = self._migrations[pack_no]
            manifest = self._manifests.get(pack_no)
            if migration["status"] != MIGRATION_ROLLED_BACK or manifest is None:
                return False, f"{pack_no} 没有可重新启用的清单"
            migration.update({
                "status": MIGRATION_CONFIRMED,
                "confirmed_at": _now(),
                "confirmed_by": actor,
                "rolled_back_at": None,
                "rolled_back_by": None,
            })
            manifest.update({
                "status": MANIFEST_ACTIVE,
                "confirmed_at": migration["confirmed_at"],
                "confirmed_by": actor,
            })
            self._log(
                pack_no, "重新启用清单", actor,
                f"复用清单 {manifest['manifest_id']}，未新建第二份清单",
            )
            # 回滚前的核验结论已失效，切回后必须重新取结论，避免出现两份互相矛盾的有效清单。
            self._results.pop(pack_no, None)
            self._resolve_pending(pack_no)
            return True, f"已复用并重新启用清单 {manifest['manifest_id']}"

    # ------------------------------------------------------------ 清单构建
    def _build_manifest(
        self, pack_no: str, migration: dict[str, Any],
    ) -> dict[str, Any]:
        signoff = self._signoffs[pack_no]
        entries: list[dict[str, Any]] = []
        missing_links: list[str] = []
        for filename, legacy_file in signoff.files.items():
            expected = legacy_file.sha256
            chosen: bytes | None = None
            source: str | None = None

            snapshot = migration["snapshot"].get(filename)
            if snapshot is not None and sha256_hex(snapshot) == expected:
                chosen = snapshot
                source = migration["snapshot_sources"].get(filename) or SIGNOFF_ARCHIVE_SOURCE

            if chosen is None:
                receiver = self._receiver_originals.get((pack_no, filename, expected))
                if receiver is not None:
                    chosen, source = receiver, RECEIVER_ORIGINAL_SOURCE

            if chosen is None:
                workspace = self._workspace_bytes(pack_no, filename)
                if workspace is not None and sha256_hex(workspace) == expected:
                    chosen, source = workspace, WORKSPACE_SOURCE

            if chosen is None:
                workspace = self._workspace_bytes(pack_no, filename)
                if workspace is not None:
                    reason = (
                        f"{filename}：同位置只有 V2 更正字节，与签收指纹不符，"
                        "不可代验 V1；缺少签收方归档 V1 原件"
                    )
                else:
                    reason = (
                        f"{filename}：缺少签收方归档 V1 原件，且接收方尚未提交签收时原件"
                    )
                missing_links.append(reason)
                entries.append({
                    "文件名": filename,
                    "整包指纹": "sha256:" + expected,
                    "八位旧校验": legacy_file.legacy_checksum,
                    "实际来源": signoff.actual_source,
                    "核验状态": "缺原件",
                    "纳入来源": None,
                    "字节数": 0,
                    "字幕行数": len(legacy_file.line_digests),
                    "已核验行数": 0,
                    "缺失环节": reason,
                    "逐行": [
                        {
                            "行号": index,
                            "指纹": digest,
                            "实际字幕": None,
                            "核验": False,
                            "来源": None,
                        }
                        for index, digest in enumerate(legacy_file.line_digests, start=1)
                    ],
                })
                continue

            entries.append({
                "文件名": filename,
                "整包指纹": "sha256:" + expected,
                "八位旧校验": legacy_file.legacy_checksum,
                "实际来源": signoff.actual_source,
                "核验状态": "可逐项复验",
                "纳入来源": source,
                "字节数": len(chosen),
                "字幕行数": len(legacy_file.line_texts),
                "已核验行数": len(legacy_file.line_texts),
                "缺失环节": None,
                "逐行": [
                    {
                        "行号": index,
                        "指纹": digest,
                        "实际字幕": text,
                        "核验": True,
                        "来源": source,
                    }
                    for index, (text, digest) in enumerate(
                        zip(legacy_file.line_texts, legacy_file.line_digests), start=1
                    )
                ],
            })

        blocked = bool(missing_links)
        return {
            "manifest_id": f"MAN-{pack_no}-V2",
            "原包号": pack_no,
            "清单版本": "V2",
            "关联媒体源": signoff.media_id,
            "接收方标识": signoff.receiver,
            "实际来源": signoff.actual_source,
            "旧校验": signoff.legacy_checksum,
            "status": MANIFEST_BLOCKED if blocked else MANIFEST_DRAFT,
            "blocked_reason": "；".join(missing_links) if blocked else None,
            "created_at": _now(),
            "confirmed_at": None,
            "confirmed_by": None,
            "files": entries,
            "字幕行数": sum(entry["字幕行数"] for entry in entries),
            "已核验行数": sum(entry["已核验行数"] for entry in entries),
        }

    # ------------------------------------------------------------ 复验请求
    def verify_pack(
        self,
        pack_no: str,
        receiver: str,
        files: dict[str, bytes] | None = None,
        actor: str = "接收方",
    ) -> dict[str, Any]:
        """接收方提交原件与指纹复验；允许在迁移确认前到达。"""
        with self._lock:
            signoff = self._signoff(pack_no)
            if signoff is None:
                return {
                    "ok": False,
                    "verdict": VERDICT_UNVERIFIABLE,
                    "message": f"原包号 {pack_no} 没有历史签收记录，无法复验",
                    "missing_links": ["缺少该原包号的历史签收记录"],
                    "files": [],
                }
            migration = self._migrations[pack_no]
            submitted = files or {}

            bad = [
                name for name, data in submitted.items()
                if name in signoff.files
                and sha256_hex(data) != signoff.files[name].sha256
            ]
            if bad:
                result = self._result(
                    pack_no, receiver, VERDICT_MISMATCH, False,
                    files=self._file_checks(pack_no, submitted),
                    missing_links=[
                        f"{name}：提交字节与签收指纹不符，同名 V2 更正文件不可代验 V1"
                        for name in bad
                    ],
                    message="核验不符：提交字节与签收时逐文件指纹不一致",
                    manifest=self._manifests.get(pack_no),
                )
                self._results[pack_no] = result
                self._log(pack_no, "提交复验", actor, "字节不符，当场拒绝")
                return result

            if migration["status"] == MIGRATION_NOT_STARTED:
                # 迁移还没开始也可能先收到接收方原件：字节相符的先留存并排队，
                # 等值班员启动迁移读取 V1 文件后再并入清单。
                self._remember_receiver_originals(pack_no, submitted)
                request = {
                    "request_id": f"VRQ-{pack_no}-{len(self._pending.get(pack_no, [])) + 1:03d}",
                    "receiver": receiver,
                    "files": sorted(submitted),
                    "status": "排队中",
                    "submitted_at": _now(),
                    "resolved_at": None,
                    "confirmed_by": None,
                    "result_id": None,
                }
                self._pending.setdefault(pack_no, []).append(request)
                result = self._result(
                    pack_no, receiver, VERDICT_NOT_STARTED, False,
                    files=self._file_checks(pack_no, submitted),
                    missing_links=[
                        "迁移尚未开始：缺少已读取的 V1 原件与逐文件指纹快照",
                        f"复验请求 {request['request_id']} 已排队，迁移确认后自动完成",
                    ],
                    message="无法复验：需先启动历史签收迁移并读取 V1 原件（请求已排队）",
                    manifest=None,
                    request_id=request["request_id"],
                )
                self._results[pack_no] = result
                return result

            if migration["status"] == MIGRATION_RUNNING:
                self._remember_receiver_originals(pack_no, submitted)
                request = {
                    "request_id": f"VRQ-{pack_no}-{len(self._pending.get(pack_no, [])) + 1:03d}",
                    "receiver": receiver,
                    "files": sorted(submitted),
                    "status": "排队中",
                    "submitted_at": _now(),
                    "resolved_at": None,
                    "confirmed_by": None,
                    "result_id": None,
                }
                self._pending.setdefault(pack_no, []).append(request)
                missing = [
                    name for name in signoff.files
                    if name not in submitted
                ]
                self._log(
                    pack_no, "提交复验", actor,
                    f"请求 {request['request_id']} 在迁移确认前到达，已排队",
                )
                return self._result(
                    pack_no, receiver, VERDICT_PENDING, False,
                    files=self._file_checks(pack_no, submitted),
                    missing_links=[
                        f"{name}：本次未提交签收原件" for name in missing
                    ] + ["待迁移确认：确认后自动完成这笔复验"],
                    message=(
                        f"复验请求已受理（{request['request_id']}），"
                        "迁移确认前先排队，确认后自动完成"
                    ),
                    manifest=None,
                    request_id=request["request_id"],
                )

            if migration["status"] in (MIGRATION_ROLLED_BACK, MIGRATION_BLOCKED):
                manifest = self._manifests.get(pack_no)
                if migration["status"] == MIGRATION_BLOCKED:
                    # 受阻只是“还缺原件”，不是失效：此时提交的签收原件应被
                    # 留存并排进下一次确认，而不是直接判无法复验。
                    remembered = self._remember_receiver_originals(pack_no, submitted)
                    request = {
                        "request_id": f"VRQ-{pack_no}-{len(self._pending.get(pack_no, [])) + 1:03d}",
                        "receiver": receiver,
                        "files": sorted(submitted),
                        "status": "排队中",
                        "submitted_at": _now(),
                        "resolved_at": None,
                        "confirmed_by": None,
                        "result_id": None,
                    }
                    self._pending.setdefault(pack_no, []).append(request)
                    still_missing = [
                        name for name in signoff.files
                        if self.get_original(pack_no, name)[0] is None
                    ]
                    links = [
                        f"{name}：仍缺签收时原件" for name in still_missing
                    ] + [
                        f"复验请求 {request['request_id']} 已排队，补原件后重新确认即可完成"
                    ]
                    message = (
                        "已留存提交的签收原件并排队，重新确认后即可完成"
                        if remembered and not still_missing
                        else "无法复验：还缺签收原件，本次提交已留存并排队"
                    )
                    return self._result(
                        pack_no, receiver, VERDICT_UNVERIFIABLE, False,
                        files=self._file_checks(pack_no, submitted),
                        missing_links=links,
                        message=message,
                        manifest=manifest,
                        request_id=request["request_id"],
                    )
                reason = "清单已回滚，需先重新启用同一份 V2 清单"
                result = self._result(
                    pack_no, receiver, VERDICT_ROLLED_BACK, False,
                    files=self._file_checks(pack_no, submitted),
                    missing_links=[reason],
                    message="无法复验：" + reason,
                    manifest=manifest,
                )
                self._results[pack_no] = result
                return result

            manifest = self._manifests.get(pack_no)
            if manifest is None:
                return {
                    "ok": False,
                    "verdict": VERDICT_UNVERIFIABLE,
                    "message": "迁移状态异常：缺少 V2 清单",
                    "missing_links": ["缺少 V2 交付清单"],
                    "files": [],
                }
            result = self._compute_against_manifest(pack_no, manifest, receiver, submitted)
            self._results[pack_no] = result
            self._log(pack_no, "提交复验", actor, f"核验结论：{result['verdict']}")
            return result

    def _compute_against_manifest(
        self,
        pack_no: str,
        manifest: dict[str, Any],
        receiver: str,
        submitted: dict[str, bytes],
    ) -> dict[str, Any]:
        file_checks: list[dict[str, Any]] = []
        missing_links: list[str] = []
        any_verified = False
        all_verified = True
        any_mismatch = False

        for entry in manifest["files"]:
            filename = entry["文件名"]
            expected = entry["整包指纹"].removeprefix("sha256:")
            data = submitted.get(filename)
            if data is None:
                file_checks.append({
                    "文件名": filename,
                    "期望指纹": "sha256:" + expected,
                    "提交指纹": None,
                    "状态": "未提交原件",
                    "来源": None,
                    "字幕行数": entry["字幕行数"],
                    "已核验行数": 0,
                    "说明": "接收方未提交该文件的签收原件",
                })
                all_verified = False
                missing_links.append(
                    entry["缺失环节"]
                    or f"{filename}：本次未提交该文件的签收原件，缺这一环才能继续"
                )
                continue

            actual_digest = sha256_hex(data)
            if actual_digest != expected:
                workspace = self._workspace_bytes(pack_no, filename)
                reason = "提交字节与签收指纹不符"
                if workspace is not None and sha256_hex(workspace) == actual_digest:
                    reason = "交付的是同位置 V2 更正文件，字节与签收指纹不符，不可代验 V1"
                file_checks.append({
                    "文件名": filename,
                    "期望指纹": "sha256:" + expected,
                    "提交指纹": "sha256:" + actual_digest,
                    "状态": "核验不符",
                    "来源": None,
                    "字幕行数": entry["字幕行数"],
                    "已核验行数": 0,
                    "说明": reason,
                })
                missing_links.append(f"{filename}：{reason}")
                all_verified = False
                any_mismatch = True
                continue

            actual_lines = parse_subtitle_lines(data)
            line_results = []
            verified_count = 0
            for line in entry["逐行"]:
                index = line["行号"] - 1
                text = actual_lines[index] if index < len(actual_lines) else None
                ok = text is not None and line_digest(text) == line["指纹"]
                if ok:
                    verified_count += 1
                line_results.append({
                    "行号": line["行号"],
                    "指纹": line["指纹"],
                    "实际字幕": text,
                    "核验": ok,
                })
            file_checks.append({
                "文件名": filename,
                "期望指纹": "sha256:" + expected,
                "提交指纹": "sha256:" + actual_digest,
                "状态": "已复验" if verified_count == entry["字幕行数"] else "行级不符",
                "来源": RECEIVER_ORIGINAL_SOURCE,
                "字幕行数": entry["字幕行数"],
                "已核验行数": verified_count,
                "说明": None,
                "逐行": line_results,
            })
            if verified_count == entry["字幕行数"]:
                any_verified = True
            else:
                all_verified = False
                missing_links.append(f"{filename}：存在 {entry['字幕行数'] - verified_count} 条字幕与签收指纹不符")

        for name in submitted:
            if name not in {entry["文件名"] for entry in manifest["files"]}:
                missing_links.append(f"{name}：不属于本包签收清单，未纳入")

        if any_mismatch:
            verdict, ok = VERDICT_MISMATCH, False
            message = "核验不符：提交字节与签收指纹不一致"
        elif all_verified and file_checks:
            verdict, ok = VERDICT_VERIFIED, True
            message = "已复验：全部字幕行与签收指纹一致"
        elif any_verified:
            verdict, ok = VERDICT_PARTIAL, False
            message = "部分复验：仍有文件缺少可核验的签收原件"
        else:
            verdict, ok = VERDICT_UNVERIFIABLE, False
            message = "无法复验：缺少可核验的签收原件"

        return self._result(
            pack_no, receiver, verdict, ok,
            files=file_checks,
            missing_links=missing_links,
            message=message,
            manifest=manifest,
        )

    def _file_checks(
        self, pack_no: str, submitted: dict[str, bytes],
    ) -> list[dict[str, Any]]:
        signoff = self._signoffs[pack_no]
        checks = []
        for filename, data in submitted.items():
            legacy_file = signoff.files.get(filename)
            if legacy_file is None:
                checks.append({
                    "文件名": filename,
                    "期望指纹": None,
                    "提交指纹": "sha256:" + sha256_hex(data),
                    "状态": "非签收清单文件",
                    "说明": "不属于本包签收清单，未纳入",
                })
                continue
            same = sha256_hex(data) == legacy_file.sha256
            checks.append({
                "文件名": filename,
                "期望指纹": "sha256:" + legacy_file.sha256,
                "提交指纹": "sha256:" + sha256_hex(data),
                "旧校验": legacy_file.legacy_checksum,
                "提交八位旧校验": legacy8(data),
                "状态": "字节一致" if same else "字节不符",
                "字幕行数": len(legacy_file.line_texts),
                "说明": None if same else "同名 V2 更正文件不可代验 V1",
            })
        return checks

    def _result(
        self,
        pack_no: str,
        receiver: str,
        verdict: str,
        ok: bool,
        *,
        files: list[dict[str, Any]],
        missing_links: list[str],
        message: str,
        manifest: dict[str, Any] | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        migration = self._migrations.get(pack_no, {})
        result_id = self._next_result_id()
        return {
            "result_id": result_id,
            "原包号": pack_no,
            "接收方标识": receiver,
            "verdict": verdict,
            "ok": ok,
            "message": message,
            "files": files,
            "missing_links": [item for item in missing_links if item],
            "manifest_id": manifest["manifest_id"] if manifest else None,
            "manifest_status": manifest["status"] if manifest else None,
            "confirmed_by": migration.get("confirmed_by"),
            "confirmed_at": migration.get("confirmed_at"),
            "request_id": request_id,
            "requested_at": _now(),
            "consumers": ["subtitle_pack", "delivery_receipt"],
        }

    def _resolve_pending(self, pack_no: str) -> list[dict[str, Any]]:
        manifest = self._manifests[pack_no]
        resolved = []
        for request in self._pending.get(pack_no, []):
            if request["status"] != "排队中":
                continue
            files = {
                name: self._receiver_originals[(pack_no, name, digest)]
                for name in request["files"]
                for digest in [self._signoffs[pack_no].files[name].sha256]
                if (pack_no, name, digest) in self._receiver_originals
            }
            result = self._compute_against_manifest(
                pack_no, manifest, request["receiver"], files,
            )
            request.update({
                "status": "已完成",
                "resolved_at": _now(),
                "confirmed_by": self._migrations[pack_no].get("confirmed_by"),
                "result_id": result["result_id"],
                "verdict": result["verdict"],
            })
            self._results[pack_no] = result
            resolved.append(dict(request))
            self._log(
                pack_no, "完成排队复验", request["receiver"],
                f"{request['request_id']} 在迁移确认后完成，结论 {result['verdict']}",
            )
        return resolved

    def _shared_or_invalidated(self, pack_no: str) -> dict[str, Any] | None:
        """回滚后旧结论不能再当作有效核验；共用读取方立刻看到失效。"""
        result = self._results.get(pack_no)
        if result is None:
            return None
        migration = self._migrations[pack_no]
        if migration["status"] == MIGRATION_ROLLED_BACK:
            payload = dict(result)
            payload.update({
                "ok": False,
                "verdict": VERDICT_ROLLED_BACK,
                "message": "清单已回滚，此前的核验结论不再有效",
            })
            return payload
        return result

    # ------------------------------------------------------- 共用核验结论
    def shared_result(self, pack_no: str) -> dict[str, Any] | None:
        """字幕包与交付回执共用的服务端核验结论。"""
        result = self._shared_or_invalidated(pack_no)
        if result is None:
            return None
        payload = dict(result)
        payload["shared"] = True
        payload["share_note"] = "字幕包与交付回执读取同一份服务端核验结论"
        return payload

    def pack_verification(self, pack_no: str) -> dict[str, Any] | None:
        return self.shared_result(pack_no)


signoff_registry = SignoffRegistry()


def reset_signoff_registry() -> None:
    """测试用：把历史签收与清单状态重置回种子数据。"""
    signoff_registry.reset()
