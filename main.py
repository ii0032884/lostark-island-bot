###############################################
# LOST ARK DISCORD BOT (Render Free Web Service)
# Flask 제거 / Socket 서버로 포트 유지 / Bot 단일 프로세스
###############################################

import threading
import socket
import os

# ─────────────────────────────────────────────
# 포트 열어서 Render Web Service 정상 유지
# ─────────────────────────────────────────────
def keep_render_alive():
    port = int(os.environ.get("PORT", 10000))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(5)

    while True:
        conn, addr = s.accept()
        try:
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK")
        except:
            pass
        conn.close()

threading.Thread(target=keep_render_alive, daemon=True).start()


# ─────────────────────────────────────────────
# 이하 Discord Bot (Flask 없음)
# ─────────────────────────────────────────────

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

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
logging.basicConfig(level=logging.INFO)

scheduler = AsyncIOScheduler(timezone=KST)

# ─────────────────────────────────────────────
# Lost Ark API 처리
# ─────────────────────────────────────────────
_calendar_cache_date = None
_calendar_cache_data = None

def get_calendar():
    global _calendar_cache_data, _calendar_cache_date
    today = datetime.now(KST).date()

    if _calendar_cache_date == today and _calendar_cache_data:
        return _calendar_cache_data

    r = requests.get(API_URL, headers={
        "accept": "application/json",
        "authorization": f"bearer {LOSTARK_JWT}"
    }, timeout=15)

    r.raise_for_status()
    data = r.json()
    _calendar_cache_data, _calendar_cache_date = data, today
    return data


def rewards_to_text(rewards):
    if not rewards:
        return "보상: (정보 없음)"

    names = []

    def extract(o):
        if isinstance(o, dict):
            if o.get("Name"): names.append(str(o["Name"]))
            if o.get("RewardName"): names.append(str(o["RewardName"]))
            for v in o.values(): extract(v)
        elif isinstance(o, list):
            for x in o: extract(x)

    extract(rewards)
    names = [n for n in names if n.strip()]

    def is_gold(s):
        s2 = s.lower()
        return ("골드" in s) or ("gold" in s2)

    gold = [n for n in names if is_gold(n)]
    others = [n for n in names if not is_gold(n)]

    lines = [f"- {n}" for n in gold]
    lines += [f"  {n}" for n in others]

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
                out.append({
                    "name": name,
                    "desc": desc,
                    "times": sorted(valid),
                    "rewards": rewards,
                })

    out.sort(key=lambda x: x["times"][0])
    return out


def build_adventure_embed(for_date=None, prefix="오늘의 모험섬"):
    data = get_calendar()
    arr = parse_adventure_islands(data, for_date)
    ds = (for_date or datetime.now(KST).date()).strftime("%m/%d (%a)")

    embed = discord.Embed(title=f"{prefix} {ds}", color=0xA3BFFA)
    embed.set_footer(text="데이터 출처: Lost Ark OpenAPI")

    if not arr:
        embed.description = "해당 날짜의 모험섬 정보가 없습니다."
        return embed

    for it in arr:
        times = " / ".join(dt.strftime("%H:%M") for dt in it["times"])
        body = [
            f"시간: {times}",
            f"메모: {it['desc']}" if it["desc"] else "",
            rewards_to_text(it["rewards"])
        ]

        embed.add_field(name=it["name"], value="\n".join(body), inline=False)

    return embed


async def send_island_info():
    logging.info(f"[send_island_info] 시작, CHANNEL_ID={CHANNEL_ID}")

    ch = bot.get_channel(CHANNEL_ID)
    logging.info(f"[send_island_info] 채널 객체={ch} (type={type(ch)})")

    if not ch:
        logging.error("[send_island_info] 채널을 찾지 못했습니다. DISCORD_CHANNEL_ID를 확인하세요.")
        return

    try:
        embed = build_adventure_embed()
        logging.info(f"[send_island_info] embed 생성 완료: title={embed.title}")
        await ch.send(embed=embed)
        logging.info("[send_island_info] 메시지 전송 완료")
    except Exception as e:
        logging.exception(f"[send_island_info] 메시지 전송 중 예외 발생: {e}")


@bot.event
async def on_ready():
    logging.info(f"로그인 성공: {bot.user}")

    if not scheduler.get_jobs():
        scheduler.add_job(send_island_info, CronTrigger(hour=6, minute=1))
        scheduler.add_job(
            send_island_info,
            DateTrigger(run_date=datetime.now(KST) + timedelta(seconds=5)),
        )
        scheduler.start()

    await bot.tree.sync()


@bot.tree.command(name="island")
async def island_today(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send(embed=build_adventure_embed())


@bot.tree.command(name="island_tomorrow")
async def island_tomorrow(interaction: discord.Interaction):
    await interaction.response.defer()
    tomorrow = (datetime.now(KST) + timedelta(days=1)).date()
    await interaction.followup.send(embed=build_adventure_embed(for_date=tomorrow, prefix="내일의 모험섬"))


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
