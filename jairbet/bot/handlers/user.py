"""Commands for Jair, the admin and the group: matches, bets, results, stats."""

import asyncio
from datetime import timedelta

from telegram import InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from jairbet import charts, stats
from jairbet.bettors import corvo_for_match, team_profile
from jairbet.bot import formatters, keyboards
from jairbet.bot.auth import access_for, restricted
from jairbet.config import (
    BRASILEIRAO_CODE,
    JAIR_NAME,
    OPEN_MATCHES_LIMIT,
    Settings,
)
from jairbet.db import connect, session
from jairbet.repository import (
    bets_for_match,
    get_bettor_id,
    get_competition,
    get_match,
    get_ranking,
    list_matches,
    recent_results,
    upcoming_matches,
)
from jairbet.services import (
    ServiceError,
    bet_is_open,
    parse_score_text,
    save_bet,
    search_teams,
)
from jairbet.timeutil import local_date, utc_now

PENDING_BET_KEY = "pending_bet"


def settings_of(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    """Return the runtime settings stored in ``bot_data``.

    Args:
        context: Handler context.

    Returns:
        Settings.
    """
    return context.bot_data["settings"]


async def reply(update: Update, text: str, **kwargs: object) -> None:
    """Reply to the message (or callback) that triggered a handler.

    Args:
        update: Telegram update.
        text: HTML text.
        **kwargs: Extra ``reply_text`` arguments.
    """
    if update.effective_message is not None:
        await update.effective_message.reply_text(text, **kwargs)  # type: ignore[arg-type]


def _int_arg(context: ContextTypes.DEFAULT_TYPE, index: int = 0) -> int | None:
    """Read an integer command argument.

    Args:
        context: Handler context.
        index: Argument position.

    Returns:
        The integer, or ``None`` if missing or invalid.
    """
    args = context.args or []
    if len(args) > index and args[index].lstrip("#").isdigit():
        return int(args[index].lstrip("#"))
    return None


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tell anyone their user ID and the chat ID (used to fill in ``.env``).

    Args:
        update: Telegram update.
        context: Handler context.
    """
    user_id = update.effective_user.id if update.effective_user else "?"
    chat_id = update.effective_chat.id if update.effective_chat else "?"
    await reply(
        update,
        f"Seu ID de usuário: <code>{user_id}</code>\n"
        f"ID deste chat: <code>{chat_id}</code>",
    )


@restricted("read")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the help text for the user's role.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    access = access_for(update, context)
    await reply(update, formatters.help_message(access.is_admin, access.is_jair))


@restricted("read")
async def jogos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List matches: upcoming (default), today, tomorrow, week or a round.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    args = [arg.lower() for arg in context.args or []]
    today = local_date(utc_now(), settings.timezone)
    with session(settings.db_path) as connection:
        jair_id = get_bettor_id(connection, JAIR_NAME)
        if args and args[0] in ("hoje", "amanha", "amanhã", "semana"):
            start_day = today + timedelta(days=1 if args[0].startswith("amanh") else 0)
            end_day = start_day + timedelta(days=6 if args[0] == "semana" else 0)
            title = {"hoje": "Jogos de hoje", "semana": "Jogos da semana"}.get(
                args[0], "Jogos de amanhã"
            )
            rows = list_matches(
                connection,
                start_day.isoformat(),
                end_day.isoformat(),
                bettor_id=jair_id,
            )
        elif len(args) >= 2 and args[0] == "rodada" and args[1].isdigit():
            title = f"Rodada {args[1]} · Brasileirão"
            rows = list_matches(
                connection,
                competition=BRASILEIRAO_CODE,
                round_number=int(args[1]),
                bettor_id=jair_id,
            )
        else:
            title = "Próximos jogos"
            rows = upcoming_matches(
                connection, today.isoformat(), jair_id, OPEN_MATCHES_LIMIT
            )
    text = formatters.matches_message(rows, title, settings.timezone)
    markup = None
    if access_for(update, context).can_bet:
        open_rows = [row for row in rows if bet_is_open(row, tz_name=settings.timezone)]
        markup = keyboards.matches_keyboard(open_rows[:10], settings.timezone)
        if markup:
            text += "\n\nToque num jogo para palpitar:"
    await reply(update, text, reply_markup=markup)


@restricted("read")
async def jogo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show one match with every bet.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    match_id = _int_arg(context)
    if match_id is None:
        await reply(update, "Use: /jogo &lt;id&gt; (o id aparece em /jogos).")
        return
    with session(settings.db_path) as connection:
        match = get_match(connection, match_id)
        bets = bets_for_match(connection, match_id) if match else []
    if match is None:
        await reply(update, f"Jogo #{match_id} não encontrado.")
        return
    await reply(update, formatters.match_card(match, bets, settings.timezone))


async def _show_goal_picker(
    update: Update, context: ContextTypes.DEFAULT_TYPE, match_id: int
) -> None:
    """Edit or send the home-goals picker for a match.

    Args:
        update: Telegram update.
        context: Handler context.
        match_id: Match identifier.
    """
    settings = settings_of(context)
    with session(settings.db_path) as connection:
        match = get_match(connection, match_id)
    if match is None or not bet_is_open(match, tz_name=settings.timezone):
        text = "As apostas para este jogo já estão fechadas."
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await reply(update, text)
        return
    context.user_data[PENDING_BET_KEY] = match_id  # type: ignore[index]
    text = (
        f"{formatters.teams(match)}\n"
        f"Quantos gols do <b>{formatters.esc(match['home_team'])}</b>?\n"
        "<i>Ou digite o placar, ex.: 2x1</i>"
    )
    markup = keyboards.goals_keyboard(match_id)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await reply(update, text, reply_markup=markup)


async def _save_jair_bet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    match_id: int,
    prediction: tuple[int, int],
) -> None:
    """Save Jair's bet and confirm it.

    Args:
        update: Telegram update.
        context: Handler context.
        match_id: Match identifier.
        prediction: Home and away goals.
    """
    settings = settings_of(context)
    try:
        with session(settings.db_path) as connection:
            outcome = save_bet(
                connection,
                match_id,
                JAIR_NAME,
                *prediction,
                actor_telegram_id=update.effective_user.id
                if update.effective_user
                else None,
                tz_name=settings.timezone,
            )
    except ServiceError as exc:
        text = f"⚠️ {formatters.esc(exc)}"
    else:
        text = formatters.bet_saved_message(
            outcome.match, outcome.prediction, outcome.animals, settings.timezone
        )
        context.user_data.pop(PENDING_BET_KEY, None)  # type: ignore[union-attr]
    if update.callback_query:
        await update.callback_query.edit_message_text(text)
    else:
        await reply(update, text)


@restricted("bet")
async def palpitar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bet directly (``/palpitar 12 2x1``) or pick an open match.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    match_id = _int_arg(context)
    args = context.args or []
    if match_id is not None and len(args) >= 2:
        try:
            prediction = parse_score_text("".join(args[1:]))
        except ValueError as exc:
            await reply(update, f"⚠️ {formatters.esc(exc)}")
            return
        await _save_jair_bet(update, context, match_id, prediction)
        return
    if match_id is not None:
        await _show_goal_picker(update, context, match_id)
        return
    today = local_date(utc_now(), settings.timezone).isoformat()
    with session(settings.db_path) as connection:
        jair_id = get_bettor_id(connection, JAIR_NAME)
        rows = upcoming_matches(connection, today, jair_id)
    open_rows = [
        row
        for row in rows
        if row["pred_home"] is None and bet_is_open(row, tz_name=settings.timezone)
    ][:OPEN_MATCHES_LIMIT]
    if not open_rows:
        await reply(
            update,
            "🎉 Nenhum jogo aberto sem palpite. Veja /meuspalpites para alterar.",
        )
        return
    await reply(
        update,
        "🎯 Escolha um jogo para palpitar:",
        reply_markup=keyboards.matches_keyboard(open_rows, settings.timezone),
    )


@restricted("bet")
async def meuspalpites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List Jair's open bets, with buttons to change them.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    today = local_date(utc_now(), settings.timezone).isoformat()
    with session(settings.db_path) as connection:
        jair_id = get_bettor_id(connection, JAIR_NAME)
        rows = upcoming_matches(connection, today, jair_id)
    mine = [
        row
        for row in rows
        if row["pred_home"] is not None and bet_is_open(row, tz_name=settings.timezone)
    ]
    text = formatters.matches_message(
        mine, "🎯 Meus palpites em aberto", settings.timezone
    )
    markup = keyboards.matches_keyboard(mine[:10], settings.timezone)
    if markup:
        text += "\n\nToque para alterar:"
    await reply(update, text, reply_markup=markup)


@restricted("bet")
async def on_pick_match(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback ``p:<match>``: start the goal picker.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    query = update.callback_query
    await query.answer()
    await _show_goal_picker(update, context, int(query.data.split(":")[1]))


@restricted("bet")
async def on_home_goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback ``gh:<match>:<h>``: ask for the away goals.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    query = update.callback_query
    await query.answer()
    _, match_id, home = query.data.split(":")
    settings = settings_of(context)
    with session(settings.db_path) as connection:
        match = get_match(connection, int(match_id))
    if match is None:
        await query.edit_message_text("Jogo não encontrado.")
        return
    await query.edit_message_text(
        f"{formatters.esc(match['home_team'])} <b>{home}</b> x ? "
        f"{formatters.esc(match['away_team'])}\n"
        f"Quantos gols do <b>{formatters.esc(match['away_team'])}</b>?",
        reply_markup=keyboards.goals_keyboard(int(match_id), int(home)),
    )


@restricted("bet")
async def on_away_goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback ``ga:<match>:<h>:<a>``: save the bet.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    query = update.callback_query
    await query.answer()
    _, match_id, home, away = query.data.split(":")
    await _save_jair_bet(update, context, int(match_id), (int(home), int(away)))


async def on_cancel_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback ``x``: close the picker.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    query = update.callback_query
    await query.answer()
    context.user_data.pop(PENDING_BET_KEY, None)  # type: ignore[union-attr]
    await query.edit_message_text("Cancelado.")


async def on_score_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Typed score (``2x1``) after choosing a match.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    match_id = (context.user_data or {}).get(PENDING_BET_KEY)
    if match_id is None or not access_for(update, context).can_bet:
        return
    try:
        prediction = parse_score_text(update.effective_message.text or "")
    except ValueError:
        return
    await _save_jair_bet(update, context, int(match_id), prediction)


@restricted("read")
async def resultados(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the latest results with every bet.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    limit = min(_int_arg(context) or 5, 15)
    with session(settings.db_path) as connection:
        matches = recent_results(connection, limit)
        cards = [
            formatters.match_card(
                match, bets_for_match(connection, match["match_id"]), settings.timezone
            )
            for match in matches
        ]
    if not cards:
        await reply(update, "Ainda não há resultados.")
        return
    await reply(update, formatters.truncate("\n\n".join(cards)))


@restricted("read")
async def ranking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the ranking: overall, by month, by year or by competition.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    arg = (context.args or [""])[0].strip()
    today = local_date(utc_now(), settings.timezone)
    competition = month = season = None
    title = "🏆 Ranking geral"
    if arg.lower() in ("mes", "mês"):
        month = today.strftime("%Y-%m")
    elif arg.lower() == "ano":
        season = str(today.year)
    elif len(arg) == 7 and arg[4] == "-":
        month = arg
    elif len(arg) == 4 and arg.isdigit():
        season = arg
    with session(settings.db_path) as connection:
        if arg and not (month or season):
            found = get_competition(connection, arg)
            if found is None:
                await reply(update, f"Competição {formatters.esc(arg)} não encontrada.")
                return
            competition = found["code"]
            title = f"🏆 Ranking · {found['name']}"
        if month:
            title = f"🏆 Ranking de {month[5:]}/{month[:4]}"
        if season:
            title = f"🏆 Ranking de {season}"
        rows = get_ranking(connection, competition, month, season)
    await reply(update, formatters.ranking_message(rows, title))


def _render_stats(settings: Settings) -> list[bytes]:
    """Render every stats chart (runs in a worker thread).

    Args:
        settings: Runtime settings.

    Returns:
        PNG images.
    """
    connection = connect(settings.db_path)
    try:
        scored = stats.scored_bets(connection)
        predictions = stats.all_predictions(connection)
    finally:
        connection.close()
    return [
        charts.cumulative_points(stats.cumulative_points(scored)),
        charts.monthly_points(stats.monthly_points(scored)),
        charts.head_to_head(stats.head_to_head(scored)),
        charts.hit_rates(stats.team_hit_rates(scored), JAIR_NAME),
        charts.predicted_scorelines(stats.top_scorelines(predictions)),
    ]


@restricted("read")
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the statistics charts as an album.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    await reply(update, "📊 Gerando gráficos…")
    images = await asyncio.to_thread(_render_stats, settings)
    captions = [
        "Pontos acumulados",
        "Pontos por mês",
        "Confronto direto",
        "Acertos do Jair por time",
        "Placares mais apostados",
    ]
    media = [
        InputMediaPhoto(image, caption=caption)
        for image, caption in zip(images, captions)
    ]
    await update.effective_message.reply_media_group(media)


@restricted("read")
async def time_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show a team's form and Corvo strengths.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    name = " ".join(context.args or []).strip()
    if not name:
        await reply(update, "Use: /time &lt;nome&gt;, ex.: /time Flamengo")
        return
    with session(settings.db_path) as connection:
        found = search_teams(connection, name, limit=1)
        if not found:
            await reply(update, f"Time {formatters.esc(name)} não encontrado.")
            return
        team_id, team_name = found[0]
        form, home, away, sample = team_profile(connection, team_id)
    await reply(update, formatters.team_message(team_name, form, home, away, sample))


@restricted("read")
async def corvo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show Corvo's metrics for one match, or its picks for the next matches.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    match_id = _int_arg(context)
    with session(settings.db_path) as connection:
        if match_id is not None:
            match = get_match(connection, match_id)
            if match is None:
                await reply(update, f"Jogo #{match_id} não encontrado.")
                return
            metrics = corvo_for_match(connection, match_id, settings.timezone)
            await reply(
                update, formatters.corvo_message(match, metrics, settings.timezone)
            )
            return
        today = local_date(utc_now(), settings.timezone).isoformat()
        rows = upcoming_matches(connection, today, limit=8)
        lines = [
            "🐦‍⬛ <b>Palpites do Corvo</b> (use /corvo &lt;id&gt; para detalhes)",
            "",
        ]
        for row in rows:
            metrics = corvo_for_match(connection, row["match_id"], settings.timezone)
            lines.append(
                f"<code>#{row['match_id']}</code> {formatters.esc(row['home_team'])} "
                f"<b>{formatters.score(*metrics.prediction)}</b> "
                f"{formatters.esc(row['away_team'])} · "
                f"{metrics.p_home:.0%}/{metrics.p_draw:.0%}/{metrics.p_away:.0%}"
            )
    if len(lines) == 2:
        lines.append("Nenhum jogo agendado.")
    await reply(update, "\n".join(lines), parse_mode=ParseMode.HTML)
