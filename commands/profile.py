import discord
from discord.ext import commands

class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def profile(self, ctx):
        await ctx.send("Профиль работает!")

async def setup(bot):
    await bot.add_cog(Profile(bot))

async def setup(bot):
    await bot.add_cog(Profile(bot))