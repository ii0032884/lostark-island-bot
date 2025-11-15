# main.py
import os
import logging
from datetime import datetime, timedelta

import pytz
import requests
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

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
bot = commands.Bot(command_prefix="!", intents=intents)
logging.basicConfig(level=logging.INFO)

# ──────────────────────────────────────────────────────────────────────────────
# 🔥 캐시 삭제(빈 응답이 하루 종일 고정되는 문제 제거)
# ──────────────────────────────────────────────────────────────────────────────
_calendar_cache_date = None
_calendar_cache_data = None


# ──────────────────────────────────────────────────────────────────────────────
# API 호출 / 데이터 유틸
# ──────────────────────────────────────────────────────────────────────────────
def get_calendar():
    """Lost Ark 캘린더 전체(주간) 응답을 가져오고, 당일 기준으로 캐시한다."""
    global _calendar_cache_date, _calendar_cache_data
    today = datetime.now(KST).date()

    # 🔥 캐시 비활성화 (API 변경 대응 위해)
    _calendar_cache_date = None
    _calendar_cache_data = None

    headers = {
        "accept": "application/json",
        "authorization": f"bearer {LOSTARK_JWT}",
    }
    r = requests.get(API_URL, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data


# ──────────────────────────────────────────────────────────────────────────────
# 보상 처리
# ──────────────────────────────────────────────────────────────────────────────
def rewards_to_text(rewards):
    """중첩 구조에서도 모든 보상 이름(Name, RewardName)을 추출."""
    if not rewards:
        return "보상: (정보 없음)"

    names = []

    def extract(obj):
        if isinstance(obj, dict):
            if obj.get("Name"):
                names.append(str(obj["Name"]))
            if obj.get("RewardName"):
                names.append(str(obj["RewardName"]))
            for v in obj.values():
                extract(v)
        elif isinstance(obj, list):
            for x in obj:
                extract(x)

    extract(rewards)
    names = [n.strip() for n in names if n.strip()]
    if names:
        return "보상: " + ", ".join(sorted(set(names)))
    return "보상: (이벤트 데이터 없음)"


# ──────────────────────────────────────────────────────────────────────────────
# 🔥 모험섬 파싱 최소 수정(핵심 문제 fix)
# ──────────────────────────────────────────────────────────────────────────────
def parse_adventure_islands(data, date=None):
    """
    캘린더 응답(data)에서 특정 날짜(KST)의 '모험섬'만 추출.
    """
    if date is None:
        date = datetime.now(KST).date()

    out = []
    if not isinstance(data, list):
        return out

    for e in data:
        # 🔥 Category 인식 강화 (공백 삭제 + 한글 정상화)
        cat = (e.get("Category") or e.get("CategoryName") or "")
        cat_norm = cat.replace(" ", "").lower()

        if not (
            "모험섬" in cat_norm
            or ("adventure" in cat_norm and "island" in cat_norm)
        ):
            continue

        name = e.get("ContentsName") or e.get("Title") or "모험섬"
        desc = e.get("ContentsNote") or e.get("Description") or ""
        rewards = e.get("RewardItems") or e.get("Rewards")

        times = e.get("StartTimes") or e.get("StartTime") or []

        # 🔥 단일 문자열 시간도 리스트로 변환
        if not isinstance(times, list):
            times = [times]

        day_times = []
        for t in times:
            try:
                # 🔥 공백/대문자 Z 처리
                t = str(t).replace(" ", "").replace("Z", "+00:00")

                dt = datetime.fromisoformat(t)
                if dt.tzinfo is None:
                    dt = KST.localize(dt)
                else:
                    dt = dt.astimezone(KST)

                if dt.date() == date:
                    day_times.append(dt)

            except Exception:
                continue

        if day_times:
            out.append({
                "name": name,
                "desc": desc,
                "times": sorted(day_times),
                "rewards": rewards,
            })

    out.sort(key=lambda x: x["times"][0])
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 임베드 생성
# ──────────────────────────────────────────────────────────────────────────────
def build_adventure_embed(for_date=None, title_prefix="오늘의 모험섬"):
    data = get_calendar()
    islands = parse_adventure_islands(data, for_date)
    date_str = (for_date or datetime.now(KST).date()).strftime("%m/%d %a")

    embed = discord.Embed(
        title=f"{title_prefix} ({date_str})",
        color=0x2ecc71,
    )

    if not islands:
        embed.description = "⚠ 오늘 모험섬 데이터가 없습니다. (API 응답 없음)"
        return embed

    for it in islands:
        times_str = " / ".join(dt.strftime("%H:%M") for dt in it["times"])
        lines = [f"시간: {times_str}"]
        if it["desc"]:
            lines.append(f"메모: {it['desc']}")
        lines.append(rewards_to_text(it["rewards"]))
        embed.add_field(name=it["name"], value="\n".join(lines), inline=False)

    return embed


# ──────────────────────────────────────────────────────────────────────────────
# 디스코드 이벤트
# ──────────────────────────────────────────────────────────────────────────────
async def send_island_info():
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(embed=build_adventure_embed())
    else:
        logging.error("채널을 찾지 못했습니다. DISCORD_CHANNEL_ID를 확인하세요.")


@bot.event
async def on_ready():
    logging.info(f"✅ 로그인 성공: {bot.user} (ID: {bot.user.id})")

    scheduler = AsyncIOScheduler(timezone=KST)
    scheduler.add_job(send_island_info, CronTrigger(hour=6, minute=1))
    scheduler.start()

    try:
        await bot.tree.sync()
        logging.info("Slash commands synced.")
    except Exception as e:
        logging.warning(f"Slash sync failed: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Slash Command
# ──────────────────────────────────────────────────────────────────────────────
@bot.tree.command(name="island", description="오늘의 모험섬(시간/보상)")
async def island_today(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    await interaction.followup.send(embed=build_adventure_embed())


@bot.tree.command(name="island_tomorrow", description="내일 모험섬 미리보기")
async def island_tomorrow(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    tomorrow = (datetime.now(KST) + timedelta(days=1)).date()
    await interaction.followup.send(
        embed=build_adventure_embed(for_date=tomorrow, title_prefix="내일의 모험섬")
    )


# ──────────────────────────────────────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN or not CHANNEL_ID or not LOSTARK_JWT:
        raise SystemExit("DISCORD_TOKEN / DISCORD_CHANNEL_ID / LOSTARK_JWT 확인 필수")
    bot.run(DISCORD_TOKEN)