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

# 🔥 스케줄러는 전역에서 1개만 생성
scheduler = AsyncIOScheduler(timezone=KST)

# 간단 캐시(당일 1회) – 필요하면 나중에 다시 켤 수 있음
_calendar_cache_date = None
_calendar_cache_data = None

# ──────────────────────────────────────────────────────────────────────────────
# API 호출 / 데이터 유틸
# ──────────────────────────────────────────────────────────────────────────────
def get_calendar():
    """
    Lost Ark 캘린더 전체(주간) 응답을 가져옴.
    (지금은 캐시를 너무 믿지 않도록 매번 새로 불러옴)
    """
    headers = {
        "accept": "application/json",
        "authorization": f"bearer {LOSTARK_JWT}",
    }
    r = requests.get(API_URL, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()

    # 디버그용: 카테고리 몇 개만 로그 찍어보기
    try:
        cats = list({
            (d.get("Category") or d.get("CategoryName") or "None")
            for d in data if isinstance(d, dict)
        })
        logging.info(f"[DEBUG] Calendar Category 샘플: {cats[:5]}")
    except Exception as e:
        logging.warning(f"[DEBUG] 카테고리 로그 중 에러: {e}")

    return data


def rewards_to_text(rewards):
    """
    RewardItems가 dict/list로 중첩돼 있어도 Name(또는 RewardName)을 모두 수집한다.
    """
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
    names = [n.strip() for n in names if n and n.strip()]
    if names:
        return "보상: " + ", ".join(sorted(set(names)))
    else:
        return "보상: (이벤트 데이터 없음)"


def parse_adventure_islands(data, date=None):
    """
    캘린더 응답(data)에서 특정 날짜(KST)의 '모험섬'만 추출하여
    [{name, desc, times[List[datetime]], rewards}, ...] 형태로 반환.
    """
    if date is None:
        date = datetime.now(KST).date()

    out = []
    if not isinstance(data, list):
        logging.warning("[DEBUG] calendar data가 list가 아님")
        return out

    for e in data:
        if not isinstance(e, dict):
            continue

        raw_cat = (e.get("Category") or e.get("CategoryName") or "")
        cat = str(raw_cat).replace(" ", "").lower()

        # 🔥 필터를 살짝 느슨하게: '모험섬' 또는 'adventure' & 'island'
        if not ("모험섬" in cat or ("adventure" in cat and "island" in cat)):
            continue

        name = e.get("ContentsName") or e.get("Title") or "모험섬"
        desc = e.get("ContentsNote") or e.get("Description") or ""
        rewards = e.get("RewardItems") or e.get("Rewards")

        times = e.get("StartTimes") or e.get("StartTime") or []
        if not isinstance(times, list):
            times = [times]

        day_times = []
        for t in times:
            try:
                t_str = str(t).strip()
                # 끝에 Z 붙은 경우 → UTC
                if t_str.endswith("Z"):
                    t_str = t_str.replace("Z", "+00:00")
                dt = datetime.fromisoformat(t_str)

                if dt.tzinfo is None:
                    # 타임존 없으면 일단 KST로 가정
                    dt = KST.localize(dt)
                else:
                    dt = dt.astimezone(KST)

                if dt.date() == date:
                    day_times.append(dt)
            except Exception as ex:
                logging.warning(f"[DEBUG] 시간 파싱 실패: {t} / {ex}")
                continue

        if day_times:
            out.append({
                "name": name,
                "desc": desc,
                "times": sorted(day_times),
                "rewards": rewards,
            })

    logging.info(f"[DEBUG] parse_adventure_islands 결과 개수: {len(out)}")
    return sorted(out, key=lambda x: x["times"][0]) if out else []


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
        embed.description = "해당 날짜의 모험섬 정보가 없습니다. (API에 모험섬이 없거나 파싱 실패)"
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
async def send_island_info():
    logging.info("[DEBUG] send_island_info 호출됨")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(embed=build_adventure_embed())
    else:
        logging.error("채널을 찾지 못했습니다. DISCORD_CHANNEL_ID를 확인하세요.")


@bot.event
async def on_ready():
    logging.info(f"✅ 로그인 성공: {bot.user} (ID: {bot.user.id})")

    # 🔥 스케줄러 중복 등록 방지 + 시간대 확실히 KST로 고정
    if not scheduler.get_jobs():
        scheduler.add_job(
            send_island_info,
            CronTrigger(hour=6, minute=1, timezone=KST)
        )
        scheduler.start()
        logging.info("[DEBUG] Scheduler started (06:01 KST)")

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

# 🔍 디버그용: 지금 API에 모험섬이 어떻게 찍히는지 확인하는 명령
@bot.tree.command(name="island_debug", description="모험섬 원시 데이터 디버그용 출력")
async def island_debug(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    data = get_calendar()
    lines = []
    for e in data:
        cat = (e.get("Category") or e.get("CategoryName") or "")
        name = e.get("ContentsName") or e.get("Title") or "-"
        if "모험" in str(cat) or "adventure" in str(cat).lower():
            lines.append(f"{cat} / {name}")
    txt = "\n".join(lines) or "모험 관련 항목이 없습니다."
    await interaction.followup.send(f"```{txt[:1900]}```")

# ──────────────────────────────────────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not DISCORD_TOKEN or not CHANNEL_ID or not LOSTARK_JWT:
        raise SystemExit(".env의 DISCORD_TOKEN / DISCORD_CHANNEL_ID / LOSTARK_JWT 를 채워주세요.")
    bot.run(DISCORD_TOKEN)


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