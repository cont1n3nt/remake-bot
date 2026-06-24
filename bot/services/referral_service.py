from bot.repositories.sheets_repository import SheetsRepository

# Referral levels
THRESHOLDS = [1, 5, 10, 25, 100]
LEVEL_NAMES = [
    "",           # 0
    "🧭 Скаут",
    "📣 Промоутер",
    "🧲 Вербовщик",
    "📢 Амбассадор",
    "🎩 Рекламный Барон",
]
LEVEL_BONUSES = [
    "",
    "└ 🎁 🪙 1 Coin",
    "└ 🎁 🪙 5 Coins + ⚡ 10 XP",
    "└ 🎁 🪙 15 Coins\n└ 🛡 Закрепить 1 раз/нед",
    "└ 🎁 🪙 40 Coins + ⚡ 60 XP\n└ 📉 Скидка 0.5% на бусты",
    "└ 🎁 🪙 150 Coins\n└ 💸 🪙 0.1 с любой сделки\n└ 🎫 Промокод: -1.5% новичку",
]

# Rank thresholds and bonuses
RANK_THRESHOLDS = [50, 250, 1000, 5000, 10000]
RANK_NAMES = [
    "🔹 Standard",
    "🔷 Premium",
    "💠 Prestige",
    "💎 Elite",
    "👑 Legend",
]
RANK_BONUSES = [
    "└ 🎁 🪙 5 Coin",
    "└ 🎁 🪙 10 Coins\n└ ⚡ +5% XP\n└ 📊 Скидка 0.5% / Наценка 0.5%",
    "└ 🎁 🪙 40 Coins\n└ 🔥 +2 Coin за сделку >₽50М\n└ 📊 Скидка 1.5% / Наценка 1%\n└ ⏱ Приоритет",
    "└ 🎁 🪙 100 Coins\n└ 🔥 🪙5 за сделку >₽100М\n└ 📊 Скидка 3% / Наценка 1.5%\n└ ⏱ Приоритет + бронь",
    "└ 🎁 🪙 200 Coins\n└ 💸 🪙10/мес\n└ 📈 +1% от счёта/мес (≤🪙15)\n└ 📊 Скидка 5% / Наценка 2%\n└ 🚀 Без очереди, бронь, спец-заказ",
]


class ReferralService:

    def __init__(self, repo: SheetsRepository) -> None:
        self._repo = repo

    def get_referral_count(self, nickname: str) -> int:
        referrals = self._repo.find_referrals(nickname)
        return len(referrals)

    def get_referred_users(self, nickname: str) -> list[str]:
        referrals = self._repo.find_referrals(nickname)
        seen = set()
        result = []
        for ref in referrals:
            nick = ref["nickname"]
            if nick and nick not in seen:
                seen.add(nick)
                result.append(nick)
        return result

    def get_referral_level(self, count: int) -> int:
        level = 0
        for i, threshold in enumerate(THRESHOLDS):
            if count >= threshold:
                level = i + 1
            else:
                break
        return level

    def get_level_name(self, level: int) -> str:
        if 0 <= level < len(LEVEL_NAMES):
            return LEVEL_NAMES[level]
        return ""

    def get_level_bonus(self, level: int) -> str:
        if 0 <= level < len(LEVEL_BONUSES):
            return LEVEL_BONUSES[level]
        return ""

    def get_next_level_progress(self, count: int) -> tuple[int, int, str]:
        for i, threshold in enumerate(THRESHOLDS):
            if count < threshold:
                prev = THRESHOLDS[i - 1] if i > 0 else 0
                return (count - prev, threshold - prev, LEVEL_NAMES[i])
        return (count, count, "")

    def get_rank_index(self, xp: float) -> int:
        idx = -1
        for i, t in enumerate(RANK_THRESHOLDS):
            if xp >= t:
                idx = i
            else:
                break
        return idx

    def get_rank_progress(self, xp: float) -> tuple | None:
        idx = self.get_rank_index(xp)
        if idx == -1:
            return (int(xp), RANK_THRESHOLDS[0], RANK_NAMES[0])
        if idx == len(RANK_THRESHOLDS) - 1:
            return None
        prev = RANK_THRESHOLDS[idx]
        nxt = RANK_THRESHOLDS[idx + 1]
        return (int(xp) - prev, nxt - prev, RANK_NAMES[idx + 1])

    def get_rank_bonus(self, xp: float) -> str:
        idx = self.get_rank_index(xp)
        if 0 <= idx < len(RANK_BONUSES):
            return RANK_BONUSES[idx]
        return ""
