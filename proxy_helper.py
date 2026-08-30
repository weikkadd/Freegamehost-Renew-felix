#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v2rayN proxy helper: parse proxy URI and manage sing-box process.

Supports:
  - vless://uuid@host:port?...#name
  - vmess://base64-json
  - trojan://password@host:port?...#name
  - ss://method:password@host:port#name  (Shadowsocks)
  - hysteria2://password@host:port?...#name  (hy2:// also accepted)
  - hysteria://password@host:port?...#name

Uses sing-box as backend (supports all protocols above natively).

Usage:
  from proxy_helper import ProxyManager
  pm = ProxyManager()
  pm.start("hysteria2://...")  # Start sing-box with this proxy
  # Browser uses socks5://127.0.0.1:10808
  pm.stop()
  pm.rotate()  # Switch to next proxy in list
"""

import os
import sys
import json
import time
import socket
import shutil
import base64
import subprocess
import urllib.parse
from typing import Optional, List


def log(msg, level="INFO"):
    prefix = {"INFO": "[FGH-Renew]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(level, "[FGH-Renew]")
    print(f"{prefix} {msg}", flush=True)


# Find sing-box binary
def find_singbox() -> str:
    """Find sing-box binary. Tries common paths."""
    for path in [
        shutil.which('sing-box'),
        '/usr/local/bin/sing-box',
        '/usr/bin/sing-box',
        os.path.expanduser('~/sing-box'),
        '/tmp/sing-box',
    ]:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def parse_vless(uri: str) -> dict:
    """Parse vless://uuid@host:port?type=...&security=...&sni=...#name"""
    if not uri.startswith('vless://'):
        raise ValueError(f"Not a vless URI: {uri[:30]}")
    rest = uri[len('vless://'):]
    if '#' in rest:
        rest, fragment = rest.split('#', 1)
        fragment = urllib.parse.unquote(fragment)
    else:
        fragment = ''
    if '?' in rest:
        main, query = rest.split('?', 1)
        params = dict(urllib.parse.parse_qsl(query))
    else:
        main = rest
        params = {}
    if '@' in main:
        uuid, hostport = main.rsplit('@', 1)
    else:
        raise ValueError(f"vless URI missing userinfo: {uri[:50]}")
    if ':' in hostport:
        host, port = hostport.rsplit(':', 1)
        port = int(port)
    else:
        host = hostport
        port = 443
    return {
        'protocol': 'vless',
        'uuid': uuid,
        'host': host,
        'port': port,
        'params': params,
        'name': fragment,
    }


def parse_vmess(uri: str) -> dict:
    """Parse vmess://base64-json"""
    if not uri.startswith('vmess://'):
        raise ValueError(f"Not a vmess URI: {uri[:30]}")
    b64 = uri[len('vmess://'):]
    b64 += '=' * (-len(b64) % 4)
    try:
        json_str = base64.b64decode(b64).decode('utf-8')
        cfg = json.loads(json_str)
    except Exception as e:
        raise ValueError(f"Failed to decode vmess base64: {e}")
    return {
        'protocol': 'vmess',
        'uuid': cfg.get('id', ''),
        'host': cfg.get('add', ''),
        'port': int(cfg.get('port', 443)),
        'network': cfg.get('net', 'ws'),
        'path': cfg.get('path', '/'),
        'host_header': cfg.get('host', ''),
        'tls': cfg.get('tls', ''),
        'sni': cfg.get('sni', ''),
        'aid': cfg.get('aid', '0'),
        'name': cfg.get('ps', ''),
    }


def parse_trojan(uri: str) -> dict:
    """Parse trojan://password@host:port?...#name"""
    if not uri.startswith('trojan://'):
        raise ValueError(f"Not a trojan URI: {uri[:30]}")
    rest = uri[len('trojan://'):]
    if '#' in rest:
        rest, fragment = rest.split('#', 1)
        fragment = urllib.parse.unquote(fragment)
    else:
        fragment = ''
    if '?' in rest:
        main, query = rest.split('?', 1)
        params = dict(urllib.parse.parse_qsl(query))
    else:
        main = rest
        params = {}
    if '@' in main:
        password, hostport = main.rsplit('@', 1)
        password = urllib.parse.unquote(password)
    else:
        raise ValueError(f"trojan URI missing password: {uri[:50]}")
    if ':' in hostport:
        host, port = hostport.rsplit(':', 1)
        port = int(port)
    else:
        host = hostport
        port = 443
    return {
        'protocol': 'trojan',
        'password': password,
        'host': host,
        'port': port,
        'params': params,
        'name': fragment,
    }


