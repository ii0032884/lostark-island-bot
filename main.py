# ──────────────────────────────────────────────────────────────────────────────
# HTTP Health Check (Flask) → Render Free Sleep 방지
# ──────────────────────────────────────────────────────────────────────────────
from flask import Flask
import threading, os

app = Flask(__name__)

@app.route("/")
def home():
    return "OK", 200

def run_server():
    # Render 환경의 PORT 우선 사용
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# 서버 실행을 별도 스레드에서 구동
threading.Thread(target=run_server, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────
# Discord Bot + Scheduler
# ──────────────────────────────────────────────────────────────────────────────
import logging
import pytz
import requests
import os
from datetime import datetime, timedelta

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
LOSTARK_JWT = os.getenv("LOSTARK_JWT")

# 타임존 설정
KST = pytz.timezone("Asia/Seoul")
API_URL = "https://developer-lostark.game.onstove.com/gamecontents/calendar"

logging.basicConfig(level=logging.INFO)

# Discord Intents 설정
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ──────────────────────────────────────────────────────────────────────────────
# Lost Ark API 함수 (2026년 날짜 대응 및 캐시 보강)
# ──────────────────────────────────────────────────────────────────────────────
_calendar_cache_date = None
_calendar_cache_data = None

def get_calendar():
    """API 요청 및 날짜별 캐싱"""
    global _calendar_cache_date, _calendar_cache_data

    now_kst = datetime.now(KST)
    today = now_kst.date()

    # 날짜가 바뀌었으면 캐시 강제 초기화
    if _calendar_cache_date != today:
        _calendar_cache_date = today
        _calendar_cache_data = None

    if _calendar_cache_data:
        return _calendar_cache_data

    headers = {
        "accept": "application/json",
        "authorization": f"bearer {LOSTARK_JWT}",
    }
    
    try:
        r = requests.get(API_URL, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        _calendar_cache_data = data
        logging.info(f"[API] {today} 데이터 로드 및 캐싱 완료")
        return data
    except Exception as e:
        logging.error(f"[API 에러] {e}")
        return []


def rewards_to_text(rewards):
    """보상 리스트 파싱 및 diff 포맷팅"""
    if not rewards:
        return "보상: (정보 없음)"

    names = []
    def extract(o):
        if isinstance(o, dict):
            if o.get("Name"): names.append(str(o["Name"]))
            if o.get("RewardName"): names.append(str(o["RewardName"]))
            if o.get("Item") and isinstance(o["Item"], dict) and o["Item"].get("Name"):
                names.append(str(o["Item"]["Name"]))
            for v in o.values(): extract(v)
        elif isinstance(o, list):
            for x in o: extract(x)

    extract(rewards)
    names = [n.strip() for n in names if n.strip()]

    if not names:
        return "보상: (정보 없음)"

    gold = [n for n in names if "골드" in n]
    other = [n for n in names if n not in gold]

    lines = [f"- {n}" for n in sorted(set(gold))]
    lines += [f"  {n}" for n in sorted(set(other))]

    return "보상:\n```diff\n" + "\n".join(lines) + "\n```"


def parse_adventure_islands(data, date=None):
    """날짜 매칭 로직 강화 (UTC -> KST 변환 포함)"""
    if date is None:
        date = datetime.now(KST).date()

    out = []
    for e in data:
        cat = (e.get("CategoryName") or "").lower()
        name = e.get("ContentsName") or ""
        
        # '모험 섬' 판별
        if "모험" in cat and "섬" in cat:
            times = e.get("StartTimes") or e.get("StartTime") or []
            if not isinstance(times, list):
                times = [times]

            valid_times = []
            for t in times:
                try:
                    # ISO 포맷 파싱 (Z를 +00:00으로 보정)
                    dt_raw = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                    # 타임존이 지정되어 있다면 KST로 변환, 없으면 KST 주입
                    if dt_raw.tzinfo:
                        dt_kst = dt_raw.astimezone(KST)
                    else:
                        dt_kst = KST.localize(dt_raw)

                    # 요청받은 날짜와 일치하는지 확인
                    if dt_kst.date() == date:
                        valid_times.append(dt_kst)
                except:
                    continue

            if valid_times:
                out.append({
                    "name": name,
                    "desc": e.get("ContentsNote") or "",
                    "times": sorted(list(set(valid_times))),
                    "rewards": e.get("RewardItems") or e.get("Rewards")
                })

    out.sort(key=lambda x: x["times"][0] if x["times"] else datetime.max.replace(tzinfo=KST))
    return out


def build_adventure_embed(for_date=None, prefix="오늘의 모험섬"):
    """디스코드 임베드 생성"""
    data = get_calendar()
    arr = parse_adventure_islands(data, for_date)

    target_date = for_date or datetime.now(KST).date()
    ds = target_date.strftime("%m/%d (%a)")

    embed = discord.Embed(title=f"{prefix} {ds}", color=0x2ecc71)
    embed.set_footer(text="데이터 출처: Lost Ark OpenAPI")

    if not arr:
        embed.description = "해당 날짜에 예정된 모험섬이 없습니다."
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
# 자동 발송 기능 (Scheduler)
# ──────────────────────────────────────────────────────────────────────────────
send_history = {}
TARGET_TIMES = ["06:01", "08:50", "10:50", "12:50", "18:50", "20:50", "22:50"]

async def daily_check():
    now = datetime.now(KST)
    today = now.date()
    current_time = now.strftime("%H:%M")

    if current_time in TARGET_TIMES:
        if send_history.get(current_time) == today:
            return

        ch = bot.get_channel(CHANNEL_ID)
        if ch:
            embed = build_adventure_embed()
            await ch.send(embed=embed)
            logging.info(f"[자동 발송] {current_time} 완료")
            send_history[current_time] = today


# ──────────────────────────────────────────────────────────────────────────────
# Bot Events & Commands
# ──────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    logging.info(f"로그인 성공: {bot.user}")

    # APScheduler 설정
    scheduler = AsyncIOScheduler(timezone=KST)
    scheduler.add_job(daily_check, "interval", minutes=1)
    scheduler.start()

    try:
        await bot.tree.sync()
        logging.info("Slash commands synced.")
    except Exception as e:
        logging.error(f"Sync error: {e}")


@bot.tree.command(name="island", description="오늘의 모험섬 정보를 출력합니다.")
async def island_today(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = build_adventure_embed()
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="island_tomorrow", description="내일의 모험섬 정보를 확인합니다.")
async def island_tomorrow(interaction: discord.Interaction):
    await interaction.response.defer()
    tomorrow = (datetime.now(KST) + timedelta(days=1)).date()
    embed = build_adventure_embed(for_date=tomorrow, prefix="내일의 모험섬")
    await interaction.followup.send(embed=embed)


# 실행
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN is missing!")
    else:
        bot.run(DISCORD_TOKEN)







