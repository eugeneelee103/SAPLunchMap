import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from email.utils import formatdate
import requests
from datetime import datetime
import subprocess

# ========== 설정 ==========
STORE_IDX = 6848
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")
# GitHub Actions Secret 또는 로컬에서: export TEAMS_WEBHOOK_URL="https://..."
FEED_FILE  = "feed.xml"
FEED_TITLE = "SAP Korea 구내식당 메뉴"
FEED_LINK  = "https://front.cjfreshmeal.co.kr/menu/today"
FEED_DESC  = "SAP Korea CJ 프레시밀 오늘의 메뉴"
MAX_ITEMS  = 7   # 최대 7일치 보관
# ==========================

MEAL_NAMES = {"1": "조식", "2": "중식", "3": "석식"}
MEAL_EMOJI = {"1": "🌅", "2": "🍱", "3": "🌙"}


def get_today_menu():
    today = datetime.now().strftime("%Y%m%d")
    url = (
        "https://front.cjfreshmeal.co.kr/meal/v1/today-all-meal"
        f"?storeIdx={STORE_IDX}&mealDt={today}&reqType=main"
    )
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://front.cjfreshmeal.co.kr/",
    }
    res = requests.get(url, headers=headers, timeout=10)
    res.raise_for_status()
    return res.json()


def format_menu_text(api_data):
    """RSS description용 텍스트 포맷 (Teams FeedSummary 표시용)"""
    meal_data = api_data.get("data", {})
    lines = []
    for meal_cd in sorted(meal_data.keys()):
        items = meal_data[meal_cd]
        emoji = MEAL_EMOJI.get(meal_cd, "")
        label = MEAL_NAMES.get(meal_cd, f"식사{meal_cd}")
        lines.append(f"{emoji} {label}")
        for item in items:
            corner = item.get("corner") or ""
            name   = item.get("name")   or ""
            side   = item.get("side")   or ""
            kcal   = item.get("kcal")   or 0
            lines.append(f"  [{corner}] {name} ({kcal} kcal)")
            if side:
                lines.append(f"  {side}")
        lines.append("")
    return "\n".join(lines)


def update_rss_feed(api_data):
    """feed.xml 생성 / 업데이트 (최근 7일치 유지)"""
    today        = datetime.now()
    today_str    = today.strftime("%Y%m%d%H%M")  # 테스트용: 분단위 GUID
    today_label  = today.strftime("%Y년 %m월 %d일")
    pub_date     = formatdate(time.mktime(today.timetuple()), localtime=False)

    # 기존 항목 읽기 (오늘 것은 덮어씀)
    old_items = []
    if Path(FEED_FILE).exists():
        try:
            root    = ET.parse(FEED_FILE).getroot()
            channel = root.find("channel")
            if channel is not None:
                for el in channel.findall("item"):
                    guid = el.findtext("guid", "")
                    if guid != today_str:
                        old_items.append(ET.tostring(el, encoding="unicode"))
        except Exception:
            pass

    menu_text = format_menu_text(api_data)
    new_item  = (
        f"<item>"
        f"<title>{today_label} 🍽️ 구내식당 메뉴</title>"
        f"<description>{menu_text}</description>"
        f"<link>{FEED_LINK}</link>"
        f"<pubDate>{pub_date}</pubDate>"
        f"<guid>{today_str}</guid>"
        f"</item>"
    )

    all_items = ([new_item] + old_items)[:MAX_ITEMS]

    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        '  <channel>\n'
        f'    <title>{FEED_TITLE}</title>\n'
        f'    <link>{FEED_LINK}</link>\n'
        f'    <description>{FEED_DESC}</description>\n'
        '    <language>ko</language>\n'
        + "\n    ".join(all_items) +
        '\n  </channel>\n'
        '</rss>'
    )

    with open(FEED_FILE, "w", encoding="utf-8") as f:
        f.write(rss)
    print(f"[OK] feed.xml 업데이트 완료 ({today_label})")


def send_to_teams(api_data):
    """Teams Webhook 전송 (URL 없으면 건너뜀)"""
    if not TEAMS_WEBHOOK_URL:
        print("[SKIP] TEAMS_WEBHOOK_URL 없음 - Teams 전송 건너뜀")
        return
    meal_data = api_data.get("data", {})
    today     = datetime.now().strftime("%Y년 %m월 %d일")
    lines     = [f"## 🍽️ {today} 구내식당 오늘의 메뉴", "**SAP Korea · CJ 프레시밀**", ""]
    for meal_cd in sorted(meal_data.keys()):
        items = meal_data[meal_cd]
        emoji = MEAL_EMOJI.get(meal_cd, "")
        label = MEAL_NAMES.get(meal_cd, f"식사{meal_cd}")
        lines.append(f"### {emoji} {label}")
        for item in items:
            corner = item.get("corner") or ""
            name   = item.get("name")   or ""
            side   = item.get("side")   or ""
            kcal   = item.get("kcal")   or 0
            lines.append(f"**[{corner}] {name}** ({kcal} kcal)")
            if side:
                lines.append(f"> {side}")
        lines.append("")
    res = requests.post(
        TEAMS_WEBHOOK_URL,
        json={"text": "\n".join(lines)},
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    res.raise_for_status()
    print(f"[OK] Teams 전송 완료 (status: {res.status_code})")


if __name__ == "__main__":
    print("메뉴 불러오는 중...")
    api_data = get_today_menu()

    if api_data.get("status") != "success":
        print(f"[!] API 오류: {api_data}")
        exit(1)

    update_rss_feed(api_data)
    send_to_teams(api_data)

    # feed.xml 자동 GitHub push
    try:
        subprocess.run(["git", "add", "feed.xml"], check=True)
        subprocess.run(["git", "commit", "-m", f"메뉴 업데이트"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("[OK] GitHub push 완료")
    except subprocess.CalledProcessError:
        print("[SKIP] 변경사항 없음")
