"""RAG Agent 本地开发环境统一启动入口。

同时启动 FastAPI 后端与 Vite 前端，合并展示服务日志，并在退出时关闭子进程。

用法：
    python main.py
    python main.py --open
    python main.py --backend-port 8000 --frontend-port 5173
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
DEFAULT_BACKEND_PORT = 8000
DEFAULT_FRONTEND_PORT = 5173
# This deadline covers core FastAPI startup only. Optional OCR/reranker models
# continue downloading in the backend after the web service becomes ready.
DEFAULT_STARTUP_TIMEOUT = 180.0


def _configure_windows_console() -> None:
    """确保 Windows 控制台能正确显示中英文日志。"""
    if sys.platform != "win32":
        return
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
        elif hasattr(stream, "buffer"):
            setattr(
                sys,
                stream_name,
                io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"),
            )


_configure_windows_console()

_USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ
_ESC = "\033" if _USE_COLOR else ""
COLORS = {
    "reset": f"{_ESC}[0m" if _ESC else "",
    "bold": f"{_ESC}[1m" if _ESC else "",
    "dim": f"{_ESC}[2m" if _ESC else "",
    "red": f"{_ESC}[91m" if _ESC else "",
    "green": f"{_ESC}[92m" if _ESC else "",
    "yellow": f"{_ESC}[93m" if _ESC else "",
    "blue": f"{_ESC}[94m" if _ESC else "",
    "magenta": f"{_ESC}[95m" if _ESC else "",
    "cyan": f"{_ESC}[96m" if _ESC else "",
}

PROCESSES: dict[str, subprocess.Popen[str]] = {}
PRINT_LOCK = threading.Lock()
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def backend_python_executable() -> Path:
    """优先使用项目虚拟环境，避免启动器误用缺少依赖的系统 Python。"""
    if sys.platform == "win32":
        project_python = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        project_python = ROOT / ".venv" / "bin" / "python"
    return project_python if project_python.is_file() else Path(sys.executable)


def terminal_link(url: str, label: str | None = None) -> str:
    """返回终端可点击链接；非交互输出保留普通 URL。"""
    text = label or url
    if not _USE_COLOR:
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def _print(message: str = "") -> None:
    with PRINT_LOCK:
        print(message, flush=True)


def print_status(service: str, status: str, color: str = "green") -> None:
    marker = {"green": "OK", "yellow": "..", "red": "!!"}.get(color, "--")
    _print(f"  [{marker}] {COLORS[color]}{service:<8}{COLORS['reset']} {status}")


def print_banner(backend_port: int, frontend_port: int) -> None:
    _print(
        f"{COLORS['bold']}{COLORS['cyan']}"
        "\n  ========================================\n"
        "             RAG Agent\n"
        f"       后端 :{backend_port}  |  前端 :{frontend_port}\n"
        "  ========================================"
        f"{COLORS['reset']}\n"
    )


def _listening_pids(port: int) -> set[int]:
    """查询 Windows 上精确监听指定端口的进程。"""
    if sys.platform != "win32":
        return set()
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[3].upper() != "LISTENING":
            continue
        local_address = parts[1].strip("[]")
        if local_address.rsplit(":", 1)[-1] == str(port) and parts[-1].isdigit():
            pids.add(int(parts[-1]))
    return pids


def release_port(port: int) -> None:
    """关闭由旧版启动器遗留、仍占用目标端口的本机进程。"""
    for pid in _listening_pids(port):
        result = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0:
            print_status("端口", f"已停止 :{port} 上的旧进程 (PID {pid})", "yellow")
            time.sleep(0.3)
        else:
            raise RuntimeError(f"无法释放端口 {port}（PID {pid}）")


class ServiceOutput:
    """持续转发子进程日志，并通过日志特征判断服务是否就绪。"""

    def __init__(
        self,
        process: subprocess.Popen[str],
        name: str,
        color: str,
        ready_when: Callable[[str], bool],
        on_line: Callable[[str], None] | None = None,
    ) -> None:
        self.process = process
        self.name = name
        self.color = color
        self.ready_when = ready_when
        self.on_line = on_line
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name=f"{name}-logs", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        pipe = self.process.stdout
        if pipe is None:
            return
        try:
            for line in pipe:
                clean_line = line.rstrip()
                timestamp = time.strftime("%H:%M:%S")
                _print(
                    f"{COLORS['dim']}{timestamp}{COLORS['reset']} "
                    f"{COLORS[self.color]}[{self.name}]{COLORS['reset']} {clean_line}"
                )
                if self.on_line:
                    self.on_line(clean_line)
                if self.ready_when(clean_line):
                    self.ready.set()
        except (OSError, ValueError):
            return
        finally:
            pipe.close()


def _spawn(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


def _wait_until_ready(output: ServiceOutput, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if output.ready.wait(timeout=0.1):
            return True
        if output.process.poll() is not None:
            return False
    return False


def frontend_ready_line(line: str, port: int) -> bool:
    """识别 Vite 输出的本地地址，兼容 ANSI 颜色和常见回环主机名。"""
    clean_line = ANSI_ESCAPE_PATTERN.sub("", line)
    local_url_pattern = re.compile(
        rf"https?://(?:localhost|127\.0\.0\.1|\[::1\]):{port}(?:/|\s|$)"
    )
    return bool(local_url_pattern.search(clean_line))


def start_backend(port: int, timeout: float) -> bool:
    print_status("后端", f"准备端口 {port}", "yellow")
    print_status("后端", f"等待核心服务就绪（最长 {timeout:g} 秒）", "yellow")
    print_status("可选模型", "OCR/Reranker 未缓存时会在服务启动后继续后台下载，不受此时限影响", "yellow")
    try:
        release_port(port)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        # 根启动器仅启动一个本地后端实例，可安全执行向前 Alembic 迁移。
        # 直接启动后端和生产部署仍由严格 revision gate 控制。
        env["AUTO_MIGRATE"] = "1"
        backend_python = backend_python_executable()
        process = _spawn(
            [
                str(backend_python),
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            BACKEND_DIR,
            env,
        )
    except (OSError, RuntimeError) as exc:
        print_status("后端", f"启动失败：{exc}", "red")
        return False

    PROCESSES["后端"] = process
    output = ServiceOutput(
        process,
        "后端",
        "blue",
        ready_when=lambda line: "Application startup complete" in line,
    )
    output.start()
    if not _wait_until_ready(output, timeout):
        reason = f"进程已退出，代码 {process.poll()}" if process.poll() is not None else f"{timeout:g} 秒内未就绪"
        print_status("后端", f"启动失败：{reason}", "red")
        return False
    print_status("后端", f"已启动：http://localhost:{port}")
    return True


def start_frontend(port: int, backend_port: int, timeout: float) -> bool:
    print_status("前端", f"准备端口 {port}", "yellow")
    try:
        release_port(port)
        env = os.environ.copy()
        env["BROWSER"] = "none"
        env["VITE_API_PROXY_TARGET"] = f"http://localhost:{backend_port}"
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        process = _spawn(
            [npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
            FRONTEND_DIR,
            env,
        )
    except (OSError, RuntimeError) as exc:
        print_status("前端", f"启动失败：{exc}", "red")
        return False

    PROCESSES["前端"] = process
    output = ServiceOutput(
        process,
        "前端",
        "magenta",
        ready_when=lambda line: frontend_ready_line(line, port),
    )
    output.start()
    if not _wait_until_ready(output, timeout):
        reason = f"进程已退出，代码 {process.poll()}" if process.poll() is not None else f"{timeout:g} 秒内未就绪"
        print_status("前端", f"启动失败：{reason}", "red")
        return False
    print_status("前端", f"已启动：http://localhost:{port}")
    return True


def check_prerequisites() -> bool:
    """检查目录、配置和前端依赖；首次运行时完成必要初始化。"""
    if not BACKEND_DIR.is_dir() or not FRONTEND_DIR.is_dir():
        print_status("环境", "项目目录不完整，需要 backend/ 和 frontend/", "red")
        return False

    env_file = BACKEND_DIR / ".env"
    env_example = BACKEND_DIR / ".env.example"
    if not env_file.exists():
        if not env_example.exists():
            print_status("环境", "缺少 backend/.env 和 backend/.env.example", "red")
            return False
        shutil.copyfile(env_example, env_file)
        print_status("环境", "已由 backend/.env.example 创建 backend/.env", "yellow")
        print_status("环境", "请按需在设置页填写模型服务配置", "yellow")

    (BACKEND_DIR / "data").mkdir(parents=True, exist_ok=True)

    backend_python = backend_python_executable()
    if backend_python.resolve() != Path(sys.executable).resolve():
        print_status("环境", f"后端使用项目虚拟环境：{backend_python}")

    if not (FRONTEND_DIR / "node_modules").is_dir():
        print_status("环境", "首次运行，正在安装前端依赖", "yellow")
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        try:
            result = subprocess.run(
                [npm, "install"],
                cwd=str(FRONTEND_DIR),
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            print_status("环境", f"无法运行 npm：{exc}", "red")
            return False
        if result.returncode != 0:
            print_status("环境", "npm install 失败", "red")
            return False
        print_status("环境", "前端依赖安装完成")

    return True


def shutdown() -> None:
    """先温和终止全部子进程，超时后再强制结束。"""
    if not PROCESSES:
        return
    _print(f"\n{COLORS['yellow']}正在关闭服务……{COLORS['reset']}")
    for name, process in PROCESSES.items():
        if process.poll() is None:
            print_status(name, f"正在停止 (PID {process.pid})", "yellow")
            process.terminate()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(process.poll() is None for process in PROCESSES.values()):
        time.sleep(0.1)

    for process in PROCESSES.values():
        if process.poll() is None:
            process.kill()
    PROCESSES.clear()
    print_status("服务", "已全部关闭")


def _open_url(url: str) -> None:
    try:
        webbrowser.open(url)
    except webbrowser.Error as exc:
        print_status("浏览器", f"无法自动打开：{exc}", "red")


def handle_key(key: str, frontend_url: str, backend_url: str) -> bool:
    """处理快捷键；返回 False 表示请求退出。"""
    actions = {
        "o": (frontend_url, "网页"),
        "a": (f"{backend_url}/docs", "API 文档"),
        "s": (f"{frontend_url}/settings", "设置页"),
    }
    if key in actions:
        url, label = actions[key]
        _open_url(url)
        print_status("浏览器", f"已打开{label}：{url}")
    elif key == "d":
        _open_url(frontend_url)
        _open_url(f"{backend_url}/docs")
        print_status("浏览器", "已打开网页和 API 文档")
    elif key == "q":
        return False
    return True


def keyboard_listener(stop_event: threading.Event, frontend_url: str, backend_url: str) -> None:
    """后台监听单键快捷命令，不要求按回车。"""
    if not sys.stdin.isatty():
        return
    if sys.platform == "win32":
        import msvcrt

        while not stop_event.is_set():
            if msvcrt.kbhit():
                key = msvcrt.getwch().lower()
                if not handle_key(key, frontend_url, backend_url):
                    stop_event.set()
            time.sleep(0.1)
        return

    import select
    import termios
    import tty

    file_descriptor = sys.stdin.fileno()
    old_settings = termios.tcgetattr(file_descriptor)
    tty.setcbreak(file_descriptor)
    try:
        while not stop_event.is_set():
            readable, _, _ = select.select([sys.stdin], [], [], 0.2)
            if readable and not handle_key(sys.stdin.read(1).lower(), frontend_url, backend_url):
                stop_event.set()
    finally:
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, old_settings)


def print_startup_summary(frontend_url: str, backend_url: str) -> None:
    """打印启动摘要，并把用户最常用的网页 URL 放在最后。"""
    _print()
    _print(f"{COLORS['bold']}{COLORS['cyan']}所有服务已启动{COLORS['reset']}")
    _print(f"  API 文档：{terminal_link(f'{backend_url}/docs')}")
    _print(f"  设置页面：{terminal_link(f'{frontend_url}/settings')}")
    _print("  快捷键：o 网页｜a API 文档｜s 设置页｜d 全部打开｜q 退出｜Ctrl+C 退出")
    _print()
    # 保持为启动摘要最后一行，方便用户直接点击。
    _print(f"  打开网页：{terminal_link(frontend_url)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同时启动 RAG Agent 后端和前端")
    parser.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT, help="后端端口（默认 8000）")
    parser.add_argument("--frontend-port", type=int, default=DEFAULT_FRONTEND_PORT, help="前端端口（默认 5173）")
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=DEFAULT_STARTUP_TIMEOUT,
        help=f"单个服务的启动超时秒数（默认 {DEFAULT_STARTUP_TIMEOUT:g}）",
    )
    parser.add_argument("--open", action="store_true", help="启动成功后自动打开网页")
    args = parser.parse_args(argv)
    for name in ("backend_port", "frontend_port"):
        value = getattr(args, name)
        if not 1 <= value <= 65535:
            parser.error(f"{name.replace('_', '-')} 必须在 1 到 65535 之间")
    if args.backend_port == args.frontend_port:
        parser.error("前端和后端不能使用同一个端口")
    if args.startup_timeout <= 0:
        parser.error("startup-timeout 必须大于 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print_banner(args.backend_port, args.frontend_port)

    _print(f"{COLORS['bold']}环境检查{COLORS['reset']}")
    if not check_prerequisites():
        return 1

    _print(f"\n{COLORS['bold']}启动服务{COLORS['reset']}")
    if not start_backend(args.backend_port, args.startup_timeout):
        shutdown()
        return 1
    if not start_frontend(args.frontend_port, args.backend_port, args.startup_timeout):
        shutdown()
        return 1

    frontend_url = f"http://localhost:{args.frontend_port}"
    backend_url = f"http://localhost:{args.backend_port}"
    print_startup_summary(frontend_url, backend_url)
    if args.open:
        _open_url(frontend_url)

    stop_event = threading.Event()
    input_thread = threading.Thread(
        target=keyboard_listener,
        args=(stop_event, frontend_url, backend_url),
        name="keyboard-listener",
        daemon=True,
    )
    input_thread.start()

    exit_code = 0
    try:
        while not stop_event.wait(0.5):
            for name, process in tuple(PROCESSES.items()):
                code = process.poll()
                if code is None:
                    continue
                print_status(name, f"异常退出 (code={code})", "red")
                exit_code = 1
                stop_event.set()
                break
    except KeyboardInterrupt:
        _print()
    finally:
        stop_event.set()
        shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
