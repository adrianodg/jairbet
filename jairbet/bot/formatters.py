"""pt-BR message text for the bot (Telegram HTML), as pure functions."""

import html
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from jairbet.bettors.corvo import CorvoMetrics, TeamForm
from jairbet.config import CARANGUEJO_NAME, CORVO_NAME, JAIR_NAME, MACACO_NAME
from jairbet.services import STATUS_LABELS
from jairbet.timeutil import format_kickoff

Row = Mapping[str, Any] | sqlite3.Row
MAX_MESSAGE = 3900

ICONS = {
    JAIR_NAME: "🧑",
    MACACO_NAME: "🐒",
    CARANGUEJO_NAME: "🦀",
    CORVO_NAME: "🐦‍⬛",
}
FORM_LETTERS = {"W": "V", "D": "E", "L": "D"}
FORM_EMOJI = {"W": "🟢", "D": "🟡", "L": "🔴"}


def esc(value: object) -> str:
    """Escape a value for Telegram HTML.

    Args:
        value: Any value.

    Returns:
        Escaped text.
    """
    return html.escape(str(value), quote=False)


def icon(bettor: str) -> str:
    """Return a bettor's emoji.

    Args:
        bettor: Bettor name.

    Returns:
        Emoji, or a generic one.
    """
    return ICONS.get(bettor, "👤")


def score(home: int | None, away: int | None) -> str:
    """Format a score.

    Args:
        home: Home goals.
        away: Away goals.

    Returns:
        ``2x1``, or ``–`` when unknown.
    """
    return "–" if home is None or away is None else f"{home}x{away}"


def truncate(text: str, limit: int = MAX_MESSAGE) -> str:
    """Keep a message under Telegram's size limit.

    Args:
        text: Message text.
        limit: Maximum characters.

    Returns:
        The text, cut on a line break with an ellipsis when too long.
    """
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > 0 else limit] + "\n…"


def join_newest_last(cards: Sequence[str], limit: int = MAX_MESSAGE) -> str:
    """Join match cards (oldest first), dropping the oldest ones if too long.

    Args:
        cards: Message blocks in chronological order.
        limit: Maximum characters.

    Returns:
        The blocks that fit, always keeping the most recent one.
    """
    kept = list(cards)
    while len(kept) > 1 and len("\n\n".join(kept)) > limit:
        kept.pop(0)
    return truncate("\n\n".join(kept), limit)


def teams(match: Row) -> str:
    """Return ``Home x Away`` in bold.

    Args:
        match: Match row.

    Returns:
        HTML text.
    """
    return f"<b>{esc(match['home_team'])}</b> x <b>{esc(match['away_team'])}</b>"


def result_line(match: Row) -> str:
    """Return ``Home 2x1 Away`` with the score in bold.

    Args:
        match: Match row with goals.

    Returns:
        HTML text.
    """
    result = score(match["home_goals"], match["away_goals"])
    return f"{esc(match['home_team'])} <b>{result}</b> {esc(match['away_team'])}"


def match_line(match: Row, tz_name: str, show_bet: bool = False) -> str:
    """One-line summary of a match for lists.

    Args:
        match: Match row (optionally with ``pred_home``/``pred_away``).
        tz_name: IANA timezone.
        show_bet: Append Jair's bet (or a warning when missing).

    Returns:
        HTML line.
    """
    when = format_kickoff(match["match_date"], match["kickoff_at"], tz_name)
    line = f"<code>#{match['match_id']}</code> {when} · "
    if match["home_goals"] is not None:
        line += result_line(match)
    else:
        line += teams(match)
    status = match["status"]
    if status in ("LIVE", "POSTPONED", "CANCELLED"):
        line += f" <i>({STATUS_LABELS[status]})</i>"
    if show_bet:
        keys = match.keys() if hasattr(match, "keys") else []
        if "pred_home" in keys and match["pred_home"] is not None:
            line += f" · 🎯 {score(match['pred_home'], match['pred_away'])}"
        elif match["home_goals"] is None and status in ("SCHEDULED", "POSTPONED"):
            line += " · ⚠️ sem palpite"
    return line


