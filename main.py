"""
RAG Agent — 统一启动入口
同时启动后端 (FastAPI) 和前端 (Vite)，实时展示双方的日志、状态和错误。
Ctrl+C 优雅关闭所有服务。
运行: python main.py
"""

import subprocess
import sys
import time
import threading
import os
import re
import io
import webbrowser
from pathlib import Path

# 强制 UTF-8 输出 (解决 Windows GBK 编码问题)
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"

# ── 颜色 ──────────────────────────────────────────────────────
C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[91m", "green": "\033[92m", "yellow": "\033[93m",
    "blue": "\033[94m", "magenta": "\033[95m", "cyan": "\033[96m",
}

processes: dict[str, subprocess.Popen] = {}
backend_port = 8000
frontend_actual_port = 5173


def link(url: str, label: str = "") -> str:
    """生成终端可点击的 OSC 8 超链接 (支持 Windows Terminal / VS Code / iTerm2)。"""
    text = label or url
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def open_browser(url: str):
    """在默认浏览器中打开 URL。"""
    try:
        webbrowser.open(url)
    except Exception:
        pass


def kill_port(port: int):
    """终止占用指定端口的进程 (Windows)。"""
    if sys.platform != "win32":
        return
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                time.sleep(0.5)
                print(f"  {C['yellow']}已终止端口 {port} 上的旧进程 (PID {pid}){C['reset']}")
    except Exception:
        pass


def print_banner():
    print(C["bold"] + C["cyan"] + """
  ========================================
        RAG Agent 启动中...
       后端 :8000  |  前端 :5173
  ========================================
""" + C["reset"])


def print_status(service: str, status: str, color: str = "green"):
    tag = "[OK]" if color == "green" else "[..]" if color == "yellow" else "[ER]"
    print(f"  {tag} {C[color]}[{service}]{C['reset']} {status}")


def stream_output(pipe, name: str, color: str):
    """后台线程：持续读取子进程输出。"""
    try:
        for line in iter(pipe.readline, ""):
            if line:
                ts = time.strftime("%H:%M:%S")
                print(f"{C['dim']}{ts}{C['reset']} {C[color]}[{name}]{C['reset']} {line.rstrip()}", flush=True)
    except (ValueError, OSError):
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def start_backend() -> bool:
    """启动 FastAPI 后端。"""
    global backend_port
    print_status("backend", "正在检查端口...", "yellow")
    kill_port(backend_port)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(backend_port)],
        cwd=str(BACKEND_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    processes["backend"] = proc

    stdout = proc.stdout
    if not stdout:
        print_status("backend", "无法读取进程输出", "red")
        return False

    deadline = time.time() + 30
    had_error = False
    while time.time() < deadline:
        line = stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            time.sleep(0.1)
            continue

        ts = time.strftime("%H:%M:%S")
        print(f"{C['dim']}{ts}{C['reset']} {C['blue']}[backend]{C['reset']} {line.rstrip()}", flush=True)

        if "ERROR" in line or "Error" in line or "error while attempting to bind" in line:
            had_error = True
        if "Uvicorn running on" in line or "Application startup complete" in line:
            m = re.search(r"http://[\d.]+:(\d+)", line)
            if m:
                backend_port = int(m.group(1))
            threading.Thread(target=stream_output, args=(stdout, "backend", "blue"), daemon=True).start()
            print_status("backend", f"启动成功 -> http://localhost:{backend_port}", "green")
            return True

    if not had_error:
        threading.Thread(target=stream_output, args=(stdout, "backend", "blue"), daemon=True).start()
        return True
    elif proc.poll() is not None:
        print_status("backend", f"进程退出 (code={proc.poll()})", "red")
        return False
    else:
        threading.Thread(target=stream_output, args=(proc.stdout, "backend", "blue"), daemon=True).start()
        return not had_error


def start_frontend() -> bool:
    """启动 Vite 前端。"""
    global frontend_actual_port
    print_status("frontend", "正在检查端口...", "yellow")
    kill_port(frontend_actual_port)

    env = os.environ.copy()
    env["BROWSER"] = "none"
    npm = "npm.cmd" if sys.platform == "win32" else "npm"

    proc = subprocess.Popen(
        [npm, "run", "dev"],
        cwd=str(FRONTEND_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    processes["frontend"] = proc

    stdout = proc.stdout
    if not stdout:
        print_status("frontend", "无法读取进程输出", "red")
        return False

    port_pattern = re.compile(r"localhost:(\d+)")
    deadline = time.time() + 30
    had_error = False

    while time.time() < deadline:
        line = stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            time.sleep(0.1)
            continue

        ts = time.strftime("%H:%M:%S")
        print(f"{C['dim']}{ts}{C['reset']} {C['magenta']}[frontend]{C['reset']} {line.rstrip()}", flush=True)

        if "Local:" in line or "localhost" in line.lower() or "ready in" in line:
            m = port_pattern.search(line)
            if m:
                frontend_actual_port = int(m.group(1))
            if "ready in" in line and not m:
                continue
            threading.Thread(target=stream_output, args=(stdout, "frontend", "magenta"), daemon=True).start()
            if frontend_actual_port != 5173:
                print_status("frontend", f"启动成功 -> http://localhost:{frontend_actual_port}", "green")
            else:
                print_status("frontend", "启动成功", "green")
            return True

        if "error" in line.lower() and "ready" not in line.lower():
            had_error = True

    if had_error:
        print_status("frontend", "启动时出现错误", "red")
        return False
    elif proc.poll() is not None:
        print_status("frontend", f"进程退出 (code={proc.poll()})", "red")
        return False
    # 超时但进程还在，当作成功
    threading.Thread(target=stream_output, args=(proc.stdout, "frontend", "magenta"), daemon=True).start()
    return True


def shutdown():
    """优雅关闭所有子进程。"""
    print(f"\n{C['yellow']}正在关闭所有服务...{C['reset']}")
    for name, proc in processes.items():
        if proc.poll() is None:
            print(f"  -> 终止 {name} (PID {proc.pid})...")
            proc.terminate()
    deadline = time.time() + 5
    while time.time() < deadline:
        if all(p.poll() is not None for p in processes.values()):
            break
        time.sleep(0.2)
    for name, proc in processes.items():
        if proc.poll() is None:
            proc.kill()
    print(C["green"] + "已关闭。" + C["reset"])


def check_prerequisites() -> bool:
    """检查前置条件并自动修复。"""
    all_ok = True

    if not (BACKEND_DIR / ".env").exists():
        print(f"  {C['yellow']}[!] backend/.env 不存在，自动创建模板{C['reset']}")
        (BACKEND_DIR / ".env").write_text(
            "LLM_API_KEY=your-api-key-here\nEMBEDDING_API_KEY=your-api-key-here\n",
            encoding="utf-8",
        )
        print(f"  {C['yellow']}  -> 请编辑 backend/.env 填入有效的 API Key{C['reset']}")

    if not (BACKEND_DIR / "data").exists():
        (BACKEND_DIR / "data").mkdir(parents=True)

    if not (FRONTEND_DIR / "node_modules").exists():
        print(f"  {C['yellow']}[!] 正在 npm install (首次运行)...{C['reset']}")
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        result = subprocess.run(
            [npm, "install"], cwd=str(FRONTEND_DIR),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            print(f"  {C['red']}[X] npm install 失败:{C['reset']}")
            print(result.stderr[-500:])
            all_ok = False
        else:
            print(f"  {C['green']}[OK] 依赖安装完成{C['reset']}")

    return all_ok


def _input_thread(stop_event: threading.Event):
    """后台线程：监听键盘输入，不发回车即可响应 (Windows)。"""
    if sys.platform == "win32":
        import msvcrt
        while not stop_event.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                handle_key(ch)
            time.sleep(0.1)
    else:
        # Unix: 使用 select + sys.stdin
        import select
        import tty
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        try:
            while not stop_event.is_set():
                r, _, _ = select.select([sys.stdin], [], [], 0.2)
                if r:
                    ch = sys.stdin.read(1).lower()
                    handle_key(ch)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def handle_key(ch: str):
    """处理键盘命令。"""
    frontend_url = f"http://localhost:{frontend_actual_port}"
    backend_url = f"http://localhost:{backend_port}"

    if ch == "o":
        open_browser(frontend_url)
        print(f"  {C['green']}-> 已打开前端 {frontend_url}{C['reset']}")
    elif ch == "a":
        open_browser(f"{backend_url}/docs")
        print(f"  {C['green']}-> 已打开 API 文档 {backend_url}/docs{C['reset']}")
    elif ch == "s":
        open_browser(f"{frontend_url}/settings")
        print(f"  {C['green']}-> 已打开配置页面 {frontend_url}/settings{C['reset']}")
    elif ch == "d":
        open_browser(frontend_url)
        open_browser(f"{backend_url}/docs")
        print(f"  {C['green']}-> 已打开前端 + API 文档{C['reset']}")
    elif ch == "q":
        print(f"\n{C['yellow']}收到退出命令...{C['reset']}")
        shutdown()
        sys.exit(0)


def main():
    print_banner()

    print(C["bold"] + "-- 环境检查 --" + C["reset"])
    if not check_prerequisites():
        print(f"\n{C['red']}前置条件检查失败，请修复后重试。{C['reset']}")
        sys.exit(1)

    print(f"\n{C['bold']}-- 启动服务 --{C['reset']}")

    if not start_backend():
        shutdown()
        sys.exit(1)

    if not start_frontend():
        shutdown()
        sys.exit(1)

    frontend_url = f"http://localhost:{frontend_actual_port}"
    backend_url = f"http://localhost:{backend_port}"

    print(f"""
{C['bold']}{C['cyan']}============================================================
  所有服务已启动{C['reset']}

  {C['bold']}前端    {C['reset']} {link(frontend_url)}
  {C['bold']}API 文档 {C['reset']} {link(f"{backend_url}/docs")}
  {C['bold']}配置页  {C['reset']} {link(f"{frontend_url}/settings")}
{C['cyan']}============================================================{C['reset']}

  {C['bold']}快捷键:{C['reset']}
    {C['yellow']}o{C['reset']} = 打开前端    {C['yellow']}a{C['reset']} = 打开 API 文档
    {C['yellow']}s{C['reset']} = 打开配置页  {C['yellow']}d{C['reset']} = 全部打开
    {C['yellow']}q{C['reset']} = 退出        {C['yellow']}Ctrl+C{C['reset']} = 退出
""")

    # 启动键盘监听线程
    stop_event = threading.Event()
    input_thread = threading.Thread(target=_input_thread, args=(stop_event,), daemon=True)
    input_thread.start()

    # 监控运行
    try:
        while processes:
            for name, proc in list(processes.items()):
                code = proc.poll()
                if code is not None:
                    if code == 0:
                        print(f"  {C['yellow']}[{name}] 已退出{C['reset']}")
                    else:
                        print(f"  {C['red']}[{name}] 异常退出 (code={code}), 检查上方日志{C['reset']}")
                    processes.pop(name)
                    if processes:
                        print(f"  {C['red']}服务异常，关闭中...{C['reset']}")
                        stop_event.set()
                        shutdown()
                        sys.exit(1)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        shutdown()


if __name__ == "__main__":
    main()
