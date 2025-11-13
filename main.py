# ──────────────────────────────────────────────────────────────────────────────
# HTTP Health Check (Flask) → Render/UptimeRobot 안정 유지
# ──────────────────────────────────────────────────────────────────────────────
from flask import Flask
import threading, os

app = Flask(__name__)

@app.route("/")
def home():
    return "OK", 200

def run_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_server, daemon=True).start()


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
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
LOSTARK_JWT = os.getenv("LOSTARK_JWT")

KST = pytz.timezone("Asia/Seoul")
API_URL = "https://developer-lostark.game.onstove.com/gamecontents/calendar"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
logging.basicConfig(level=logging.INFO)

# ──────────────────────────────────────────────────────────────────────────────
# Lost Ark API 처리
# ──────────────────────────────────────────────────────────────────────────────
_calendar_cache_date = None
_calendar_cache_data = None

def get_calendar():
    global _calendar_cache_data, _calendar_cache_date
    today = datetime.now(KST).date()
    if _calendar_cache_date == today and _calendar_cache_data:
        return _calendar_cache_data

    headers = {"accept": "application/json", "authorization": f"bearer {LOSTARK_JWT}"}
    r = requests.get(API_URL, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    _calendar_cache_data, _calendar_cache_date = data, today
    return data


def rewards_to_text(rewards):
    if not rewards:
        return "보상: (정보 없음)"

    # 보상 이름 추출
    names = []
    def extract(o):
        if isinstance(o, dict):
            if o.get("Name"): names.append(str(o["Name"]))
            if o.get("RewardName"): names.append(str(o["RewardName"]))
            for v in o.values(): extract(v)
        elif isinstance(o, list):
            for x in o: extract(x)
    extract(rewards)

    names = [n.strip() for n in names if n.strip()]
    if not names:
        return "보상: (이벤트 데이터 없음)"

    def is_gold(s):
        s2 = s.lower()
        return "골드" in s or "gold" in s2

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
                    dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                    dt = dt.astimezone(KST)
                    if dt.date() == date:
                        valid.append(dt)
                except:
                    pass

            if valid:
                out.append({"name": name, "desc": desc, "times": sorted(valid), "rewards": rewards})

    out.sort(key=lambda x: x["times"][0])
    return out


def build_adventure_embed(for_date=None, prefix="오늘의 모험섬"):
    data = get_calendar()
    arr = parse_adventure_islands(data, for_date)
    ds = (for_date or datetime.now(KST).date()).strftime("%m/%d %a")

    embed = discord.Embed(title=f"{prefix} ({ds})", color=0x2ecc71)
    embed.set_footer(text="데이터 출처: Lost Ark Open API")

    if not arr:
        embed.description = "해당 날짜의 모험섬 정보가 없습니다."
        return embed

    for it in arr:
        t_str = " / ".join(d.strftime("%H:%M") for d in it["times"])
        msg = [f"시간: {t_str}"]
        if it["desc"]: msg.append(f"메모: {it['desc']}")
        msg.append(rewards_to_text(it["rewards"]))
        embed.add_field(name=it["name"], value="\n".join(msg), inline=False)

    return embed


# ──────────────────────────────────────────────────────────────────────────────
# Discord 이벤트
# ──────────────────────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone=KST)

@bot.event
async def on_ready():
    logging.info(f"로그인 성공: {bot.user} ({bot.user.id})")

    scheduler.add_job(send_island_info, CronTrigger(hour=6, minute=1))
    scheduler.start()

    try:
        await bot.tree.sync()
    except Exception as e:
        logging.warning(f"Slash sync 실패: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Slash 커맨드
# ──────────────────────────────────────────────────────────────────────────────
@bot.tree.command(name="island")
async def island_today(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send(embed=build_adventure_embed())


@bot.tree.command(name="island_tomorrow")
async def island_tomorrow(interaction: discord.Interaction):
    await interaction.response.defer()
    tomorrow = (datetime.now(KST) + timedelta(days=1)).date()
    await interaction.followup.send(embed=build_adventure_embed(for_date=tomorrow, prefix="내일 모험섬"))


# ──────────────────────────────────────────────────────────────────────────────
# 메시지 보내기 (06:01 자동)
# ──────────────────────────────────────────────────────────────────────────────
async def send_island_info():
    ch = bot.get_channel(CHANNEL_ID)
    if ch:
        await ch.send(embed=build_adventure_embed())
    else:
        logging.error("채널을 찾을 수 없습니다. DISCORD_CHANNEL_ID 확인 필요.")


# ──────────────────────────────────────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