def matches_message(
    matches: Sequence[Row], title: str, tz_name: str, show_bet: bool = True
) -> str:
    """List matches grouped under a title.

    Args:
        matches: Match rows.
        title: Heading.
        tz_name: IANA timezone.
        show_bet: Show Jair's bet per match.

    Returns:
        HTML message.
    """
    if not matches:
        return f"<b>{esc(title)}</b>\n\nNenhum jogo encontrado."
    lines = [f"<b>{esc(title)}</b>", ""]
    current_competition = None
    for match in matches:
        if match["competition_name"] != current_competition:
            current_competition = match["competition_name"]
            lines.append(f"🏆 <i>{esc(current_competition)}</i>")
        lines.append(match_line(match, tz_name, show_bet))
    return truncate("\n".join(lines))


def bet_line(bet: Row) -> str:
    """Format one bettor's bet with its points.

    Args:
        bet: ``v_bet_score`` row.

    Returns:
        HTML line.
    """
    text = (
        f"{icon(bet['bettor'])} {esc(bet['bettor'])}: "
        f"<b>{score(bet['pred_home'], bet['pred_away'])}</b>"
    )
    if bet["points"] is not None:
        mark = "🎯" if bet["exact_hit"] else ("✅" if bet["winner_hit"] else "❌")
        text += f" {mark} {bet['points']} pts"
    return text


def match_card(match: Row, bets: Iterable[Row], tz_name: str) -> str:
    """Detailed view of a match with every bet.

    Args:
        match: Match row.
        bets: ``v_bet_score`` rows for the match.
        tz_name: IANA timezone.

    Returns:
        HTML message.
    """
    when = format_kickoff(match["match_date"], match["kickoff_at"], tz_name)
    lines = [
        f"🏆 {esc(match['competition_name'])} · <code>#{match['match_id']}</code>",
        f"🗓 {when} · {STATUS_LABELS.get(match['status'], match['status'])}"
        + (" 🔒" if match["admin_locked"] else ""),
    ]
    if match["home_goals"] is not None:
        lines.append(f"⚽ {result_line(match)}")
    else:
        lines.append(f"⚽ {teams(match)}")
    bet_lines = [bet_line(bet) for bet in bets]
    lines.append("")
    lines.extend(bet_lines or ["Sem palpites."])
    return "\n".join(lines)


def ranking_message(
    rows: Sequence[Row], title: str = "Ranking", note: str | None = None
) -> str:
    """Monospaced ranking table.

    Args:
        rows: ``v_ranking``-shaped rows.
        title: Heading.
        note: Optional footer.

    Returns:
        HTML message.
    """
    if not rows:
        empty = f"<b>{esc(title)}</b>\n\nAinda não há jogos pontuados."
        return f"{empty}\n\n{note}" if note else empty
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    header = f"{'':2} {'Apostador':<11}{'J':>4}{'V':>4}{'E':>4}{'Pts':>5}"
    lines = [header]
    for row in rows:
        lines.append(
            f"{row['rank']:>2} {row['bettor'][:11]:<11}{row['matches']:>4}"
            f"{row['winner_hits']:>4}{row['exact_hits']:>4}{row['points']:>5}"
        )
    leader = rows[0]
    podium = " ".join(
        f"{medals.get(row['rank'], '')}{icon(row['bettor'])}{esc(row['bettor'])}"
        for row in rows[:3]
    )
    text = (
        f"<b>{esc(title)}</b>\n{podium}\n\n<pre>{esc(chr(10).join(lines))}</pre>\n"
        "J = jogos, V = acertou vencedor/empate, E = placar exato"
    )
    if len(rows) > 1:
        gap = int(leader["points"]) - int(rows[1]["points"])
        text += f"\nVantagem do líder: <b>{gap}</b> pts"
    if note:
        text += f"\n\n{note}"
    return text


def form_text(form: TeamForm) -> str:
    """Format recent form as emoji plus pt-BR letters.

    Args:
        form: Team form.

    Returns:
        Text like ``🟢🟢🔴 (VVD)``.
    """
    if not form.form:
        return "sem jogos"
    emoji = "".join(FORM_EMOJI[letter] for letter in form.form)
    letters = "".join(FORM_LETTERS[letter] for letter in form.form)
    return f"{emoji} ({letters})"


