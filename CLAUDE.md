# GOAL: Jairbet v2 – Telegram bot with automatic Brasileirão sync

## Role
You are a senior Python engineer. Build the whole project in this repository.
Work on your own: if something is ambiguous, pick the simplest sensible option,
record it in `DECISIONS.md`, and keep going. Do not stop to ask questions.

## Context
Jairbet v1 (done) is a SQLite tracker with a Streamlit UI that replaced the
`Jairbet_2026_v1.xlsx` workbook. Bettors predict the exact score of football
matches. v1 already has the schema, scoring, Excel importer/exporter, and
**Macaco** as a random baseline (`jairbet/random_bettor.py`).

v2 turns Jairbet into a **Telegram bot** running 24/7 on an old Linux laptop:
- Brasileirão Série A fixtures and results are synced automatically from
  **football-data.org** (free tier, competition code `BSA`). Nothing about
  Brasileirão is entered by hand any more.
- **Jair** uses the bot to check matches, results and metrics, and to bet.
- The **admin** (the project owner) can edit anything through the bot, and can
  register specific matches from other championships.
- Two new automatic "animal" bettors are added: **Caranguejo** and **Corvo**.
- Streamlit is **removed**. Telegram is the only UI.

### Bettors
| Name | `kind` | How it bets |
|---|---|---|
| Jair | `human` | Through the bot, before kickoff |
| Macaco | `random` | Random score, each side drawn from 0 to 3, saved once (unchanged from v1) |
| Caranguejo | `inverse` | The opposite of Jair's bet (see the rules below) |
| Corvo | `model` | Poisson model based on the teams' previous results |

Automatic bettors only bet on fixtures Jair bet on, so all four are compared on
exactly the same matches. They bet at the moment Jair's bet is saved.

### Caranguejo rules (pure function `invert(pred_home, pred_away, rng)`)
- Not a draw: swap the score. `1x2 -> 2x1`, `1x0 -> 0x1`.
- `0x0`: random pick of `1x0` or `0x1`.
- `nxn` with n >= 1: random pick of `nx(n-1)` or `(n-1)xn`, so
  `2x2 -> 2x1` or `1x2`.
- The RNG is `random.Random(match_id)`, so the result is reproducible.
- It is recomputed every time Jair edits his bet before kickoff.

### Corvo rules (pure, deterministic, in `bettors/corvo.py`)
- Data: FINISHED matches (any competition) that kicked off **before** the
  target match's kickoff and in the previous 365 days. Never use future data.
- League averages `mu_home` and `mu_away` come from that data. If there are
  fewer than 20 matches, fall back to `1.4` and `1.1`.
- Per-team strengths by venue, from each team's last 10 matches at that venue:
  - `att_home`, `def_home`, `att_away`, `def_away`
  - goals scored or conceded, divided by the matching league average
  - exponential decay weights (half-life of 5 matches)
  - Bayesian shrinkage toward 1.0 with prior weight `k = 3`
- `lambda_home = mu_home * att_home(H) * def_away(A)` and
  `lambda_away = mu_away * att_away(A) * def_home(H)`.
- Build an independent Poisson score matrix for 0 to 6 goals per side.
- Pick the most likely outcome (home win, draw or away win), then the most
  likely scoreline within that outcome. This maximises winner hits.
