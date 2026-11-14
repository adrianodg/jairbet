"""Admin-only commands: manage matches, bets, scoring, competitions and data."""

import asyncio
import tempfile
from datetime import timedelta
from pathlib import Path

from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from jairbet.bot import formatters, keyboards
from jairbet.bot.auth import restricted
from jairbet.bot.handlers.user import reply, settings_of
from jairbet.bot.jobs import (
    announce_results,
    handle_sync_outcome,
    make_backup,
    send_backup,
    sync_blocking,
)
from jairbet.config import JAIR_NAME, MANUAL_CODE, Settings
from jairbet.db import session
from jairbet.exporter import export_workbook
from jairbet.importer import import_workbook
from jairbet.providers.base import ProviderError
from jairbet.providers.football_data import FootballDataProvider
from jairbet.repository import (
    add_competition,
    bets_for_match,
    get_competition_by_id,
    get_competitions,
    get_match,
    get_ranking,
    get_scoring_config,
    get_team,
    query_rows,
    recent_sync_runs,
    set_competition_auto_sync,
)
from jairbet.services import (
    ServiceError,
    change_scoring,
    create_manual_match,
    edit_match,
    parse_date_text,
    parse_score_text,
    parse_time_text,
    remove_bet,
    remove_match,
    save_bet,
    search_teams,
    set_alias,
    set_result,
)
from jairbet.sync import track_match
from jairbet.timeutil import utc_now

COMP, NEW_COMP, HOME, AWAY, DAY, CLOCK, CONFIRM = range(7)
AWAIT_IMPORT_KEY = "await_import"
TRACK_DAYS = 14
TRACK_LIMIT = 15


def _actor(update: Update) -> int | None:
    """Return the Telegram ID of the user making a change.

    Args:
        update: Telegram update.

    Returns:
        User ID, or ``None``.
    """
    return update.effective_user.id if update.effective_user else None


def _match_id(args: list[str]) -> int | None:
    """Parse the first argument as a match ID.

    Args:
        args: Command arguments.

    Returns:
        Match ID, or ``None``.
    """
    if args and args[0].lstrip("#").isdigit():
        return int(args[0].lstrip("#"))
    return None


async def _send_card(update: Update, settings: Settings, match_id: int) -> None:
    """Reply with a match card.

    Args:
        update: Telegram update.
        settings: Runtime settings.
        match_id: Match identifier.
    """
    with session(settings.db_path) as connection:
        match = get_match(connection, match_id)
        bets = bets_for_match(connection, match_id) if match else []
    if match is not None:
        await reply(update, formatters.match_card(match, bets, settings.timezone))


# --- /novojogo conversation ----------------------------------------------------


@restricted("admin")
async def new_match_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start /novojogo: pick a competition.

    Args:
        update: Telegram update.
        context: Handler context.

    Returns:
        Next conversation state.
    """
    settings = settings_of(context)
    context.user_data["new_match"] = {}  # type: ignore[index]
    with session(settings.db_path) as connection:
        competitions = get_competitions(connection)
    await reply(
        update,
        "🆕 <b>Novo jogo</b> — qual competição? (/cancelar para sair)",
        reply_markup=keyboards.competition_picker_keyboard(competitions),
    )
    return COMP


async def new_match_competition(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Competition chosen (``nc:<code>``) or new one requested (``nc:+``).

    Args:
        update: Telegram update.
        context: Handler context.

    Returns:
        Next conversation state.
    """
    query = update.callback_query
    await query.answer()
    code = query.data.split(":", 1)[1]
    if code == "+":
        await query.edit_message_text(
            "Digite o código e o nome da competição, ex.: "
            "<code>CDB Copa do Brasil</code>"
        )
        return NEW_COMP
    context.user_data["new_match"]["competition"] = code  # type: ignore[index]
    await query.edit_message_text(f"Competição: <b>{formatters.esc(code)}</b>")
    await reply(update, "Qual é o time <b>mandante</b>?")
    return HOME


