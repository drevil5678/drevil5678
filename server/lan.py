"""Local interface discovery. Never contact an external address to find the IP."""
import ipaddress
import socket

import psutil


PRIVATE_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))


def private_ipv4(address):
    try:
        value = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        return False
    return any(value in network for network in PRIVATE_NETWORKS)


def admin_url(host, port):
    address = "127.0.0.1" if host in ("0.0.0.0", "localhost") else host
    return f"http://{address}:{port}/#admin"


def network_info(host, port):
    enabled = host == "0.0.0.0" or private_ipv4(host)
    interfaces = []
    warning = ""
    if enabled:
        try:
            stats = psutil.net_if_stats()
            for name, entries in psutil.net_if_addrs().items():
                if name not in stats or not stats[name].isup:
                    continue
                for item in entries:
                    if item.family != socket.AF_INET or not private_ipv4(item.address):
                        continue
                    if host != "0.0.0.0" and item.address != host:
                        continue
                    interfaces.append({"name": name, "address": item.address,
                                       "url": f"http://{item.address}:{port}/#arena"})
        except (OSError, psutil.Error):
            warning = "無法讀取網卡；請用 ipconfig 查看活動網卡的 IPv4。"
    # Deduplicate aliases without silently choosing between Ethernet, Wi-Fi or VPN.
    unique = {item["address"]: item for item in interfaces}
    interfaces = sorted(unique.values(), key=lambda item: (item["name"], item["address"]))
    if not enabled:
        warning = "目前僅供本機測試。請停止程式後，使用 Start-LAN.cmd 啟動區網模式。"
    elif not interfaces:
        warning = warning or "未找到區域網路 IPv4。請連接場地的有線網路或 Wi-Fi，再重新檢查。"
    return {"bind_host": host, "port": port, "lan_enabled": enabled,
            "interfaces": interfaces, "warning": warning, "admin_url": admin_url(host, port)}
