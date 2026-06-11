"""
IRIS 一键启动脚本 — 自动安装依赖、启动前后端、打开浏览器
用法:
    python start.py           # 一键启动
    python start.py --setup   # 仅安装依赖
    python start.py --stop    # 停止所有服务
    python start.py --clean   # 清理环境 (删 venv + node_modules)
"""

import os
import sys
import shutil
import signal
import subprocess
import time
import json
import argparse
import webbrowser
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR / "backend"
FRONTEND_DIR = SCRIPT_DIR / "frontend"
PID_FILE = SCRIPT_DIR / ".iris_pids.json"
LOG_DIR = SCRIPT_DIR / ".logs"

# ── 终端颜色 ──────────────────────────────────────────
class C:
    R = "\033[0;31m"
    G = "\033[0;32m"
    Y = "\033[1;33m"
    B = "\033[0;34m"
    N = "\033[0m"

def _c(c, msg): return f"{c}{msg}{C.N}"

def log_info(msg):  print(f"{_c(C.B, '[INFO]')}  {msg}")
def log_ok(msg):    print(f"{_c(C.G, '[OK]')}    {msg}")
def log_warn(msg):  print(f"{_c(C.Y, '[WARN]')}  {msg}")
def log_error(msg): print(f"{_c(C.R, '[ERROR]')} {msg}")

def banner(text):
    print(f"\n{C.G}{'='*50}{C.N}\n  {text}\n{C.G}{'='*50}{C.N}\n")


# ── 平台与环境检测 ────────────────────────────────────
IS_WIN = sys.platform == "win32"

