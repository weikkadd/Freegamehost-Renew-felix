#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v2rayN proxy helper: parse proxy URI and manage xray-core process.

Supports:
  - vless://uuid@host:port?...#name
  - vmess://base64-json
  - trojan://password@host:port?...#name
  - ss://method:password@host:port#name  (or base64(method:password)@host:port)

Usage:
  from proxy_helper import ProxyManager
  pm = ProxyManager()
  pm.start("vless://...")  # Start xray with this proxy
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
from typing import Optional, List, Tuple


def log(msg, level="INFO"):
    prefix = {"INFO": "[FGH-Renew]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(level, "[FGH-Renew]")
    print(f"{prefix} {msg}", flush=True)


# Find xray binary
def find_xray() -> str:
    """Find xray binary. Tries common paths."""
    for path in [
        shutil.which('xray'),
        '/usr/local/bin/xray',
        '/usr/bin/xray',
        os.path.expanduser('~/xray'),
        '/tmp/xray',
    ]:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def parse_vless(uri: str) -> dict:
    """Parse vless://uuid@host:port?type=...&security=...&sni=...#name"""
    if not uri.startswith('vless://'):
        raise ValueError(f"Not a vless URI: {uri[:30]}")
    # Strip vless://
    rest = uri[len('vless://'):]
    # Split fragment
    if '#' in rest:
        rest, fragment = rest.split('#', 1)
        fragment = urllib.parse.unquote(fragment)
    else:
        fragment = ''
    # Split query
    if '?' in rest:
        main, query = rest.split('?', 1)
        params = dict(urllib.parse.parse_qsl(query))
    else:
        main = rest
        params = {}
    # Split userinfo@hostport
    if '@' in main:
        uuid, hostport = main.rsplit('@', 1)
    else:
        raise ValueError(f"vless URI missing userinfo: {uri[:50]}")
    # Split host:port
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
    # Fix padding
    b64 += '=' * (-len(b64) % 4)
    try:
        json_str = base64.b64decode(b64).decode('utf-8')
        cfg = json.loads(json_str)
    except Exception as e:
        raise ValueError(f"Failed to decode vmess base64: {e}")
    # vmess JSON: {"v":"2","ps":"name","add":"host","port":"443","id":"uuid","aid":"0","net":"ws","type":"none","host":"","path":"/","tls":"tls","sni":""}
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
    # Two formats:
    # 1. ss://base64#name  (base64 = method:password@host:port)
    # 2. ss://method:password@host:port#name
    if '@' in rest:
        # Format 2
        userinfo, hostport = rest.rsplit('@', 1)
        # Try to decode as base64 first (SIP002 format)
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
        # Format 1: pure base64
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
    else:
        raise ValueError(f"Unsupported protocol: {uri[:30]}")


def build_xray_config(proxy: dict, listen_port: int = 10808) -> dict:
    """Build xray-core JSON config for given proxy.
    
    Always listens as socks5://127.0.0.1:listen_port
    Also exposes http://127.0.0.1:listen_port+1 for HTTP proxy.
    """
    inbounds = [
        {
            "tag": "socks-in",
            "port": listen_port,
            "listen": "127.0.0.1",
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": True}
        },
        {
            "tag": "http-in",
            "port": listen_port + 1,
            "listen": "127.0.0.1",
            "protocol": "http",
            "settings": {}
        }
    ]
    
    outbounds = []
    
    if proxy['protocol'] == 'vless':
        params = proxy.get('params', {})
        network = params.get('type', 'tcp')
        security = params.get('security', 'none')
        
        stream_settings = {
            "network": network,
            "security": security,
        }
        
        if security == 'tls':
            stream_settings["tlsSettings"] = {
                "serverName": params.get('sni', proxy['host']),
                "allowInsecure": params.get('allowInsecure', '0') == '1',
                "fingerprint": params.get('fp', 'chrome'),
            }
        elif security == 'reality':
            stream_settings["realitySettings"] = {
                "serverName": params.get('sni', ''),
                "fingerprint": params.get('fp', 'chrome'),
                "publicKey": params.get('pbk', ''),
                "shortId": params.get('sid', ''),
                "spiderX": params.get('spx', ''),
            }
        
        if network == 'ws':
            stream_settings["wsSettings"] = {
                "path": params.get('path', '/'),
                "headers": {"Host": params.get('host', '')} if params.get('host') else {},
            }
        elif network == 'grpc':
            stream_settings["grpcSettings"] = {
                "serviceName": params.get('serviceName', ''),
            }
        
        outbounds.append({
            "tag": "proxy",
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": proxy['host'],
                    "port": proxy['port'],
                    "users": [{
                        "id": proxy['uuid'],
                        "encryption": "none",
                        "flow": params.get('flow', ''),
                    }]
                }]
            },
            "streamSettings": stream_settings,
        })
    
    elif proxy['protocol'] == 'vmess':
        security = 'tls' if proxy.get('tls') in ('tls', '') and proxy.get('sni') else 'none'
        stream_settings = {
            "network": proxy.get('network', 'ws'),
            "security": security,
        }
        if security == 'tls':
            stream_settings["tlsSettings"] = {
                "serverName": proxy.get('sni', proxy['host']),
                "allowInsecure": False,
            }
        if proxy.get('network') == 'ws':
            stream_settings["wsSettings"] = {
                "path": proxy.get('path', '/'),
                "headers": {"Host": proxy.get('host_header', '')} if proxy.get('host_header') else {},
            }
        
        outbounds.append({
            "tag": "proxy",
            "protocol": "vmess",
            "settings": {
                "vnext": [{
                    "address": proxy['host'],
                    "port": proxy['port'],
                    "users": [{
                        "id": proxy['uuid'],
                        "alterId": int(proxy.get('aid', '0')),
                        "security": "auto",
                    }]
                }]
            },
            "streamSettings": stream_settings,
        })
    
    elif proxy['protocol'] == 'trojan':
        params = proxy.get('params', {})
        security = 'tls'
        stream_settings = {
            "network": params.get('type', 'tcp'),
            "security": security,
            "tlsSettings": {
                "serverName": params.get('sni', proxy['host']),
                "allowInsecure": params.get('allowInsecure', '0') == '1',
                "fingerprint": params.get('fp', 'chrome'),
            },
        }
        if stream_settings["network"] == 'ws':
            stream_settings["wsSettings"] = {
                "path": params.get('path', '/'),
                "headers": {"Host": params.get('host', '')} if params.get('host') else {},
            }
        
        outbounds.append({
            "tag": "proxy",
            "protocol": "trojan",
            "settings": {
                "servers": [{
                    "address": proxy['host'],
                    "port": proxy['port'],
                    "password": proxy['password'],
                }]
            },
            "streamSettings": stream_settings,
        })
    
    elif proxy['protocol'] == 'shadowsocks':
        outbounds.append({
            "tag": "proxy",
            "protocol": "shadowsocks",
            "settings": {
                "servers": [{
                    "address": proxy['host'],
                    "port": proxy['port'],
                    "method": proxy['method'],
                    "password": proxy['password'],
                }]
            },
        })
    
    else:
        raise ValueError(f"Unsupported protocol: {proxy['protocol']}")
    
    # Direct outbound for non-proxy traffic (DNS, etc.)
    outbounds.append({
        "tag": "direct",
        "protocol": "freedom",
        "settings": {}
    })
    
    # Routing: send all traffic through proxy by default
    routing = {
        "rules": [
            {"type": "field", "outboundTag": "proxy", "port": "0-65535"},
        ]
    }
    
    return {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "routing": routing,
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
    """Manage xray-core process and proxy rotation.
    
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
        self.xray_path: Optional[str] = find_xray()
        self.xray_proc: Optional[subprocess.Popen] = None
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
        """Start xray with proxy at given index. Stops any running xray first."""
        if not self.proxies:
            log("No proxies available", "ERROR")
            return False
        if idx >= len(self.proxies):
            log(f"Proxy index {idx} out of range (have {len(self.proxies)})", "ERROR")
            return False
        if not self.xray_path:
            log("xray binary not found", "ERROR")
            return False
        
        self.stop()
        
        proxy = self.proxies[idx]
        self.current_idx = idx
        log(f"Starting xray with proxy [{idx}]: {proxy['protocol']} → "
            f"{proxy['host']}:{proxy['port']}")
        
        config = build_xray_config(proxy, self.LISTEN_PORT)
        self.config_path = f"/tmp/xray_config_{os.getpid()}.json"
        try:
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            log(f"Failed to write xray config: {e}", "ERROR")
            return False
        
        # Start xray as subprocess
        try:
            self.xray_proc = subprocess.Popen(
                [self.xray_path, 'run', '-c', self.config_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                # Don't capture stdout to avoid blocking
            )
        except Exception as e:
            log(f"Failed to start xray: {e}", "ERROR")
            return False
        
        # Wait for xray to be ready (socks5 port should be reachable)
        if not wait_for_port('127.0.0.1', self.LISTEN_PORT, timeout=10):
            log("xray failed to start listening on socks5 port", "ERROR")
            # Read stderr for diagnostics
            try:
                stderr = self.xray_proc.stderr.read().decode('utf-8', errors='ignore') if self.xray_proc.stderr else ''
                if stderr:
                    log(f"xray stderr: {stderr[:500]}", "ERROR")
            except Exception:
                pass
            self.stop()
            return False
        
        log(f"✓ xray listening on socks5://127.0.0.1:{self.LISTEN_PORT}")
        return True
    
    def stop(self):
        """Stop xray process if running."""
        if self.xray_proc:
            try:
                self.xray_proc.terminate()
                try:
                    self.xray_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.xray_proc.kill()
                    self.xray_proc.wait(timeout=2)
            except Exception as e:
                log(f"Error stopping xray: {e}", "WARN")
            finally:
                self.xray_proc = None
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
    # Test parsing different formats
    test_uris = [
        "vless://abc-uuid@example.com:443?type=ws&security=tls&sni=example.com&path=%2Fpath#Test",
        "trojan://password123@host.com:443?sni=host.com#MyTrojan",
        "ss://aes-256-gcm:password@host.com:8388#Shadowsocks",
    ]
    for uri in test_uris:
        try:
            p = parse_proxy_uri(uri)
            print(f"✓ {p['protocol']}: {p['host']}:{p['port']} ({p.get('name', '')})")
            cfg = build_xray_config(p)
            print(f"  Config generated with {len(cfg['outbounds'])} outbounds")
        except Exception as e:
            print(f"✗ {uri[:40]}: {e}")
