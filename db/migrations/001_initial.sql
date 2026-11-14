PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS bettor (
    bettor_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS team (
    team_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS team_alias (
    alias TEXT PRIMARY KEY COLLATE NOCASE,
    team_id INTEGER NOT NULL REFERENCES team(team_id)
);

CREATE TABLE IF NOT EXISTS match (
    match_id INTEGER PRIMARY KEY,
    match_date TEXT NOT NULL CHECK (date(match_date) = match_date),
    home_team_id INTEGER NOT NULL REFERENCES team(team_id),
    away_team_id INTEGER NOT NULL REFERENCES team(team_id),
    home_goals INTEGER CHECK (home_goals IS NULL OR home_goals >= 0),
    away_goals INTEGER CHECK (away_goals IS NULL OR away_goals >= 0),
    CHECK (home_team_id <> away_team_id),
    CHECK ((home_goals IS NULL) = (away_goals IS NULL)),
    UNIQUE (match_date, home_team_id, away_team_id)
);

CREATE TABLE IF NOT EXISTS bet (
    bet_id INTEGER PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES match(match_id) ON DELETE CASCADE,
    bettor_id INTEGER NOT NULL REFERENCES bettor(bettor_id),
    pred_home INTEGER NOT NULL CHECK (pred_home >= 0),
    pred_away INTEGER NOT NULL CHECK (pred_away >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    UNIQUE (match_id, bettor_id)
);

CREATE TABLE IF NOT EXISTS scoring_config (
    config_id INTEGER PRIMARY KEY CHECK (config_id = 1),
    points_winner INTEGER NOT NULL DEFAULT 3 CHECK (points_winner >= 0),
    bonus_exact INTEGER NOT NULL DEFAULT 3 CHECK (bonus_exact >= 0)
);

INSERT OR IGNORE INTO scoring_config (config_id, points_winner, bonus_exact)
VALUES (1, 3, 3);

CREATE INDEX IF NOT EXISTS idx_bet_bettor_id ON bet(bettor_id);
CREATE INDEX IF NOT EXISTS idx_match_match_date ON match(match_date);

CREATE VIEW IF NOT EXISTS v_bet_score AS
SELECT
    b.bet_id,
    b.match_id,
    bettor.name AS bettor,
    m.match_date,
    home.name AS home_team,
    away.name AS away_team,
    b.pred_home,
    b.pred_away,
    m.home_goals,
    m.away_goals,
    CASE WHEN m.home_goals IS NULL THEN NULL
         WHEN ((b.pred_home > b.pred_away) - (b.pred_home < b.pred_away)) =
              ((m.home_goals > m.away_goals) - (m.home_goals < m.away_goals))
         THEN 1 ELSE 0 END AS winner_hit,
    CASE WHEN m.home_goals IS NULL THEN NULL
         WHEN b.pred_home = m.home_goals AND b.pred_away = m.away_goals
         THEN 1 ELSE 0 END AS exact_hit,
    CASE WHEN m.home_goals IS NULL THEN NULL
         ELSE (CASE WHEN ((b.pred_home > b.pred_away) - (b.pred_home < b.pred_away)) =
                         ((m.home_goals > m.away_goals) - (m.home_goals < m.away_goals))
                    THEN sc.points_winner ELSE 0 END) +
              (CASE WHEN b.pred_home = m.home_goals AND b.pred_away = m.away_goals
                    THEN sc.bonus_exact ELSE 0 END)
    END AS points
FROM bet AS b
JOIN bettor ON bettor.bettor_id = b.bettor_id
JOIN match AS m ON m.match_id = b.match_id
JOIN team AS home ON home.team_id = m.home_team_id
JOIN team AS away ON away.team_id = m.away_team_id
CROSS JOIN scoring_config AS sc;

CREATE VIEW IF NOT EXISTS v_ranking AS
SELECT
    bettor,
    COUNT(*) AS matches,
    SUM(winner_hit) AS winner_hits,
    SUM(exact_hit) AS exact_hits,
    ROUND(100.0 * SUM(winner_hit) / COUNT(*), 1) AS pct_winner,
    ROUND(100.0 * SUM(exact_hit) / COUNT(*), 1) AS pct_exact,
    SUM(points) AS points,
    RANK() OVER (ORDER BY SUM(points) DESC) AS rank
FROM v_bet_score
WHERE points IS NOT NULL
GROUP BY bettor;
