"""管理面板的机器人进程与 PID 文件操作。"""

import ctypes
import json
import os
import socket
import subprocess
import time
import uuid


PROCESS_STOP_VERIFY_TIMEOUT = 2.0
PROCESS_STOP_POLL_INTERVAL = 0.05


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("low", ctypes.c_ulong),
        ("high", ctypes.c_ulong),
    ]


def _kernel32_api():
    """Declare Win32 signatures explicitly so HANDLE values stay pointer-sized."""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.GetLastError.restype = ctypes.c_ulong
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
    filetime_pointer = ctypes.POINTER(_FILETIME)
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        filetime_pointer,
        filetime_pointer,
        filetime_pointer,
        filetime_pointer,
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateProcess.restype = ctypes.c_int
    return kernel32


class BotProcessManager:
    """封装机器人进程生命周期，供桌面管理路由和看门狗复用。"""

    def __init__(
        self,
        project_root,
        python_path,
        bot_entry,
        pid_file,
        process_state,
        logger,
        localize_error,
    ):
        self.project_root = project_root
        self.python_path = python_path
        self.bot_entry = bot_entry
        self.pid_file = pid_file
        self.process_state = process_state
        self.logger = logger
        self.localize_error = localize_error

    @staticmethod
    def port_open(port):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except Exception:
            return False

    def _query_process_identity(self, pid):
        """Return (image_matches, creation_time), False, or None for unknown."""
        if pid <= 0:
            return False
        try:
            process_query_limited_information = 0x1000
            process_query_information = 0x0400
            synchronize = 0x00100000
            kernel32 = _kernel32_api()
            handle = kernel32.OpenProcess(
                process_query_limited_information
                | process_query_information
                | synchronize,
                False,
                pid,
            )
            if not handle:
                error = kernel32.GetLastError()
                # ERROR_INVALID_PARAMETER means the PID does not exist. Other
                # failures may be permissions or a transient query error; keep
                # the PID file so start_bot cannot launch a second instance.
                return False if error == 87 else None
            try:
                # WAIT_TIMEOUT 表示进程仍在运行；已终止进程返回 WAIT_OBJECT_0。
                wait_status = kernel32.WaitForSingleObject(handle, 0)
                if wait_status == 0:
                    return False
                if wait_status != 0x102:
                    return None

                size = ctypes.c_ulong(32768)
                buf = ctypes.create_unicode_buffer(size.value)
                if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)
                ):
                    return None
                created = _FILETIME()
                exited = _FILETIME()
                kernel = _FILETIME()
                user = _FILETIME()
                if not kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(created),
                    ctypes.byref(exited),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    return None
                creation_time = (int(created.high) << 32) | int(created.low)
                image_matches = (
                    os.path.normcase(os.path.abspath(buf.value))
                    == os.path.normcase(os.path.abspath(self.python_path))
                )
                return image_matches, creation_time
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None

    def process_identity_status(self, pid, expected_creation_time=None):
        """Return True/False/None for matching, dead/mismatched, or inaccessible PID."""
        identity = self._query_process_identity(pid)
        if not isinstance(identity, tuple):
            return identity
        image_matches, creation_time = identity
        if not image_matches:
            return False
        if (
            expected_creation_time is not None
            and creation_time != expected_creation_time
        ):
            return False
        return True

    def proc_exists(self, pid):
        """Return True only when a live process is verified as this project's Python."""
        return self.process_identity_status(pid) is True

    def _wait_for_process_exit(self, pid, expected_creation_time):
        deadline = time.monotonic() + PROCESS_STOP_VERIFY_TIMEOUT
        while True:
            status = self.process_identity_status(pid, expected_creation_time)
            if status is False:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(PROCESS_STOP_POLL_INTERVAL, remaining))

    def read_pid(self):
        return self.read_pid_record()["pid"]

    def read_pid_record(self):
        try:
            with open(self.pid_file, "r", encoding="ascii") as f:
                raw = f.read().strip()
        except FileNotFoundError:
            return {"pid": 0, "creation_time": None, "error": None}
        except Exception:
            return {"pid": 0, "creation_time": None, "error": "read"}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict):
            pid = data.get("pid")
            creation_time = data.get("creation_time")
            if (
                type(pid) is int
                and pid > 0
                and type(creation_time) is int
                and creation_time > 0
            ):
                return {"pid": pid, "creation_time": creation_time, "error": None}
            return {"pid": 0, "creation_time": None, "error": "format"}
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            return {"pid": 0, "creation_time": None, "error": "format"}
        if pid < 0:
            return {"pid": 0, "creation_time": None, "error": "format"}
        return {"pid": pid, "creation_time": None, "error": None}

    def write_pid(self, pid):
        identity = self._query_process_identity(pid)
        if not isinstance(identity, tuple) or not identity[0]:
            raise OSError("无法验证机器人进程镜像或创建时间")
        record = {
            "pid": int(pid),
            "creation_time": int(identity[1]),
        }
        parent = os.path.dirname(os.path.abspath(self.pid_file))
        os.makedirs(parent, exist_ok=True)
        temp_path = "%s.%s.%s.tmp" % (
            self.pid_file,
            os.getpid(),
            uuid.uuid4().hex,
        )
        try:
            with open(temp_path, "w", encoding="ascii") as f:
                json.dump(record, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            for attempt in range(5):
                try:
                    os.replace(temp_path, self.pid_file)
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

    def clear_pid(self):
        try:
            os.remove(self.pid_file)
        except Exception:
            pass

    def bot_status(self):
        proc = self.process_state["proc"]
        if proc is not None and proc.poll() is None:
            return {"running": True, "pid": proc.pid, "managed": True}
        record = self.read_pid_record()
        pid = record["pid"]
        if record.get("error"):
            return {
                "running": True,
                "pid": pid,
                "managed": False,
                "verified": False,
                "status_error": record["error"],
            }
        if pid:
            identity_status = self.process_identity_status(
                pid,
                record["creation_time"],
            )
            if identity_status is True:
                if record["creation_time"] is None:
                    try:
                        self.write_pid(pid)
                    except Exception as exc:
                        self.logger("机器人 PID 创建时间升级失败: %s" % exc)
                return {"running": True, "pid": pid, "managed": False}
            if identity_status is None:
                return {
                    "running": True,
                    "pid": pid,
                    "managed": False,
                    "verified": False,
                }
            # 清理过期 PID，避免 PID 被复用后误判为机器人仍在运行。
            self.clear_pid()
        return {"running": False, "pid": 0, "managed": False}

    @staticmethod
    def kill_tree(pid):
        if pid <= 0:
            return False
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

        try:
            kernel32 = _kernel32_api()
            handle = kernel32.OpenProcess(0x0001, False, pid)
            if not handle:
                return False
            try:
                return bool(kernel32.TerminateProcess(handle, 1))
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    def start_bot(self):
        status = self.bot_status()
        if status["running"]:
            if status.get("verified") is False:
                if status.get("status_error") == "read":
                    msg = "无法读取机器人 PID 文件；请检查 data/bot.pid 后重试"
                elif status.get("status_error") == "format":
                    msg = "机器人 PID 文件格式无效；请检查 data/bot.pid 后重试"
                else:
                    msg = "无法确认机器人 PID %s 的状态；请以启动机器人的相同 Windows 权限运行管理面板" % status["pid"]
                return {"ok": False, "msg": self.localize_error(msg, "启动失败")}
            return {"ok": False, "msg": "机器人已在运行"}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = os.environ.copy()
        # 日志由 bot.py 写入轮转日志文件，不重定向到同一文件以免双写。
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        try:
            proc = subprocess.Popen(
                [self.python_path, self.bot_entry] if self.bot_entry else [self.python_path],
                cwd=self.project_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                env=env,
            )
        except Exception as exc:
            self.logger("启动机器人失败: %s" % exc)
            return {
                "ok": False,
                "msg": self.localize_error(f"启动失败: {exc}", "启动失败"),
            }
        self.process_state["proc"] = proc
        try:
            self.write_pid(proc.pid)
        except Exception as exc:
            self.logger("写入机器人 PID 文件失败，尝试停止进程: %s" % exc)
            self.kill_tree(proc.pid)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            except Exception as wait_exc:
                self.logger("等待 PID 文件写入失败后的机器人退出时出错: %s" % wait_exc)

            if proc.poll() is None:
                error = "PID 文件写入失败，机器人进程仍在运行 pid=%s" % proc.pid
                self.logger(error)
                return {
                    "ok": False,
                    "msg": self.localize_error(error, "启动失败"),
                }

            self.process_state["proc"] = None
            self.clear_pid()
            error = "启动机器人失败，无法写入 PID 文件: %s" % exc
            self.logger(error)
            return {
                "ok": False,
                "msg": self.localize_error(error, "启动失败"),
            }
        self.logger("启动机器人 pid=%s" % proc.pid)
        return {"ok": True, "pid": proc.pid}

    def stop_bot(self):
        stopped = False
        proc = self.process_state["proc"]
        if proc is not None and proc.poll() is None:
            self.kill_tree(proc.pid)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            if proc.poll() is None:
                error = "停止机器人失败，PID %s 仍在运行" % proc.pid
                self.logger(error)
                return {
                    "ok": False,
                    "stopped": False,
                    "msg": self.localize_error(error, "停止失败"),
                }
            stopped = True
        if proc is not None and proc.poll() is not None:
            self.process_state["proc"] = None

        record = self.read_pid_record()
        pid = record["pid"]
        if record.get("error"):
            reason = "无法读取" if record["error"] == "read" else "格式无效"
            error = "机器人 PID 文件%s，已保留文件并中止停止操作" % reason
            self.logger(error)
            return {
                "ok": False,
                "stopped": False,
                "msg": self.localize_error(error, "停止失败"),
            }
        if pid:
            identity_status = self.process_identity_status(
                pid,
                record["creation_time"],
            )
            if identity_status is None:
                error = (
                    "无法确认机器人 PID %s 的状态；请以启动机器人的相同 Windows 权限运行管理面板"
                    % pid
                )
                self.logger(error)
                return {
                    "ok": False,
                    "stopped": False,
                    "msg": self.localize_error(error, "停止失败"),
                }
            if identity_status is True:
                self.kill_tree(pid)
                exited = self._wait_for_process_exit(
                    pid,
                    record["creation_time"],
                )
                if not exited:
                    error = "停止机器人失败，PID %s 仍运行或状态无法确认" % pid
                    self.logger(error)
                    return {
                        "ok": False,
                        "stopped": False,
                        "msg": self.localize_error(error, "停止失败"),
                    }
                stopped = True
            else:
                # A confirmed stale PID is safe to clear.
                self.clear_pid()
        self.clear_pid()
        if stopped:
            self.logger("停止机器人")
        return {"ok": True, "stopped": stopped}
