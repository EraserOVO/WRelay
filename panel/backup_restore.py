"""管理面板配置备份与恢复服务。"""

import io
import json
import os
import shutil
import time
import uuid
import zipfile
from contextlib import ExitStack
from datetime import datetime, timezone


BACKUP_SETTINGS_NAME = "settings.json"
BACKUP_RUNTIME_NAMES = ("discord_last.json", "retry_pending.json")
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
    """执行白名单 JSON 文件的安全备份与恢复。"""

    def __init__(
        self,
        settings_file,
        discord_last_file,
        retry_pending_file,
        restore_backup_dir,
        file_lock,
        atomic_write_json_unlocked,
        default_settings_factory,
        settings_validator,
        logger,
    ):
        self.settings_file = settings_file
        self.discord_last_file = discord_last_file
        self.retry_pending_file = retry_pending_file
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
        }

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
            manifest_payload = json.dumps(
                self.backup_manifest(names, include_runtime_state),
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(BACKUP_MANIFEST_NAME, manifest_payload)
                uncompressed_size = len(manifest_payload)
                for name in names:
                    try:
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
                    archive.writestr(name, content)
            if payload.tell() > BACKUP_MAX_BYTES:
                raise ValueError("生成的备份 ZIP 超过 5 MB 限制")
        return payload.getvalue()

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
                    or manifest.get("version") != 1
                ):
                    raise ValueError("不支持的备份格式")
                if manifest.get("files") != [
                    name for name in names if name != BACKUP_MANIFEST_NAME
                ]:
                    raise ValueError("备份清单与文件不一致")
                if (
                    any(name in names for name in BACKUP_RUNTIME_NAMES)
                    and manifest.get("include_runtime_state") is not True
                ):
                    raise ValueError("备份清单未声明运行状态")
                data = {}
                for name in names:
                    if name == BACKUP_MANIFEST_NAME:
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
