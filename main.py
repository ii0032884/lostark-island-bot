import discord
import asyncio
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz

TOKEN = "YOUR_TOKEN_HERE"  # <-- 토큰 넣기
CHANNEL_ID = 000000000000  # <-- 알림 보낼 채널 ID 넣기

KST = pytz.timezone("Asia/Seoul")

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())


# -----------------------------
# 모험섬 정보 & 보상 문자열 만들기
# -----------------------------
def get_adventure_island_message():
    island_name = "오늘의 모험섬"
    rewards = [
        "모험물 : 죽은자의 눈",
        "비밀지도",
        "수신 아포라스 카드",
        "실링",
        "영혼의 잎사귀",
        "전설 ~ 고급 카드 팩 III",
        "전설 ~ 고급 카드 팩 IV",
        "죽음의 협곡 섬의 마음",
    ]

    reward_text = "\n".join([f"- {r}" for r in rewards])

    msg = (
        f"🌴 **{island_name} 정보 안내**\n"
        f"⏰ 시간: 20:00 / 22:00 (그날 기준)\n"
        f"🎁 보상 목록:\n{reward_text}"
    )
    return msg


# -----------------------------
# 시간 맞춰 보내는 스케줄러
# -----------------------------
async def schedule_daily_task(target_time):
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)

    while not bot.is_closed():
        now = datetime.now(KST)
        target = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)

        # 이미 시간이 지났으면 내일 같은 시간
        if now > target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        # 메시지 보내기
        if channel:
            await channel.send(get_adventure_island_message())


# -----------------------------
# 봇 켜질 때 스케줄러 3개 실행
# -----------------------------
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")

    asyncio.create_task(schedule_daily_task(datetime.strptime("06:01", "%H:%M")))
    asyncio.create_task(schedule_daily_task(datetime.strptime("07:00", "%H:%M")))
    asyncio.create_task(schedule_daily_task(datetime.strptime("08:00", "%H:%M")))


# -----------------------------
# 테스트용 명령어
# -----------------------------
@bot.command()
async def 모험(ctx):
    """수동으로 출력"""
    await ctx.send(get_adventure_island_message())


bot.run(TOKEN)


