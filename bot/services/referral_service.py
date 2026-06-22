from bot.repositories.sheets_repository import SheetsRepository

THRESHOLDS = [1, 3, 10, 25, 50]
LEVEL_NAMES = [
    "",           # 0
    "🧭 Скаут",
    "📣 Промоутер",
    "🧲 Вербовщик",
    "📢 Амбассадор",
    "🎩 Рекламный Барон",
]


class ReferralService:

    def __init__(self, repo: SheetsRepository) -> None:
        self._repo = repo

    def get_referral_count(self, nickname: str) -> int:
        referrals = self._repo.find_referrals(nickname)
        return len(referrals)

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

    def get_next_level_progress(self, count: int) -> tuple[int, int, str]:
        for i, threshold in enumerate(THRESHOLDS):
            if count < threshold:
                prev = THRESHOLDS[i - 1] if i > 0 else 0
                return (count - prev, threshold - prev, LEVEL_NAMES[i])
        return (count, count, "")
