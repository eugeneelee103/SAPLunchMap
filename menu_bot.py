import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from email.utils import formatdate
import requests
from datetime import datetime
import subprocess

# ========== 설정 ==========
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")
FEED_FILE  = "feed.xml"
FEED_TITLE = "SAP Korea 주변 구내식당 점심 메뉴"
FEED_LINK  = "https://welplan.pmh.codes/restaurants/welstory/REST000100/ifc%EC%84%9C%EC%9A%B8"
FEED_DESC  = "SAP Korea 주변 구내식당 점심 메뉴 모음"
MAX_ITEMS  = 7

STORES = [
    {"name": "교직원 공제회",       "type": "cjfreshmeal", "id": 6848},
    {"name": "FKI 타워",          "type": "cjfreshmeal", "id": 6083},
    {"name": "IFC 서울",          "type": "welstory",    "id": "REST000100"},
]
# ==========================


def get_cjfreshmeal_menu(store_id, date):
    """CJ 프레시밀 점심 메뉴 조회 (mealCd=2)"""
    url = (
        "https://front.cjfreshmeal.co.kr/meal/v1/today-all-meal"
        f"?storeIdx={store_id}&mealDt={date}&reqType=main"
    )
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://front.cjfreshmeal.co.kr/",
    }
    res = requests.get(url, headers=headers, timeout=10)
    res.raise_for_status()
    data = res.json()
    if data.get("status") != "success":
        return []
    result = []
    for item in data.get("data", {}).get("2", []):  # "2" = 점심
        result.append({
            "name":   item.get("name")   or "",
            "side":   item.get("side")   or "",
            "kcal":   item.get("kcal")   or 0,
            "corner": item.get("corner") or "",
        })
    return result


def get_welstory_menu(restaurant_id, date):
    """Welstory 점심 메뉴 조회 (mealTimeId=2) - 최대 3회 재시도"""
    url = (
        "https://welplan.pmh.codes/api/menu/live"
        f"?kind=gallery&date={date}&time=all&restaurantId={restaurant_id}"
    )
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://welplan.pmh.codes/",
    }
    try:
        res = requests.get(url, headers=headers, timeout=60)
        res.raise_for_status()
        data = res.json()
        result = []
        for item in data.get("menus", []):
            if str(item.get("mealTimeId")) != "2":
                continue
            components = item.get("components", [])
            side = ", ".join(c["name"] for c in components if not c.get("isMain"))
            result.append({
                "name":   item.get("name") or "",
                "side":   side,
                "kcal":   item.get("nutrition", {}).get("calories") or 0,
                "corner": "",
            })
        return result
    except Exception as e:
        print(f"[ERROR] IFC 서울: {e}")
        return []


def fetch_all_menus(date):
    """모든 식당 점심 메뉴 수집"""
    all_menus = []
    for store in STORES:
        try:
            if store["type"] == "cjfreshmeal":
                items = get_cjfreshmeal_menu(store["id"], date)
            elif store["type"] == "welstory":
                items = get_welstory_menu(store["id"], date)
            else:
                items = []
            print(f"[OK] {store['name']} 메뉴 {len(items)}개 수집")
        except Exception as e:
            print(f"[ERROR] {store['name']}: {e}")
            items = []
        all_menus.append({"name": store["name"], "items": items})
    return all_menus


def format_all_menus(all_menus):
    """모든 식당 메뉴 텍스트 포맷"""
    lines = []
    for store in all_menus:
        lines.append(f"📍 {store['name']}")
        if not store["items"]:
            lines.append("  (메뉴 정보 없음)")
        else:
            for item in store["items"]:
                corner = f"[{item['corner']}] " if item["corner"] else ""
                lines.append(f"  {corner}{item['name']}")
                if item["side"]:
                    lines.append(f"  {item['side']}")
        lines.append("")
    return "\n".join(lines)


def update_rss_feed(all_menus):
    """feed.xml 생성 / 업데이트 (최근 7일치 유지)"""
    today        = datetime.now()
    today_str    = today.strftime("%Y%m%d%H%M")  # 분 단위 GUID (매 실행마다 새 항목)
    today_label  = today.strftime("%Y년 %m월 %d일")
    pub_date     = formatdate(time.mktime(today.timetuple()), localtime=False)

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

    menu_text = format_all_menus(all_menus)
    new_item  = (
        f"<item>"
        f"<title>{today_label} 🍽️ 점심 메뉴</title>"
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


def send_to_teams(all_menus):
    """Teams Webhook 전송 (URL 없으면 건너뜀)"""
    if not TEAMS_WEBHOOK_URL:
        print("[SKIP] TEAMS_WEBHOOK_URL 없음 - Teams 전송 건너뜀")
        return
    today = datetime.now().strftime("%Y년 %m월 %d일")
    text  = f"## 🍽️ {today} 점심 메뉴\n\n" + format_all_menus(all_menus)
    res = requests.post(
        TEAMS_WEBHOOK_URL,
        json={"text": text},
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    res.raise_for_status()
    print(f"[OK] Teams 전송 완료 (status: {res.status_code})")


if __name__ == "__main__":
    today = datetime.now().strftime("%Y%m%d")
    print(f"점심 메뉴 수집 중... ({today})")

    all_menus = fetch_all_menus(today)
    update_rss_feed(all_menus)
    send_to_teams(all_menus)

    # feed.xml 자동 GitHub push
    try:
        subprocess.run(["git", "add", "feed.xml"], check=True)
        subprocess.run(["git", "commit", "-m", f"메뉴 업데이트 {today}"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("[OK] GitHub push 완료")
    except subprocess.CalledProcessError:
        print("[SKIP] 변경사항 없음")
