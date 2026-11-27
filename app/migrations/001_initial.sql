-- Calendar-date validation and reservation state transitions belong to services.
CREATE TABLE reservations (
    id TEXT NOT NULL PRIMARY KEY CHECK (
        typeof(id) = 'text' AND length(id) = 36 AND instr(id, char(0)) = 0
        AND substr(id, 9, 1) = '-' AND substr(id, 14, 1) = '-'
        AND substr(id, 19, 1) = '-' AND substr(id, 24, 1) = '-'
        AND length(replace(id, '-', '')) = 32
        AND replace(id, '-', '') NOT GLOB '*[^0-9a-f]*'
    ),
    name TEXT NOT NULL,
    memo TEXT NOT NULL DEFAULT '',
    cancel_token_hash TEXT NOT NULL CHECK (
        typeof(cancel_token_hash) = 'text'
        AND length(cancel_token_hash) = 64 AND instr(cancel_token_hash, char(0)) = 0
        AND cancel_token_hash NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED', 'CANCELLED')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    cancelled_at TEXT
);

CREATE TABLE reserved_dates (
    date TEXT NOT NULL PRIMARY KEY,
    reservation_id TEXT REFERENCES reservations(id) ON DELETE RESTRICT,
    type TEXT NOT NULL CHECK (
        (type = 'RESERVED' AND reservation_id IS NOT NULL)
        OR (type = 'BLOCKED' AND reservation_id IS NULL)
    ),
    memo TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_reserved_dates_reservation_id ON reserved_dates(reservation_id);

CREATE TABLE reservation_logs (
    id INTEGER PRIMARY KEY,
    reservation_id TEXT REFERENCES reservations(id) ON DELETE RESTRICT,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_reservation_logs_reservation_id ON reservation_logs(reservation_id);
CREATE INDEX idx_reservation_logs_created_at ON reservation_logs(created_at, id);

CREATE TABLE settings (
    key TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL
);
