"""管理面板配置备份与恢复服务。"""

import io
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile
from contextlib import ExitStack
from datetime import datetime, timezone

from plugins.state_validation import validate_runtime


BACKUP_SETTINGS_NAME = "settings.json"
BACKUP_DELIVERY_DEDUP_NAME = "delivery_dedup.sqlite3"
BACKUP_RUNTIME_NAMES = (
    "discord_last.json",
    "retry_pending.json",
    "x_protection.json",
    "bilibili_protection.json",
    "bilibili.json",
    "x_rss.json",
    BACKUP_DELIVERY_DEDUP_NAME,
)
BACKUP_MANIFEST_NAME = "manifest.json"
BACKUP_MAX_BYTES = 5 * 1024 * 1024
RESTORE_BACKUP_RETENTION = 10


class RestoreRollbackError(RuntimeError):
    """恢复失败且无法完整还原恢复前状态。"""

    def __init__(self, rollback_path, rollback_error):
        self.rollback_path = rollback_path
        self.rollback_error = rollback_error
        super().__init__(str(rollback_error))


class BackupRestoreService:
    """执行白名单 JSON 状态与 SQLite 投递记录的安全备份和恢复。"""

    def __init__(
        self,
        settings_file,
        discord_last_file,
        retry_pending_file,
        delivery_dedup_file,
        restore_backup_dir,
        file_lock,
        atomic_write_json_unlocked,
        default_settings_factory,
        settings_validator,
        logger,
        x_protection_file=None,
    ):
        self.settings_file = settings_file
        self.discord_last_file = discord_last_file
        self.retry_pending_file = retry_pending_file
        self.delivery_dedup_file = delivery_dedup_file
        self.x_protection_file = x_protection_file or os.path.join(
            os.path.dirname(retry_pending_file),
            "x_protection.json",
        )
        self.restore_backup_dir = restore_backup_dir
        self.file_lock = file_lock
        self.atomic_write_json_unlocked = atomic_write_json_unlocked
        self.default_settings_factory = default_settings_factory
        self.settings_validator = settings_validator
        self.logger = logger

    def backup_targets(self):
        return {
            BACKUP_SETTINGS_NAME: self.settings_file,
            "discord_last.json": self.discord_last_file,
            "retry_pending.json": self.retry_pending_file,
            "x_protection.json": self.x_protection_file,
            "bilibili_protection.json": os.path.join(os.path.dirname(self.settings_file), "bilibili_protection.json"),
            "bilibili.json": os.path.join(os.path.dirname(self.settings_file), "bilibili.json"),
            "x_rss.json": os.path.join(os.path.dirname(self.settings_file), "x_rss.json"),
            BACKUP_DELIVERY_DEDUP_NAME: self.delivery_dedup_file,
        }

    @staticmethod
    def _sqlite_snapshot_bytes(path):
        """Read a consistent SQLite snapshot while the bot may still be running."""
        with tempfile.TemporaryDirectory(prefix="wrelay_backup_sqlite_") as tmp:
            snapshot_path = os.path.join(tmp, BACKUP_DELIVERY_DEDUP_NAME)
            source = sqlite3.connect(path, timeout=10)
            snapshot = sqlite3.connect(snapshot_path, timeout=10)
            try:
                source.backup(snapshot)
            finally:
                snapshot.close()
                source.close()
            with open(snapshot_path, "rb") as file:
                return file.read(BACKUP_MAX_BYTES + 1)

    @staticmethod
    def _validate_sqlite_payload(payload):
        with tempfile.TemporaryDirectory(prefix="wrelay_restore_sqlite_") as tmp:
            path = os.path.join(tmp, BACKUP_DELIVERY_DEDUP_NAME)
            with open(path, "wb") as file:
                file.write(payload)
            try:
                connection = sqlite3.connect(path, timeout=10)
                try:
                    result = connection.execute("PRAGMA integrity_check").fetchone()
                    tables = {
                        row[0]
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    }
                    columns = {row[1] for row in connection.execute("PRAGMA table_info(delivered_items)")}
                finally:
                    connection.close()
            except sqlite3.DatabaseError as exc:
                raise ValueError("delivery_dedup.sqlite3 不是有效 SQLite 数据库") from exc
            if not result or result[0] != "ok" or "delivered_items" not in tables or not {"identity_key", "group_id", "delivered_at"} <= columns:
                raise ValueError("delivery_dedup.sqlite3 结构校验失败")

    @staticmethod
    def _atomic_write_bytes(path, payload):
        path = os.fspath(path)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        temp_path = "%s.%s.%s.restore.tmp" % (
            path,
            os.getpid(),
            uuid.uuid4().hex,
        )
        try:
            with open(temp_path, "xb") as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            for attempt in range(5):
                try:
                    os.replace(temp_path, path)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def lock_backup_targets(self, names):
        """按稳定顺序锁住一批文件，避免快照交错或死锁。"""
        targets = self.backup_targets()
        stack = ExitStack()
        for name in sorted(names):
            stack.enter_context(self.file_lock(targets[name]))
        return stack

    @staticmethod
    def backup_manifest(names, include_runtime_state):
        return {
            "format": "wrelay-backup",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "include_runtime_state": bool(include_runtime_state),
            "files": list(names),
        }

    def create_backup_archive(self, include_runtime_state=False):
        """生成只含白名单文件的 ZIP。"""
        targets = self.backup_targets()
        requested_names = [BACKUP_SETTINGS_NAME]
        if include_runtime_state:
            requested_names.extend(BACKUP_RUNTIME_NAMES)
        with self.lock_backup_targets(requested_names):
            names = [name for name in requested_names if os.path.exists(targets[name])]
            if BACKUP_SETTINGS_NAME not in names:
                names.insert(0, BACKUP_SETTINGS_NAME)
            manifest = self.backup_manifest(names, include_runtime_state)
            manifest["version"] = 2
            manifest["sha256"] = {}
            manifest_payload = json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
                uncompressed_size = len(manifest_payload)
                for name in names:
                    try:
                        if name == BACKUP_DELIVERY_DEDUP_NAME:
                            content = self._sqlite_snapshot_bytes(targets[name])
                        else:
                            with open(targets[name], "rb") as source:
                                content = source.read(
                                    BACKUP_MAX_BYTES - uncompressed_size + 1
                                )
                    except FileNotFoundError:
                        if name == BACKUP_SETTINGS_NAME:
                            content = json.dumps(
                                self.default_settings_factory(),
                                ensure_ascii=False,
                                indent=2,
                            ).encode("utf-8")
                        else:
                            raise
                    uncompressed_size += len(content)
                    if uncompressed_size > BACKUP_MAX_BYTES:
                        raise ValueError(
                            "备份内容超过 5 MB 限制，请取消包含运行状态或减少队列数据"
                        )
                    manifest["sha256"][name] = hashlib.sha256(content).hexdigest()
                    archive.writestr(name, content)
                manifest_payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
                if uncompressed_size + len(manifest_payload) > BACKUP_MAX_BYTES:
                    raise ValueError("备份内容超过 5 MB 限制")
                archive.writestr(BACKUP_MANIFEST_NAME, manifest_payload)
            if payload.tell() > BACKUP_MAX_BYTES:
                raise ValueError("生成的备份 ZIP 超过 5 MB 限制")
        result = payload.getvalue()
        self.read_restore_archive(result)
        return result

    def read_restore_archive(self, payload):
        if (
            not isinstance(payload, (bytes, bytearray))
            or not payload
            or len(payload) > BACKUP_MAX_BYTES
        ):
            raise ValueError("备份文件为空或超过 5 MB 限制")
        try:
            with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                allowed = {
                    BACKUP_MANIFEST_NAME,
                    BACKUP_SETTINGS_NAME,
                    *BACKUP_RUNTIME_NAMES,
                }
                if len(names) != len(set(names)) or set(names) - allowed:
                    raise ValueError("备份内含不允许的文件")
                if (
                    BACKUP_MANIFEST_NAME not in names
                    or BACKUP_SETTINGS_NAME not in names
                ):
                    raise ValueError("备份缺少清单或 settings.json")
                if any(
                    "/" in name or "\\" in name or name.startswith(".")
                    for name in names
                ):
                    raise ValueError("备份文件名不安全")
                total = sum(info.file_size for info in infos)
                if total > BACKUP_MAX_BYTES or any(
                    info.file_size > BACKUP_MAX_BYTES for info in infos
                ):
                    raise ValueError("备份解压后超过 5 MB 限制")
                try:
                    manifest = json.loads(
                        archive.read(BACKUP_MANIFEST_NAME).decode("utf-8")
                    )
                except Exception as exc:
                    raise ValueError("备份清单格式错误") from exc
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("format") != "wrelay-backup"
                    or type(manifest.get("version")) is not int
                    or manifest.get("version") not in (1, 2)
                ):
                    raise ValueError("不支持的备份格式")
                if type(manifest.get("include_runtime_state")) is not bool:
                    raise ValueError("备份清单的运行状态标记格式错误")
                if manifest.get("files") != [
                    name for name in names if name != BACKUP_MANIFEST_NAME
                ]:
                    raise ValueError("备份清单与文件不一致")
                if (
                    any(name in names for name in BACKUP_RUNTIME_NAMES)
                    and manifest.get("include_runtime_state") is not True
                ):
                    raise ValueError("备份清单未声明运行状态")
                if manifest["version"] == 2:
                    hashes = manifest.get("sha256")
                    if not isinstance(hashes, dict) or set(hashes) != set(manifest["files"]):
                        raise ValueError("备份缺少完整校验值")
                    for name in manifest["files"]:
                        if hashlib.sha256(archive.read(name)).hexdigest() != hashes[name]:
                            raise ValueError(name + " 完整性校验失败")
                data = {}
                for name in names:
                    if name == BACKUP_MANIFEST_NAME:
                        continue
                    if name == BACKUP_DELIVERY_DEDUP_NAME:
                        content = archive.read(name)
                        self._validate_sqlite_payload(content)
                        data[name] = content
                        continue
                    try:
                        data[name] = json.loads(
                            archive.read(name).decode("utf-8")
                        )
                    except Exception as exc:
                        raise ValueError("%s 不是有效 JSON" % name) from exc
        except zipfile.BadZipFile as exc:
            raise ValueError("请选择有效的 ZIP 备份文件") from exc
        err = self.settings_validator(data[BACKUP_SETTINGS_NAME])
        if err:
            raise ValueError("settings.json 配置校验失败: " + err)
        for name, value in data.items():
            if name not in (BACKUP_SETTINGS_NAME, BACKUP_DELIVERY_DEDUP_NAME):
                validate_runtime(name, value)
        return data

    def save_restore_rollback(self, names):
        """先完整备份当前待覆盖文件；失败时绝不开始写入恢复数据。"""
        os.makedirs(self.restore_backup_dir, exist_ok=True)
        path = os.path.join(
            self.restore_backup_dir,
            "restore-before-%s-%s.zip"
            % (datetime.now().strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8]),
        )
        targets = self.backup_targets()
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                BACKUP_MANIFEST_NAME,
                json.dumps(
                    {
                        "format": "wrelay-restore-rollback",
                        "version": 1,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "files": list(names),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            for name in names:
                if os.path.exists(targets[name]):
                    archive.write(targets[name], arcname=name)
        self.prune_restore_rollbacks(path)
        return path

    def prune_restore_rollbacks(self, current_path):
        """仅清理本功能创建的旧回退 ZIP。"""
        try:
            current = os.path.normcase(os.path.abspath(current_path))
            backups = []
            with os.scandir(self.restore_backup_dir) as entries:
                for entry in entries:
                    if (
                        entry.is_file(follow_symlinks=False)
                        and entry.name.startswith("restore-before-")
                        and entry.name.endswith(".zip")
                    ):
                        path = os.path.normcase(os.path.abspath(entry.path))
                        if path != current:
                            backups.append(
                                (
                                    entry.stat(follow_symlinks=False).st_mtime_ns,
                                    entry.name,
                                    entry.path,
                                )
                            )
            backups.sort(reverse=True)
            for _mtime, _name, path in backups[
                max(RESTORE_BACKUP_RETENTION - 1, 0):
            ]:
                os.remove(path)
        except OSError as exc:
            self.logger("清理旧恢复前备份失败: %s" % exc)

    @staticmethod
    def _atomic_write_archive_entry(archive, name, path):
        """分块解压并原子替换，避免大型回退文件整体载入内存。"""
        path = os.fspath(path)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        temp_path = "%s.%s.%s.restore.tmp" % (
            path,
            os.getpid(),
            uuid.uuid4().hex,
        )
        try:
            with archive.open(name, "r") as source, open(temp_path, "xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            for attempt in range(5):
                try:
                    os.replace(temp_path, path)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def _restore_from_rollback(self, rollback_path, names, targets):
        failures = []
        try:
            with zipfile.ZipFile(rollback_path, "r") as archive:
                previous_names = set(archive.namelist()) - {BACKUP_MANIFEST_NAME}
                for name in reversed(names):
                    path = targets[name]
                    try:
                        if name in previous_names:
                            self._atomic_write_archive_entry(archive, name, path)
                        else:
                            try:
                                os.remove(path)
                            except FileNotFoundError:
                                pass
                    except Exception as exc:
                        failures.append("%s: %s" % (name, exc))
        except Exception as exc:
            failures.append("无法读取恢复前备份: %s" % exc)
        if failures:
            raise RuntimeError("; ".join(failures))

    def restore_backup_archive(
        self,
        payload,
        include_runtime_state=False,
        confirmed_runtime_state=False,
        *,
        targets_provider=None,
        lock_targets=None,
        save_rollback=None,
        read_archive=None,
    ):
        read_archive = read_archive or self.read_restore_archive
        targets_provider = targets_provider or self.backup_targets
        lock_targets = lock_targets or self.lock_backup_targets
        save_rollback = save_rollback or self.save_restore_rollback

        data = read_archive(payload)
        names = [BACKUP_SETTINGS_NAME]
        if include_runtime_state:
            if not confirmed_runtime_state:
                raise ValueError("恢复运行状态需要明确确认风险")
            names.extend(name for name in BACKUP_RUNTIME_NAMES if name in data)
        targets = targets_provider()
        with lock_targets(names):
            rollback_path = save_rollback(names)
            written_names = []
            try:
                for name in names:
                    if name == BACKUP_DELIVERY_DEDUP_NAME:
                        self._atomic_write_bytes(targets[name], data[name])
                    else:
                        self.atomic_write_json_unlocked(
                            targets[name], data[name], indent=2
                        )
                    written_names.append(name)
            except Exception as restore_error:
                try:
                    self._restore_from_rollback(
                        rollback_path, written_names, targets
                    )
                except Exception as rollback_error:
                    raise RestoreRollbackError(
                        rollback_path, rollback_error
                    ) from restore_error
                raise
        return {"restored": names, "rollback": rollback_path}
