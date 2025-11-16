# main.py
import os
import logging
from datetime import datetime, timedelta
import threading

import pytz
import requests
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from flask import Flask

# ──────────────────────────────────────────────────────────────────────────────
# 🔥 Flask Health Check → Render Sleep 방지 (절대 제거 금지)
# ──────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def home():
    return "OK", 200

def run_server():
    port = int(os.environ.get("PORT", 10000))
    print(f"[FLASK] Running health server on port {port}")
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_server, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────
# 환경설정
# ──────────────────────────────────────────────────────────────────────────────
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

# 전역 스케줄러 (1개만)
scheduler = AsyncIOScheduler(timezone=KST)


# ──────────────────────────────────────────────────────────────────────────────
# API
# ──────────────────────────────────────────────────────────────────────────────
def get_calendar():
    headers = {
        "accept": "application/json",
        "authorization": f"bearer {LOSTARK_JWT}",
    }
    r = requests.get(API_URL, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


def rewards_to_text(rewards):
    if not rewards:
        return "(보상 없음)"
    names = []

    def extract(x):
        if isinstance(x, dict):
            if x.get("Name"):
                names.append(x["Name"])
            if x.get("RewardName"):
                names.append(x["RewardName"])
            for v in x.values():
                extract(v)
        elif isinstance(x, list):
            for v in x:
                extract(v)

    extract(rewards)
    names = [n for n in names if n]
    if not names:
        return "(보상 없음)"
    return ", ".join(sorted(set(names)))


def parse_islands(data, date=None):
    if date is None:
        date = datetime.now(KST).date()

    out = []
    for e in data:
        if not isinstance(e, dict):
            continue

        cat = (e.get("Category") or "").replace(" ", "").lower()
        if not ("모험섬" in cat or ("adventure" in cat and "island" in cat)):
            continue

        name = e.get("ContentsName")
        times = e.get("StartTimes") or []
        if not isinstance(times, list):
            times = [times]

        parsed_times = []
        for t in times:
            try:
                tstr = str(t).replace("Z", "+00:00")
                dt = datetime.fromisoformat(tstr)
                dt = dt.astimezone(KST)
                if dt.date() == date:
                    parsed_times.append(dt)
            except:
                pass

        if parsed_times:
            out.append({
                "name": name,
                "times": sorted(parsed_times),
                "rewards": e.get("RewardItems")
            })

    return sorted(out, key=lambda x: x["times"][0]) if out else []


def build_embed(date=None):
    data = get_calendar()
    islands = parse_islands(data, date)

    embed = discord.Embed(
        title="오늘의 모험섬",
        color=0x2ecc71
    )
    if not islands:
        embed.description = "오늘 모험섬이 없습니다 (API 응답 없음)"
        return embed

    for isl in islands:
        times = " / ".join(t.strftime("%H:%M") for t in isl["times"])
        rewards = rewards_to_text(isl["rewards"])
        embed.add_field(
            name=isl["name"],
            value=f"시간: {times}\n보상: {rewards}",
            inline=False
        )

    return embed


# ──────────────────────────────────────────────────────────────────────────────
# Discord 이벤트
# ──────────────────────────────────────────────────────────────────────────────
async def send_island_info():
    ch = bot.get_channel(CHANNEL_ID)
    if ch:
        await ch.send(embed=build_embed())
    else:
        logging.error("DISCORD_CHANNEL_ID가 잘못됨")


@bot.event
async def on_ready():
    logging.info(f"로그인 성공 {bot.user}")

    # 스케줄러 1번만 등록
    if not scheduler.get_jobs():
        scheduler.add_job(send_island_info,
            CronTrigger(hour=6, minute=1, timezone=KST))
        scheduler.start()
        logging.info("Scheduler started at 06:01 KST")

    try:
        await bot.tree.sync()
        logging.info("Slash commands synced")
    except Exception as e:
        logging.error(f"Slash sync 실패: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Slash Command
# ──────────────────────────────────────────────────────────────────────────────
@bot.tree.command(name="island")
async def island_today(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send(embed=build_embed())


# ──────────────────────────────────────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