def corvo_message(match: Row, metrics: CorvoMetrics, tz_name: str) -> str:
    """Explain Corvo's prediction for a match.

    Args:
        match: Match row.
        metrics: Corvo's metrics.
        tz_name: IANA timezone.

    Returns:
        HTML message.
    """
    home, away = esc(match["home_team"]), esc(match["away_team"])
    when = format_kickoff(match["match_date"], match["kickoff_at"], tz_name)
    sample = (
        f"{metrics.sample_size} jogos anteriores"
        if metrics.sample_size
        else "sem histórico (médias padrão)"
    )
    return (
        f"🐦‍⬛ <b>Corvo</b> · <code>#{match['match_id']}</code> {when}\n"
        f"{teams(match)}\n\n"
        f"Palpite: <b>{score(*metrics.prediction)}</b>\n"
        f"Gols esperados: {home} <b>{metrics.lambda_home:.2f}</b> x "
        f"<b>{metrics.lambda_away:.2f}</b> {away}\n"
        f"Chances: {home} {metrics.p_home:.0%} · empate {metrics.p_draw:.0%} · "
        f"{away} {metrics.p_away:.0%}\n\n"
        f"<b>{home}</b> em casa: ataque {metrics.attack_home:.2f}, "
        f"defesa {metrics.defence_home:.2f}\n"
        f"Forma: {form_text(metrics.home_form)} · "
        f"{metrics.home_form.goals_for:.1f} gols pró / "
        f"{metrics.home_form.goals_against:.1f} contra\n"
        f"<b>{away}</b> fora: ataque {metrics.attack_away:.2f}, "
        f"defesa {metrics.defence_away:.2f}\n"
        f"Forma: {form_text(metrics.away_form)} · "
        f"{metrics.away_form.goals_for:.1f} gols pró / "
        f"{metrics.away_form.goals_against:.1f} contra\n\n"
        f"<i>Base: {sample}. Força 1.00 = média da liga; defesa acima de 1 "
        "sofre mais gols que a média.</i>"
    )


def team_message(
    name: str,
    form: TeamForm,
    home_strengths: tuple[float, float],
    away_strengths: tuple[float, float],
    sample_size: int,
) -> str:
    """Summarise a team's recent performance.

    Args:
        name: Team name.
        form: Recent form.
        home_strengths: Attack and defence at home.
        away_strengths: Attack and defence away.
        sample_size: Matches in the model window.

    Returns:
        HTML message.
    """
    return (
        f"📊 <b>{esc(name)}</b>\n\n"
        f"Forma recente: {form_text(form)}\n"
        f"Últimos {form.matches} jogos: {form.goals_for:.1f} gols pró, "
        f"{form.goals_against:.1f} contra (média)\n\n"
        f"🏠 Em casa: ataque {home_strengths[0]:.2f}, defesa {home_strengths[1]:.2f}\n"
        f"✈️ Fora: ataque {away_strengths[0]:.2f}, defesa {away_strengths[1]:.2f}\n\n"
        f"<i>Força 1.00 = média da liga ({sample_size} jogos nos últimos 365 dias).</i>"
    )


def bet_saved_message(
    match: Row,
    prediction: tuple[int, int],
    animals: Mapping[str, tuple[int, int]],
    tz_name: str,
) -> str:
    """Confirm a saved bet and show the animals' answers.

    Args:
        match: Match row.
        prediction: Jair's prediction.
        animals: Animal bettor predictions.
        tz_name: IANA timezone.

    Returns:
        HTML message.
    """
    when = format_kickoff(match["match_date"], match["kickoff_at"], tz_name)
    lines = [
        f"✅ Palpite salvo · <code>#{match['match_id']}</code> {when}",
        f"{esc(match['home_team'])} <b>{score(*prediction)}</b> "
        f"{esc(match['away_team'])}",
    ]
    if animals:
        lines.append("")
        lines.extend(
            f"{icon(name)} {esc(name)}: <b>{score(*value)}</b>"
            for name, value in animals.items()
        )
    lines.append("\nVocê pode alterar até o início do jogo.")
    return "\n".join(lines)


def result_announcement(match: Row, bets: Iterable[Row], ranking: Sequence[Row]) -> str:
    """Group message when a match finishes.

    Args:
        match: Finished match row.
        bets: ``v_bet_score`` rows for the match.
        ranking: Current ranking rows.

    Returns:
        HTML message.
    """
    lines = [
        f"🏁 <b>Fim de jogo</b> · {esc(match['competition_name'])}",
        result_line(match),
        "",
    ]
    bet_lines = [bet_line(bet) for bet in bets]
    lines.extend(bet_lines or ["Ninguém palpitou neste jogo."])
    if ranking and bet_lines:
        lines.append("")
        lines.append(
            "📈 "
            + " · ".join(
                f"{row['rank']}º {esc(row['bettor'])} {row['points']}"
                for row in ranking
            )
        )
    return "\n".join(lines)


