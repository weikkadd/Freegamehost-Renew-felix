#!/usr/bin/env python3
"""
FreeGameHost.xyz 自动续期脚本
使用 DrissionPage + reCAPTCHA 音频识别 自动解决验证�?环境变量:
  FGH_ACCOUNT  �?必填，格�? email,password
  TG_BOT_TOKEN �?Telegram Bot Token
  TG_CHAT_ID   �?Telegram Chat ID
"""

import os
import sys
import time
import random
import html
import requests
import tempfile
from datetime import datetime
from xvfbwrapper import Xvfb
from DrissionPage import ChromiumPage, ChromiumOptions

try:
    import speech_recognition as sr
    from pydub import AudioSegment
except ImportError:
    print("[WARN] speech_recognition/pydub 未安装，音频验证码功能不可用")
    sr = None

PANEL_URL = "https://panel.freegamehost.xyz"
MAX_CAPTCHA_ATTEMPTS = 3
SCREENSHOT_DIR = "output/screenshots"


def log(msg, level="INFO"):
    prefix = {"INFO": "[FGH-Renew]", "WARN": "[WARN]", "ERROR": "[ERROR]"}.get(level, "[FGH-Renew]")
    print(f"{prefix} {msg}", flush=True)


def send_tg(token, chat_id, text):
    """发�?Telegram 通知"""
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        r = requests.post(url, data=data, timeout=30)
        r.raise_for_status()
        log("Telegram 通知已发�?)
    except Exception as e:
        log(f"Telegram 通知失败: {e}", "ERROR")


def mask_url(url):
    """隐藏 UUID，只保留域名"""
    import re
    return re.sub(r'(\?i=)([^&]{1})([^&]*)', r'\1\2***', url)


def get_server_name(page):
    try:
        ele = page.ele('#serverName', timeout=2)
        if ele:
            return ele.text.strip()
    except:
        pass
    try:
        ele = page.ele('text:/服务器|server/i', timeout=2)
        if ele:
            return ele.text.strip()
    except:
        pass
    return "未知"


def get_expire_time(page):
    selectors = ['#expireDate', 'text:Expires in:', 'text:Deletes on:']
    for sel in selectors:
        try:
            ele = page.ele(sel, timeout=2)
            if ele:
                return ele.text.strip()
        except:
            continue
    return "未知"


# ── reCAPTCHA 相关函数 ──

def find_recaptcha_frame(page, kind):
    try:
        for frame in page.get_frames():
            url = frame.url or ""
            if "recaptcha" in url and kind in url:
                return frame
    except:
        pass
    return None


def is_recaptcha_solved(page):
    try:
        for frame in page.get_frames():
            try:
                token = frame.run_js("return document.querySelector(\"textarea[name='g-recaptcha-response']\")?.value")
                if token and len(token) > 30:
                    return True
            except:
                pass
    except:
        pass
    anchor = find_recaptcha_frame(page, "anchor")
    if anchor:
        try:
            checked = anchor.run_js("return document.querySelector('#recaptcha-anchor')?.getAttribute('aria-checked') === 'true'")
            if checked:
                return True
        except:
            pass
    return False


def is_blocked(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        return bool(bframe.run_js("""
            const h = document.querySelector('.rc-doscaptcha-header-text');
            if (h && h.textContent.toLowerCase().includes('try again later')) return true;
            const e = document.querySelector('.rc-audiochallenge-error-message');
            if (e && e.offsetParent !== null) return true;
            return false;
        """))
    except:
        return False


def click_recaptcha_checkbox(page):
    anchor = find_recaptcha_frame(page, "anchor")
    if not anchor:
        for _ in range(60):
            anchor = find_recaptcha_frame(page, "anchor")
            if anchor:
                break
            time.sleep(1)
    if not anchor:
        raise RuntimeError("未找�?reCAPTCHA anchor frame")
    checkbox = anchor.ele('#recaptcha-anchor', timeout=3)
    if not checkbox:
        raise RuntimeError("未找�?reCAPTCHA 复选框")
    page.actions.move_to(checkbox, duration=random.uniform(0.4, 1.0))
    time.sleep(random.uniform(0.2, 0.5))
    try:
        checkbox.click()
    except:
        checkbox.click(by_js=True)
    time.sleep(3)
    if is_blocked(page):
        raise Exception("IP �?Google reCAPTCHA 封锁")


def switch_to_audio(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=1)
        if input_box and input_box.states.is_displayed:
            return True
    except:
        pass
    for _ in range(3):
        try:
            audio_btn = bframe.ele('#recaptcha-audio-button', timeout=3)
            if audio_btn:
                try:
                    audio_btn.click()
                except:
                    audio_btn.click(by_js=True)
                time.sleep(3)
                if is_blocked(page):
                    raise Exception("IP 被封锁（点击音频按钮后）")
                input_box = bframe.ele('#audio-response', timeout=1)
                if input_box and input_box.states.is_displayed:
                    return True
        except Exception as e:
            if "封锁" in str(e) or "blocked" in str(e).lower():
                raise
            pass
        try:
            bframe.run_js("document.querySelector('#recaptcha-audio-button')?.click();")
            time.sleep(3)
            if is_blocked(page):
                raise Exception("IP 被封锁（JS点击后）")
            input_box = bframe.ele('#audio-response', timeout=1)
            if input_box and input_box.states.is_displayed:
                return True
        except Exception as e:
            if "封锁" in str(e) or "blocked" in str(e).lower():
                raise
            pass
        time.sleep(2)
    return False


def is_audio_mode(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=1)
        return bool(input_box and input_box.states.is_displayed)
    except:
        return False


def get_audio_url(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return None
    for _ in range(10):
        try:
            link = bframe.ele('.rc-audiochallenge-tdownload-link', timeout=1)
            if link:
                href = link.attr('href')
                if href and len(href) > 10:
                    return html.unescape(href)
            link = bframe.ele('.rc-audiochallenge-ndownload-link', timeout=1)
            if link:
                href = link.attr('href')
                if href and len(href) > 10:
                    return html.unescape(href)
            audio = bframe.ele('#audio-source', timeout=1)
            if audio:
                src = audio.attr('src')
                if src and len(src) > 10:
                    return html.unescape(src)
        except:
            pass
        time.sleep(1)
    return None


def reload_challenge(page):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return
    try:
        reload_btn = bframe.ele('#recaptcha-reload-button', timeout=2)
        if reload_btn:
            try:
                reload_btn.click()
            except:
                reload_btn.click(by_js=True)
            time.sleep(3)
    except:
        pass


def fill_and_verify(page, text):
    bframe = find_recaptcha_frame(page, "bframe")
    if not bframe:
        return False
    try:
        input_box = bframe.ele('#audio-response', timeout=2)
        if not input_box:
            return False
        input_box.click()
        input_box.clear()
        input_box.input(text)
    except:
        return False
    time.sleep(random.uniform(0.5, 1.5))
    try:
        verify_btn = bframe.ele('#recaptcha-verify-button', timeout=2)
        if verify_btn:
            try:
                verify_btn.click()
            except:
                verify_btn.click(by_js=True)
    except:
        pass
    return True


def download_audio(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.google.com/",
    }
    urls = [url]
    if "recaptcha.net" in url:
        urls.append(url.replace("recaptcha.net", "www.google.com"))
    elif "google.com" in url:
        urls.append(url.replace("www.google.com", "recaptcha.net"))
    for audio_url in urls:
        try:
            r = requests.get(audio_url, headers=headers, timeout=30)
            r.raise_for_status()
            if len(r.content) < 1000:
                continue
            path = tempfile.mktemp(suffix=".mp3")
            with open(path, "wb") as f:
                f.write(r.content)
            return path
        except:
            pass
    return None


def recognize_audio(mp3_path):
    if sr is None:
        return None
    try:
        wav_path = mp3_path.replace(".mp3", ".wav")
        AudioSegment.from_mp3(mp3_path).export(wav_path, format="wav")
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as src:
            audio_data = recognizer.record(src)
            text = recognizer.recognize_google(audio_data)
        try:
            os.remove(wav_path)
        except:
            pass
        return text
    except:
        return None


def solve_recaptcha(page):
    start = time.time()
    while time.time() - start < 15:
        if find_recaptcha_frame(page, "anchor"):
            break
        time.sleep(1)
    else:
        raise RuntimeError("reCAPTCHA 加载超时")

    dl_fails = 0
    for i in range(MAX_CAPTCHA_ATTEMPTS):
        if is_recaptcha_solved(page):
            return True
        if is_blocked(page):
            raise Exception("IP �?Google reCAPTCHA 封锁")

        if i == 0:
            click_recaptcha_checkbox(page)
            time.sleep(2)
            if is_recaptcha_solved(page):
                return True

        if not is_audio_mode(page):
            if not switch_to_audio(page):
                time.sleep(3)
                if not switch_to_audio(page):
                    click_recaptcha_checkbox(page)
                    time.sleep(3)
                    continue
            time.sleep(random.uniform(2, 4))

        if is_blocked(page):
            raise Exception("音频模式检测到 IP 被封")

        audio_url = get_audio_url(page)
        if not audio_url:
            reload_challenge(page)
            continue

        mp3 = download_audio(audio_url)
        if not mp3:
            dl_fails += 1
            if dl_fails >= 3:
                raise RuntimeError("音频连续下载失败")
            reload_challenge(page)
            time.sleep(random.uniform(3, 6))
            continue
        dl_fails = 0

        text = recognize_audio(mp3)
        try:
            os.remove(mp3)
        except:
            pass
        if not text:
            reload_challenge(page)
            time.sleep(3)
            continue

        log(f"识别结果: [{text}]")
        fill_and_verify(page, text)
        time.sleep(5)
        if is_recaptcha_solved(page):
            return True
        reload_challenge(page)
        time.sleep(random.uniform(2, 4))

    raise RuntimeError("验证码达到最大尝试次�?)


def capture_screenshot(page, filename):
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        page.get_screenshot(path=filename)
        return filename
    except Exception as e:
        log(f"截图失败: {e}", "WARN")
        return None


def main():
    # ── 解析配置 ──
    account = os.environ.get("FGH_ACCOUNT", "").strip()
    if not account or "," not in account:
        log("错误: FGH_ACCOUNT 未设置或格式不正确（应为 email,password�?, "ERROR")
        sys.exit(1)

    email, password = account.split(",", 1)
    email = email.strip()
    password = password.strip()
    log(f"账号: {email}")
    log(f"密码长度: {len(password)}")

    tg_token = os.environ.get("TG_BOT_TOKEN", "").strip()
    tg_chat_id = os.environ.get("TG_CHAT_ID", "").strip()

    # ── 启动虚拟显示 ──
    vdisplay = Xvfb(width=1920, height=1080, colordepth=24)
    vdisplay.start()

    success = False
    error_msg = ""

    try:
        # ── 启动浏览�?──
        co = ChromiumOptions()
        co.set_browser_path('/usr/bin/google-chrome')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-gpu')
        co.set_argument('--disable-setuid-sandbox')
        co.set_argument('--disable-software-rasterizer')
        co.set_argument('--disable-extensions')
        co.set_argument('--no-first-run')
        co.set_argument('--no-default-browser-check')
        co.set_argument('--window-size=1920,1080')
        co.set_argument('--log-level=3')
        co.headless(False)

        # 启动浏览�?
        page = ChromiumPage(co)

        # ── 反指�?──
        page.run_js("""
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel(R) UHD Graphics 630';
                return getParameter.apply(this, [parameter]);
            };
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)

        # ── 访问登录�?──
        log("正在打开登录页面...")
        page.get(f"{PANEL_URL}/auth/login")
        time.sleep(8)
        page.get_screenshot(f"{SCREENSHOT_DIR}/01_login_page.png")

        # ── 填入凭据 ──
        log("正在填入登录信息...")
        try:
            page.ele('input[name="username"]').input(email)
            log(f"已填入用户名: {email}")
        except Exception as e:
            log(f"填入用户名失�? {e}", "WARN")

        try:
            page.ele('input[name="password"]').input(password)
            log(f"已填入密�?(长度{len(password)})")
        except Exception as e:
            log(f"填入密码失败: {e}", "WARN")

        # ── 点击登录 ──
        log("正在点击登录按钮...")
        try:
            page.ele('button[type="submit"]').click()
        except:
            try:
                page.ele('button:contains("LOGIN")').click()
            except:
                page.run_js('document.querySelector("button[type=submit]")?.click()')

        time.sleep(10)
        current_url = page.url
        log(f"登录后URL: {current_url}")
        page.get_screenshot(f"{SCREENSHOT_DIR}/02_after_login.png")

        if "login" in current_url.lower():
            # 检查是否有 reCAPTCHA
            if find_recaptcha_frame(page, "anchor"):
                log("检测到 reCAPTCHA，开始破�?..")
                try:
                    solve_recaptcha(page)
                    log("reCAPTCHA 破解成功")
                    time.sleep(3)
                    # 重新点击登录
                    try:
                        page.ele('button[type="submit"]').click()
                    except:
                        page.run_js('document.querySelector("button[type=submit]")?.click()')
                    time.sleep(10)
                    current_url = page.url
                except Exception as e:
                    error_msg = f"reCAPTCHA 破解失败: {e}"
                    log(f"�?{error_msg}", "ERROR")
                    page.get_screenshot(f"{SCREENSHOT_DIR}/error_captcha.png")
                    raise

            if "login" in page.url.lower():
                error_msg = "登录失败：请检查账号密码是否正�?
                page.get_screenshot(f"{SCREENSHOT_DIR}/error_login_failed.png")
                raise Exception(error_msg)

        log("�?登录成功�?)

        # ── 前往仪表�?──
        log("正在打开仪表�?..")
        page.get(f"{PANEL_URL}/")
        time.sleep(5)
        page.get_screenshot(f"{SCREENSHOT_DIR}/03_dashboard.png")

        # ── 查找续期链接 ──
        server_links = page.eles('a[href*="/server/renew"]')
        if not server_links:
            server_links = page.eles('a[href*="/server/"]')

        if server_links:
            log(f"发现 {len(server_links)} 个服务器，开始逐个续期...")
            for i, link in enumerate(server_links):
                try:
                    href = link.attr('href')
                    log(f"进入服务�?{i+1}: {mask_url(PANEL_URL + href if not href.startswith('http') else href)}")
                    page.get(href if href.startswith('http') else f"{PANEL_URL}{href}")
                    time.sleep(5)
                    page.get_screenshot(f"{SCREENSHOT_DIR}/server_{i+1}_page.png")

                    old_expire = get_expire_time(page)
                    log(f"服务�?{i+1} 到期时间: {old_expire}")

                    # 点击 Renew 按钮
                    renew_btn = None
                    try:
                        renew_btn = page.ele('xpath://button[contains(text(), "Renew server")]', timeout=3)
                    except:
                        pass
                    if not renew_btn:
                        try:
                            renew_btn = page.ele('xpath://button[contains(text(), "Renew")]', timeout=3)
                        except:
                            pass
                    if not renew_btn:
                        buttons = page.eles('button')
                        for btn in buttons:
                            if "Renew" in (btn.text or ""):
                                renew_btn = btn
                                break

                    if renew_btn:
                        try:
                            renew_btn.click()
                        except:
                            renew_btn.click(by_js=True)
                        log(f"服务�?{i+1} 已点击续期按�?)
                    else:
                        log(f"服务�?{i+1} 未找到续期按�?, "WARN")
                        continue

                    # 等待弹窗
                    time.sleep(5)

                    # 检查是否有 reCAPTCHA
                    if find_recaptcha_frame(page, "anchor"):
                        log(f"服务�?{i+1} 检测到 reCAPTCHA，开始破�?..")
                        try:
                            solve_recaptcha(page)
                            log(f"服务�?{i+1} reCAPTCHA 破解成功")
                            time.sleep(3)
                        except Exception as e:
                            log(f"服务�?{i+1} reCAPTCHA 破解失败: {e}", "ERROR")
                            continue

                    # 确认续期
                    confirm_btn = None
                    try:
                        confirm_btn = page.ele('xpath://button[normalize-space(text())="Renew"]', timeout=5)
                    except:
                        pass
                    if confirm_btn:
                        try:
                            confirm_btn.click()
                        except:
                            confirm_btn.click(by_js=True)
                        log(f"服务�?{i+1} 已确认续�?)
                    else:
                        log(f"服务�?{i+1} 未找到确认按�?, "WARN")
                        continue

                    time.sleep(10)
                    new_expire = get_expire_time(page)
                    page.get_screenshot(f"{SCREENSHOT_DIR}/server_{i+1}_result.png")

                    if new_expire != old_expire and new_expire != "未知":
                        log(f"�?服务�?{i+1} 续期成功: {old_expire} -> {new_expire}")
                        success = True
                    else:
                        log(f"⚠️ 服务�?{i+1} 可能已续期或状态未更新", "WARN")
                        success = True

                except Exception as e:
                    log(f"服务�?{i+1} 续期失败: {e}", "ERROR")
                    page.get_screenshot(f"{SCREENSHOT_DIR}/server_{i+1}_error.png")
        else:
            log("⚠️ 未找到服务器续期链接，尝试点击页面续期按�?..", "WARN")
            # 尝试其他方式
            renew_buttons = page.eles('button')
            found_renew = False
            for btn in renew_buttons:
                if "Renew" in (btn.text or ""):
                    try:
                        btn.click()
                        found_renew = True
                        log("已点�?Renew 按钮")
                        break
                    except:
                        pass
            if found_renew:
                time.sleep(10)
                page.get_screenshot(f"{SCREENSHOT_DIR}/04_after_click_renew.png")
                success = True

    except Exception as e:
        error_msg = str(e)
        log(f"�?出错: {error_msg}", "ERROR")
        try:
            page.get_screenshot(f"{SCREENSHOT_DIR}/error_exception.png")
        except:
            pass

    finally:
        try:
            page.quit()
        except:
            pass
        vdisplay.stop()

    # ── Telegram 通知 ──
    if tg_token and tg_chat_id:
        if success:
            msg = "�?<b>FreeGameHost 续期成功</b>\n请查看截图确认�?
        else:
            msg = f"�?<b>FreeGameHost 续期失败</b>\n错误: {error_msg[:200]}"
        send_tg(tg_token, tg_chat_id, msg)

    if success:
        log("完成�?)
        sys.exit(0)
    else:
        log(f"失败: {error_msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
