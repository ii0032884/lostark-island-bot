# main.py
import os
import threading
import logging
from datetime import datetime, timedelta

import pytz
import requests
from flask import Flask
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# --------------------------------------------------------------------
# Flask (Render가 Web Service로 인식하도록 포트 바인딩)
# --------------------------------------------------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# --------------------------------------------------------------------
# 환경변수
# --------------------------------------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))
LOSTARK_JWT = os.getenv("LOSTARK_JWT")

KST = pytz.timezone("Asia/Seoul")
API_URL = "https://developer-lostark.game.onstove.com/gamecontents/calendar"


# --------------------------------------------------------------------
# Discord Bot 설정
# --------------------------------------------------------------------
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True  # 중요!

bot = commands.Bot(command_prefix="!", intents=intents)
logging.basicConfig(level=logging.INFO)


# --------------------------------------------------------------------
# Lost Ark 캘린더 API
# --------------------------------------------------------------------
_calendar_cache_date = None
_calendar_cache_data = None

def get_calendar():
    global _calendar_cache_date, _calendar_cache_data
    today = datetime.now(KST).date()

    if _calendar_cache_date == today and _calendar_cache_data:
        return _calendar_cache_data

    headers = {
        "accept": "application/json",
        "authorization": f"bearer {LOSTARK_JWT}",
    }

    res = requests.get(API_URL, headers=headers, timeout=15)
    if res.status_code != 200:
        return None

    data = res.json()
    _calendar_cache_date = today
    _calendar_cache_data = data
    return data


def parse_adventure(data, date=None):
    """2025년 최신 Lost Ark API 구조 대응 파서"""
    if not isinstance(data, list):
        return []

    if date is None:
        date = datetime.now(KST).date()

    result = []
    for item in data:
        cat = (item.get("CategoryName") or item.get("Category") or "").lower()

        if "모험" not in cat:
            continue

        name = item.get("ContentsName") or item.get("Title")
        desc = item.get("ContentsNote") or item.get("Description", "")

        rewards = item.get("RewardItems") or item.get("Rewards")

        times = item.get("StartTimes") or []
        if not isinstance(times, list):
            times = [times]

        daily_times = []
        for t in times:
            try:
                dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                dt = dt.astimezone(KST)
                if dt.date() == date:
                    daily_times.append(dt)
            except:
                pass

        if daily_times:
            result.append({
                "name": name,
                "desc": desc,
                "times": sorted(daily_times),
                "rewards": rewards,
            })

    result.sort(key=lambda x: x["times"][0])
    return result


def rewards_to_text(rewards):
    if not rewards:
        return "보상 없음"

    names = set()

    def parse(obj):
        if isinstance(obj, dict):
            if "Name" in obj:
                names.add(obj["Name"])
            if "RewardName" in obj:
                names.add(obj["RewardName"])
            for v in obj.values():
                parse(v)
        elif isinstance(obj, list):
            for x in obj:
                parse(x)

    parse(rewards)

    if not names:
        return "보상 없음"

    return ", ".join(sorted(names))


def build_embed(for_date=None):
    data = get_calendar()
    if data is None:
        embed = discord.Embed(title="오늘의 모험섬", description="API 응답이 없습니다.", color=0xff0000)
        return embed

    date = for_date or datetime.now(KST).date()
    date_str = date.strftime("%m/%d %a")

    islands = parse_adventure(data, date)

    embed = discord.Embed(title=f"오늘의 모험섬 ({date_str})", color=0x2ecc71)
    embed.set_footer(text="Lost Ark OpenAPI 기준")

    if not islands:
        embed.description = "오늘 모험섬이 없습니다 (API 구조 변경?)"
        return embed

    for it in islands:
        t_str = " / ".join(t.strftime("%H:%M") for t in it["times"])
        text = f"시간: {t_str}\n보상: {rewards_to_text(it['rewards'])}"
        if it["desc"]:
            text += f"\n메모: {it['desc']}"
        embed.add_field(name=it["name"], value=text, inline=False)

    return embed


# --------------------------------------------------------------------
# 자동 발송
# --------------------------------------------------------------------
async def send_island():
    ch = bot.get_channel(CHANNEL_ID)
    if ch:
        await ch.send(embed=build_embed())
    else:
        logging.error("채널 없음")


# --------------------------------------------------------------------
# Discord Events
# --------------------------------------------------------------------
@bot.event
async def on_ready():
    logging.info(f"로그인 성공: {bot.user}")

    scheduler = AsyncIOScheduler(timezone=KST)
    scheduler.add_job(send_island, CronTrigger(hour=6, minute=1))
    scheduler.start()

    try:
        await bot.tree.sync()
        logging.info("Slash Commands Synced!")
    except Exception as e:
        logging.warning(f"Slash Sync Error: {e}")


# --------------------------------------------------------------------
# 슬래시 명령어
# --------------------------------------------------------------------
@bot.tree.command(name="island", description="오늘 모험섬 정보")
async def island_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send(embed=build_embed())


# --------------------------------------------------------------------
# 실행
# --------------------------------------------------------------------
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(DISCORD_TOKEN)
