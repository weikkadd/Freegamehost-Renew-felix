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


def fill_with_js(sb, selector, value):
    """使用 JavaScript 直接设置输入框值，绕过可能的 UI 阻塞"""
    sb.execute(f"""
        const el = document.querySelector('{selector}');
        if (el) {{
            el.value = '{value.replace("'", "\\'")}';
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
    """)


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
    else:
        log(f"⚠️ TG_BOT 配置不完整: {tg}")

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
            time.sleep(8)

            # 截图用于调试
            sb.save_screenshot("01_login_page.png")
            log(f"当前URL: {sb.get_current_url()}")

            # 检查是否有 CSRF token
            csrf_value = sb.evaluate("""
                const csrf = document.querySelector('input[name="csrf_token"]');
                return csrf ? csrf.value : null;
            """)
            if csrf_value:
                log(f"发现 CSRF token: {csrf_value[:20]}...")

            # ── 方法1: 使用 JavaScript 直接填充并提交 ──
            log("\n=== 尝试 JS 方式登录 ===")
            js_success = False

            try:
                # 使用 JS 填充表单
                sb.evaluate("""
                    const userEl = document.querySelector('input[name="username"]');
                    const passEl = document.querySelector('input[name="password"]');
                    if (userEl) { userEl.value = arguments[0]; userEl.dispatchEvent(new Event('input', {bubbles:true})); }
                    if (passEl) { passEl.value = arguments[1]; passEl.dispatchEvent(new Event('input', {bubbles:true})); }
                """, email, password)
                log("✅ JS 已填入凭据")

                # 等待一下让 JS 框架（如 React/Vue）更新状态
                time.sleep(1)

                # 点击登录按钮
                click_result = sb.evaluate("""
                    const btn = document.querySelector('button[type="submit"]');
                    if (btn) { btn.click(); return 'clicked'; }
                    return 'not found';
                """)
                log(f"点击结果: {click_result}")

                # 等待登录响应
                log("等待登录响应...")
                time.sleep(10)

                current_url = sb.get_current_url()
                log(f"登录后URL: {current_url}")

                if "login" not in current_url.lower():
                    log("✅ JS 登录成功！")
                    js_success = True
                    sb.save_screenshot("02_logged_in_js.png")
                else:
                    # 检查是否有错误
                    error_text = sb.evaluate("""
                        const bodies = document.querySelectorAll('.alert, .error, [class*="error"], [class*="invalid"], .text-danger');
                        for (const b of bodies) { if (b.textContent.trim()) return b.textContent.trim(); }
                        return '';
                    """)
                    if error_text:
                        log(f"❌ 服务器返回错误: {error_text[:200]}")
                    else:
                        log("⚠️ 登录无响应，仍在登录页")
                    sb.save_screenshot("03_login_attempt_failed.png")

            except Exception as e:
                log(f"JS 登录方式失败: {e}")

            # ── 方法2: 如果 JS 方式失败，尝试标准 Selenium 方式 ──
            if not js_success:
                log("\n=== 尝试 Selenium 方式登录 ===")
                sb.open(f"{PANEL_URL}/auth/login")
                time.sleep(5)

                try:
                    # 使用 type() 方法填充
                    sb.type('input[name="username"]', email)
                    sb.type('input[name="password"]', password)
                    log("已填入凭据")

                    # 点击登录
                    sb.click('button[type="submit"]')
                    log("已点击 LOGIN 按钮")

                    # 等待
                    time.sleep(10)
                    current_url = sb.get_current_url()
                    log(f"登录后URL: {current_url}")

                    if "login" not in current_url.lower():
                        log("✅ Selenium 登录成功！")
                        js_success = True
                        sb.save_screenshot("02_logged_in_sb.png")
                    else:
                        sb.save_screenshot("03_login_attempt_failed2.png")
                except Exception as e:
                    log(f"Selenium 登录方式也失败: {e}")
                    sb.save_screenshot("error_sb_login_fail.png")

            # ── 验证登录结果 ──
            if not js_success:
                final_url = sb.get_current_url()
                log(f"\n最终URL: {final_url}")

                # 再次检查错误
                try:
                    error_text = sb.get_text(".alert, .error, [class*='error'], [class*='invalid']")
                    if error_text.strip():
                        log(f"页面错误: {error_text.strip()[:200]}")
                except Exception:
                    pass

                error_msg = "登录失败：请检查账号密码是否正确，或站点是否需要额外验证"
                sb.save_screenshot("error_login_final.png")
                raise Exception(error_msg)

            log("✅ 登录成功！")

            # ── 第二步：前往仪表盘 ──
            log("正在打开仪表盘...")
            sb.open(f"{PANEL_URL}/")
            time.sleep(5)
            sb.save_screenshot("04_dashboard.png")

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
            sb.save_screenshot("05_after_renew.png")

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
