"""Запрос скриншота после публикации заявки.

Перенесено из bot/cogs/tickets.py без изменений (REFACTORING_PLAN.md,
Этап F.4c). Единственный из четырёх views_*-модулей без зависимостей от
соседних подмодулей пакета tickets."""

import asyncio
import logging

import discord

logger = logging.getLogger("bot")


class ScreenshotPromptView(discord.ui.View):

    def __init__(self, user_id: int, target_message: discord.Message, original_embed: discord.Embed):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.target_message = target_message
        self.original_embed = original_embed

    @discord.ui.button(label="📎 Жду скриншот", style=discord.ButtonStyle.primary, custom_id="screenshot_wait")
    async def wait_screenshot(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это не ваша заявка.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="📷 **Отправьте изображение в этот чат.** После получения скриншот будет добавлен к заявке.",
            view=self,
        )

        def check(m: discord.Message) -> bool:
            return m.author.id == self.user_id and m.channel.id == interaction.channel_id

        try:
            msg = await interaction.client.wait_for("message", timeout=120.0, check=check)
        except asyncio.TimeoutError:
            try:
                await interaction.edit_original_response(content="⏱ Время ожидания скриншота истекло.")
            except Exception:
                pass
            return

        image_files = []
        for att in msg.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                fp = await att.to_file()
                image_files.append(fp)

        if not image_files:
            try:
                await msg.reply("❌ Пожалуйста, прикрепите файл с изображением.", delete_after=10)
            except Exception:
                pass
            for child in self.children:
                child.disabled = False
            try:
                await interaction.edit_original_response(
                    content="📷 **Прикрепите изображение следующим сообщением.**",
                    view=self,
                )
            except Exception:
                pass
            return

        self.original_embed.set_image(url=f"attachment://{image_files[0].filename}")
        try:
            await self.target_message.edit(embed=self.original_embed, attachments=image_files[:1])
            if len(image_files) > 1:
                await self.target_message.reply(
                    f"📎 **Дополнительные скриншоты:** прикреплено ещё {len(image_files) - 1} файл(ов).",
                )
        except (discord.HTTPException, discord.Forbidden) as e:
            logger.warning("Failed to attach screenshot: %s", e)
            try:
                await interaction.followup.send("⚠️ Не удалось прикрепить скриншот.", ephemeral=True)
            except Exception:
                pass

        try:
            await interaction.edit_original_response(content="✅ Скриншот прикреплён.", view=None)
        except Exception:
            pass

        try:
            await msg.delete()
        except Exception:
            pass

    @discord.ui.button(label="⏭ Пропустить", style=discord.ButtonStyle.secondary, custom_id="screenshot_skip")
    async def skip_screenshot(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это не ваша заявка.", ephemeral=True)
            return
        await interaction.response.edit_message(content="✅ Заявка опубликована без скриншота.", view=None)
