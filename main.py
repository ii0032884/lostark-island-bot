# ──────────────────────────────────────────────────────────────────────────────
# HTTP Health Check (Flask) → Render Free Sleep 방지 + 502 방지
# ──────────────────────────────────────────────────────────────────────────────
from flask import Flask
import threading, os, time
import requests

app = Flask(__name__)

@app.route("/")
def home():
    return "OK", 200


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# Render가 서버를 끊지 않도록 30초마다 자기 자신을 ping
def keep_alive():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    while True:
        try:
            requests.get(url, timeout=5)
        except:
            pass
        time.sleep(30)  # 30초마다 ping


threading.Thread(target=run_flask, daemon=True).start()
threading.Thread(target=keep_alive, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────
# 디스코드 봇 & 스케줄러
# ──────────────────────────────────────────────────────────────────────────────
import logging
from datetime import datetime, timedelta
import pytz
import requests
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

# 인텐트 보강
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)
logging.basicConfig(level=logging.INFO)

scheduler = AsyncIOScheduler(timezone=KST)


# ──────────────────────────────────────────────────────────────────────────────
# Lost Ark API 처리 (UTC → KST 변환 버그 패치 반영)
# ──────────────────────────────────────────────────────────────────────────────

_calendar_cache_date = None
_calendar_cache_data = None


def get_calendar():
    global _calendar_cache_data, _calendar_cache_date
    today = datetime.now(KST).date()
    if _calendar_cache_date == today and _calendar_cache_data:
        return _calendar_cache_data

    headers = {
        "accept": "application/json",
        "authorization": f"bearer {LOSTARK_JWT}",
    }
    r = requests.get(API_URL, headers=headers, timeout=15)
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
        return "보상: (이벤트 데이터 없음)"

    def is_gold(s: str):
        s2 = s.lower()
        return ("골드" in s) or ("gold" in s2)

    gold = [n for n in names if is_gold(n)]
    other = [n for n in names if not is_gold(n)]

    lines = [f"- {n}" for n in sorted(set(gold))]
    lines += [f"  {n}" for n in sorted(set(other))]

    return "보상:\n```diff\n" + "\n".join(lines) + "\n```"


def parse_adventure_islands(data, date=None):
    if date is None:
        date = datetime.now(KST).date()

    out = []
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
                    # 🔥 UTC → KST 정확 변환 (API 날짜 밀림 버그 해결)
                    utc_dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                    kst_dt = utc_dt.astimezone(KST)

                    # 🔥 KST 날짜로 비교
                    if kst_dt.date() == date:
                        valid.append(kst_dt)
                except:
                    pass

            if valid:
                out.append(
                    {
                        "name": name,
                        "desc": desc,
                        "times": sorted(valid),
                        "rewards": rewards,
                    }
                )

    out.sort(key=lambda x: x["times"][0])
    return out


def build_adventure_embed(for_date=None, prefix="오늘의 모험섬"):
    data = get_calendar()
    arr = parse_adventure_islands(data, for_date)
    ds = (for_date or datetime.now(KST).date()).strftime("%m/%d %a")

    embed = discord.Embed(title=f"{prefix} ({ds})", color=0x2ecc71)
    embed.set_footer(text="데이터 출처: Lost Ark OpenAPI")

    if not arr:
        embed.description = "해당 날짜의 모험섬 정보가 없습니다."
        return embed

    for it in arr:
        t_str = " / ".join(d.strftime("%H:%M") for d in it["times"])
        msg = [f"시간: {t_str}"]
        if it["desc"]:
            msg.append(f"메모: {it['desc']}")
        msg.append(rewards_to_text(it["rewards"]))
        embed.add_field(name=it["name"], value="\n".join(msg), inline=False)

    return embed


# ──────────────────────────────────────────────────────────────────────────────
# 자동 발송
# ──────────────────────────────────────────────────────────────────────────────
async def send_island_info():
    logging.info(f"send_island_info 실행, CHANNEL_ID={CHANNEL_ID}")
    ch = bot.get_channel(CHANNEL_ID)
    logging.info(f"send_island_info 채널 객체: {ch}")
    if ch:
        await ch.send(embed=build_adventure_embed())
    else:
        logging.error("❌ 채널을 찾지 못했습니다. DISCORD_CHANNEL_ID 확인 필요!")


# ──────────────────────────────────────────────────────────────────────────────
# on_ready
# ──────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    logging.info(f"로그인 성공: {bot.user} (ID: {bot.user.id})")
    await bot.wait_until_ready()

    ch = bot.get_channel(CHANNEL_ID)
    logging.info(f"on_ready에서 채널 객체: {ch}")

    if not scheduler.get_jobs():
        scheduler.add_job(send_island_info, CronTrigger(hour=6, minute=1))

        scheduler.add_job(
            send_island_info,
            DateTrigger(run_date=datetime.now(KST) + timedelta(seconds=10)),
        )

        scheduler.start()
        logging.info("Scheduler started")

    try:
        await bot.tree.sync()
        logging.info("Slash commands synced")
    except:
        pass


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
    bot.run(DISCORD_TOKEN)