"""管理面板的机器人进程与 PID 文件操作。"""

import ctypes
import os
import socket
import subprocess
import time
import uuid
import urllib.request


def _kernel32_api():
    """Declare Win32 signatures explicitly so HANDLE values stay pointer-sized."""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateProcess.restype = ctypes.c_int
    return kernel32


class BotProcessManager:
    """封装机器人进程生命周期，供面板 API 和看门狗复用。"""

    def __init__(
        self,
        project_root,
        python_path,
        bot_entry,
        pid_file,
        panel_ping_url,
        panel_token,
        process_state,
        logger,
        localize_error,
    ):
        self.project_root = project_root
        self.python_path = python_path
        self.bot_entry = bot_entry
        self.pid_file = pid_file
        self.panel_ping_url = panel_ping_url
        self.panel_token = panel_token
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

    def panel_alive(self):
        """只有 /api/status 返回 200 才认为当前端口运行着本面板。"""
        try:
            req = urllib.request.Request(
                self.panel_ping_url,
                headers={"X-WRelay-Token": self.panel_token},
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def proc_exists(self, pid):
        """确认 PID 仍运行且其进程镜像是本项目使用的 Python。"""
        if pid <= 0:
            return False
        try:
            process_query_limited_information = 0x1000
            synchronize = 0x00100000
            kernel32 = _kernel32_api()
            handle = kernel32.OpenProcess(
                process_query_limited_information | synchronize,
                False,
                pid,
            )
            if not handle:
                return False
            try:
                # WAIT_TIMEOUT 表示进程仍在运行；已终止进程返回 WAIT_OBJECT_0。
                alive = kernel32.WaitForSingleObject(handle, 0) == 0x102
                if not alive:
                    return False

                size = ctypes.c_ulong(32768)
                buf = ctypes.create_unicode_buffer(size.value)
                if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)
                ):
                    return False
                return os.path.normcase(os.path.abspath(buf.value)) == os.path.normcase(
                    os.path.abspath(self.python_path)
                )
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    def read_pid(self):
        try:
            with open(self.pid_file, "r") as f:
                return int(f.read().strip() or 0)
        except Exception:
            return 0

    def write_pid(self, pid):
        parent = os.path.dirname(os.path.abspath(self.pid_file))
        os.makedirs(parent, exist_ok=True)
        temp_path = "%s.%s.%s.tmp" % (
            self.pid_file,
            os.getpid(),
            uuid.uuid4().hex,
        )
        try:
            with open(temp_path, "w", encoding="ascii") as f:
                f.write(str(pid))
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
        pid = self.read_pid()
        if pid:
            if self.proc_exists(pid):
                return {"running": True, "pid": pid, "managed": False}
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
            return {"ok": False, "msg": "机器人已在运行"}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = os.environ.copy()
        # 日志由 bot.py 写入轮转日志文件，不重定向到同一文件以免双写。
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        try:
            proc = subprocess.Popen(
                [self.python_path, self.bot_entry],
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

        pid = self.read_pid()
        if pid and self.proc_exists(pid):
            self.kill_tree(pid)
            if self.proc_exists(pid):
                error = "停止机器人失败，PID %s 仍在运行" % pid
                self.logger(error)
                return {
                    "ok": False,
                    "stopped": False,
                    "msg": self.localize_error(error, "停止失败"),
                }
            stopped = True
        self.clear_pid()
        if stopped:
            self.logger("停止机器人")
        return {"ok": True, "stopped": stopped}
