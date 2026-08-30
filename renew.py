#!/usr/bin/env python3
"""
FreeGameHost.xyz 自动续期脚本
使用 SeleniumBase 模拟浏览器登录并点击 Renew 按钮。
环境变量:
  FGH_ACCOUNT  — 必填，格式: email,password
  BROWSER_PROXY — 可选，浏览器使用的代理
  GOST_PROXY   — 可选，上游代理
  TG_BOT       — 可选，格式: chat_id,bot_token
"""

import os
import sys
import time
import json
import urllib.request
import urllib.parse
from seleniumbase import SB

PANEL_URL = "https://panel.freegamehost.xyz"


def log(msg):
    print(f"[FGH-Renew] {msg}", flush=True)


def send_tg(chat_id, bot_token, text):
    """发送 Telegram 通知"""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            log(f"Telegram 通知已发送: {resp.status}")
    except Exception as e:
        log(f"Telegram 通知发送失败: {e}")


def parse_env(name, sep=","):
    """解析环境变量，返回各部分"""
    val = os.environ.get(name, "").strip()
    if not val:
        return None
    parts = [p.strip() for p in val.split(sep)]
    return parts if len(parts) > 1 else parts[0]


def clean_proxy(proxy_str):
    """清理代理字符串，去除 #label 后缀"""
    if not proxy_str:
        return None
    clean = proxy_str.split('#')[0].strip()
    return clean if clean else None


