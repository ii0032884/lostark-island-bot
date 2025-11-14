# ==============================================
# 0. IMPORT
# ==============================================
import os
import time
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
from apscheduler.triggers.date import DateTrigger
from dotenv import load_dotenv


# ==============================================
# 1. ENV
# ==============================================
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
LOSTARK_JWT = os.getenv("LOSTARK_JWT")

KST = pytz.timezone("Asia/Seoul")
API_URL = "https://developer-lostark.game.onstove.com/gamecontents/calendar"

logging.basicConfig(level=logging.INFO)


# ==============================================
# 2. HEALTH CHECK (Flask)
# ==============================================
app = Flask(__name__)
server_ready = False

@app.route("/health")
def health():
    if server_ready:
        return "OK", 200
    else:
        return "Starting…", 503


def run_server():
    global server_ready
    time.sleep(5)   # 디스코드 봇 초기화 기다림
    server_ready = True
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


threading.Thread(target=run_server, daemon=True).start()


# ==============================================
# 3. DISCORD BOT + INTENTS
# ==============================================
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)
scheduler = AsyncIOScheduler(timezone=KST)

_calendar_cache_date = None
_calendar_cache_data = None


# ==============================================
# 4. LOST ARK API
# ==============================================
def get_calendar():
    global _calendar_cache_date, _calendar_cache_data
    today = datetime.now(KST).date()

    if _calendar_cache_date == today and _calendar_cache_data:
        return _calendar_cache_data

    headers = {
        "accept": "application/json",
        "authorization": f"bearer {LOSTARK_JWT}"
    }
    r = requests.get(API_URL, headers=headers, timeout=20)
    r.raise_for_status()

    data = r.json()
    _calendar_cache_date = today
    _calendar_cache_data = data
    return data


def rewards_to_text(rewards):
    if not rewards:
        return "보상: (정보 없음)"

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
    names = [n.strip() for n in names if n.strip()]
    if not names:
        return "보상: (정보 없음)"

    def is_gold(n):
        low = n.lower()
        return ("골드" in n) or ("gold" in low)

    gold = [n for n in names if is_gold(n)]
    other = [n for n in names if not is_gold(n)]

    lines = [f"- {n}" for n in sorted(set(gold))]
    lines += [f"  {n}" for n in sorted(set(other))]

    return "보상:\n```diff\n" + "\n".join(lines) + "\n```"


def parse_adventure_islands(data, date=None):
    if date is None:
        date = datetime.now(KST).date()

    out = []
    korean_date = date

    for e in data:
        cat = (e.get("Category") or "").lower()
        if ("모험" in cat and "섬" in cat) or ("adventure" in cat and "island" in cat):

            name = e.get("ContentsName") or "모험섬"
            desc = e.get("ContentsNote") or ""
            rewards = e.get("RewardItems") or e.get("Rewards")

            times = e.get("StartTimes") or []
            if not isinstance(times, list):
                times = [times]

            valid = []
            for t in times:
                try:
                    raw = str(t).replace("Z", "+00:00")
                    dt = datetime.fromisoformat(raw)

                    # UTC → KST 변환 기준으로 오늘인지 판단
                    kst_dt = dt.astimezone(KST)
                    if kst_dt.date() == korean_date:
                        valid.append(kst_dt)

                except:
                    pass

            if valid:
                out.append({
                    "name": name,
                    "desc": desc,
                    "times": sorted(valid),
                    "rewards": rewards
                })

    out.sort(key=lambda x: x["times"][0])
    return out


def build_adventure_embed(for_date=None, prefix="오늘의 모험섬"):
    data = get_calendar()
    arr = parse_adventure_islands(data, for_date)

    ds = (for_date or datetime.now(KST).date()).strftime("%m/%d %a")
    embed = discord.Embed(title=f"{prefix} ({ds})", color=0x2ecc71)
    embed.set_footer(text="데이터 출처: Lost Ark OpenAPI")

    if not arr:
        embed.description = "오늘의 모험섬 정보가 없습니다."
        return embed

    for it in arr:
        timestr = " / ".join(t.strftime("%H:%M") for t in it["times"])
        msg = [f"시간: {timestr}"]
        if it["desc"]:
            msg.append(f"메모: {it['desc']}")
        msg.append(rewards_to_text(it["rewards"]))

        embed.add_field(name=it["name"], value="\n".join(msg), inline=False)

    return embed


# ==============================================
# 5. AUTO SEND
# ==============================================
async def send_island_info():
    logging.info(f"[AUTO] send_island_info 실행 / channel={CHANNEL_ID}")
    ch = bot.get_channel(CHANNEL_ID)
    logging.info(f"[AUTO] channel 객체: {ch}")

    if ch:
        await ch.send(embed=build_adventure_embed())
    else:
        logging.error("⚠ 채널을 찾지 못했습니다. DISCORD_CHANNEL_ID 확인 필요.")


# ==============================================
# 6. DISCORD READY
# ==============================================
@bot.event
async def on_ready():
    logging.info(f"로그인 성공 → {bot.user}")
    await bot.wait_until_ready()

    logging.info(f"환경 CHANNEL_ID = {CHANNEL_ID}")
    logging.info(f"채널 객체 = {bot.get_channel(CHANNEL_ID)}")

    if not scheduler.get_jobs():
        scheduler.add_job(send_island_info, CronTrigger(hour=6, minute=1))
        scheduler.add_job(send_island_info,
            DateTrigger(run_date=datetime.now(KST) + timedelta(seconds=10))
        )
        scheduler.start()
        logging.info("Scheduler started ✔")

    try:
        await bot.tree.sync()
    except Exception as e:
        logging.warning(f"Slash sync 실패: {e}")


# ==============================================
# 7. SLASH COMMAND
# ==============================================
@bot.tree.command(name="island", description="오늘 모험섬")
async def island_today(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send(embed=build_adventure_embed())


@bot.tree.command(name="island_tomorrow", description="내일 모험섬")
async def island_tomorrow(interaction: discord.Interaction):
    tomorrow = (datetime.now(KST) + timedelta(days=1)).date()
    await interaction.response.defer()
    await interaction.followup.send(
        embed=build_adventure_embed(tomorrow, prefix="내일 모험섬")
    )


# ==============================================
# 8. RUN
# ==============================================
if __name__ == "__main__":
    if not DISCORD_TOKEN or not CHANNEL_ID or not LOSTARK_JWT:
        raise SystemExit("DISCORD_TOKEN / CHANNEL_ID / LOSTARK_JWT 환경변수 확인!")

    bot.run(DISCORD_TOKEN)


# ──────────────────────────────────────────────────────────────────────────────
# Slash 명령어
# ──────────────────────────────────────────────────────────────────────────────
@bot.tree.command(name="island")
async def island_today(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send(embed=build_adventure_embed())


@bot.tree.command(name="island_tomorrow")
async def island_tomorrow(interaction: discord.Interaction):
    await interaction.response.defer()
    tomorrow = (datetime.now(KST) + timedelta(days=1)).date()
    await interaction.followup.send(
        embed=build_adventure_embed(for_date=tomorrow, prefix="내일 모험섬")
    )


# ──────────────────────────────────────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN or not CHANNEL_ID or not LOSTARK_JWT:
        raise SystemExit(
            ".env / Render 환경변수의 DISCORD_TOKEN, DISCORD_CHANNEL_ID, LOSTARK_JWT를 확인하세요."
        )
    bot.run(DISCORD_TOKEN)
