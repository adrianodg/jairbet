# Decisions

Assumptions made while building Jairbet. Newest (v2) first.

## v2 – Telegram bot

### Data source and sync
- **football-data.org** (free plan, `BSA`) is the only provider. There is no
  truly open-source Brasileirão API; the free plan is stable and documented.
  Copa do Brasil is not on the free plan: such matches are added manually with
  `/novojogo` and scored with `/placar`.
- Only the **final score** is stored, and only when the API says `FINISHED`.
  Live scores are ignored, so a partial score never counts. For cup ties decided
  on penalties, the score after extra time is used (penalties excluded).
- `match_date` is the **local (Brasília) date** of the kickoff, so a 21:30 game
  on 12/11 is on 12/11 even though it is 00:30 UTC on 13/11.
- Matches **edited by the admin are locked** (`admin_locked = 1`) and sync skips
  them. `/editarjogo <id> destravar` unlocks.
- **Legacy reconciliation:** a match without a provider ID with the same home and
  away team within ±1 day is linked instead of duplicated. If none is found, a
  legacy match with the same fixture **and the same final score** within ±7 days
  is linked (the workbook dated round 5 on 08/03, but it was played on 11–12/03).
  Wider windows are not used: two-legged cup ties between Série A clubs would be
  linked by mistake. Postponed games further off need a manual date fix first. Legacy matches are
  put in the `MANUAL` ("Outros") competition by the migration and move to `BSA`
  when linked.
- **Team names:** provider names are normalised (accents, case, FC/EC/SE…,
  years) and matched against team names, stored aliases and `TEAM_ALIASES` in
  `config.py` (seeded for names like Atlético Mineiro → Galo). Unknown teams are
  created with the provider's short name and the admin is told; `/alias` merges
  a duplicate into the right team.
- A sync uses one request per competition (season or date window); tracked
  one-off matches use one request each. This stays far below 10 requests/min.
- `/buscarjogo` creates the competition with `auto_sync = 0`; only the tracked
  match is refreshed. `/competicoes` can switch a whole competition on.

### Bettors
- Animals bet **only on matches Jair bet on**, when his bet is saved. If his bet
  is removed, their automatic bets are removed too.
- **Caranguejo** uses `random.Random(match_id)` for the draw tie-break, so the
  same draw on the same match always inverts the same way, and recomputation is
  reproducible.
- **Corvo** is computed when Jair saves (and recomputed if he edits before
  kickoff). It uses matches from **any competition** in the previous 365 days,
  finished before the target kickoff. Legacy matches without a kickoff count as
  played at 00:00 of their date. With fewer than 20 matches it uses league
  defaults 1.4/1.1. Ties between outcomes prefer home, then draw; ties between
  scorelines prefer fewer goals.
- Admin edits of any bet set `source = 'admin'`; those bets are never
  regenerated.
- The startup backfill only **fills missing** animal bets. Existing automatic
  bets are recomputed only with `python -m jairbet.bettors --backfill --recompute`.

### Bot behaviour
- Long polling (no webhook), so the laptop needs no public address.
- Admin is not a bettor. Only the configured Jair account can use `/palpitar`.
  For testing alone, set `JAIR_TELEGRAM_ID` to your own ID.
- Bets and admin commands only work in **private chats**; the group gets
  read-only commands and notifications. Unknown users get a refusal in private
  and silence in other groups. `/chatid` answers anyone (it only reveals IDs) to
  help fill in `.env`.
- Results are announced only for finished matches that have bets, to avoid
  posting all ten games of a round. Without `GROUP_CHAT_ID`, announcements go
  privately to Jair and the admins.
- Pre-kickoff reminders remember which matches were reminded in memory; after a
  restart a reminder can be repeated once.
- A failed sync alerts the admins once per distinct error (not every 10 min).
- Bet locking: at `kickoff_at`, or at the end of the local match date for
  matches without a kickoff time.
- Charts use **matplotlib** PNGs instead of Plotly (no browser needed on the
  server), with the same Okabe-Ito palette: Caranguejo `#009E73`, Corvo
  `#CC79A7`. "Hits per team" shows Jair's rates for the 12 teams with most
  matches (minimum 3).
- The timezone setting is `TZ_NAME` (not `TZ`) to avoid clashing with the system
  `TZ` variable, which may hold a non-IANA value.

### Storage and operations
- Migrations live in `db/migrations/NNN_name.sql`, tracked by
  `PRAGMA user_version`. A v1 database without a version is treated as v1. File
  databases are backed up next to the database file before migrating.
- SQLite runs in WAL mode. Handlers open short-lived connections; heavy work
  (sync, charts, import) runs in worker threads with their own connection.
- `deploy/install.sh` installs the package in editable mode so the migration
  files in `/opt/jairbet/db/migrations` are found.
- Backups use the SQLite online backup API and are rotated after 14 days.
- Streamlit, Plotly and `requirements.txt` were removed; dependencies live only
  in `pyproject.toml`.

## v1 (still valid)

- One global scoring configuration. Changing it recalculates historical
  rankings through the SQL views. A future `valid_from` column on
  `scoring_config` can support scoring periods.
- Imports keep fixtures even if both predictions are empty, but skip the empty
  betting rows and report a warning.
- Export writes the six source columns needed by the importer plus a hidden
  `_Jairbet` sheet marking that the animals' predictions must be preserved on
  re-import. Excel helper formula columns are omitted.
- Macaco is a random baseline: a score from `0x0` to `3x3`, drawn once per
  fixture and then kept.

## Docker deploy
- Docker is an alternative to `deploy/install.sh`; both are kept. The image
  (`python:3.12-slim`) installs only the dependencies from `pyproject.toml` and
  runs the code from `/app` via `PYTHONPATH`, so `PROJECT_ROOT` and the
  migrations folder resolve the same way as in a source checkout.
- Data lives in a bind mount `./data` -> `/data`; `DB_PATH` and `BACKUP_DIR`
  are fixed by `docker-compose.yml` and override `.env`.
- The container runs as a non-root user whose UID/GID match the ssh user on the
  server (passed by `deploy/docker-deploy.sh`), so `data/` stays editable.
- Code is copied with rsync from the developer machine instead of `git pull`,
  so the server needs no GitHub access and the DB can be seeded in one step.
- Logs use Docker's `json-file` driver with rotation (5 x 10 MB).
