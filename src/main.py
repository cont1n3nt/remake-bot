import asyncio

import discord
from discord.ext import commands

from src.config.config import TOKEN

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


async def load_extensions():
    await bot.load_extension("commands.profile")


@bot.event
async def on_ready():
    print("-" * 40)
    print(f"✅ Бот запущен как {bot.user}")
    print(f"ID: {bot.user.id}")

    try:
        synced = await bot.tree.sync()
        print(f"✅ Slash-команд: {len(synced)}")
    except Exception as e:
        print(e)

    print("-" * 40)


async def main():
    async with bot:
        await load_extensions()
        await bot.start(TOKEN)


asyncio.run(main())