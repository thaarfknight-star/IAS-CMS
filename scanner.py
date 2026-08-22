import socket
import concurrent.futures

COMMON_CCTV_PORTS = [554, 80, 8000, 37777, 8899]

def check_ip_port(ip: str, port: int, timeout=0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        return result == 0

def scan_single_host(ip: str):
    open_ports = []
    for port in COMMON_CCTV_PORTS:
        if check_ip_port(ip, port):
            open_ports.append(port)
    if open_ports:
        return {"ip": ip, "ports": open_ports}
    return None

def scan_subnet(base_subnet: str = "192.168.1", max_threads: int = 50):
    """Scan local subnet for active CCTV devices and RTSP ports."""
    active_devices = []
    ip_list = [f"{base_subnet}.{i}" for i in range(1, 255)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        results = executor.map(scan_single_host, ip_list)
        for res in results:
            if res:
                active_devices.append(res)

    return active_devices
