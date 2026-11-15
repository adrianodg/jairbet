"""Project paths, runtime settings, bettor names, model constants and colours.

Secrets and machine-specific values are read from environment variables, which
may be provided by a ``.env`` file in the project root (see ``.env.example``).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "jairbet.db"
DEFAULT_XLSX_PATH = PROJECT_ROOT / "Jairbet_2026_v1.xlsx"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backups"
MIGRATIONS_DIR = PROJECT_ROOT / "db" / "migrations"
ENV_PATH = PROJECT_ROOT / ".env"

DEFAULT_TIMEZONE = "America/Sao_Paulo"

# Correct a known erroneous year in the source workbook.
DATE_FIXES = {"2023-08-18": "2026-08-18"}

# Alternate spellings mapped to the canonical team name stored in ``team``.
# API names are also normalised (accents, FC/EC/SE suffixes) before lookup.
TEAM_ALIASES = {
    "Vitoria": "Vitória",
    "Atlético Mineiro": "Galo",
    "Atletico Mineiro": "Galo",
    "Atlético-MG": "Galo",
    "Clube Atlético Mineiro": "Galo",
    "CA Mineiro": "Galo",
    "Athletico Paranaense": "A. Paranaense",
    "Athletico-PR": "A. Paranaense",
    "CA Paranaense": "A. Paranaense",
    "Club Athletico Paranaense": "A. Paranaense",
    "RB Bragantino": "Bragantino",
    "Red Bull Bragantino": "Bragantino",
    "CR Vasco da Gama": "Vasco",
    "Vasco da Gama": "Vasco",
    "Grêmio FBPA": "Grêmio",
    "Novorizontino": "Novohorizontino",
    "Grêmio Novorizontino": "Novohorizontino",
    "Independiente del Valle": "Ind. Del Valle",
    "LDU Quito": "LDU",
    "Bolívar": "Bolivar",
    "Boca Juniors": "Boca",
    "Rosario Central": "Rosario",
}

# Words dropped when normalising team names for matching.
TEAM_NAME_STOPWORDS = frozenset(
    {
        "fc",
        "ec",
        "se",
        "cr",
        "sc",
        "ac",
        "ca",
        "fr",
        "fbpa",
        "afc",
        "club",
        "clube",
        "de",
        "da",
        "do",
        "regatas",
        "futebol",
        "esporte",
        "saf",
    }
)

JAIR_NAME = "Jair"
MACACO_NAME = "Macaco"
CARANGUEJO_NAME = "Caranguejo"
CORVO_NAME = "Corvo"

# Bettor name -> ``bettor.kind``. Every non-human bettor bets automatically.
BETTOR_KINDS = {
    JAIR_NAME: "human",
    MACACO_NAME: "random",
    CARANGUEJO_NAME: "inverse",
    CORVO_NAME: "model",
}
AUTO_BETTORS = (MACACO_NAME, CARANGUEJO_NAME, CORVO_NAME)

RANDOM_SCORE_MIN = 0
RANDOM_SCORE_MAX = 3

# Legacy reconciliation: an imported match is linked to a provider match for the
# same fixture within RECONCILE_DAYS, or within RECONCILE_SAME_SCORE_DAYS when
# both have the same final score (rounds moved to midweek, for example).
RECONCILE_DAYS = 1
RECONCILE_SAME_SCORE_DAYS = 7

# Corvo's Poisson model.
CORVO_WINDOW_DAYS = 365
CORVO_MIN_MATCHES = 20
CORVO_DEFAULT_MU_HOME = 1.4
CORVO_DEFAULT_MU_AWAY = 1.1
CORVO_LAST_N = 10
CORVO_HALF_LIFE = 5.0
CORVO_PRIOR_K = 3.0
CORVO_MAX_GOALS = 6
CORVO_FORM_N = 5

# Competitions seeded by the v2 migration.
BRASILEIRAO_CODE = "BSA"
MANUAL_CODE = "MANUAL"
SEED_COMPETITIONS = (
    (BRASILEIRAO_CODE, "Brasileirão Série A", "football_data", 1),
    (MANUAL_CODE, "Outros", None, 0),
)

FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
HTTP_TIMEOUT_SECONDS = 20.0
HTTP_MAX_RETRIES = 3

# Bot scheduling (local time) and limits.
DAILY_SYNC_TIME = "06:00"
DAILY_REMINDER_TIME = "09:00"
DAILY_BACKUP_TIME = "03:00"
LIVE_SYNC_INTERVAL_MINUTES = 10
LIVE_SYNC_WINDOW_HOURS = 3
PRE_KICKOFF_REMINDER_MINUTES = 60
BACKUP_KEEP_DAYS = 14
MAX_GOALS_KEYBOARD = 6
OPEN_MATCHES_LIMIT = 12

PALETTE = {
    JAIR_NAME: "#0072B2",
    MACACO_NAME: "#E69F00",
    CARANGUEJO_NAME: "#009E73",
    CORVO_NAME: "#CC79A7",
    "light_blue": "#56B4E9",
    "red": "#D55E00",
}


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the bot and sync jobs.

    Attributes:
        telegram_token: Bot token from BotFather.
        football_data_token: football-data.org API key, or empty to disable sync.
        admin_ids: Telegram user IDs with admin rights.
        jair_id: Telegram user ID of Jair, or ``None``.
        group_chat_id: Telegram group that receives notifications, or ``None``.
        db_path: SQLite database file.
        backup_dir: Folder receiving rotated database backups.
        timezone: IANA timezone used for display and schedules.
    """

    telegram_token: str
    football_data_token: str
    admin_ids: frozenset[int]
    jair_id: int | None
    group_chat_id: int | None
    db_path: Path
    backup_dir: Path
    timezone: str


def _optional_int(value: str | None, name: str) -> int | None:
    """Parse an optional integer environment variable.

    Args:
        value: Raw variable value.
        name: Variable name used in the error message.

    Returns:
        Parsed integer, or ``None`` when empty.

    Raises:
        ValueError: If the value is not an integer.
    """
    if value is None or not value.strip():
        return None
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def load_settings(env_file: Path | None = ENV_PATH) -> Settings:
    """Load settings from the environment and an optional ``.env`` file.

    Args:
        env_file: Optional dotenv file; real environment variables take priority.

    Returns:
        Parsed settings.

    Raises:
        ValueError: If a numeric setting is malformed.
    """
    if env_file is not None and env_file.exists():
        load_dotenv(env_file, override=False)
    admin_ids = frozenset(
        value
        for part in os.environ.get("ADMIN_TELEGRAM_IDS", "").split(",")
        if (value := _optional_int(part, "ADMIN_TELEGRAM_IDS")) is not None
    )
    return Settings(
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        football_data_token=os.environ.get("FOOTBALL_DATA_TOKEN", "").strip(),
        admin_ids=admin_ids,
        jair_id=_optional_int(os.environ.get("JAIR_TELEGRAM_ID"), "JAIR_TELEGRAM_ID"),
        group_chat_id=_optional_int(os.environ.get("GROUP_CHAT_ID"), "GROUP_CHAT_ID"),
        db_path=Path(os.environ.get("DB_PATH") or DEFAULT_DB_PATH),
        backup_dir=Path(os.environ.get("BACKUP_DIR") or DEFAULT_BACKUP_DIR),
        timezone=os.environ.get("TZ_NAME") or DEFAULT_TIMEZONE,
    )
