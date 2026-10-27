import time
import subprocess
import concurrent.futures
from unittest.mock import patch, MagicMock

# Simulate a mock adb connect taking 0.1 seconds per call

def mock_subprocess_run(*args, **kwargs):
    time.sleep(0.1)
    return MagicMock()

def run_sequential(ips):
    start = time.time()
    for ip in ips:
        subprocess.run(["adb", "connect", f"{ip}:5555"], capture_output=True, timeout=1)
    end = time.time()
    return end - start

def run_concurrent(ips):
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for ip in ips:
            futures.append(executor.submit(subprocess.run, ["adb", "connect", f"{ip}:5555"], capture_output=True, timeout=1))
        concurrent.futures.wait(futures)
    end = time.time()
    return end - start

if __name__ == "__main__":
    ips = [f"192.168.1.{i}" for i in range(1, 21)]  # 20 IPs

    with patch('subprocess.run', side_effect=mock_subprocess_run):
        t_seq = run_sequential(ips)
        print(f"Sequential: {t_seq:.3f}s")
        t_conc = run_concurrent(ips)
        print(f"Concurrent: {t_conc:.3f}s")
