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

# 간단 캐시(당일 1회)
_calendar_cache_date = None
_calendar_cache_data = None

# ──────────────────────────────────────────────────────────────────────────────
# API 호출 / 데이터 유틸
# ──────────────────────────────────────────────────────────────────────────────
def get_calendar():
    """Lost Ark 캘린더 전체(주간) 응답을 가져오고, 당일 기준으로 캐시한다."""
    global _calendar_cache_date, _calendar_cache_data
    today = datetime.now(KST).date()
    if _calendar_cache_date == today and _calendar_cache_data is not None:
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
    """
    RewardItems 중 '골드/Gold' 포함 항목은 diff 코드블록의 빨간 줄로 강조.
    그 외 보상은 일반 줄로 표시.
    """
    if not rewards:
        return "보상: (정보 없음)"

    # 1) 보상 이름들 추출 (중첩 구조 커버)
    names = []
    def extract(obj):
        if isinstance(obj, dict):
            if "Name" in obj and obj["Name"]:
                names.append(str(obj["Name"]))
            if "RewardName" in obj and obj["RewardName"]:
                names.append(str(obj["RewardName"]))
            for v in obj.values():
                extract(v)
        elif isinstance(obj, list):
            for x in obj:
                extract(x)
    extract(rewards)

    names = [n.strip() for n in names if n and n.strip()]
    if not names:
        return "보상: (이벤트 데이터 없음)"

    # 2) '골드' 항목만 분리
    def is_gold(s: str) -> bool:
        s_low = s.lower()
        return ("gold" in s_low) or ("골드" in s)

    gold = [n for n in names if is_gold(n)]
    others = [n for n in names if not is_gold(n)]

    # 3) diff 코드블록으로 빨간 줄 연출
    #   - 골드:   '- ' 접두사 → 빨간색
    #   - 나머지: 앞에 공백 두 칸(색없음)
    lines = [f"- {n}" for n in sorted(set(gold))]
    lines += [f"  {n}" for n in sorted(set(others))]
    block = "```diff\n" + "\n".join(lines) + "\n```"
    return "보상:\n" + block



def parse_adventure_islands(data, date=None):
    """
    캘린더 응답(data)에서 특정 날짜(KST)의 '모험섬'만 추출하여
    [{name, desc, times[List[datetime]], rewards}, ...] 형태로 반환.
    """
    if date is None:
        date = datetime.now(KST).date()

    out = []
    if not isinstance(data, list):
        return out

    for e in data:
        cat = (e.get("Category") or e.get("CategoryName") or "").lower()
        if ("모험" in cat and "섬" in cat) or ("adventure" in cat and "island" in cat):
            name = e.get("ContentsName") or e.get("Title") or "모험섬"
            desc = e.get("ContentsNote") or e.get("Description") or ""
            rewards = e.get("RewardItems") or e.get("Rewards")

            times = e.get("StartTimes") or e.get("StartTime") or []
            if not isinstance(times, list):
                times = [times]

            day_times = []
            for t in times:
                try:
                    dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = KST.localize(dt)
                    else:
                        dt = dt.astimezone(KST)
                    if dt.date() == date:
                        day_times.append(dt)
                except Exception:
                    # 형식이 특이한 항목은 건너뜀
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


def build_adventure_embed(for_date=None, title_prefix="오늘의 모험섬"):
    """모험섬 임베드(시간 + 보상 포함) 생성."""
    data = get_calendar()
    islands = parse_adventure_islands(data, for_date)
    date_str = (for_date or datetime.now(KST).date()).strftime("%m/%d %a")

    embed = discord.Embed(
        title=f"{title_prefix} ({date_str})",
        color=0x2ecc71
    )
    embed.set_footer(text="데이터 출처: Lost Ark OpenAPI (매일 06:00 KST 초기화)")

    if not islands:
        embed.description = "해당 날짜의 모험섬 정보가 없습니다."
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
# 디스코드 봇 이벤트/명령
# ──────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    logging.info(f"✅ 로그인 성공: {bot.user} (ID: {bot.user.id})")

    # 매일 06:01 KST 자동 알림
    scheduler = AsyncIOScheduler(timezone=KST)
    scheduler.add_job(send_island_info, CronTrigger(hour=6, minute=1))
    scheduler.start()

    # 슬래시 커맨드 동기화
    try:
        await bot.tree.sync()
        logging.info("Slash commands synced.")
    except Exception as e:
        logging.warning(f"Slash sync failed: {e}")


@bot.tree.command(name="island", description="오늘의 모험섬(시간/보상) 정보를 보여줍니다.")
async def island_today(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    embed = build_adventure_embed()
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="island_tomorrow", description="내일 모험섬(시간/보상) 미리보기.")
async def island_tomorrow(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    tomorrow = (datetime.now(KST) + timedelta(days=1)).date()
    embed = build_adventure_embed(for_date=tomorrow, title_prefix="내일의 모험섬")
    await interaction.followup.send(embed=embed)


async def send_island_info():
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(embed=build_adventure_embed())
    else:
        logging.error("채널을 찾지 못했습니다. DISCORD_CHANNEL_ID를 확인하세요.")

# ──────────────────────────────────────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN or not CHANNEL_ID or not LOSTARK_JWT:
        raise SystemExit(".env의 DISCORD_TOKEN / DISCORD_CHANNEL_ID / LOSTARK_JWT 를 채워주세요.")
    bot.run(DISCORD_TOKEN)

    from apscheduler.triggers.date import DateTrigger
from datetime import datetime, timedelta

@bot.event
async def on_ready():
    logging.info(f"✅ 로그인 성공: {bot.user} (ID: {bot.user.id})")

    scheduler = AsyncIOScheduler(timezone=KST)
    # ▶ 매일 06:01 정식 알림
    scheduler.add_job(send_island_info, CronTrigger(hour=6, minute=1))
    # ▶ 테스트용: 실행 후 1분 뒤 한 번 보내보기
    scheduler.add_job(send_island_info, DateTrigger(run_date=datetime.now(KST)+timedelta(minutes=1)))
    scheduler.start()
    ...

