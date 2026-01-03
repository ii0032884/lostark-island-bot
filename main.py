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
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_server, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────
# Discord Bot + Scheduler
# ──────────────────────────────────────────────────────────────────────────────
import logging
import pytz
import requests
from datetime import datetime, timedelta

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# env 읽기
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
LOSTARK_JWT = os.getenv("LOSTARK_JWT")

# 타임존 설정
KST = pytz.timezone("Asia/Seoul")
API_URL = "https://developer-lostark.game.onstove.com/gamecontents/calendar"

logging.basicConfig(level=logging.INFO)

# Discord 설정
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ──────────────────────────────────────────────────────────────────────────────
# Lost Ark API 함수들 (파싱 개선 및 2026년 날짜 보정)
# ──────────────────────────────────────────────────────────────────────────────
_calendar_cache_date = None
_calendar_cache_data = None

def get_calendar():
    """API 요청 + 캐싱"""
    global _calendar_cache_date, _calendar_cache_data
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
        return "보상: (이벤트 데이터 없음)"

    gold = [n for n in names if ("골드" in n or "gold" in n.lower())]
    other = [n for n in names if n not in gold]

    lines = [f"- {n}" for n in sorted(set(gold))]
    lines += [f"  {n}" for n in sorted(set(other))]

    return "보상:\n```diff\n" + "\n".join(lines) + "\n```"


def parse_adventure_islands(data, date=None):
    """타임존 보정 및 날짜 비교 로직 강화"""
    if date is None:
        date = datetime.now(KST).date()

    out = []

    for e in data:
        # 카테고리 또는 이름에서 '모험섬' 추출
        cat_name = e.get("CategoryName") or ""
        cont_name = e.get("ContentsName") or ""
        
        if "모험" in cat_name and "섬" in cat_name:
            times = e.get("StartTimes") or e.get("StartTime") or []
            if not isinstance(times, list):
                times = [times]

            valid_times = []
            for t in times:
                try:
                    # ISO 문자열 파싱 (Z를 +00:00으로 치환하여 표준화)
                    dt_raw = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                    
                    # [핵심] API 응답이 UTC 기반일 경우 KST로 변환하여 날짜 계산
                    # 만약 KST 기반인데 Z만 붙어있는 경우라면, 이 과정을 거쳐야 정확한 KST 날짜가 나옵니다.
                    dt_kst = dt_raw.astimezone(KST)

                    if dt_kst.date() == date:
                        valid_times.append(dt_kst)
                except Exception as ex:
                    logging.error(f"Time parsing error: {ex}")
                    continue

            if valid_times:
                out.append({
                    "name": cont_name,
                    "desc": e.get("ContentsNote") or "",
                    "times": sorted(valid_times),
                    "rewards": e.get("RewardItems") or e.get("Rewards")
                })

    out.sort(key=lambda x: x["times"][0] if x["times"] else datetime.min.replace(tzinfo=KST))
    return out


def build_adventure_embed(for_date=None, prefix="오늘의 모험섬"):
    try:
        data = get_calendar()
        arr = parse_adventure_islands(data, for_date)
    except Exception as e:
        logging.error(f"Build embed error: {e}")
        embed = discord.Embed(title="오류 발생", description="API 데이터를 가져올 수 없습니다.", color=0xff0000)
        return embed

    ds = (for_date or datetime.now(KST).date()).strftime("%m/%d (%a)")
    embed = discord.Embed(title=f"{prefix} {ds}", color=0x2ecc71)
    embed.set_footer(text="데이터 출처: Lost Ark OpenAPI")

    if not arr:
        embed.description = "해당 날짜 모험섬 정보가 없습니다."
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
# 자동 발송 설정
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
            logging.info(f"[자동 발송] {current_time} 전송 완료")
            send_history[current_time] = today
        else:
            logging.error(f"채널 ID {CHANNEL_ID}를 찾을 수 없습니다.")


# ──────────────────────────────────────────────────────────────────────────────
# Discord Event & Commands
# ──────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    logging.info(f"로그인 성공: {bot.user}")
    
    # 스케줄러 시작
    scheduler = AsyncIOScheduler(timezone=KST)
    scheduler.add_job(daily_check, "interval", minutes=1)
    scheduler.start()

    try:
        await bot.tree.sync()
        logging.info("슬래시 커맨드 동기화 완료")
    except Exception as e:
        logging.error(f"커맨드 동기화 에러: {e}")


@bot.tree.command(name="island", description="오늘 모험섬 출력")
async def island_today(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = build_adventure_embed()
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="island_tomorrow", description="내일 모험섬 미리보기")
async def island_tomorrow(interaction: discord.Interaction):
    await interaction.response.defer()
    tomorrow = (datetime.now(KST) + timedelta(days=1)).date()
    embed = build_adventure_embed(for_date=tomorrow, prefix="내일의 모험섬")
    await interaction.followup.send(embed=embed)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)