async def new_match_new_competition(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Create a manual competition from ``CODE Name``.

    Args:
        update: Telegram update.
        context: Handler context.

    Returns:
        Next conversation state.
    """
    code, _, name = (update.effective_message.text or "").strip().partition(" ")
    if not code or not name.strip() or len(code) > 10:
        await reply(
            update, "Formato: <code>CÓDIGO Nome</code>, ex.: CDB Copa do Brasil"
        )
        return NEW_COMP
    settings = settings_of(context)
    with session(settings.db_path) as connection:
        add_competition(connection, code, name)
    context.user_data["new_match"]["competition"] = code.upper()  # type: ignore[index]
    await reply(update, f"Competição <b>{formatters.esc(name)}</b> criada.")
    await reply(update, "Qual é o time <b>mandante</b>?")
    return HOME


async def _ask_team(
    update: Update, context: ContextTypes.DEFAULT_TYPE, side: str
) -> int | None:
    """Resolve a typed team name or offer suggestions.

    Args:
        update: Telegram update.
        context: Handler context.
        side: ``home`` or ``away``.

    Returns:
        ``None`` when the team was accepted, otherwise the state to stay in.
    """
    typed = (update.effective_message.text or "").strip()
    draft = context.user_data["new_match"]  # type: ignore[index]
    draft[f"{side}_typed"] = typed
    settings = settings_of(context)
    with session(settings.db_path) as connection:
        found = search_teams(connection, typed)
    if len(found) == 1 and found[0][1].casefold() == typed.casefold():
        draft[side] = found[0][1]
        return None
    await reply(
        update,
        f"Não encontrei exatamente “{formatters.esc(typed)}”. É algum destes?",
        reply_markup=keyboards.team_suggestions_keyboard(found, typed),
    )
    return HOME if side == "home" else AWAY


async def new_match_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Home team typed.

    Args:
        update: Telegram update.
        context: Handler context.

    Returns:
        Next conversation state.
    """
    pending = await _ask_team(update, context, "home")
    if pending is not None:
        return pending
    await reply(update, "Qual é o time <b>visitante</b>?")
    return AWAY


async def new_match_away(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Away team typed.

    Args:
        update: Telegram update.
        context: Handler context.

    Returns:
        Next conversation state.
    """
    pending = await _ask_team(update, context, "away")
    if pending is not None:
        return pending
    await reply(update, "Qual a <b>data</b>? (dd/mm/aaaa)")
    return DAY


async def new_match_team_choice(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """A suggested team (``ns:<id>``) or "create new" (``ns:new``) was chosen.

    Args:
        update: Telegram update.
        context: Handler context.

    Returns:
        Next conversation state.
    """
    query = update.callback_query
    await query.answer()
    draft = context.user_data["new_match"]  # type: ignore[index]
    side = "home" if "home" not in draft else "away"
    choice = query.data.split(":", 1)[1]
    if choice == "new":
        name = draft[f"{side}_typed"]
    else:
        with session(settings_of(context).db_path) as connection:
            team = get_team(connection, int(choice))
        name = team["name"] if team else draft[f"{side}_typed"]
    draft[side] = name
    await query.edit_message_text(f"✅ {formatters.esc(name)}")
    if side == "home":
        await reply(update, "Qual é o time <b>visitante</b>?")
        return AWAY
    await reply(update, "Qual a <b>data</b>? (dd/mm/aaaa)")
    return DAY


async def new_match_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Match date typed.

    Args:
        update: Telegram update.
        context: Handler context.

    Returns:
        Next conversation state.
    """
    try:
        day = parse_date_text(update.effective_message.text or "")
    except ServiceError as exc:
        await reply(update, f"⚠️ {formatters.esc(exc)}")
        return DAY
    context.user_data["new_match"]["day"] = day  # type: ignore[index]
    await reply(update, "Qual o <b>horário</b> (Brasília)? HH:MM, ou - se não souber.")
    return CLOCK


async def new_match_clock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Kickoff time typed; show the summary for confirmation.

    Args:
        update: Telegram update.
        context: Handler context.

    Returns:
        Next conversation state.
    """
    try:
        clock = parse_time_text(update.effective_message.text or "")
    except ServiceError as exc:
        await reply(update, f"⚠️ {formatters.esc(exc)}")
        return CLOCK
    draft = context.user_data["new_match"]  # type: ignore[index]
    draft["clock"] = clock
    when = draft["day"].strftime("%d/%m/%Y") + (
        f" {clock:%H:%M}" if clock else " (sem horário)"
    )
    await reply(
        update,
        "Confirma?\n"
        f"🏆 {formatters.esc(draft.get('competition', MANUAL_CODE))}\n"
        f"⚽ <b>{formatters.esc(draft['home'])}</b> x "
        f"<b>{formatters.esc(draft['away'])}</b>\n🗓 {when}",
        reply_markup=keyboards.confirm_new_match_keyboard(),
    )
    return CONFIRM


async def new_match_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Create the match (``nk:y``) or cancel (``nk:n``).

    Args:
        update: Telegram update.
        context: Handler context.

    Returns:
        ``ConversationHandler.END``.
    """
    query = update.callback_query
    await query.answer()
    draft = context.user_data.pop("new_match", {})  # type: ignore[union-attr]
    if query.data != "nk:y":
        await query.edit_message_text("Cancelado.")
        return ConversationHandler.END
    settings = settings_of(context)
    try:
        with session(settings.db_path) as connection:
            match_id = create_manual_match(
                connection,
                draft.get("competition", MANUAL_CODE),
                draft["home"],
                draft["away"],
                draft["day"],
                draft["clock"],
                _actor(update),
                settings.timezone,
            )
    except ServiceError as exc:
        await query.edit_message_text(f"⚠️ {formatters.esc(exc)}")
        return ConversationHandler.END
    await query.edit_message_text(f"✅ Jogo <code>#{match_id}</code> criado.")
    await _send_card(update, settings, match_id)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel a conversation.

    Args:
        update: Telegram update.
        context: Handler context.

    Returns:
        ``ConversationHandler.END``.
    """
    context.user_data.pop("new_match", None)  # type: ignore[union-attr]
    context.user_data.pop(AWAIT_IMPORT_KEY, None)  # type: ignore[union-attr]
    await reply(update, "Cancelado.")
    return ConversationHandler.END


def new_match_conversation() -> ConversationHandler:
    """Build the /novojogo conversation handler.

    Returns:
        Conversation handler.
    """
    text = filters.TEXT & ~filters.COMMAND
    team_choice = CallbackQueryHandler(new_match_team_choice, pattern=r"^ns:")
    return ConversationHandler(
        entry_points=[CommandHandler("novojogo", new_match_start)],
        states={
            COMP: [CallbackQueryHandler(new_match_competition, pattern=r"^nc:")],
            NEW_COMP: [MessageHandler(text, new_match_new_competition)],
            HOME: [MessageHandler(text, new_match_home), team_choice],
            AWAY: [MessageHandler(text, new_match_away), team_choice],
            DAY: [MessageHandler(text, new_match_day)],
            CLOCK: [MessageHandler(text, new_match_clock)],
            CONFIRM: [CallbackQueryHandler(new_match_confirm, pattern=r"^nk:")],
        },
        fallbacks=[CommandHandler("cancelar", cancel)],
        conversation_timeout=600,
    )


# --- Match and bet editing -----------------------------------------------------


@restricted("admin")
async def buscarjogo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List a competition's upcoming API fixtures to track one.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    args = context.args or []
    if not args:
        await reply(
            update,
            "Use: /buscarjogo &lt;código&gt;\nCódigos do plano grátis: BSA, CLI "
            "(Libertadores), CL (Champions), PL, PD, SA, BL1, FL1, PPL, DED, ELC, "
            "WC, EC.",
        )
        return
    if not settings.football_data_token:
        await reply(update, "⚠️ FOOTBALL_DATA_TOKEN não configurado.")
        return
    code = args[0].upper()
    today = utc_now().date()

    def fetch() -> list:
        provider = FootballDataProvider(settings.football_data_token)
        try:
            return provider.fetch_matches(
                code, today, today + timedelta(days=TRACK_DAYS)
            )
        finally:
            provider.close()

    try:
        fixtures = await asyncio.to_thread(fetch)
    except ProviderError as exc:
        await reply(update, f"⚠️ {formatters.esc(exc)}")
        return
    fixtures = [m for m in fixtures if m.status in ("SCHEDULED", "POSTPONED")]
    if not fixtures:
        await reply(update, f"Nenhum jogo de {code} nos próximos {TRACK_DAYS} dias.")
        return
    await reply(
        update,
        f"📡 Próximos jogos de <b>{formatters.esc(code)}</b>. Toque para acompanhar:",
        reply_markup=keyboards.track_keyboard(
            fixtures[:TRACK_LIMIT], settings.timezone
        ),
    )


@restricted("admin")
async def on_track(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback ``tr:<external_id>``: start tracking a provider match.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    query = update.callback_query
    await query.answer()
    settings = settings_of(context)
    external_id = query.data.split(":", 1)[1]

    def track() -> int | None:
        provider = FootballDataProvider(settings.football_data_token)
        try:
            with session(settings.db_path) as connection:
                return track_match(
                    connection, provider, external_id, settings.timezone
                )[0]
        finally:
            provider.close()

    try:
        match_id = await asyncio.to_thread(track)
    except ProviderError as exc:
        await query.edit_message_text(f"⚠️ {formatters.esc(exc)}")
        return
    if match_id is None:
        await query.edit_message_text("⚠️ Não foi possível cadastrar o jogo.")
        return
    await query.edit_message_text(
        f"✅ Jogo <code>#{match_id}</code> cadastrado e acompanhado."
    )
    await _send_card(update, settings, match_id)


@restricted("admin")
async def editarjogo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit one field of a match: ``/editarjogo 12 hora 21:30``.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    args = context.args or []
    match_id = _match_id(args)
    if match_id is None:
        await reply(
            update,
            "Use: /editarjogo &lt;id&gt; &lt;campo&gt; &lt;valor&gt;\nCampos: data "
            "(dd/mm/aaaa), hora (HH:MM), mandante, visitante, status (agendado, "
            "adiado, cancelado, encerrado), competicao (código), rodada, travar, "
            "destravar.\nEdições travam o jogo contra a sincronização.",
        )
        return
    if len(args) < 2:
        await _send_card(update, settings, match_id)
        return
    try:
        with session(settings.db_path) as connection:
            message = edit_match(
                connection,
                match_id,
                args[1],
                " ".join(args[2:]),
                _actor(update),
                settings.timezone,
            )
    except ServiceError as exc:
        await reply(update, f"⚠️ {formatters.esc(exc)}")
        return
    await reply(update, f"✅ {formatters.esc(message)}")
    await _send_card(update, settings, match_id)


@restricted("admin")
async def placar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set or clear a result: ``/placar 12 2x1`` or ``/placar 12 limpar``.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    args = context.args or []
    match_id = _match_id(args)
    if match_id is None or len(args) < 2:
        await reply(update, "Use: /placar &lt;id&gt; 2x1 (ou limpar)")
        return
    value = "".join(args[1:])
    try:
        score = None if value.lower() == "limpar" else parse_score_text(value)
        with session(settings.db_path) as connection:
            newly_finished = set_result(connection, match_id, score, _actor(update))
    except (ServiceError, ValueError) as exc:
        await reply(update, f"⚠️ {formatters.esc(exc)}")
        return
    await reply(update, "✅ Placar salvo (jogo travado contra a sincronização).")
    await _send_card(update, settings, match_id)
    if newly_finished:
        await announce_results(context.bot, settings, [match_id])


@restricted("admin")
async def apagarjogo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask for confirmation before deleting a match.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    match_id = _match_id(context.args or [])
    if match_id is None:
        await reply(update, "Use: /apagarjogo &lt;id&gt;")
        return
    with session(settings.db_path) as connection:
        match = get_match(connection, match_id)
    if match is None:
        await reply(update, f"Jogo #{match_id} não encontrado.")
        return
    await reply(
        update,
        f"Apagar {formatters.teams(match)} e todos os palpites?",
        reply_markup=keyboards.confirm_delete_keyboard(match_id),
    )


@restricted("admin")
async def on_delete_match(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback ``dm:<match>:<y|n>``.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    query = update.callback_query
    await query.answer()
    _, match_id, answer = query.data.split(":")
    if answer != "y":
        await query.edit_message_text("Cancelado.")
        return
    try:
        with session(settings_of(context).db_path) as connection:
            match = remove_match(connection, int(match_id), _actor(update))
    except ServiceError as exc:
        await query.edit_message_text(f"⚠️ {formatters.esc(exc)}")
        return
    await query.edit_message_text(f"🗑 {formatters.teams(match)} apagado.")


@restricted("admin")
async def editarpalpite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit any bet: ``/editarpalpite 12 Corvo 1x1`` or ``... apagar``.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    args = context.args or []
    match_id = _match_id(args)
    if match_id is None or len(args) < 3:
        await reply(
            update,
            "Use: /editarpalpite &lt;id&gt; &lt;apostador&gt; 2x1 (ou apagar)\n"
            "Apostadores: Jair, Macaco, Caranguejo, Corvo.",
        )
        return
    bettor, value = args[1], "".join(args[2:])
    try:
        with session(settings.db_path) as connection:
            if value.lower() == "apagar":
                remove_bet(connection, match_id, bettor, _actor(update))
            else:
                save_bet(
                    connection,
                    match_id,
                    bettor,
                    *parse_score_text(value),
                    actor_telegram_id=_actor(update),
                    as_admin=True,
                    tz_name=settings.timezone,
                )
    except (ServiceError, ValueError) as exc:
        await reply(update, f"⚠️ {formatters.esc(exc)}")
        return
    note = (
        "" if bettor.casefold() == JAIR_NAME.casefold() else " (não será recalculado)"
    )
    await reply(update, f"✅ Palpite atualizado{note}.")
    await _send_card(update, settings, match_id)


@restricted("admin")
async def pontuacao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show or change the scoring: ``/pontuacao 3 3``.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    args = context.args or []
    with session(settings.db_path) as connection:
        if len(args) == 2 and all(arg.isdigit() for arg in args):
            try:
                change_scoring(connection, int(args[0]), int(args[1]), _actor(update))
            except ServiceError as exc:
                await reply(update, f"⚠️ {formatters.esc(exc)}")
                return
            note = (
                "⚠️ A nova pontuação vale para <b>todos</b> os jogos, inclusive os já "
                "disputados (o ranking foi recalculado)."
            )
            await reply(
                update,
                formatters.ranking_message(get_ranking(connection), "🏆 Ranking", note),
            )
            return
        config = get_scoring_config(connection)
    await reply(
        update,
        f"Pontuação atual: vencedor/empate = <b>{config['points_winner']}</b>, "
        f"bônus placar exato = <b>{config['bonus_exact']}</b>.\n"
        "Para mudar: /pontuacao &lt;vencedor&gt; &lt;exato&gt; (vale retroativamente).",
    )


@restricted("admin")
async def competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List competitions with auto-sync toggles.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    with session(settings_of(context).db_path) as connection:
        rows = get_competitions(connection)
    await reply(
        update,
        formatters.competitions_message(rows),
        reply_markup=keyboards.competitions_keyboard(rows),
    )


@restricted("admin")
async def on_toggle_competition(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback ``ct:<competition>``: toggle auto sync.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    query = update.callback_query
    await query.answer()
    competition_id = int(query.data.split(":")[1])
    with session(settings_of(context).db_path) as connection:
        competition = get_competition_by_id(connection, competition_id)
        if competition is not None:
            set_competition_auto_sync(
                connection, competition_id, not competition["auto_sync"]
            )
        rows = get_competitions(connection)
    await query.edit_message_text(
        formatters.competitions_message(rows),
        reply_markup=keyboards.competitions_keyboard(rows),
    )


@restricted("admin")
async def alias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Map a name variant to a team: ``/alias Atlético-MG = Galo``.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    text = " ".join(context.args or [])
    left, sep, right = text.partition("=")
    if not sep or not left.strip() or not right.strip():
        await reply(
            update,
            "Use: /alias &lt;apelido&gt; = &lt;time&gt;\nSe o apelido for um time "
            "criado por engano, os dois são unidos.",
        )
        return
    try:
        with session(settings_of(context).db_path) as connection:
            message = set_alias(connection, left.strip(), right.strip(), _actor(update))
    except ServiceError as exc:
        await reply(update, f"⚠️ {formatters.esc(exc)}")
        return
    await reply(update, f"✅ {formatters.esc(message)}")


@restricted("admin")
async def sync_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Force a sync now: ``/sync`` or ``/sync BSA``.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    codes = [arg.upper() for arg in context.args or []] or None
    await reply(update, "🔄 Sincronizando…")
    results, errors = await asyncio.to_thread(sync_blocking, settings, codes)
    await reply(update, formatters.sync_summary(results, errors))
    await handle_sync_outcome(context.application, results, [])


@restricted("admin")
async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent sync runs and admin changes.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    with session(settings_of(context).db_path) as connection:
        runs = recent_sync_runs(connection, 10)
        audits = query_rows(
            connection, "SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT 10"
        )
    await reply(update, formatters.sync_runs_message(runs, audits))


@restricted("admin")
async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create and send a database backup.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    path = await asyncio.to_thread(make_backup, settings)
    await send_backup(context.bot, update.effective_chat.id, path)


@restricted("admin")
async def exportar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the database as an Excel workbook.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    settings = settings_of(context)
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / f"jairbet-{utc_now():%Y%m%d}.xlsx"
        await asyncio.to_thread(export_workbook, settings.db_path, target)
        with target.open("rb") as handle:
            await update.effective_message.reply_document(handle, filename=target.name)


@restricted("admin")
async def importar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask for an .xlsx file to import.

    Args:
        update: Telegram update.
        context: Handler context.
    """
    context.user_data[AWAIT_IMPORT_KEY] = True  # type: ignore[index]
    await reply(
        update,
        "📥 Envie o arquivo .xlsx (mesmo layout da planilha Apostas). "
        "A importação é segura para repetir. /cancelar para desistir.",
    )


@restricted("admin")
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Import an uploaded workbook after /importar (or with caption /importar).

    Args:
        update: Telegram update.
        context: Handler context.
    """
    message = update.effective_message
    document = message.document
    caption = (message.caption or "").strip().lower()
    waiting = context.user_data.pop(AWAIT_IMPORT_KEY, False)  # type: ignore[union-attr]
    if document is None or not (waiting or caption.startswith("/importar")):
        return
    if not (document.file_name or "").lower().endswith(".xlsx"):
        await reply(update, "⚠️ Envie um arquivo .xlsx.")
        return
    settings = settings_of(context)
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "upload.xlsx"
        telegram_file = await document.get_file()
        await telegram_file.download_to_drive(source)
        await asyncio.to_thread(make_backup, settings)
        try:
            summary = await asyncio.to_thread(import_workbook, source, settings.db_path)
        except Exception as exc:  # shown to the admin; the import rolled back
            await reply(update, f"⚠️ Importação falhou: {formatters.esc(exc)}")
            return
    with session(settings.db_path) as connection:
        ranking = get_ranking(connection)
    await reply(
        update,
        f"✅ Importado: {summary.rows_read} linhas, {summary.matches} jogos, "
        f"{summary.bets} palpites, {summary.skipped_rows} ignoradas, "
        f"{summary.fixes_applied} correções.\n\n" + formatters.ranking_message(ranking),
    )
