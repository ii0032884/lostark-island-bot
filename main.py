# ──────────────────────────────────────────────────────────────────────────────
# Flask Health Check → Render 502 방지
# ──────────────────────────────────────────────────────────────────────────────
from flask import Flask
import threading, os, time, requests

app = Flask(__name__)

@app.route("/")
def home():
    return "OK", 200

@app.route("/health")
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    while True:
        try:
            requests.get(url + "/health", timeout=5)
        except:
            pass
        time.sleep(30)

threading.Thread(target=run_flask, daemon=True).start()
threading.Thread(target=keep_alive, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────
# 디스코드 봇 설정
# ──────────────────────────────────────────────────────────────────────────────
import logging
from datetime import datetime, timedelta
import pytz
import requests as req
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
LOSTARK_JWT = os.getenv("LOSTARK_JWT")

KST = pytz.timezone("Asia/Seoul")
API_URL = "https://developer-lostark.game.onstove.com/gamecontents/calendar"

intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

logging.basicConfig(level=logging.INFO)
scheduler = AsyncIOScheduler(timezone=KST)


# ──────────────────────────────────────────────────────────────────────────────
# 오늘의 모험섬 이름 3개를 API에서 자동 추출하는 함수
# ──────────────────────────────────────────────────────────────────────────────
def get_today_islands_from_api(api_data, date):
    """API에서 오늘 등장한 모험섬 이름만 3개 추출"""
    results = set()

    for e in api_data:
        cat = (e.get("Category") or "")
        if "모험" not in cat:
            continue

        name = e.get("ContentsName")
        if not name:
            continue

        times = e.get("StartTimes") or []
        if not isinstance(times, list):
            times = [times]

        for t in times:
            try:
                utc_dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                kst_dt = utc_dt.astimezone(KST)
                if kst_dt.date() == date:
                    results.add(name)
            except:
                pass

    return list(results)


# ──────────────────────────────────────────────────────────────────────────────
# Lost Ark API 데이터 캐시
# ──────────────────────────────────────────────────────────────────────────────
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
    r = req.get(API_URL, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()

    _calendar_cache_date = today
    _calendar_cache_data = data
    return data


# ──────────────────────────────────────────────────────────────────────────────
# 오늘 모험섬의 보상을 API에서 추출
# ──────────────────────────────────────────────────────────────────────────────
def get_rewards_for_island(api_data, island_name, date):
    for e in api_data:
        if e.get("ContentsName") != island_name:
            continue

        times = e.get("StartTimes") or []
        if not isinstance(times, list):
            times = [times]

        for t in times:
            try:
                utc_dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                kst_dt = utc_dt.astimezone(KST)
                if kst_dt.date() == date:
                    return e.get("RewardItems")
            except:
                pass

    return None


# ──────────────────────────────────────────────────────────────────────────────
# 보상 텍스트 포맷팅
# ──────────────────────────────────────────────────────────────────────────────
def rewards_to_text(rewards):
    if not rewards:
        return "보상: 정보 없음"

    names = []

    def extract(o):
        if isinstance(o, dict):
            if o.get("Name"):
                names.append(str(o["Name"]))
            if o.get("RewardName"):
                names.append(str(o["RewardName"]))
            for v in o.values():
                extract(v)
        elif isinstance(o, list):
            for x in o:
                extract(x)

    extract(rewards)
    names = [n for n in names if n.strip()]

    if not names:
        return "보상: 정보 없음"

    def is_gold(x):
        return "골드" in x or "gold" in x.lower()

    gold = [n for n in names if is_gold(n)]
    other = [n for n in names if not is_gold(n)]

    lines = ["- " + g for g in gold]
    lines += ["  " + o for o in other]

    return "보상:\n```diff\n" + "\n".join(lines) + "\n```"


# ──────────────────────────────────────────────────────────────────────────────
# 전체 시간표(인벤 스타일): 평일/주말 구분
# ──────────────────────────────────────────────────────────────────────────────
def get_daily_times(date):
    wd = date.weekday()
    if wd <= 4:  # 월~금
        return ["11:00", "13:00", "19:00", "21:00", "23:00"]
    else:        # 토,일
        return ["14:00", "16:00", "19:00", "21:00", "23:00"]


# ──────────────────────────────────────────────────────────────────────────────
# 전체 모험섬 임베드 생성
# ──────────────────────────────────────────────────────────────────────────────
def build_full_island_embed(date=None):
    if date is None:
        date = datetime.now(KST).date()

    api_data = get_calendar()
    island_names = get_today_islands_from_api(api_data, date)
    times = get_daily_times(date)

    embed = discord.Embed(
        title=f"🔥 전체 모험섬 정보 {date.strftime('%m/%d (%a)')}",
        color=0x2ecc71
    )

    if not island_names:
        embed.description = "오늘 모험섬 정보가 없습니다."
        return embed

    for name in island_names:
        rewards = get_rewards_for_island(api_data, name, date)
        reward_text = rewards_to_text(rewards)

        embed.add_field(
            name=name,
            value=f"시간: {', '.join(times)}\n{reward_text}",
            inline=False
        )
    return embed


# ──────────────────────────────────────────────────────────────────────────────
# 자동 발송
# ──────────────────────────────────────────────────────────────────────────────
async def send_island_info():
    ch = bot.get_channel(CHANNEL_ID)
    if ch:
        await ch.send(embed=build_full_island_embed())
    else:
        logging.error("채널을 찾을 수 없음.")


# ──────────────────────────────────────────────────────────────────────────────
# on_ready
# ──────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    logging.info(f"Bot Ready: {bot.user}")

    if not scheduler.get_jobs():
        # 매일 06:01 자동 전체 모험섬
        scheduler.add_job(send_island_info, CronTrigger(hour=6, minute=1))

        # 서버 시작 테스트용 10초 뒤
        scheduler.add_job(
            send_island_info,
            DateTrigger(run_date=datetime.now(KST) + timedelta(seconds=10)),
        )
        scheduler.start()

    try:
        await bot.tree.sync()
    except:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Slash Commands
# ──────────────────────────────────────────────────────────────────────────────
@bot.tree.command(name="island_full", description="오늘 전체 모험섬(전체 시간표) 정보")
async def island_full(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send(embed=build_full_island_embed())


@bot.tree.command(name="island_tomorrow", description="내일 전체 모험섬 정보")
async def island_tomorrow(interaction: discord.Interaction):
    await interaction.response.defer()
    tomorrow = (datetime.now(KST) + timedelta(days=1)).date()
    await interaction.followup.send(embed=build_full_island_embed(tomorrow))


@bot.tree.command(name="island", description="API 원본(남은 일정만)")
async def island_today(interaction: discord.Interaction):
    await interaction.response.defer()
    data = get_calendar()
    embed = discord.Embed(title="API 기준 남은 모험섬", color=0x3498db)
    for e in data:
        if "모험" in (e.get("Category") or ""):
            embed.add_field(
                name=e.get("ContentsName"),
                value=str(e.get("StartTimes")),
                inline=False
            )
    await interaction.followup.send(embed=embed)


# ──────────────────────────────────────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)