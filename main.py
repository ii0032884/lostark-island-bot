import os
import discord
import asyncio
import requests
from datetime import datetime
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
JWT = os.getenv("LOSTARK_JWT")

intents = discord.Intents.default()
client = discord.Client(intents=intents)


# ===========================
#  LostArk API 호출 함수
# ===========================
def get_adventure_island_info():
    url = "https://developer-lostark.game.onstove.com/gamecontents/calendar"
    headers = {
        "accept": "application/json",
        "authorization": f"bearer {JWT}",
    }

    try:
        res = requests.get(url, headers=headers)
        data = res.json()

        # Adventure Island 필터
        islands = [d for d in data if d["CategoryName"] == "모험 섬"]

        if len(islands) == 0:
            return "오늘 모험섬 정보 없음."

        msg = "📢 **오늘의 모험섬 정보**\n\n"
        for i in islands:
            msg += f"■ **{i['ContentsName']}**\n"
            msg += f"- 시간: {i['StartTimes'][0].replace('T', ' ')}\n"
            msg += f"- 보상: {', '.join(i['RewardItems'])}\n\n"

        return msg

    except Exception as e:
        return f"API 호출 오류: {e}"


# ===========================
#  매일 06:01에 자동 전송
# ===========================
@tasks.loop(minutes=1)
async def daily_notice():
    now = datetime.utcnow().strftime("%H:%M")
    # 한국시간 06:01 → UTC 기준 21:01 (전날)
    if now == "21:01":  
        channel = client.get_channel(CHANNEL_ID)
        if channel is not None:
            msg = get_adventure_island_info()
            await channel.send(msg)


@client.event
async def on_ready():
    print(f"로그인됨: {client.user}")
    daily_notice.start()


# ===========================
#      실행
# ===========================
client.run(TOKEN)


bot.run(TOKEN)