def parse_ss(uri: str) -> dict:
    """Parse ss://method:password@host:port#name or ss://base64#name"""
    if not uri.startswith('ss://'):
        raise ValueError(f"Not a ss URI: {uri[:30]}")
    rest = uri[len('ss://'):]
    if '#' in rest:
        rest, fragment = rest.split('#', 1)
        fragment = urllib.parse.unquote(fragment)
    else:
        fragment = ''
    if '@' in rest:
        userinfo, hostport = rest.rsplit('@', 1)
        try:
            decoded = base64.urlsafe_b64decode(userinfo + '=' * (-len(userinfo) % 4)).decode('utf-8')
            if ':' in decoded:
                userinfo = decoded
        except Exception:
            pass
        if ':' not in userinfo:
            raise ValueError(f"ss URI missing method:password: {uri[:50]}")
        method, password = userinfo.split(':', 1)
    else:
        try:
            decoded = base64.urlsafe_b64decode(rest + '=' * (-len(rest) % 4)).decode('utf-8')
            userinfo, hostport = decoded.rsplit('@', 1)
            method, password = userinfo.split(':', 1)
        except Exception as e:
            raise ValueError(f"Failed to decode ss base64: {e}")
    if ':' in hostport:
        host, port = hostport.rsplit(':', 1)
        port = int(port)
    else:
        raise ValueError(f"ss URI missing port: {uri[:50]}")
    return {
        'protocol': 'shadowsocks',
        'method': method,
        'password': password,
        'host': host,
        'port': port,
        'name': fragment,
    }


def parse_hysteria2(uri: str) -> dict:
    """Parse hysteria2://password@host:port?...#name (also accepts hy2://)"""
    if uri.startswith('hysteria2://'):
        rest = uri[len('hysteria2://'):]
    elif uri.startswith('hy2://'):
        rest = uri[len('hy2://'):]
    else:
        raise ValueError(f"Not a hysteria2 URI: {uri[:30]}")
    if '#' in rest:
        rest, fragment = rest.split('#', 1)
        fragment = urllib.parse.unquote(fragment)
    else:
        fragment = ''
    if '?' in rest:
        main, query = rest.split('?', 1)
        params = dict(urllib.parse.parse_qsl(query))
    else:
        main = rest
        params = {}
    if '@' in main:
        password, hostport = main.rsplit('@', 1)
        password = urllib.parse.unquote(password)
    else:
        raise ValueError(f"hysteria2 URI missing password: {uri[:50]}")
    if ':' in hostport:
        host, port = hostport.rsplit(':', 1)
        port = int(port)
    else:
        host = hostport
        port = 443
    return {
        'protocol': 'hysteria2',
        'password': password,
        'host': host,
        'port': port,
        'params': params,
        'name': fragment,
    }


def parse_hysteria(uri: str) -> dict:
    """Parse hysteria://password@host:port?...#name (v1, legacy)"""
    if not uri.startswith('hysteria://'):
        raise ValueError(f"Not a hysteria URI: {uri[:30]}")
    rest = uri[len('hysteria://'):]
    if '#' in rest:
        rest, fragment = rest.split('#', 1)
        fragment = urllib.parse.unquote(fragment)
    else:
        fragment = ''
    if '?' in rest:
        main, query = rest.split('?', 1)
        params = dict(urllib.parse.parse_qsl(query))
    else:
        main = rest
        params = {}
    if '@' in main:
        password, hostport = main.rsplit('@', 1)
        password = urllib.parse.unquote(password)
    else:
        raise ValueError(f"hysteria URI missing password: {uri[:50]}")
    if ':' in hostport:
        host, port = hostport.rsplit(':', 1)
        port = int(port)
    else:
        host = hostport
        port = 443
    return {
        'protocol': 'hysteria',
        'auth_str': password,
        'host': host,
        'port': port,
        'params': params,
        'name': fragment,
    }


