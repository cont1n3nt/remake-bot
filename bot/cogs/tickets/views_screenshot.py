"""Подсказка про скриншот после публикации заявки.

Раньше здесь был собственный `wait_for("message")`, который сам скачивал
вложение, редактировал карточку и удалял сообщение. Ровно то же самое делает
слушатель `TicketCog.on_message`, поэтому на один скриншот срабатывали оба
обработчика — карточка правилась дважды, а в канал летели одинаковые
сообщения подряд (пункты 7, 8).

Теперь вся обработка скриншота живёт в одном месте — `TicketCog.on_message`, —
а это представление лишь показывает подсказку и даёт её закрыть.
"""

import logging

import discord

logger = logging.getLogger("bot")


class ScreenshotPromptView(discord.ui.View):

    def __init__(self, user_id: int):
        super().__init__(timeout=600)
        self.user_id = user_id

    @discord.ui.button(label="⏭ Пропустить", style=discord.ButtonStyle.secondary, custom_id="screenshot_skip")
    async def skip_screenshot(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это не ваша заявка.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content="✅ Заявка опубликована без скриншота. "
                    "Скриншот можно отправить в этот канал в любой момент — "
                    "бот сам добавит его в заявку.",
            view=None,
        )
