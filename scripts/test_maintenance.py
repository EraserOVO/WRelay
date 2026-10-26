"""Isolated verified-backup, fault alert and desktop supervisor regression checks."""
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from panel.backup_restore import BackupRestoreService
from panel.maintenance import MaintenanceService
from panel import desktop_guardian as guardian
from plugins.json_io import json_file_lock, _atomic_write_json_unlocked


def main():
    with tempfile.TemporaryDirectory(prefix='wrelay_maintenance_') as tmp:
        root = Path(tmp)
        settings = root / 'settings.json'
        settings.write_text('{"good": true}', encoding='utf-8')
        queue = root / 'bilibili_protection.json'
        queue.write_text('{"version":1,"queues":{},"sent_at":[]}', encoding='utf-8')
        cursor = root / 'discord_last.json'
        cursor.write_text('{"channel":{"group":"123"}}', encoding='utf-8')
        service = BackupRestoreService(str(settings), str(cursor), str(root/'retry_pending.json'),
            str(root/'delivery_dedup.sqlite3'), str(root/'restore_backups'), json_file_lock,
            _atomic_write_json_unlocked, lambda: {'good': True}, lambda data: None if data.get('good') else 'invalid', lambda text: None)
        payload = service.create_backup_archive(True)
        restored = service.read_restore_archive(payload)
        assert 'bilibili_protection.json' in restored
        changed = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive, zipfile.ZipFile(changed,'w') as target:
            for name in archive.namelist():
                target.writestr(name, b'{"good":true,"changed":1}' if name=='settings.json' else archive.read(name))
        try:
            service.read_restore_archive(changed.getvalue())
        except ValueError:
            pass
        else:
            raise AssertionError('Tampered archive accepted')
        before = settings.read_bytes()
        queue.write_bytes(b'\0'*32)
        try:
            service.create_backup_archive(True)
        except ValueError:
            pass
        else:
            raise AssertionError('Corrupt queue backed up as valid')
        assert queue.read_bytes()==b'\0'*32
        service.restore_backup_archive(payload, True, True)
        assert json.loads(queue.read_text())['queues']=={}
        assert settings.read_bytes()!=b''
        assert list((root/'restore_backups').glob('*.zip'))
        # A write failure restores everything written earlier in the transaction.
        writer=service.atomic_write_json_unlocked
        def fail_settings(path,data,indent=2):
            if str(path)==str(queue): raise OSError('simulated disk failure')
            return writer(path,data,indent=indent)
        settings.write_text('{"good":true,"current":1}',encoding='utf-8')
        before=settings.read_bytes()
        with mock.patch.object(service,'atomic_write_json_unlocked',side_effect=fail_settings):
            try: service.restore_backup_archive(payload,True,True)
            except OSError: pass
            else: raise AssertionError('Simulated failure not raised')
        assert settings.read_bytes()==before
        notifications=[]
        rss={'source_health':[{'id':'s','name':'source','consecutive_failures':2}]}
        maintenance=MaintenanceService(lambda:service,root,lambda *a,**k:rss,
            lambda k,m: notifications.append(k) or {'sent':1},lambda text:None)
        name=maintenance.backup_now()
        maintenance.check_once()
        assert not maintenance.status()['alerts']
        rss['source_health'][0]['consecutive_failures']=3
        maintenance.check_once();maintenance.check_once()
        assert len(notifications)==3 # X、Bilibili、YouTube 各一次，重复检查不重复通知。
        assert len(maintenance.status()['alerts'])==3
        assert any(a['id']=='rss:YouTube:s' and 'RSSHub' not in a['message'] for a in maintenance.status()['alerts'])
        queue.write_bytes(b'\0'*32)
        maintenance.check_once()
        assert any(a['id']=='file:bilibili_protection.json' for a in maintenance.status()['alerts'])
        assert maintenance.read_backup(name)
        try: maintenance.read_backup('../settings.json')
        except ValueError: pass
        else: raise AssertionError('Path escape accepted')
        print('PASS: archive hashes, corrupt queue preservation, verified restore, rollback, RSS threshold/cooldown, safe names')
        class Kernel:
            def __init__(self, intentional=False): self.intentional=intentional;self.closed=[]
            def OpenEventW(self,*args):return 1
            def OpenProcess(self,*args):return 2
            def WaitForSingleObject(self,handle,delay):
                return 0 if handle==2 or (handle==1 and self.intentional) else 258
            def CloseHandle(self,h):self.closed.append(h)
        launches=[]
        with mock.patch.object(guardian,'data_path',side_effect=lambda name:str(root/name)), mock.patch.object(guardian,'_log'), mock.patch.object(guardian,'_launch',side_effect=lambda args,**k:launches.append(args)):
            api=Kernel(True)
            with mock.patch.object(guardian,'_kernel',return_value=api):guardian.supervise(123,'event')
            assert not launches and api.closed==[2,1]
            with mock.patch.object(guardian,'_kernel',return_value=Kernel()):guardian.supervise(123,'event')
            assert '--recover-desktop' in launches[-1]
            for _ in range(5):
                with mock.patch.object(guardian,'_kernel',return_value=Kernel()):guardian.supervise(123,'event')
            assert len(launches)==5
        print('PASS: deliberate desktop exit respected, crash recovery launches replacement, restart loop capped')


if __name__=='__main__':main()