def parse_proxy_uri(uri: str) -> dict:
    """Parse any supported proxy URI. Returns dict with at least 'protocol', 'host', 'port'."""
    uri = uri.strip()
    if not uri:
        raise ValueError("Empty URI")
    if uri.startswith('vless://'):
        return parse_vless(uri)
    elif uri.startswith('vmess://'):
        return parse_vmess(uri)
    elif uri.startswith('trojan://'):
        return parse_trojan(uri)
    elif uri.startswith('ss://'):
        return parse_ss(uri)
    elif uri.startswith('hysteria2://') or uri.startswith('hy2://'):
        return parse_hysteria2(uri)
    elif uri.startswith('hysteria://'):
        return parse_hysteria(uri)
    else:
        raise ValueError(f"Unsupported protocol: {uri[:30]}")


def build_singbox_config(proxy: dict, listen_port: int = 10808) -> dict:
    """Build sing-box JSON config for given proxy.
    
    Always listens as socks5://127.0.0.1:listen_port + http://127.0.0.1:listen_port+1
    """
    inbounds = [
        {
            "type": "socks",
            "tag": "socks-in",
            "listen": "127.0.0.1",
            "listen_port": listen_port,
        },
        {
            "type": "http",
            "tag": "http-in",
            "listen": "127.0.0.1",
            "listen_port": listen_port + 1,
        }
    ]
    
    outbound = {
        "tag": "proxy-out",
    }
    
    if proxy['protocol'] == 'vless':
        params = proxy.get('params', {})
        network = params.get('type', 'tcp')
        security = params.get('security', 'none')
        
        tls = {}
        if security == 'tls':
            tls = {
                "enabled": True,
                "server_name": params.get('sni', proxy['host']),
                "insecure": params.get('allowInsecure', '0') == '1',
                "utls": {
                    "enabled": True,
                    "fingerprint": params.get('fp', 'chrome'),
                }
            }
        elif security == 'reality':
            tls = {
                "enabled": True,
                "server_name": params.get('sni', ''),
                "reality": {
                    "enabled": True,
                    "public_key": params.get('pbk', ''),
                    "short_id": params.get('sid', ''),
                },
                "utls": {
                    "enabled": True,
                    "fingerprint": params.get('fp', 'chrome'),
                }
            }
        
        transport = {}
        if network == 'ws':
            transport = {
                "type": "ws",
                "path": params.get('path', '/'),
            }
            if params.get('host'):
                transport["headers"] = {"Host": params['host']}
        elif network == 'grpc':
            transport = {
                "type": "grpc",
                "service_name": params.get('serviceName', ''),
            }
        
        outbound.update({
            "type": "vless",
            "server": proxy['host'],
            "server_port": proxy['port'],
            "uuid": proxy['uuid'],
            "flow": params.get('flow', '') or None,
            "tls": tls if tls else None,
            "transport": transport if transport else None,
        })
        # Remove None values
        outbound = {k: v for k, v in outbound.items() if v is not None}
    
    elif proxy['protocol'] == 'vmess':
        # 部分订阅只有 tls 标记但没有独立 sni 字段, 此时回退用 ws host 作 server_name
        vmess_sni = proxy.get('sni') or proxy.get('host_header')
        security = 'tls' if proxy.get('tls') in ('tls', 'auto') and vmess_sni else 'none'
        tls = {}
        if security == 'tls':
            tls = {
                "enabled": True,
                "server_name": vmess_sni or proxy['host'],
                "utls": {
                    "enabled": True,
                    "fingerprint": "chrome",
                }
            }
        
        transport = {}
        if proxy.get('network') == 'ws':
            transport = {
                "type": "ws",
                "path": proxy.get('path', '/'),
            }
            if proxy.get('host_header'):
                transport["headers"] = {"Host": proxy['host_header']}
        
        outbound.update({
            "type": "vmess",
            "server": proxy['host'],
            "server_port": proxy['port'],
            "uuid": proxy['uuid'],
            "alter_id": int(proxy.get('aid', '0')),
            "security": "auto",
            "tls": tls if tls else None,
            "transport": transport if transport else None,
        })
        outbound = {k: v for k, v in outbound.items() if v is not None}
    
    elif proxy['protocol'] == 'trojan':
        params = proxy.get('params', {})
        tls = {
            "enabled": True,
            "server_name": params.get('sni', proxy['host']),
            "insecure": params.get('allowInsecure', '0') == '1',
            "utls": {
                "enabled": True,
                "fingerprint": params.get('fp', 'chrome'),
            }
        }
        outbound.update({
            "type": "trojan",
            "server": proxy['host'],
            "server_port": proxy['port'],
            "password": proxy['password'],
            "tls": tls,
        })
    
    elif proxy['protocol'] == 'shadowsocks':
        outbound.update({
            "type": "shadowsocks",
            "server": proxy['host'],
            "server_port": proxy['port'],
            "method": proxy['method'],
            "password": proxy['password'],
        })
    
    elif proxy['protocol'] == 'hysteria2':
        params = proxy.get('params', {})
        tls = {
            "enabled": True,
            "server_name": params.get('sni', proxy['host']),
            "insecure": params.get('insecure', '0') == '1' or params.get('allowinsecure', '0') == '1',
        }
        outbound.update({
            "type": "hysteria2",
            "server": proxy['host'],
            "server_port": proxy['port'],
            "password": proxy['password'],
            "tls": tls,
        })
        # Optional: obfs
        if params.get('obfs'):
            outbound["obfs"] = {
                "type": params['obfs'],
                "password": params.get('obfs-password', ''),
            }
    
    elif proxy['protocol'] == 'hysteria':
        params = proxy.get('params', {})
        tls = {
            "enabled": True,
            "server_name": params.get('sni', proxy['host']),
            "insecure": params.get('insecure', '0') == '1',
        }
        outbound.update({
            "type": "hysteria",
            "server": proxy['host'],
            "server_port": proxy['port'],
            "auth_str": proxy['auth_str'],
            "tls": tls,
        })
        # Optional: up/down bandwidth (required by some servers)
        if params.get('upmbps'):
            outbound["up_mbps"] = int(params['upmbps'])
        if params.get('downmbps'):
            outbound["down_mbps"] = int(params['downmbps'])
    
    else:
        raise ValueError(f"Unsupported protocol: {proxy['protocol']}")
    
    # Direct outbound for fallback
    direct_outbound = {
        "tag": "direct-out",
        "type": "direct",
    }
    
    # Routing: send all traffic through proxy
    # In sing-box 1.11+ the routing config field was renamed to "route".
    # We use "route" (new name). The "routing" name is deprecated/removed.
    # If a route is needed (default route to proxy), use the new "default" field.
    # Actually for simplest "send everything to proxy-out" routing, we can omit
    # the route field entirely — sing-box's default behavior is to send to the
    # first outbound, which is our "proxy-out".
    # But to be explicit, use the new "route" key with "final" pointing to proxy.
    route = {
        "final": "proxy-out",
    }
    
    return {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": inbounds,
        "outbounds": [outbound, direct_outbound],
        "route": route,
    }


