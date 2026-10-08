"""Local hourly verified snapshots and actionable maintenance alerts."""
import json
import os
from pathlib import Path
import re
import threading
import time

from plugins.json_io import atomic_write_json, json_file_lock
from plugins.state_validation import validate_runtime


class MaintenanceService:
    def __init__(self, backup_factory, data_dir, bot_get, notify, logger):
        self.backup_factory = backup_factory
        self.data_dir = Path(data_dir)
        self.folder = self.data_dir / 'auto_backups'
        self.preferences = self.data_dir / 'maintenance.json'
        self.bot_get = bot_get
        self.notify = notify
        self.logger = logger
        self.lock = threading.RLock()
        self._status_lock = threading.Lock()
        self._alerts = {}
        self._notified = {}
        self._last_backup = 0
        self._checked = {}
        self._worker = None
        self._last_database_check = 0

    def enabled(self):
        try:
            value = json.loads(self.preferences.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return True
        if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
            raise ValueError('自动备份设置无效')
        return value['enabled']

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('enabled 必须是布尔值')
        atomic_write_json(self.preferences, {'enabled': enabled}, indent=2)

    def _paths(self):
        if not self.folder.exists():
            return []
        return sorted((p for p in self.folder.iterdir() if p.is_file() and not p.is_symlink()
                       and re.fullmatch(r'auto-\d{8}-\d{6}-[0-9a-f]{8}\.zip', p.name)),
                      key=lambda p: p.name, reverse=True)

    def status(self):
        with self._status_lock:
            result = {'alerts': list(self._alerts.values()), 'last_backup_ts': self._last_backup}
        try:
            result['enabled'] = self.enabled()
        except (OSError, ValueError):
            result['enabled'] = False
        result.update({'ok': True, 'interval_hours': 1, 'retention': 30})
        return result

    def list_backups(self):
        entries = []
        for p in self._paths():
            try:
                self.backup_factory().read_restore_archive(p.read_bytes())
                entries.append({'name': p.name, 'valid': True, 'timestamp': p.stat().st_mtime})
            except Exception:
                entries.append({'name': p.name, 'valid': False, 'timestamp': p.stat().st_mtime})
        return entries

    def read_backup(self, name):
        # Resolve only an entry from our directory; never accept a caller's path.
        match = next((p for p in self._paths() if p.name == name), None)
        if match is None:
            raise ValueError('自动备份不存在')
        payload = match.read_bytes()
        self.backup_factory().read_restore_archive(payload)
        return payload

    def backup_now(self):
        import uuid
        with self.lock:
            service = self.backup_factory()
            payload = service.create_backup_archive(True)
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / ('auto-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.zip')
            service._atomic_write_bytes(path, payload)
            # Re-read the persisted archive before declaring success or pruning.
            service.read_restore_archive(path.read_bytes())
            with self._status_lock:
                self._last_backup = time.time()
            for old in self._paths()[30:]:
                old.unlink()
            self.logger('已保存并校验自动备份：' + path.name)
            return path.name

    def _check_files(self, alerts):
        service = self.backup_factory()
        for name, path in service.backup_targets().items():
            if not os.path.exists(path):
                self._checked.pop(name, None)
                continue
            try:
                stat = os.stat(path)
                revision = (stat.st_mtime_ns, stat.st_size)
                previous = self._checked.get(name)
                if name != 'delivery_dedup.sqlite3' and previous and previous[0] == revision:
                    error = previous[1]
                else:
                    with json_file_lock(path):
                        if name == 'delivery_dedup.sqlite3':
                            # SQLite WAL writes do not always change the database mtime.
                            if time.monotonic() - self._last_database_check < 300:
                                continue
                            service._validate_sqlite_payload(service._sqlite_snapshot_bytes(path))
                            self._last_database_check = time.monotonic()
                            error = ""
                        else:
                            with open(path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            if name == "settings.json":
                                error = service.settings_validator(data) or ""
                            else:
                                validate_runtime(name, data)
                                error = ""
                    self._checked[name] = (revision, error)
                if error:
                    alerts['file:' + name] = name + ' 损坏或格式无效，请从经过校验的备份恢复。'
            except Exception:
                # Do not include RSS URLs, account details or private payloads in alerts.
                alerts['file:' + name] = name + ' 无法读取或校验失败，请保留原文件并恢复备份。'

    def check_once(self):
        alerts = {}
        self._check_files(alerts)
        for label, endpoint in [('X', '/api/x-rss/status'), ('Bilibili', '/api/bilibili/status')]:
            status = self.bot_get(endpoint, timeout=5)
            if not isinstance(status, dict):
                continue
            if status.get('protection_error'):
                alerts['protection:' + label] = label + ' 投递保护异常，自动投递已暂停，请检查维护与备份。'
            for item in status.get('source_health', []):
                if item.get('consecutive_failures', 0) >= 3:
                    key = 'rss:' + label + ':' + str(item.get('id', ''))
                    alerts[key] = label + ' 来源“' + str(item.get('name', ''))[:60] + '”连续三轮轮询失败，请检查来源与 RSSHub。'
        try:
            enabled = self.enabled()
            if enabled and time.time() - self._last_backup >= 3600:
                self.backup_now()
        except Exception:
            alerts['backup'] = '自动备份失败，已保留原有备份。请检查维护页面、数据文件和磁盘空间。'
        with self._status_lock:
            old = self._alerts
            self._alerts = {key: {'id': key, 'message': msg, 'since': old.get(key, {}).get('since', int(time.time()))}
                            for key, msg in alerts.items()}
        for key, message in alerts.items():
            if key not in old:
                self.logger('维护告警：' + message)
            if time.monotonic() - self._notified.get(key, float('-inf')) >= 300:
                try:
                    result = self.notify(key, message)
                    if result and result.get('sent'):
                        self._notified[key] = time.monotonic()
                except Exception:
                    pass
        for key in set(old) - set(alerts):
            self._notified.pop(key, None)
            self.logger('维护故障已恢复：' + key)

    def start(self):
        if self._worker is not None:
            return
        def run():
            try:
                entries = self.list_backups()
            except Exception:
                entries = []
            for entry in entries:
                if entry["valid"]:
                    with self._status_lock:
                        self._last_backup = entry["timestamp"]
                    break
            while True:
                try:
                    self.check_once()
                except Exception as exc:
                    self.logger('维护检查失败：' + type(exc).__name__)
                time.sleep(30)
        self._worker = threading.Thread(target=run, name='wrelay-maintenance', daemon=True)
        self._worker.start()