- Returns `CorvoMetrics` (prediction, both lambdas, P(home/draw/away), each
  team's last-5 form W/D/L, and average goals for and against). The bot shows
  these to Jair with `/corvo`.
- All constants live in `config.py`.

## Business rules (unchanged from v1, must still match Excel)
- **Winner hit** = `sign(pred_home - pred_away) == sign(home_goals - away_goals)`.
  A draw counts as an outcome.
- **Exact hit** = `pred_home == home_goals and pred_away == away_goals`.
- **Points** = `winner_hit * points_winner + exact_hit * bonus_exact`, so an
  exact score is worth 6 with the default config (3 + 3).
- Only matches **with a real score** count toward the ranking. Pending matches
  never score 0; they are left out. CANCELLED matches are excluded.
- Ranking = points in descending order, with ties sharing a rank (SQL `RANK()`).
- New in v2:
  - Bets lock at `kickoff_at`. Legacy matches with no kickoff time lock at the
    end of `match_date` (America/Sao_Paulo). Jair cannot bet or edit after the
    lock. The admin always can.
  - Bets are only placed in private chats with the bot.

## Architecture
```
            Telegram (long polling, no open ports needed)
                              │
           jairbet.bot (python-telegram-bot 22, async)
     ┌────────────────────────┼──────────────────────────┐
 handlers/user.py      handlers/admin.py          jobs.py (JobQueue)
     │                        │               sync · reminders · backup
     └──── services: bettors/, sync.py, stats.py, charts.py ────┘
                    │                               │
          repository.py (all SQL)     providers/football_data.py (httpx)
                    │
         SQLite (WAL) jairbet.db  +  backups/
```

## Project layout
```
jairbet/
├── pyproject.toml
├── README.md
├── DECISIONS.md
├── .env.example            # every setting, no real secrets
├── db/
│   └── migrations/
│       ├── 001_initial.sql # the v1 schema (moved from db/schema.sql)
│       └── 002_bot.sql     # v2 changes (see below)
├── deploy/
│   ├── jairbet-bot.service # systemd unit
│   └── install.sh          # venv, user, folders, enable service
├── jairbet/
│   ├── __init__.py
│   ├── config.py           # Settings from .env, paths, constants, palette
│   ├── console.py          # colored logging helpers (journald friendly)
│   ├── db.py               # connect() (foreign_keys, WAL), migrate()
│   ├── scoring.py          # pure-Python scoring functions, score parsing
│   ├── timeutil.py         # UTC <-> local time, bet lock time
│   ├── services.py         # use cases: save bet, admin edits, audit
│   ├── repository.py       # CRUD + queries (all SQL lives here or in views)
│   ├── importer.py         # Excel -> SQLite (kept, CLI + /importar)
│   ├── exporter.py         # SQLite -> .xlsx (kept, CLI + /exportar)
│   ├── stats.py            # team form, head-to-head, chart data queries
│   ├── charts.py           # matplotlib -> PNG bytes for Telegram
│   ├── sync.py             # provider -> DB upsert, reconciliation, events
│   ├── bettors/
│   │   ├── __init__.py     # place_auto_bets(conn, match_id), backfill
│   │   ├── __main__.py     # python -m jairbet.bettors --backfill
│   │   ├── macaco.py       # moved from random_bettor.py
│   │   ├── caranguejo.py
│   │   └── corvo.py
│   ├── providers/
│   │   ├── base.py         # ApiMatch dataclass, FixtureProvider Protocol
│   │   └── football_data.py
│   └── bot/
│       ├── __main__.py     # python -m jairbet.bot
│       ├── main.py         # build Application, register handlers and jobs
│       ├── auth.py         # role decorators (admin / jair / group)
│       ├── keyboards.py    # inline keyboards
│       ├── formatters.py   # pt-BR message text (pure, tested)
│       ├── jobs.py         # scheduled jobs
│       └── handlers/
│           ├── user.py
│           └── admin.py
└── tests/
```
Remove `app.py` and the `streamlit`/`plotly` dependencies.
Replace `random_bettor.py` with `bettors/macaco.py`.

## Database changes (`002_bot.sql`)
`db.migrate()` applies the migration files in order and tracks progress with
`PRAGMA user_version`. Before migrating an existing file, it makes a backup with
the `sqlite3` backup API. The current `jairbet.db` (190 matches) must migrate in
place. Keep every v1 constraint.

- `competition(competition_id PK, code TEXT UNIQUE NOT NULL, name, provider
  NULL, auto_sync 0/1 DEFAULT 0)`. Seed `BSA` (Brasileirão Série A,
  provider `football_data`, auto_sync 1) and `MANUAL` (Outros, no provider).
- `match` gets these new columns:
  - `competition_id` FK
  - `kickoff_at` TEXT UTC ISO-8601, NULL for legacy rows
  - `status` TEXT NOT NULL DEFAULT 'SCHEDULED', CHECK IN (SCHEDULED, LIVE,
    FINISHED, POSTPONED, CANCELLED). Existing matches with a score become
    FINISHED.
  - `source` TEXT NOT NULL DEFAULT 'import', CHECK IN (api, manual, import)
  - `external_id` TEXT NULL
  - `round` INT NULL
  - `admin_locked` 0/1 DEFAULT 0. When it is 1, sync never overwrites date,
    status or score.
  - `updated_at` TEXT NULL
  - A partial UNIQUE index on `(source, external_id)` WHERE
    `external_id IS NOT NULL`, and an index on `kickoff_at`.
- `bettor` gets `kind` TEXT NOT NULL DEFAULT 'human', CHECK IN (human, random,
  inverse, model), and `telegram_user_id INTEGER UNIQUE NULL`. Set Macaco to
  `random`, and seed Caranguejo (`inverse`) and Corvo (`model`).
- `bet` gets `source` TEXT NOT NULL DEFAULT 'user', CHECK IN (user, auto,
  admin). Automatic bettors write `auto`, and only `auto` bets are ever
  regenerated. An admin edit sets `admin`.
- `audit_log(audit_id PK, at DEFAULT CURRENT_TIMESTAMP, actor_telegram_id,
  action, entity, entity_id, before_json, after_json)`. Every admin write is
  logged here.
- `sync_run(run_id PK, started_at, finished_at, competition_code, status,
  fetched, created, updated, error)`
- Views: recreate `v_bet_score` with the same scoring logic, adding
  `competition` and `bettor_kind`, and excluding CANCELLED matches. `v_ranking`
  keeps the same columns. `repository.get_ranking()` takes optional
  `competition`, `season` and `month` filters.
- Backfill: after migrating, generate Caranguejo and Corvo bets for every
  existing Jair bet (done on every bot start, and by
  `python -m jairbet.bettors --backfill`). Corvo uses only
  the data available before each match.

## Sync (`sync.py` + `providers/football_data.py`)
- `FixtureProvider.fetch_matches(competition_code, date_from, date_to)` returns
  a list of `ApiMatch(external_id, competition_code, kickoff_at, status, round,
  home_name, away_name, home_goals, away_goals)`.
- football-data.org v4: `GET /v4/competitions/{code}/matches` with the header
  `X-Auth-Token`. Map the API statuses (TIMED/SCHEDULED -> SCHEDULED,
  IN_PLAY/PAUSED -> LIVE, FINISHED, POSTPONED/SUSPENDED -> POSTPONED,
  CANCELLED). Use `score.fullTime` for goals.
- The free tier allows 10 requests per minute. Use httpx with timeouts. On a
  429, honour the reset header or back off exponentially. Never crash the bot
  because the API failed: log, record `sync_run.status='error'`, and notify the
  admin once.
- `sync_competition()` runs in one transaction, is idempotent, and upserts by
  `(source='api', external_id)`. It skips fields on `admin_locked` matches.
- Team mapping: normalise the API name (strip accents and case, drop prefixes
  and suffixes like FC, EC, SE, CR, SC, AC, "Clube", "de Regatas", the year),
  then look it up in `team_alias` and `team`. If it is still unknown, create
  the team, store the API name as an alias, and tell the admin (fixable with
  `/alias`). Seed aliases for the 20 current Série A clubs in `config.py`.
- Legacy reconciliation: before creating a match, look for an `import` or
  `manual` match with no `external_id` and the same home and away team within
  ±1 day. If one exists, attach `external_id`, `kickoff_at`, `competition`, and
  set `source='api'` instead of creating a duplicate. At the end of the first
  full sync, legacy matches that are still unmatched go to `MANUAL`.
- Sync returns events (`created`, `linked`, `rescheduled`, `finished`,
  `unknown_team`). The bot turns `finished` (for matches with bets) into a group
  message with the score, each bettor's prediction and points, and the top of
  the ranking.

## Bot (python-telegram-bot 22, long polling, UI in pt-BR)
Settings come from `.env`: `TELEGRAM_BOT_TOKEN`, `FOOTBALL_DATA_TOKEN`,
`ADMIN_TELEGRAM_IDS` (comma separated), `JAIR_TELEGRAM_ID`, `GROUP_CHAT_ID`,
`DB_PATH`, `BACKUP_DIR` and `TZ_NAME` (not `TZ`, which the system may set).

Any other user gets a polite refusal. In the group, only read-only commands
work. Wrap DB work in short-lived connections (`asyncio.to_thread` for heavy
queries and charts).

### Commands for Jair and the admin
| Command | What it does |
|---|---|
| `/start`, `/ajuda` | Help text with the commands for this user's role |
| `/chatid` | Anyone: shows their user ID and the chat ID (for `.env`) |
| `/jogo <id>` | One match with every bet |
| `/jogos [hoje\|amanha\|semana\|rodada N]` | Upcoming matches, kickoff in BRT, and whether Jair has bet |
| `/palpitar` | Open matches with no bet → pick a match → inline goal keyboard (0–6), or type `2x1` → confirm. Shows the automatic bets after saving |
| `/meuspalpites` | Jair's open bets (editable until kickoff) |
| `/resultados [N]` | Last N results with all four predictions and points |
| `/ranking [competição\|mês]` | `v_ranking` as a monospaced table |
| `/stats` | PNG charts (see Charts) |
| `/time <nome>` | Team form, goals for and against, Corvo strengths |
| `/corvo <jogo>` | `CorvoMetrics` for a match |

### Admin-only commands
| Command | What it does |
|---|---|
| `/novojogo` | Guided conversation: competition (pick or create) → home → away (search or create the team) → date → time → a manual match. Duplicate fixtures get a friendly message |
| `/buscarjogo <código>` | Lists a competition's upcoming API fixtures (for example `CLI`, `CL`). Tap one to track it, so its result also syncs automatically |
| `/editarjogo <id>` | Edit teams, date, kickoff, status, or unlock it (`admin_locked`) |
| `/placar <id> HxA` | Set or override a result (sets `admin_locked=1`) |
| `/apagarjogo <id>` | Delete with a confirmation button (bets cascade) |
| `/editarpalpite <id> <apostador> HxA` | Edit any bettor's bet, including the animals (`source='admin'`) |
| `/pontuacao <vencedor> <exato>` | Change the scoring, with a warning that it is retroactive |
| `/competicoes` | List competitions and toggle `auto_sync` |
| `/alias <apelido> = <time>` | Map a team name variant |
| `/sync [código]` | Force a sync now and report the counts |
| `/backup` | Send a fresh DB backup file |
| `/exportar` / `/importar` | Send the `.xlsx` export, or import an uploaded `.xlsx` |
| `/log` | Last sync runs and errors |

### Scheduled jobs (`bot/jobs.py`, America/Sao_Paulo)
- 06:00 daily: full-season sync for every `auto_sync` competition.
- Every 10 minutes: if any match kicked off in the last 3 hours and is not
  FINISHED, sync that date window.
- 09:00 daily: remind Jair about today's matches he hasn't bet on.
- 60 minutes before an un-bet kickoff: one reminder to Jair.
- 03:00 daily: backup to `BACKUP_DIR` (14-day rotation). Sunday: also send it
  to the admin chat.

## Charts (`charts.py`, matplotlib, PNG)
- Dark background. Colorblind-safe Okabe-Ito palette: Jair `#0072B2`, Macaco
  `#E69F00`, Caranguejo `#009E73`, Corvo `#CC79A7`, extra `#56B4E9`,
  `#D55E00`.
- Charts: cumulative points over time, points per month (grouped bars), winner
  and exact hit rate per team (horizontal bars, at least 3 matches), most
  predicted scorelines per bettor, and the head-to-head matrix (matches where
  each bettor scored more than each other bettor).
- Easy for a manager to read: a clear title, labeled axes with units, no
  clutter, and data labels on bar charts. Readable on a phone screen.

## Hosting (old Linux laptop, Ubuntu/Debian)
- `deploy/install.sh`: creates a `jairbet` system user, `/opt/jairbet` with a
  venv, `/var/lib/jairbet` for the DB and backups, and installs the service.
- `deploy/jairbet-bot.service`: `Restart=always`, `EnvironmentFile=.env`
  (permissions 600), `TZ=America/Sao_Paulo`, and logs to journald.
- The README explains `HandleLidSwitch=ignore`, disabling suspend, checking
  logs with `journalctl -u jairbet-bot`, restoring a backup, and updating
  (`git pull && pip install -e . && systemctl restart jairbet-bot`).

## Tech stack
- Python 3.11+, standard library `sqlite3` (no ORM), WAL mode
- `python-telegram-bot[job-queue]` 22
- `httpx` for the API, `python-dotenv` for settings
- `matplotlib` for charts
- `pandas` and `openpyxl` for import and export
- `pytest` (+ `pytest-asyncio` if needed) and `ruff`
- Dependencies are declared in `pyproject.toml`

## Coding standards (mandatory)
- PEP 8, `ruff check` and `ruff format` must be clean, line length **88**
  (rule E501), including docstrings and comments.
- Type hints on every function, parameter and return value.
- **Google-style docstrings** on every public function, class and module.
- Use parameterized SQL only, never string formatting for values.
- Use `with` blocks or `try/finally` for connections; enable `foreign_keys` on
  every connection.
- Console output uses colored helpers from `console.py`: blue = info,
  yellow = warning, red = error, green = success.
- Code, identifiers and comments are in English; bot messages are in pt-BR.
- No hard-coded paths or secrets: keep them in `config.py` / `.env` and allow
  CLI overrides. Never commit `.env` or tokens.
- Business logic (bettors, sync, formatters, stats) stays pure and testable
  without Telegram or the network.

## Tests (pytest, no network, no real Telegram)
- Keep the v1 suites: `test_scoring.py`, `test_repository.py` and
  `test_import_parity.py`. The real-workbook parity check for **Jair** stays
  at 180 matches, 81 winner hits, 18 exact hits, 297 points (skip it if the
  workbook is absent).
- `test_macaco.py` (replaces `test_random_bettor.py`).
- `test_caranguejo.py`: parametrized with `1x2 -> 2x1`, `1x0 -> 0x1`,
  `0x0 -> {1x0, 0x1}`, `2x2 -> {2x1, 1x2}`, `3x3 -> {3x2, 2x3}`. Also checks
  that a seeded RNG is reproducible, and that it recomputes on Jair's edits but
  not on `source='admin'` bets.
- `test_corvo.py`: a strong vs weak team predicts a strong-team win, no history
  falls back to the league defaults, matches after kickoff are ignored, and the
  output is deterministic.
- `test_football_data.py`: parses a recorded JSON fixture in `tests/data/`,
  maps statuses, and handles a 429 using a mocked transport (`httpx.MockTransport`).
- `test_sync.py` (fake provider): create, update, idempotency (a second run
  makes no changes), `admin_locked` is respected, legacy reconciliation (no
  duplicates), team aliasing, and the `MatchFinished` event.
- `test_migrations.py`: a v1-schema DB migrates to v2 with an unchanged ranking
  and the new bettors seeded.
- `test_services.py`: bet lock, admin overrides, audit log, manual matches,
  match edits, alias merges, team search.
- `test_bot_handlers.py`: drive real handlers with mocked Telegram objects
  (bet by command/buttons/typed score, refusals, results, stats, admin edits).
- `test_bot_auth.py` / `test_formatters.py`: role checks, the bet lock, score
  parsing (`2x1`, `2 x 1`, `2X1`), and message formatting.

## Acceptance criteria (definition of done)
- [x] `pip install -e .[dev]` works; `ruff check .`, `ruff format --check .`
      and `pytest -q` pass.
- [x] The existing `jairbet.db` migrates in place. Jair's ranking numbers are
      unchanged, and Caranguejo and Corvo are backfilled.
- [ ] `python -m jairbet.bot` starts with a valid `.env` and fails with a clear
      red error message when settings are missing.
- [ ] `/sync BSA` fills the 2026 Série A fixtures without duplicating the
      legacy imported matches. Running it twice changes nothing.
- [x] Jair can list matches, bet (locked after kickoff), and see results,
      ranking, stats charts and Corvo metrics. The automatic bettors bet on his
      fixtures.
- [x] The admin can create, edit and delete matches and bets, set results,
      add matches from other championships, change scoring, and get backups.
      Every change is in `audit_log`.
- [ ] Finished matches are announced in the group automatically.
- [ ] `deploy/install.sh` and the systemd unit run the bot on Ubuntu/Debian
      and restart it after a crash or reboot.
- [x] Export → import round-trip keeps the ranking unchanged.
- [x] `README.md` covers BotFather setup, getting Telegram IDs, getting a
      football-data.org key, `.env`, install, deploy, backups and restore, and
      how to change the scoring.
- [x] `DECISIONS.md` lists every assumption you made.

Status (2026-11-14): checked items are verified offline (tests with mocked
Telegram/API, migration run on a copy of the real DB). Still to verify live,
which needs real tokens and the laptop: bot start with a valid `.env`, `/sync BSA`
against the API, group announcements, and `deploy/install.sh` on Ubuntu/Debian.

## Out of scope
- Webhooks, multiple groups, more human bettors, and paid API tiers.
- Live in-match notifications (only the final result is announced).
- Different scoring per period (keep it in `DECISIONS.md` as a future
  `valid_from` column on `scoring_config`).

## Working order
1. Migrations (`db.py`, `001`/`002`) and `test_migrations.py`.
2. `bettors/` (Macaco move, Caranguejo, Corvo) and their tests, then backfill.
3. `providers/` and `sync.py` with their tests.
4. Bot skeleton (`config`, `auth`, `main`) and the user commands.
5. Admin commands and `audit_log`.
6. Jobs (sync, reminders, backup) and the group notifications.
7. matplotlib charts and `/stats`; remove Streamlit, `app.py` and plotly.
8. `deploy/`, `.env.example`, `README.md` and `DECISIONS.md`.
After each step, run `ruff` and `pytest` and fix any failures before moving on.
