# Jairbet

A Telegram bot where **Jair** predicts football scores and competes against
three automatic "animal" bettors:

| | Bettor | How it bets |
|---|---|---|
| 🧑 | Jair | Through the bot, until kickoff |
| 🐒 | Macaco | Random score, each side drawn from 0 to 3 |
| 🦀 | Caranguejo | The opposite of Jair: `1x2 → 2x1`; draws become a one-goal win for a random side (`0x0 → 1x0/0x1`, `2x2 → 2x1/1x2`) |
| 🐦‍⬛ | Corvo | A Poisson model built from the teams' previous results |

The animals bet only on matches Jair bets on, so everyone is ranked on the same
fixtures. Brasileirão Série A fixtures and results are synced automatically from
[football-data.org](https://www.football-data.org). The admin can edit anything
and add matches from any other championship.

Scoring: **3 points** for the right winner (or draw) plus a **3-point bonus** for
the exact score. Only finished matches count.

## 1. What you need

1. **A bot token.** In Telegram, talk to **@BotFather**, send `/newbot` and
   follow the steps. Copy the token.
2. **A football-data.org key.** Register (free plan) at
   <https://www.football-data.org/client/register>. The key arrives by email.
3. **Telegram IDs.** Start the bot once (step 3) and send `/chatid` to it: it
   answers with your user ID. Ask Jair to do the same. For the group, add the bot
   to the group and send `/chatid` there (group IDs are negative).

## 2. Setup (development machine)

Python 3.11 or newer:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env      # then fill it in
```

`.env` settings:

| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from @BotFather (required) |
| `ADMIN_TELEGRAM_IDS` | Your user ID; several admins separated by commas (empty = setup mode, only `/chatid` works) |
| `JAIR_TELEGRAM_ID` | Jair's user ID |
| `GROUP_CHAT_ID` | Group that receives results; empty = private messages |
| `FOOTBALL_DATA_TOKEN` | football-data.org key; empty disables sync |
| `DB_PATH`, `BACKUP_DIR` | Data locations (default: project folder) |
| `TZ_NAME` | Timezone, default `America/Sao_Paulo` |

## 3. Run the bot

```bash
.venv/bin/python -m jairbet.bot
```

On start the bot upgrades the database (making a backup first), creates any
missing animal bets, and begins long polling, so no open ports or public URL
are needed. Then, as admin, send `/sync` to load the Brasileirão season.

### Commands

Everyone allowed (Jair, admin, the group):

| Command | |
|---|---|
| `/jogos [hoje\|amanha\|semana\|rodada N]` | Upcoming matches |
| `/jogo <id>` | One match with every bet |
| `/resultados [N]` | Latest results and points |
| `/ranking [mes\|AAAA-MM\|ano\|BSA]` | Ranking, overall or filtered |
| `/stats` | Charts: cumulative points, points per month, hit rate over time, hits per team, favourite scorelines |
| `/time <nome>` | A team's performance: recent form, goals, and home/away strength |
| `/corvo [id]` | Corvo's picks and probabilities |

Jair (private chat): `/palpitar` (tap a match and the goals, or type `2x1`),
`/palpitar <id> 2x1`, `/meuspalpites`. Bets lock at kickoff.

Admin (private chat):

| Command | |
|---|---|
| `/novojogo` | Guided creation of a match from any championship |
| `/buscarjogo <código>` | Pick an upcoming API fixture to track (e.g. `CLI` Libertadores, `CL` Champions League); its result then syncs automatically |
| `/editarjogo <id> <campo> <valor>` | `data`, `hora`, `mandante`, `visitante`, `status`, `competicao`, `rodada`, `travar`, `destravar` |
| `/placar <id> 2x1` / `limpar` | Set or clear a result |
| `/apagarjogo <id>` | Delete a match (asks for confirmation) |
| `/editarpalpite <id> <apostador> 2x1` / `apagar` | Edit any bet, including the animals' |
| `/pontuacao [vencedor exato]` | Show or change the scoring |
| `/competicoes` | Turn full-season sync on/off per competition |
| `/alias <apelido> = <time>` | Map a team name variant (merges duplicates) |
| `/sync [código]`, `/log` | Force a sync; see sync runs and admin changes |
| `/backup`, `/exportar`, `/importar` | Database file, Excel export, Excel import |

Admin edits **lock** the match so the API sync never overwrites them
(`/editarjogo <id> destravar` to undo). Every admin change is recorded in the
`audit_log` table.

### Automatic jobs (Brasília time)

| When | What |
|---|---|
| 06:00 | Full-season sync of every auto-sync competition |
| every 10 min | Sync while a match is in progress; results are posted to the group |
| 09:00 | Reminds Jair about today's matches without a bet |
| 1 h before kickoff | Second reminder if Jair still has no bet |
| 03:00 | Database backup (kept 14 days); on Sundays it is also sent to the admin |

## 4. Deploy on the old laptop (Ubuntu 24.04 / Debian 12)

```bash
git clone <this repository> jairbet && cd jairbet
# optional: copy your current jairbet.db into this folder to keep the history
sudo ./deploy/install.sh --no-sleep
sudo nano /opt/jairbet/.env        # fill in tokens and IDs
sudo systemctl start jairbet-bot
journalctl -u jairbet-bot -f       # live logs
```

The installer creates a `jairbet` system user, installs the app in
`/opt/jairbet`, keeps data in `/var/lib/jairbet`, and enables a systemd service
that starts on boot and restarts after a crash. `--no-sleep` sets
`HandleLidSwitch=ignore` and disables suspend, so the laptop keeps running with
the lid closed. Leave it plugged in and on a wired or stable Wi-Fi network.

**Update:** `git pull && sudo ./deploy/install.sh` (it keeps `.env` and data).

### Alternative: deploy with Docker (from your own machine, over ssh)

One-time server setup (installs Docker and Compose, adds your user to the
`docker` group, keeps the laptop awake with the lid closed, and disables the
old systemd service if it was installed):

```bash
scp deploy/docker-server-setup.sh user@server:
ssh -t user@server 'sudo ./docker-server-setup.sh'   # then reconnect ssh
```

First deploy, run on your machine from the project folder. It copies the code
to `~/jairbet` on the server, copies your local `.env` if the server has none,
copies a snapshot of your local `jairbet.db` with `--with-db`, then builds and
starts the container:

```bash
./deploy/docker-deploy.sh user@server --with-db
```

**Update:** `./deploy/docker-deploy.sh user@server` (keeps `.env` and data).

On the server (`cd ~/jairbet`):

| Task | Command |
|---|---|
| Live logs | `docker compose logs -f` |
| Status | `docker compose ps` |
| Restart / stop | `docker compose restart` / `docker compose stop` |
| Edit settings | `nano .env && docker compose up -d` |
| Run a CLI command | `docker compose exec bot python -m jairbet.bettors --backfill` |

The database and backups live in `~/jairbet/data` on the server (mounted as
`/data` in the container). The container restarts after a crash and starts on
boot (`restart: unless-stopped`, Docker enabled by systemd). Restore a backup
with `docker compose stop`, copy the file over `data/jairbet.db`, delete
`data/jairbet.db-wal` and `data/jairbet.db-shm`, then `docker compose start`.

## 5. Backups and restore

- Automatic: `/var/lib/jairbet/backups/jairbet-YYYYMMDD-HHMM.db`, every night.
- On demand: `/backup` in the bot, or
  `sqlite3 /var/lib/jairbet/jairbet.db ".backup copy.db"`.
- Restore:

  ```bash
  sudo systemctl stop jairbet-bot
  sudo cp /var/lib/jairbet/backups/jairbet-20261114-0300.db /var/lib/jairbet/jairbet.db
  sudo chown jairbet:jairbet /var/lib/jairbet/jairbet.db
  sudo rm -f /var/lib/jairbet/jairbet.db-wal /var/lib/jairbet/jairbet.db-shm
  sudo systemctl start jairbet-bot
  ```

## 6. Changing the scoring

Send `/pontuacao 3 3` (points for the winner, bonus for the exact score). The
change applies **retroactively** to every finished match, because points are
calculated by SQL views.

## 7. Excel import/export and maintenance

```bash
python -m jairbet.importer --xlsx Jairbet_2026_v1.xlsx --db jairbet.db
python -m jairbet.bettors --backfill [--recompute]   # create animal bets
```

The importer is idempotent, fixes the known workbook issues (2023 date typo,
`Vitoria`, `2 x 1` scores) and skips empty bets. `/exportar` produces a workbook
that re-imports to the same ranking.

## 8. Quality checks

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
```

Tests never use the network or Telegram: the API is mocked and the handlers
are driven with fake updates.
