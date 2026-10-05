"""Launch project-local services without opening console windows."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
log_dir = root / "artifacts" / "server"
log_dir.mkdir(parents=True, exist_ok=True)
services = [
    ("dashboard", 8501, ["-m", "streamlit", "run", "dashboard.py", "--server.address=127.0.0.1",
                          "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]),
    ("api", 8000, ["-m", "uvicorn", "bharatblend.api:app", "--host", "127.0.0.1", "--port", "8000"]),
]
started = []
for name, port, arguments in services:
    with socket.socket() as connection:
        connection.settimeout(0.5)
        if connection.connect_ex(("127.0.0.1", port)) == 0:
            print(f"Port {port} is occupied; leaving its process untouched.")
            continue
    with (log_dir / f"{name}.log").open("ab") as stdout, (log_dir / f"{name}.error.log").open("ab") as stderr:
        process = subprocess.Popen([sys.executable, *arguments], cwd=root, stdout=stdout, stderr=stderr,
                                   stdin=subprocess.DEVNULL, close_fds=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    started.append({"service": name, "pid": process.pid, "port": port})
    print(f"{name}: PID {process.pid}, http://127.0.0.1:{port}")
if started:
    process_file = log_dir / "processes.json"
    previous = json.loads(process_file.read_text(encoding="utf-8")) if process_file.exists() else []
    replaced = {entry["service"] for entry in started}
    records = [entry for entry in previous if entry["service"] not in replaced] + started
    process_file.write_text(json.dumps(records, indent=2), encoding="utf-8")