def help_message(is_admin: bool, is_jair: bool) -> str:
    """List the commands available to a user.

    Args:
        is_admin: The user is an admin.
        is_jair: The user is Jair.

    Returns:
        HTML message.
    """
    lines = [
        "⚽ <b>Jairbet</b> — palpites do Jair contra os bichos",
        "🧑 Jair · 🐒 Macaco (aleatório) · 🦀 Caranguejo (o contrário do Jair) · "
        "🐦‍⬛ Corvo (estatística)",
        "",
        "/jogos [hoje|amanha|semana|rodada N] — próximos jogos",
        "/jogo &lt;id&gt; — detalhes e palpites de um jogo",
        "/resultados [N] — últimos resultados",
        "/ranking [mes|AAAA-MM|ano|BSA] — classificação",
        "/stats — gráficos",
        "/time &lt;nome&gt; — desempenho de um time: forma, gols e força",
        "/corvo [id] — o que o Corvo calcula",
    ]
    if is_jair:
        lines += [
            "",
            "🎯 <b>Palpites</b>",
            "/palpitar — escolher um jogo e palpitar",
            "/palpitar &lt;id&gt; 2x1 — palpite direto",
            "/meuspalpites — palpites em aberto (alteráveis até o jogo)",
        ]
    if is_admin:
        lines += [
            "",
            "🛠 <b>Admin</b>",
            "/meuspalpites — palpites do Jair em aberto",
            "/novojogo — cadastrar jogo de qualquer campeonato",
            "/buscarjogo &lt;código&gt; — acompanhar jogo da API (ex.: CLI, CL)",
            "/editarjogo &lt;id&gt; &lt;campo&gt; &lt;valor&gt;",
            "/placar &lt;id&gt; 2x1|limpar",
            "/apagarjogo &lt;id&gt;",
            "/editarpalpite &lt;id&gt; &lt;apostador&gt; 2x1|apagar",
            "/pontuacao [vencedor exato]",
            "/competicoes — ligar/desligar sincronização",
            "/alias &lt;apelido&gt; = &lt;time&gt;",
            "/sync [código] · /log · /backup · /exportar · /importar",
            "/cancelar — cancela um cadastro em andamento",
        ]
    return "\n".join(lines)


def sync_summary(results: Sequence[Any], errors: Sequence[str]) -> str:
    """Summarise sync results for the admin.

    Args:
        results: ``SyncResult`` objects.
        errors: Error messages.

    Returns:
        HTML message.
    """
    lines = ["🔄 <b>Sincronização</b>"]
    for result in results:
        lines.append(
            f"{esc(result.competition)}: {result.fetched} recebidos, "
            f"{result.created} novos, {result.updated} atualizados"
        )
    lines.extend(f"⚠️ {esc(message)}" for message in errors)
    if len(lines) == 1:
        lines.append("Nada para sincronizar.")
    return "\n".join(lines)


def sync_runs_message(runs: Sequence[Row], audits: Sequence[Row]) -> str:
    """Show recent sync runs and admin changes.

    Args:
        runs: ``sync_run`` rows.
        audits: ``audit_log`` rows.

    Returns:
        HTML message.
    """
    icons = {"ok": "✅", "error": "❌", "running": "⏳"}
    lines = ["🔄 <b>Últimas sincronizações</b> (UTC)"]
    for run in runs:
        line = (
            f"{icons.get(run['status'], '•')} {run['started_at'][5:16]} "
            f"{esc(run['competition_code'])}: {run['fetched']}/{run['created']}/"
            f"{run['updated']}"
        )
        if run["error"]:
            line += f" — {esc(run['error'][:120])}"
        lines.append(line)
    if not runs:
        lines.append("Nenhuma ainda.")
    lines += ["", "🛠 <b>Últimas alterações do admin</b>"]
    lines += [
        f"• {audit['at'][5:16]} {esc(audit['action'])} {esc(audit['entity'])} "
        f"#{audit['entity_id']}"
        for audit in audits
    ] or ["Nenhuma ainda."]
    return truncate("\n".join(lines))


def competitions_message(rows: Sequence[Row]) -> str:
    """List competitions and their sync state.

    Args:
        rows: Competition rows with ``matches``.

    Returns:
        HTML message.
    """
    lines = ["🏆 <b>Competições</b> (toque para ligar/desligar a sincronização)"]
    for row in rows:
        state = "🔄 auto" if row["auto_sync"] else "✋ manual"
        lines.append(
            f"<code>{esc(row['code'])}</code> {esc(row['name'])} · {state} · "
            f"{row['matches']} jogos"
        )
    return "\n".join(lines)