def find_python() -> Path | None:
    """按优先级查找可用的 Python 解释器"""
    candidates = []
    if IS_WIN:
        candidates = [
            Path(r"D:\anaconda3\python.exe"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python/Python312/python.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python/Python311/python.exe",
            Path(r"C:\Python312\python.exe"),
            Path(r"C:\Python311\python.exe"),
            Path(r"C:\Python310\python.exe"),
        ]
    for p in candidates:
        if p.exists():
            return p
    # fallback: 系统 PATH 中的 python3 / python
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def find_venv_python() -> Path | None:
    """查找 venv 中的 Python"""
    if IS_WIN:
        return BACKEND_DIR / "venv" / "Scripts" / "python.exe"
    return BACKEND_DIR / "venv" / "bin" / "python"


def check_prereqs() -> tuple[Path, Path]:
    """检查运行环境，返回 (python_path, nodejs_found)"""
    python = find_python()
    if not python:
        log_error("未找到 Python 3.10+，请先安装")
        sys.exit(1)
    log_ok(f"Python: {python}")

    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        log_error("未找到 Node.js，请先安装")
        sys.exit(1)
    log_ok(f"Node.js: {node}")

    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        log_error("未找到 npm")
        sys.exit(1)
    log_ok(f"npm: {npm}")

    return python, Path(node)


# ── 进程管理 ──────────────────────────────────────────
def load_pids() -> dict:
    if PID_FILE.exists():
        try:
            return json.loads(PID_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_pids(pids: dict):
    PID_FILE.write_text(json.dumps(pids, indent=2))


def kill_process(pid: int):
    """终止进程"""
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                          capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass


def is_running(pid: int) -> bool:
    """检查进程是否存活"""
    try:
        if IS_WIN:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True
            )
            return str(pid) in result.stdout
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


def stop_services():
    """停止所有 IRIS 服务"""
    banner("停止 IRIS 服务")
    pids = load_pids()

    for name, pid in pids.items():
        if is_running(pid):
            log_info(f"终止 {name} (PID: {pid})")
            kill_process(pid)
            time.sleep(0.3)
        else:
            log_info(f"{name} 已停止")

    # 兜底：按端口清理
    for port, label in [(8000, "后端"), (5173, "前端")]:
        if IS_WIN:
            try:
                r = subprocess.run(
                    ["netstat", "-ano"], capture_output=True, text=True
                )
                for line in r.stdout.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.strip().split()
                        pid = int(parts[-1])
                        log_info(f"清理端口 {port} ({label}, PID: {pid})")
                        kill_process(pid)
            except Exception:
                pass
        else:
            try:
                r = subprocess.run(
                    ["lsof", "-ti", f":{port}"], capture_output=True, text=True
                )
                for pid_str in r.stdout.strip().splitlines():
                    kill_process(int(pid_str))
            except Exception:
                pass

    PID_FILE.unlink(missing_ok=True)
    log_ok("所有服务已停止")


# ── 依赖安装 ──────────────────────────────────────────
def setup_backend(python: Path):
    """安装后端依赖"""
    log_info("--- 后端依赖 ---")
    venv_dir = BACKEND_DIR / "venv"
    if not venv_dir.exists():
        log_info("创建 Python 虚拟环境...")
        subprocess.run([str(python), "-m", "venv", str(venv_dir)], check=True)

    venv_python = find_venv_python()

    # 升级 pip
    subprocess.run([str(venv_python), "-m", "pip", "install", "-q", "--upgrade", "pip"],
                   capture_output=True)

    log_info("安装后端依赖...")
    req_file = BACKEND_DIR / "requirements.txt"
    subprocess.run([str(venv_python), "-m", "pip", "install", "-q", "-r", str(req_file)],
                   check=True)
    log_ok("后端依赖安装完成")


def setup_frontend():
    """安装前端依赖"""
    log_info("--- 前端依赖 ---")
    node_modules = FRONTEND_DIR / "node_modules"
    npm = "npm.cmd" if IS_WIN else "npm"
    if not node_modules.exists():
        log_info("安装前端依赖 (npm install)...")
        subprocess.run([npm, "install", "--silent"], cwd=str(FRONTEND_DIR), check=True)
    log_ok("前端依赖安装完成")


def setup(python: Path):
    """安装全部依赖"""
    banner("安装 IRIS 依赖")
    setup_backend(python)
    setup_frontend()
    log_ok("依赖安装完成，运行 python start.py 启动服务")


def clean_env():
    """清理环境"""
    banner("清理 IRIS 环境")
    stop_services()

    for path, label in [
        (BACKEND_DIR / "venv", "后端虚拟环境"),
        (FRONTEND_DIR / "node_modules", "前端 node_modules"),
        (LOG_DIR, "日志目录"),
    ]:
        if path.exists():
            log_info(f"删除 {label}...")
            shutil.rmtree(path, ignore_errors=True)

    PID_FILE.unlink(missing_ok=True)
    log_ok("环境已清理，下次启动将重新安装依赖")


# ── 服务启动 ──────────────────────────────────────────
def start_services(python: Path):
    """启动前后端服务"""
    banner("启动 IRIS")

    # 先停止已有实例
    old_pids = load_pids()
    if old_pids:
        log_info("检测到已有服务运行，先停止...")
        stop_services()

    LOG_DIR.mkdir(exist_ok=True)
    venv_python = find_venv_python()

    # 检查 .env
    if not (BACKEND_DIR / ".env").exists() and not (SCRIPT_DIR / ".env").exists():
        log_warn("未找到 .env 文件，请先配置 API Key")
        log_info("后端将使用默认配置启动")

    # ── 启动后端 ──
    log_info("启动后端 (FastAPI + Uvicorn, port 8000)...")
    backend_log = open(LOG_DIR / "backend.log", "w")
    backend_proc = subprocess.Popen(
        [str(venv_python), "-m", "uvicorn", "main:app",
         "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(BACKEND_DIR),
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
    )
    log_ok(f"后端已启动 (PID: {backend_proc.pid})")

    # ── 启动前端 ──
    log_info("启动前端 (Vite, port 5173)...")
    npm = "npm.cmd" if IS_WIN else "npm"
    frontend_log = open(LOG_DIR / "frontend.log", "w")
    frontend_proc = subprocess.Popen(
        [npm, "run", "dev"],
        cwd=str(FRONTEND_DIR),
        stdout=frontend_log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if IS_WIN else 0,
    )
    log_ok(f"前端已启动 (PID: {frontend_proc.pid})")

    # 保存 PID
    save_pids({
        "backend": backend_proc.pid,
        "frontend": frontend_proc.pid,
    })

    # ── 等待后端就绪 ──
    log_info("等待后端就绪...")
    import urllib.request
    for _ in range(30):
        try:
            urllib.request.urlopen("http://localhost:8000/", timeout=1)
            break
        except Exception:
            time.sleep(1)
    else:
        log_warn("后端启动超时，请检查日志: .logs/backend.log")

    # ── 打印信息 ──
    banner("IRIS 启动成功!")
    print(f"  {C.B}前端界面:{C.N}  http://localhost:5173")
    print(f"  {C.B}后端 API:{C.N}   http://localhost:8000")
    print(f"  {C.B}API 文档:{C.N}   http://localhost:8000/docs")
    print(f"  {C.B}运行评测:{C.N}   cd backend && python run_eval.py")
    print()
    print(f"  {C.Y}查看日志:{C.N}   type .logs\\backend.log (Windows)")
    print(f"               cat .logs/backend.log (Linux/macOS)")
    print(f"  {C.Y}清理环境:{C.N}   python start.py --clean")
    print()
    print(f"  {C.G}{'─'*50}{C.N}")
    print(f"  {C.Y}按 Enter 或 Ctrl+C 停止所有服务{C.N}")
    print(f"  {C.G}{'─'*50}{C.N}")
    print()

    # 自动打开浏览器
    webbrowser.open("http://localhost:5173")


# ── 按键等待 ──────────────────────────────────────────
def wait_for_stop():
    """阻塞等待用户按 Enter 或 Ctrl+C，然后停止服务"""
    try:
        if IS_WIN:
            import msvcrt
            # Windows: 非阻塞检测 + 等待输入
            while True:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key in (b'\r', b'\n', b'\x03'):  # Enter 或 Ctrl+C
                        break
                # 检查子进程是否还活着
                pids = load_pids()
                all_dead = True
                for pid in pids.values():
                    if is_running(pid):
                        all_dead = False
                        break
                if all_dead and pids:
                    log_warn("检测到前后端进程已退出，检查日志: .logs/")
                    break
                time.sleep(0.3)
        else:
            # Unix: select 监听 stdin
            import select
            while True:
                if select.select([sys.stdin], [], [], 0.3)[0]:
                    sys.stdin.readline()
                    break
                pids = load_pids()
                all_dead = True
                for pid in pids.values():
                    if is_running(pid):
                        all_dead = False
                        break
                if all_dead and pids:
                    log_warn("检测到前后端进程已退出，检查日志: .logs/")
                    break
    except KeyboardInterrupt:
        pass
    finally:
        print()
        log_info("正在停止所有服务...")
        stop_services()


# ── 主入口 ────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="IRIS 一键启动脚本")
    parser.add_argument("--setup", action="store_true", help="仅安装依赖，不启动")
    parser.add_argument("--stop",  action="store_true", help="停止所有服务")
    parser.add_argument("--clean", action="store_true", help="清理环境（删 venv + node_modules）")
    args = parser.parse_args()

    if args.stop:
        stop_services()
        return

    if args.clean:
        clean_env()
        return

    python, _ = check_prereqs()

    if args.setup:
        setup(python)
        return

    # 默认模式: 安装依赖 + 启动
    setup(python)
    try:
        start_services(python)
        wait_for_stop()
    except KeyboardInterrupt:
        print()
        log_info("正在停止所有服务...")
        stop_services()


if __name__ == "__main__":
    main()
