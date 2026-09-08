import json
import hashlib
import os
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy

if os.name == "nt":
    import msvcrt
else:
    import fcntl


# =====================================================
# JSON 文件读写
#
# 单一职责：统一的 JSON 持久化工具。
# 写采用唯一 tmp + os.replace 原子替换，避免进程崩溃时
# 写坏目标文件（与面板 settings.json 的写法一致）。
# 供 command.py / history.py / config.py / 管理面板 复用。
# =====================================================


def load_json(path, default=None):
    """读取 JSON；文件缺失或损坏时返回 default"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# Windows 下目标文件正被其它进程打开读取时，os.replace 会报
# PermissionError（Python 的读句柄不带 FILE_SHARE_DELETE），
# 读取通常毫秒级结束，短间隔重试即可通过
REPLACE_ATTEMPTS = 3
REPLACE_RETRY_DELAY = 0.05


class ConcurrentWriteError(RuntimeError):
    """磁盘文件在读取后被其他进程修改。"""


def file_revision(path):
    """返回用于检测外部修改的文件版本；不存在时返回 ``missing``。

    文件通常很小，额外计算内容摘要可以识别“内容变化但 mtime/size
    恰好相同”的情况，供 settings 缓存和并发写入版本校验共用。
    """
    try:
        st = os.stat(path)
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return "missing"
    # mtime_ns/ctime_ns/size 便于快速定位版本，digest 保证内容级准确。
    return "%s:%s:%s:%s" % (
        st.st_mtime_ns,
        st.st_ctime_ns,
        st.st_size,
        digest,
    )


@contextmanager
def json_file_lock(path, timeout=5.0):
    """跨进程锁住一个 JSON 文件对应的旁车锁文件。

    Windows 使用 msvcrt，Unix 使用 flock。锁文件只负责协调写入，真正的
    数据文件仍通过 os.replace 原子替换，因此读者始终看到完整 JSON。
    """
    path = os.fspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    lock_path = path + ".lock"
    with open(lock_path, "a+b") as lock:
        if os.name == "nt":
            if os.path.getsize(lock_path) == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("获取 JSON 文件锁超时: %s" % path)
                    time.sleep(0.05)
        else:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _atomic_write_json_unlocked(path, data, indent=4):
    """原子写 JSON：先写唯一临时文件，再 os.replace 原子替换目标

    - 临时文件名带进程号 + 随机串：settings.json / qq_registrations.json /
      openid 发现记录等文件存在 bot 与面板两个写方，固定 tmp 名会让
      并发写共用同一临时文件而互相截断交错，最终把坏内容换进目标文件
    - os.replace 失败（目标被读取句柄短暂占用）时短间隔重试；
      重试耗尽则删除临时文件并抛出，目标文件保持旧内容"""
    path = os.fspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = "%s.%s.%s.tmp" % (path, os.getpid(), uuid.uuid4().hex)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(REPLACE_RETRY_DELAY)


def atomic_write_json(path, data, indent=4, expected_revision=None):
    """在跨进程锁内原子写 JSON，可选检查读取时的文件版本。"""
    path = os.fspath(path)
    with json_file_lock(path):
        if (
            expected_revision is not None
            and file_revision(path) != expected_revision
        ):
            raise ConcurrentWriteError("JSON 文件已被其他进程修改: %s" % path)
        _atomic_write_json_unlocked(path, data, indent=indent)


def update_json(path, mutate, default=None, indent=4, write_if=None):
    """在同一跨进程锁内完成 JSON 的读—改—写。

    ``mutate`` 接收当前数据并原地修改，返回值作为结果；``write_if`` 存在
    时只有它返回 True 才落盘，适合“仅新增时写入”的高频记录场景。
    """
    path = os.fspath(path)
    with json_file_lock(path):
        data = load_json(path, default=default)
        if default is not None and not isinstance(data, type(default)):
            data = deepcopy(default)
        result = mutate(data)
        if write_if is None or write_if(result):
            _atomic_write_json_unlocked(path, data, indent=indent)
        return result
