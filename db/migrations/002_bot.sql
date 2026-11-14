-- Jairbet v2: competitions, API sync metadata, bettor kinds, audit and sync logs.

CREATE TABLE IF NOT EXISTS competition (
    competition_id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    provider TEXT,
    auto_sync INTEGER NOT NULL DEFAULT 0 CHECK (auto_sync IN (0, 1))
);

ALTER TABLE match ADD COLUMN competition_id INTEGER
    REFERENCES competition(competition_id);
ALTER TABLE match ADD COLUMN kickoff_at TEXT
    CHECK (kickoff_at IS NULL OR datetime(kickoff_at) = kickoff_at);
ALTER TABLE match ADD COLUMN status TEXT NOT NULL DEFAULT 'SCHEDULED'
    CHECK (status IN ('SCHEDULED', 'LIVE', 'FINISHED', 'POSTPONED', 'CANCELLED'));
ALTER TABLE match ADD COLUMN source TEXT NOT NULL DEFAULT 'import'
    CHECK (source IN ('api', 'manual', 'import'));
ALTER TABLE match ADD COLUMN external_id TEXT;
ALTER TABLE match ADD COLUMN round INTEGER;
ALTER TABLE match ADD COLUMN admin_locked INTEGER NOT NULL DEFAULT 0
    CHECK (admin_locked IN (0, 1));
ALTER TABLE match ADD COLUMN updated_at TEXT;

UPDATE match SET status = 'FINISHED' WHERE home_goals IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_match_external
    ON match(source, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_match_kickoff_at ON match(kickoff_at);
CREATE INDEX IF NOT EXISTS idx_match_competition ON match(competition_id);

ALTER TABLE bettor ADD COLUMN kind TEXT NOT NULL DEFAULT 'human'
    CHECK (kind IN ('human', 'random', 'inverse', 'model'));
ALTER TABLE bettor ADD COLUMN telegram_user_id INTEGER;
CREATE UNIQUE INDEX IF NOT EXISTS idx_bettor_telegram
    ON bettor(telegram_user_id) WHERE telegram_user_id IS NOT NULL;

ALTER TABLE bet ADD COLUMN source TEXT NOT NULL DEFAULT 'user'
    CHECK (source IN ('user', 'auto', 'admin'));

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY,
    at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor_telegram_id INTEGER,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id INTEGER,
    before_json TEXT,
    after_json TEXT
);

CREATE TABLE IF NOT EXISTS sync_run (
    run_id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    competition_code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'ok', 'error')),
    fetched INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

DROP VIEW IF EXISTS v_ranking;
DROP VIEW IF EXISTS v_bet_score;

CREATE VIEW v_bet_score AS
SELECT
    b.bet_id,
    b.match_id,
    bettor.name AS bettor,
    bettor.kind AS bettor_kind,
    b.source AS bet_source,
    m.match_date,
    m.kickoff_at,
    COALESCE(c.code, 'MANUAL') AS competition,
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
         ELSE (CASE WHEN ((b.pred_home > b.pred_away) - (b.pred_home < b.pred_away))
                         = ((m.home_goals > m.away_goals)
                            - (m.home_goals < m.away_goals))
                    THEN sc.points_winner ELSE 0 END) +
              (CASE WHEN b.pred_home = m.home_goals AND b.pred_away = m.away_goals
                    THEN sc.bonus_exact ELSE 0 END)
    END AS points
FROM bet AS b
JOIN bettor ON bettor.bettor_id = b.bettor_id
JOIN match AS m ON m.match_id = b.match_id
JOIN team AS home ON home.team_id = m.home_team_id
JOIN team AS away ON away.team_id = m.away_team_id
LEFT JOIN competition AS c ON c.competition_id = m.competition_id
CROSS JOIN scoring_config AS sc
WHERE m.status <> 'CANCELLED';

CREATE VIEW v_ranking AS
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