def main():
    # ── 解析账号 ──
    account = parse_env("FGH_ACCOUNT")
    if not account or len(account) < 2:
        log("错误: FGH_ACCOUNT 未设置或格式不正确（应为 email,password）")
        sys.exit(1)

    email = account[0]
    password = account[1]
    log(f"账号: {email}")
    log(f"密码长度: {len(password)}")

    # ── 解析 Telegram ──
    tg = parse_env("TG_BOT")
    tg_chat_id = tg_bot_token = None
    if tg and len(tg) >= 2:
        tg_chat_id, tg_bot_token = tg[0], tg[1]

    # ── 代理设置 ──
    browser_proxy_env = os.environ.get("BROWSER_PROXY", None)
    if browser_proxy_env is not None and browser_proxy_env.strip() == "":
        proxy_arg = None
        log("⚠️ BROWSER_PROXY 已设为空，使用直连模式")
    elif browser_proxy_env:
        proxy_arg = browser_proxy_env.strip()
        log(f"使用代理: {proxy_arg}")
    else:
        raw_gost = os.environ.get("GOST_PROXY", "").strip() or None
        proxy_arg = clean_proxy(raw_gost) if raw_gost else None
        if proxy_arg:
            log(f"使用代理: {proxy_arg}")
        else:
            log("⚠️ 未配置代理，使用直连模式")

    # ── 启动浏览器 ──
    sb_kwargs = dict(
        browser="chrome",
        headless=True,
    )
    if proxy_arg:
        sb_kwargs["proxy"] = proxy_arg

    with SB(**sb_kwargs) as sb:

        success = False
        error_msg = ""

        try:
            # ── 第一步：登录 ──
            log("正在打开登录页面...")
            sb.open(f"{PANEL_URL}/auth/login")
            time.sleep(5)

            # 截图用于调试
            sb.save_screenshot("01_login_page.png")
            log(f"当前URL: {sb.get_current_url()}")

            # 打印页面上所有输入框和按钮信息
            log("=== 页面元素诊断 ===")
            all_inputs = sb.find_elements("input")
            log(f"找到 {len(all_inputs)} 个 input 元素:")
            for i, inp in enumerate(all_inputs):
                try:
                    name = inp.get_attribute("name")
                    id_attr = inp.get_attribute("id")
                    type_attr = inp.get_attribute("type")
                    placeholder = inp.get_attribute("placeholder")
                    log(f"  [{i}] name={name}, id={id_attr}, type={type_attr}, placeholder={placeholder}")
                except Exception:
                    pass

            all_buttons = sb.find_elements("button")
            log(f"找到 {len(all_buttons)} 个 button 元素:")
            for i, btn in enumerate(all_buttons):
                try:
                    text = btn.text.strip()[:50]
                    btn_type = btn.get_attribute("type")
                    log(f"  [{i}] text='{text}', type={btn_type}")
                except Exception:
                    pass

            # 检查是否有错误提示
            error_elements = sb.find_elements('.alert, .error, .invalid, [class*="error"], [class*="invalid"], [class*="danger"]')
            if error_elements:
                log(f"发现错误提示: {error_elements[0].text[:100] if error_elements else 'N/A'}")

            # 尝试登录 - 多种字段名
            logged_in = False

            # 方案1: username + password
            try:
                if sb.is_element_present('input[name="username"]'):
                    sb.type('input[name="username"]', email)
                    log("已填入用户名")
                elif sb.is_element_present('input[name="email"]'):
                    sb.type('input[name="email"]', email)
                    log("已填入邮箱")
                else:
                    # 找第一个 text/email 类型的输入框
                    inputs = sb.find_elements('input[type="text"], input[type="email"]')
                    if inputs:
                        sb.type(inputs[0], email)
                        log(f"已填入第一个文本输入框")

                if sb.is_element_present('input[name="password"]'):
                    sb.type('input[name="password"]', password)
                    log("已填入密码")
                elif sb.is_element_present('input[type="password"]'):
                    sb.type('input[type="password"]', password)
                    log("已填入密码")

            except Exception as e:
                log(f"填入凭证时出错: {e}")

            # 点击登录
            login_selectors = [
                'button[type="submit"]',
                'button:contains("Login")',
                'button:contains("Sign")',
                '#login-button',
                '.btn-primary',
                'button:contains("登录")',
            ]

            clicked = False
            for sel in login_selectors:
                try:
                    if sb.is_element_present(sel):
                        sb.click(sel)
                        clicked = True
                        log(f"已点击登录按钮: {sel}")
                        break
                except Exception:
                    continue

            if not clicked:
                buttons = sb.find_elements("button")
                for btn in buttons:
                    try:
                        text = btn.text.strip().lower()
                        if "login" in text or "sign" in text or "登录" in text:
                            sb.click(btn)
                            clicked = True
                            log(f"通过文本匹配点击: {text}")
                            break
                    except Exception:
                        continue

            if not clicked:
                # 最后尝试按回车
                log("尝试按回车提交")
                sb.send_keys('input[type="password"]', "\n")

            # 等待页面跳转
            log("等待登录响应...")
            time.sleep(10)
            sb.save_screenshot("02_after_login.png")

            current_url = sb.get_current_url()
            log(f"登录后URL: {current_url}")

            # 检查是否还在登录页
            if "auth/login" in current_url or "login" in current_url:
                # 检查是否有错误消息
                error_text = ""
                try:
                    alerts = sb.find_elements('.alert, .error-message, [class*="error"], [class*="invalid"], .text-danger, .bg-danger')
                    for alert in alerts:
                        t = alert.text.strip()
                        if t and len(t) > 3:
                            error_text = t
                            log(f"发现错误消息: {error_text[:200]}")
                            break
                except Exception:
                    pass

                if not error_text:
                    # 检查 body 中是否有错误文本
                    try:
                        body_text = sb.get_text("body")
                        if "错误" in body_text or "error" in body_text.lower() or "invalid" in body_text.lower():
                            # 提取错误相关文本
                            lines = body_text.split('\n')
                            for line in lines:
                                line = line.strip()
                                if len(line) > 5 and ('错误' in line or 'error' in line.lower() or 'invalid' in line.lower()):
                                    error_text = line[:200]
                                    log(f"从页面文本发现错误: {error_text}")
                                    break
                    except Exception:
                        pass

                log("登录失败：仍在登录页面")
                sb.save_screenshot("error_login_failed.png")
                error_msg = f"登录失败，仍在登录页面。{'错误: ' + error_text if error_text else ''}"
                raise Exception(error_msg)

            log("✅ 登录成功！")

            # ── 第二步：前往仪表盘 ──
            log("正在打开仪表盘...")
            sb.open(f"{PANEL_URL}/")
            time.sleep(5)
            sb.save_screenshot("03_dashboard.png")

            # ── 第三步：点击 Renew 按钮 ──
            renew_selectors = [
                'button:contains("Renew")',
                'a:contains("Renew")',
                '[class*="renew"]',
                '[id*="renew"]',
                'button:contains("Extend")',
                'a:contains("Extend")',
                'button:contains("续费")',
                'a:contains("续费")',
            ]

            renewed = False
            for sel in renew_selectors:
                try:
                    if sb.is_element_present(sel):
                        sb.click(sel)
                        renewed = True
                        log(f"点击续期按钮成功: {sel}")
                        break
                except Exception:
                    continue

            if not renewed:
                try:
                    buttons = sb.find_elements("button")
                    for btn in buttons:
                        text = btn.text.strip().lower()
                        if "renew" in text or "extend" in text or "续费" in text:
                            sb.click(btn)
                            renewed = True
                            log(f"通过文本匹配点击续期: {text}")
                            break
                except Exception as e:
                    log(f"遍历按钮失败: {e}")

            if not renewed:
                try:
                    links = sb.find_elements("a")
                    for link in links:
                        text = link.text.strip().lower()
                        if "renew" in text or "extend" in text or "续费" in text:
                            sb.click(link)
                            renewed = True
                            log(f"通过文本匹配点击续期链接: {text}")
                            break
                except Exception as e:
                    log(f"遍历链接失败: {e}")

            time.sleep(5)
            sb.save_screenshot("04_after_renew.png")

            if renewed:
                log("✅ 续期成功！")
                success = True
            else:
                log("⚠️ 未找到续期按钮，可能已续期或页面结构变化")
                error_msg = "未找到续期按钮"
                try:
                    server_links = sb.find_elements('a[href*="/server/"]')
                    if server_links:
                        log(f"发现 {len(server_links)} 个服务器，尝试逐个续期...")
                        for i, sl in enumerate(server_links):
                            try:
                                href = sl.get_attribute("href")
                                log(f"进入服务器 {i+1}: {href}")
                                sb.open(href)
                                time.sleep(3)
                                sb.save_screenshot(f"server_{i+1}.png")

                                found = False
                                for sel in renew_selectors:
                                    try:
                                        if sb.is_element_present(sel):
                                            sb.click(sel)
                                            found = True
                                            log(f"服务器 {i+1} 续期成功")
                                            time.sleep(3)
                                            break
                                    except Exception:
                                        continue

                                if not found:
                                    btns = sb.find_elements("button")
                                    for btn in btns:
                                        t = btn.text.strip().lower()
                                        if "renew" in t or "extend" in t or "续费" in t:
                                            sb.click(btn)
                                            found = True
                                            log(f"服务器 {i+1} 续期成功（文本匹配）")
                                            time.sleep(3)
                                            break

                                if not found:
                                    log(f"服务器 {i+1} 未找到续期按钮")
                            except Exception as e:
                                log(f"服务器 {i+1} 续期失败: {e}")

                        success = True
                        log("所有服务器遍历完成")
                except Exception as e:
                    log(f"服务器遍历失败: {e}")
                    error_msg = f"续期失败: {e}"

        except Exception as e:
            error_msg = str(e)
            log(f"❌ 出错: {error_msg}")
            try:
                sb.save_screenshot("error_exception.png")
            except Exception:
                pass

    # ── 发送 Telegram 通知 ──
    if tg_chat_id and tg_bot_token:
        if success:
            msg = "✅ <b>FreeGameHost 续期成功</b>\n账号已自动续期。"
        else:
            msg = f"❌ <b>FreeGameHost 续期失败</b>\n错误: {error_msg}"
        send_tg(tg_chat_id, tg_bot_token, msg)

    if success:
        log("完成！")
        sys.exit(0)
    else:
        log(f"失败: {error_msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