def test_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a TCP port is reachable."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """Wait until port is reachable. Returns True if reachable within timeout."""
    start = time.time()
    while time.time() - start < timeout:
        if test_port(host, port):
            return True
        time.sleep(0.5)
    return False


class ProxyManager:
    """Manage sing-box process and proxy rotation.
    
    Usage:
        pm = ProxyManager()
        pm.add_proxies_from_env("PROXY_URI")  # Load from env var
        pm.start(0)  # Start with first proxy
        # ... use socks5://127.0.0.1:10808 as browser proxy ...
        pm.stop()
        pm.start(1)  # Switch to second proxy
    """
    
    LISTEN_PORT = 10808
    
    def __init__(self):
        self.proxies: List[dict] = []
        self.current_idx: int = -1
        self.singbox_path: Optional[str] = find_singbox()
        self.singbox_proc: Optional[subprocess.Popen] = None
        self.config_path: Optional[str] = None
    
    def add_proxies_from_env(self, env_var: str = "PROXY_URI") -> int:
        """Load proxies from environment variable. Multiple proxies separated by newlines.
        Returns count of successfully parsed proxies."""
        env_val = os.environ.get(env_var, "").strip()
        if not env_val:
            return 0
        # Split by newlines (and also handle '||' separator some users use)
        lines = env_val.replace('||', '\n').split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                proxy = parse_proxy_uri(line)
                self.proxies.append(proxy)
                log(f"Loaded proxy [{len(self.proxies)-1}]: {proxy['protocol']} → "
                    f"{proxy['host']}:{proxy['port']} ({proxy.get('name', 'unnamed')})")
            except Exception as e:
                log(f"Failed to parse proxy URI '{line[:30]}...': {e}", "WARN")
        log(f"Total proxies loaded: {len(self.proxies)}")
        return len(self.proxies)
    
    def start(self, idx: int = 0) -> bool:
        """Start sing-box with proxy at given index. Stops any running sing-box first."""
        if not self.proxies:
            log("No proxies available", "ERROR")
            return False
        if idx >= len(self.proxies):
            log(f"Proxy index {idx} out of range (have {len(self.proxies)})", "ERROR")
            return False
        if not self.singbox_path:
            log("sing-box binary not found", "ERROR")
            return False
        
        self.stop()
        
        proxy = self.proxies[idx]
        self.current_idx = idx
        log(f"Starting sing-box with proxy [{idx}]: {proxy['protocol']} → "
            f"{proxy['host']}:{proxy['port']}")
        
        config = build_singbox_config(proxy, self.LISTEN_PORT)
        self.config_path = f"/tmp/singbox_config_{os.getpid()}.json"
        try:
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            log(f"Failed to write sing-box config: {e}", "ERROR")
            return False
        
        # stderr 重定向到文件而非 PIPE:
        #  - PIPE 的 read() 在 sing-box 进程存活时会一直阻塞等待 EOF, 导致脚本卡死
        #  - PIPE 缓冲满还会反过来阻塞 sing-box 写日志
        log_file = f"/tmp/singbox_stderr_{os.getpid()}.log"
        try:
            stderr_fd = open(log_file, 'w')
        except Exception as e:
            log(f"Failed to open stderr log file: {e}", "ERROR")
            return False
        try:
            self.singbox_proc = subprocess.Popen(
                [self.singbox_path, 'run', '-c', self.config_path],
                stdout=subprocess.DEVNULL,
                stderr=stderr_fd,
            )
        except Exception as e:
            log(f"Failed to start sing-box: {e}", "ERROR")
            stderr_fd.close()
            return False

        # Wait for sing-box to be ready (socks5 port should be reachable)
        if not wait_for_port('127.0.0.1', self.LISTEN_PORT, timeout=10):
            log("sing-box failed to start listening on socks5 port", "ERROR")
            # 从日志文件读 stderr 诊断(非阻塞)
            try:
                with open(log_file, 'r', errors='ignore') as f:
                    stderr = f.read()
                if stderr.strip():
                    log(f"sing-box stderr: {stderr[:1000]}", "ERROR")
            except Exception:
                pass
            stderr_fd.close()
            self.stop()
            return False
        stderr_fd.close()
        
        log(f"✓ sing-box listening on socks5://127.0.0.1:{self.LISTEN_PORT}")
        return True
    
    def stop(self):
        """Stop sing-box process if running."""
        if self.singbox_proc:
            try:
                self.singbox_proc.terminate()
                try:
                    self.singbox_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.singbox_proc.kill()
                    self.singbox_proc.wait(timeout=2)
            except Exception as e:
                log(f"Error stopping sing-box: {e}", "WARN")
            finally:
                self.singbox_proc = None
        # Clean up config file
        if self.config_path and os.path.exists(self.config_path):
            try:
                os.remove(self.config_path)
            except Exception:
                pass
            self.config_path = None
    
    def rotate(self) -> bool:
        """Switch to next proxy in list. Wraps around. Returns True if successful."""
        if not self.proxies:
            return False
        next_idx = (self.current_idx + 1) % len(self.proxies)
        return self.start(next_idx)
    
    def current_proxy_info(self) -> Optional[dict]:
        """Return info about currently active proxy."""
        if self.current_idx < 0 or self.current_idx >= len(self.proxies):
            return None
        return self.proxies[self.current_idx]
    
    def get_socks5_url(self) -> str:
        """Return the socks5:// URL to use in browser."""
        return f"socks5://127.0.0.1:{self.LISTEN_PORT}"


# Self-test
if __name__ == "__main__":
    test_uris = [
        "vless://abc-uuid@example.com:443?type=ws&security=tls&sni=example.com&path=%2Fpath#Test",
        "trojan://password123@host.com:443?sni=host.com#MyTrojan",
        "ss://aes-256-gcm:password@host.com:8388#Shadowsocks",
        "hysteria2://b5c445d1-8e59-465f@host.com:443?sni=host.com&insecure=1#MyHysteria2",
        "hy2://pass@host.com:8443?sni=host.com#Alias",
    ]
    for uri in test_uris:
        try:
            p = parse_proxy_uri(uri)
            print(f"✓ {p['protocol']:12} → {p['host']}:{p['port']} ({p.get('name', '')})")
            cfg = build_singbox_config(p)
            out = cfg['outbounds'][0]
            print(f"  Outbound: type={out.get('type')}, server={out.get('server')}, port={out.get('server_port')}")
        except Exception as e:
            print(f"✗ {uri[:40]}: {e}")
