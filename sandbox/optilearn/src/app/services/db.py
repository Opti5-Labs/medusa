"""
app/services/db.py — SQLite schema initialisation and async helper functions.

All database operations use aiosqlite. The schema uses WAL mode and foreign keys.
Call init_db() from FastAPI's lifespan startup hook.
"""
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

import aiosqlite
import bcrypt
from loguru import logger

from app.core.config import settings
from app.core.grades import age_for_grade, normalize_grade_level


# ──────────────────────────────────────────────────────────────
# Schema DDL
# ──────────────────────────────────────────────────────────────
_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS students (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    age         INTEGER,
    language    TEXT DEFAULT 'en',
    grade_level TEXT DEFAULT '7th',
    username    TEXT,
    password_hash TEXT,
    pin_visible TEXT,
    is_registered INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now')),
    last_active TEXT
);

CREATE TABLE IF NOT EXISTS import_history (
    fingerprint TEXT PRIMARY KEY,
    student_id  TEXT NOT NULL REFERENCES students(id),
    imported_at TEXT DEFAULT (datetime('now')),
    imported_by TEXT
);

CREATE TABLE IF NOT EXISTS teachers (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    username    TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    email       TEXT,
    is_admin   INTEGER DEFAULT 0,
    original_password_hash TEXT,
    initial_password TEXT,
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    last_login TEXT,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS teacher_sessions (
    token      TEXT PRIMARY KEY,
    teacher_id TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    FOREIGN KEY (teacher_id) REFERENCES teachers(id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    student_id     TEXT REFERENCES students(id),
    started_at     TEXT DEFAULT (datetime('now')),
    ended_at       TEXT,
    message_count  INTEGER DEFAULT 0,
    topics_covered TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS quiz_results (
    id             TEXT PRIMARY KEY,
    student_id     TEXT REFERENCES students(id),
    quiz_id        TEXT REFERENCES teacher_quizzes(id),
    session_id     TEXT REFERENCES sessions(id),
    topic          TEXT NOT NULL,
    question_text  TEXT,
    student_answer TEXT,
    correct        INTEGER,
    score          REAL,
    timestamp      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topic_mastery (
    student_id   TEXT REFERENCES students(id),
    topic        TEXT NOT NULL,
    mastery      REAL DEFAULT 0.0,
    level        TEXT DEFAULT 'beginner',
    last_updated TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (student_id, topic)
);

CREATE INDEX IF NOT EXISTS idx_quiz_student    ON quiz_results(student_id);
CREATE INDEX IF NOT EXISTS idx_quiz_topic      ON quiz_results(topic);
CREATE INDEX IF NOT EXISTS idx_mastery_student ON topic_mastery(student_id);
CREATE INDEX IF NOT EXISTS idx_sessions_student ON sessions(student_id);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT REFERENCES sessions(id),
    role        TEXT NOT NULL,
    content     TEXT,
    tool_name   TEXT,
    timestamp   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS class_notes (
    id                  TEXT PRIMARY KEY,
    student_id          TEXT REFERENCES students(id),
    session_id          TEXT REFERENCES sessions(id),
    raw_transcript      TEXT,
    translated_chunks   TEXT DEFAULT '[]',
    notes_text          TEXT,
    notes_language      TEXT,
    subject             TEXT,
    faiss_indexed       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS materials (
    id            TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    title         TEXT NOT NULL,
    subject       TEXT,
    file_path     TEXT NOT NULL,
    uploaded_by   TEXT,
    student_id    TEXT,
    translated_text TEXT,
    target_language TEXT,
    source_language TEXT,
    detected_confidence REAL DEFAULT 0,
    material_type TEXT,
    page_count INTEGER DEFAULT 1,
    preview TEXT,
    tutor_summary TEXT,
    tutor_history TEXT DEFAULT '[]',
    faiss_indexed INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS teacher_quizzes (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    title       TEXT NOT NULL,
    subject     TEXT,
    questions   TEXT NOT NULL,
    assigned_to TEXT DEFAULT 'all',
    created_by  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_session   ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_materials_subject  ON materials(subject);
CREATE INDEX IF NOT EXISTS idx_materials_student  ON materials(student_id);
CREATE INDEX IF NOT EXISTS idx_tquiz_assigned     ON teacher_quizzes(assigned_to);
CREATE INDEX IF NOT EXISTS idx_teacher_sessions_teacher ON teacher_sessions(teacher_id);
CREATE INDEX IF NOT EXISTS idx_teacher_sessions_expiry  ON teacher_sessions(expires_at);

CREATE TABLE IF NOT EXISTS scheduled_classes (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    title       TEXT NOT NULL,
    subject     TEXT,
    description TEXT,
    start_datetime TEXT NOT NULL,
    end_datetime   TEXT NOT NULL,
    created_by  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS teacher_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS live_games (
    id                      TEXT PRIMARY KEY,
    quiz_id                 TEXT NOT NULL,
    teacher_id              TEXT NOT NULL,
    phase                   TEXT DEFAULT 'lobby',
    current_question_index  INTEGER DEFAULT 0,
    is_answer_revealed      INTEGER DEFAULT 0,
    quiz_title              TEXT DEFAULT '',
    subject                 TEXT DEFAULT '',
    questions_json          TEXT DEFAULT '[]',
    join_code               TEXT DEFAULT '',
    created_at              TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS live_participants (
    id          TEXT PRIMARY KEY,
    game_id     TEXT NOT NULL REFERENCES live_games(id),
    nickname    TEXT NOT NULL,
    student_id  TEXT REFERENCES students(id),
    mastery_recorded INTEGER DEFAULT 0,
    mastery_recorded_score REAL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS live_answers (
    id               TEXT PRIMARY KEY,
    game_id          TEXT NOT NULL REFERENCES live_games(id),
    question_index   INTEGER NOT NULL,
    participant_id   TEXT NOT NULL REFERENCES live_participants(id),
    choice_index     INTEGER,
    is_correct       INTEGER DEFAULT 0,
    score            INTEGER DEFAULT 0,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_live_participants_game ON live_participants(game_id);
CREATE INDEX IF NOT EXISTS idx_live_answers_game      ON live_answers(game_id);
CREATE INDEX IF NOT EXISTS idx_live_answers_participant ON live_answers(participant_id);

CREATE TABLE IF NOT EXISTS generated_cache (
    cache_key   TEXT PRIMARY KEY,
    feature     TEXT NOT NULL,
    input_hash  TEXT,
    payload     TEXT NOT NULL,
    metadata    TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_generated_cache_feature ON generated_cache(feature);

-- ── Shared Teacher-Led Live Class ─────────────────────────────
CREATE TABLE IF NOT EXISTS live_classes (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    teacher_id      TEXT REFERENCES teachers(id),
    title           TEXT NOT NULL,
    subject         TEXT,
    source_language TEXT NOT NULL DEFAULT 'en',
    audience_type   TEXT NOT NULL DEFAULT 'all',
    audience_ids    TEXT DEFAULT '[]',
    join_code       TEXT UNIQUE NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT DEFAULT (datetime('now')),
    started_at      TEXT DEFAULT (datetime('now')),
    ended_at        TEXT
);

CREATE TABLE IF NOT EXISTS live_class_chunks (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    session_id      TEXT NOT NULL REFERENCES live_classes(id),
    chunk_index     INTEGER NOT NULL,
    source_text     TEXT NOT NULL,
    detected_language TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(session_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS live_class_translations (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    session_id      TEXT NOT NULL REFERENCES live_classes(id),
    chunk_index     INTEGER NOT NULL,
    target_language TEXT NOT NULL,
    translated_text TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(session_id, chunk_index, target_language)
);

CREATE TABLE IF NOT EXISTS live_class_notes (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    session_id      TEXT NOT NULL REFERENCES live_classes(id),
    target_language TEXT NOT NULL,
    notes_text      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(session_id, target_language)
);

CREATE TABLE IF NOT EXISTS live_class_participants (
    id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    session_id      TEXT NOT NULL REFERENCES live_classes(id),
    student_id      TEXT,
    target_language TEXT NOT NULL,
    joined_at       TEXT DEFAULT (datetime('now')),
    last_seen_at    TEXT DEFAULT (datetime('now')),
    left_at         TEXT,
    UNIQUE(session_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_live_class_chunks_session ON live_class_chunks(session_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_live_class_translations_session ON live_class_translations(session_id, target_language, chunk_index);
CREATE INDEX IF NOT EXISTS idx_live_class_participants_session ON live_class_participants(session_id);

CREATE INDEX IF NOT EXISTS idx_students_last_active ON students(last_active);
"""


# ──────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────
def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    """Convert an aiosqlite Row to a plain dict."""
    return dict(row)


def _mastery_level(mastery: float) -> str:
    """Return mastery level label from a 0–1 mastery score."""
    if mastery >= 0.75:
        return "advanced"
    if mastery >= 0.40:
        return "intermediate"
    return "beginner"


@asynccontextmanager
async def _get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager that opens, configures, and closes a DB connection."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA foreign_keys=ON;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("PRAGMA cache_size=-32000;")
        await db.execute("PRAGMA temp_store=MEMORY;")
        await db.execute("PRAGMA mmap_size=134217728;")
        yield db


def _hash_secret(secret: str) -> str:
    """Hash a teacher password or student PIN with bcrypt."""
    return bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _parse_db_datetime_utc(value: str | None) -> datetime | None:
    """Parse SQLite ISO timestamps as naive UTC datetimes for safe comparisons."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _username_from_name(name: str, student_id: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", (name or "student").lower()).strip("_")
    stem = stem or "student"
    return f"{stem}_{student_id[:4].lower()}"


async def _student_username_exists(conn: aiosqlite.Connection, username: str, exclude_id: str | None = None) -> bool:
    if exclude_id:
        cursor = await conn.execute(
            "SELECT 1 FROM students WHERE lower(username) = lower(?) AND id != ? LIMIT 1",
            (username, exclude_id),
        )
    else:
        cursor = await conn.execute(
            "SELECT 1 FROM students WHERE lower(username) = lower(?) LIMIT 1",
            (username,),
        )
    return await cursor.fetchone() is not None


async def _unique_student_username(conn: aiosqlite.Connection, name: str, student_id: str) -> str:
    base = _username_from_name(name, student_id)
    candidate = base
    suffix = 2
    while await _student_username_exists(conn, candidate, exclude_id=student_id):
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _backup_path() -> Path:
    db_path = Path(settings.DB_PATH)
    backup_dir = db_path.parent if db_path.parent != Path("") else Path("data")
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir / f"student_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"


async def _migrate_students_for_auth(conn: aiosqlite.Connection) -> dict[str, Any]:
    """Back up and add auth fields for existing students without deleting data."""
    cursor = await conn.execute("SELECT * FROM students ORDER BY created_at ASC")
    rows = await cursor.fetchall()
    students = [_row_to_dict(r) for r in rows]

    migrated = [s for s in students if not (s.get("username") or "").strip()]
    backup = None
    if migrated:
        backup = _backup_path()
        backup.write_text(json.dumps(students, ensure_ascii=False, indent=2), encoding="utf-8")
        msg = f"Student auth migration backup: {backup}"
        print(msg)
        logger.info(msg)

    migrated_count = 0
    for student in students:
        sid = student["id"]
        grade = normalize_grade_level(student.get("grade_level"))
        age = age_for_grade(grade)
        await conn.execute(
            "UPDATE students SET grade_level = ?, age = ? WHERE id = ?",
            (grade, age, sid),
        )

        if (student.get("username") or "").strip():
            continue

        username = await _unique_student_username(conn, student.get("name") or "student", sid)
        pin = "1234"
        await conn.execute(
            """
            UPDATE students
            SET username = ?, password_hash = ?, pin_visible = ?, is_registered = 1
            WHERE id = ?
            """,
            (username, _hash_secret(pin), pin, sid),
        )
        migrated_count += 1

    await conn.commit()
    if migrated_count:
        msg = (
            f"Migrated {migrated_count} existing students. Default PIN: 1234. "
            "Teachers should ask students to update their PIN on first login."
        )
        print(msg)
        logger.info(msg)

    return {"migrated": migrated_count, "backup_file": str(backup) if backup else None}


async def _sync_mastery_from_quiz_summaries(conn: aiosqlite.Connection) -> None:
    """Rebuild topic mastery from completed quiz summary scores."""
    cursor = await conn.execute(
        """
        SELECT student_id,
               topic,
               AVG(score) AS avg_score,
               MAX(timestamp) AS last_timestamp
        FROM quiz_results
        WHERE question_text LIKE '%:summary'
        GROUP BY student_id, topic
        """
    )
    rows = await cursor.fetchall()

    for row in rows:
        mastery = float(row["avg_score"] or 0.0)
        level = _mastery_level(mastery)
        last_updated = row["last_timestamp"] or datetime.utcnow().isoformat()
        await conn.execute(
            """
            INSERT INTO topic_mastery (student_id, topic, mastery, level, last_updated)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, topic) DO UPDATE SET
                mastery = excluded.mastery,
                level = excluded.level,
                last_updated = excluded.last_updated
            """,
            (row["student_id"], row["topic"], mastery, level, last_updated),
        )

    if rows:
        await conn.commit()
        logger.info("Synced {} topic mastery rows from quiz summaries", len(rows))


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────
async def init_db() -> None:
    """Initialise the SQLite schema. Safe to call multiple times (IF NOT EXISTS)."""
    logger.info("Initialising database at {}", settings.DB_PATH)
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(_SCHEMA_SQL)
        await db.commit()
        # Additive migrations — safe to run on existing DBs (ignores duplicate column errors)
        for migration in [
            "ALTER TABLE students ADD COLUMN username TEXT",
            "ALTER TABLE students ADD COLUMN password_hash TEXT",
            "ALTER TABLE students ADD COLUMN pin_visible TEXT",
            "ALTER TABLE students ADD COLUMN is_registered INTEGER DEFAULT 0",
            "ALTER TABLE teachers ADD COLUMN initial_password TEXT",
            "ALTER TABLE teachers ADD COLUMN email TEXT",
            "ALTER TABLE teacher_quizzes ADD COLUMN created_by TEXT",
            "ALTER TABLE quiz_results ADD COLUMN quiz_id TEXT",
            "ALTER TABLE materials ADD COLUMN student_id TEXT",
            "ALTER TABLE materials ADD COLUMN translated_text TEXT",
            "ALTER TABLE materials ADD COLUMN target_language TEXT",
            "ALTER TABLE materials ADD COLUMN source_language TEXT",
            "ALTER TABLE materials ADD COLUMN detected_confidence REAL DEFAULT 0",
            "ALTER TABLE materials ADD COLUMN material_type TEXT",
            "ALTER TABLE materials ADD COLUMN page_count INTEGER DEFAULT 1",
            "ALTER TABLE materials ADD COLUMN preview TEXT",
            "ALTER TABLE materials ADD COLUMN tutor_summary TEXT",
            "ALTER TABLE materials ADD COLUMN tutor_history TEXT DEFAULT '[]'",
            "ALTER TABLE materials ADD COLUMN updated_at TEXT",
            "CREATE INDEX IF NOT EXISTS idx_materials_student_date ON materials(student_id, COALESCE(updated_at, created_at) DESC)",
            "ALTER TABLE class_notes ADD COLUMN created_at TEXT DEFAULT (datetime('now'))",
            "INSERT OR IGNORE INTO teacher_settings (key, value) VALUES ('master_language', 'en')",
            "INSERT OR IGNORE INTO teacher_settings (key, value) VALUES ('master_language_name', 'English')",
            "ALTER TABLE live_games ADD COLUMN join_code TEXT DEFAULT ''",
            "ALTER TABLE live_participants ADD COLUMN student_id TEXT",
            "ALTER TABLE live_participants ADD COLUMN mastery_recorded INTEGER DEFAULT 0",
            "ALTER TABLE live_participants ADD COLUMN mastery_recorded_score REAL",
        ]:
            try:
                await db.execute(migration)
                await db.commit()
            except Exception:
                pass
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_students_username ON students(username)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_teacher_sessions_teacher ON teacher_sessions(teacher_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_teacher_sessions_expiry ON teacher_sessions(expires_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_student ON sessions(student_id)")
        await db.commit()
        await _migrate_students_for_auth(db)
        await _sync_mastery_from_quiz_summaries(db)
    logger.info("Database schema ready.")


async def get_generated_cache(cache_key: str) -> dict[str, Any] | None:
    """Return a generated artifact cache entry by stable cache key."""
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM generated_cache WHERE cache_key = ?",
            (cache_key,),
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def set_generated_cache(
    *,
    cache_key: str,
    feature: str,
    payload: str,
    input_hash: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist generated artifact output across server restarts."""
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        await db.execute(
            """
            INSERT INTO generated_cache (
                cache_key, feature, input_hash, payload, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                feature = excluded.feature,
                input_hash = excluded.input_hash,
                payload = excluded.payload,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                cache_key,
                feature,
                input_hash,
                payload,
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.commit()


async def create_student(
    name: str,
    age: int | None,
    language: str = "en",
    grade_level: str | int = "7th",
) -> dict[str, Any]:
    """Create a new student record and return it as a dict."""
    student_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    grade = normalize_grade_level(grade_level)
    inferred_age = age_for_grade(grade)
    async with _get_db() as db:
        await db.execute(
            """
            INSERT INTO students (id, name, age, language, grade_level, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (student_id, name, inferred_age, language, grade, now),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT * FROM students WHERE id = ?", (student_id,)
        )
        row = await cursor.fetchone()
    logger.info("Created student id={} name={}", student_id, name)
    return _row_to_dict(row)


async def get_student(student_id: str) -> dict[str, Any] | None:
    """Retrieve a single student by ID. Returns None if not found."""
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM students WHERE id = ?", (student_id,)
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def update_last_active(student_id: str) -> None:
    """Update the last_active timestamp of a student to now (UTC, with Z suffix)."""
    now = datetime.utcnow().isoformat() + "Z"
    async with _get_db() as db:
        await db.execute(
            "UPDATE students SET last_active = ? WHERE id = ?",
            (now, student_id),
        )
        await db.commit()


async def count_teachers() -> int:
    async with _get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM teachers WHERE is_active = 1")
        row = await cursor.fetchone()
    return int(row["n"] if row else 0)


async def create_teacher(
    username: str,
    password_hash: str,
    display_name: str,
    is_admin: bool = False,
    initial_password: str | None = None,
    created_by: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    teacher_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        await db.execute(
            """
            INSERT INTO teachers (
                id, username, password_hash, display_name, email, is_admin,
                original_password_hash, initial_password, created_by, created_at, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                teacher_id,
                username.strip().lower(),
                password_hash,
                display_name.strip() or username.strip(),
                (email or "").strip().lower() or None,
                1 if is_admin else 0,
                password_hash,
                initial_password,
                created_by,
                now,
            ),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM teachers WHERE id = ?", (teacher_id,))
        row = await cursor.fetchone()
    return _row_to_dict(row)


async def get_teacher_by_username(username: str) -> dict[str, Any] | None:
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM teachers WHERE lower(username) = lower(?) AND is_active = 1",
            (username.strip(),),
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def get_teacher_by_id(teacher_id: str) -> dict[str, Any] | None:
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM teachers WHERE id = ? AND is_active = 1",
            (teacher_id,),
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def list_teachers(include_inactive: bool = True) -> list[dict[str, Any]]:
    async with _get_db() as db:
        if include_inactive:
            cursor = await db.execute("SELECT * FROM teachers ORDER BY is_active DESC, created_at ASC")
        else:
            cursor = await db.execute("SELECT * FROM teachers WHERE is_active = 1 ORDER BY created_at ASC")
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def update_teacher_profile(
    teacher_id: str,
    display_name: str | None = None,
    is_admin: bool | None = None,
) -> dict[str, Any] | None:
    updates: dict[str, Any] = {}
    if display_name is not None:
        updates["display_name"] = display_name.strip()
    if is_admin is not None:
        updates["is_admin"] = 1 if is_admin else 0
    if not updates:
        return await get_teacher_by_id(teacher_id)

    set_clause = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [teacher_id]
    async with _get_db() as db:
        await db.execute(f"UPDATE teachers SET {set_clause} WHERE id = ?", values)
        await db.commit()
        cursor = await db.execute("SELECT * FROM teachers WHERE id = ?", (teacher_id,))
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def update_teacher_password(
    teacher_id: str,
    password_hash: str,
    initial_password: str | None = None,
) -> None:
    async with _get_db() as db:
        if initial_password is None:
            await db.execute(
                "UPDATE teachers SET password_hash = ? WHERE id = ?",
                (password_hash, teacher_id),
            )
        else:
            await db.execute(
                "UPDATE teachers SET password_hash = ?, initial_password = ?, original_password_hash = ? WHERE id = ?",
                (password_hash, initial_password, password_hash, teacher_id),
            )
        await db.commit()


async def set_teacher_last_login(teacher_id: str) -> None:
    async with _get_db() as db:
        await db.execute(
            "UPDATE teachers SET last_login = ? WHERE id = ?",
            (datetime.utcnow().isoformat() + "Z", teacher_id),
        )
        await db.commit()


async def count_active_admins() -> int:
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM teachers WHERE is_active = 1 AND is_admin = 1"
        )
        row = await cursor.fetchone()
    return int(row["n"] if row else 0)


async def soft_delete_teacher(teacher_id: str) -> None:
    async with _get_db() as db:
        await db.execute("UPDATE teachers SET is_active = 0 WHERE id = ?", (teacher_id,))
        await db.execute("DELETE FROM teacher_sessions WHERE teacher_id = ?", (teacher_id,))
        await db.commit()


async def close_stale_live_classes() -> int:
    """Mark any live_classes still 'active' or 'paused' as 'ended'.

    Called once at server startup — if the process was killed mid-session the DB
    rows are left open, which makes students see phantom active sessions.
    Returns the number of rows closed.
    """
    now = datetime.utcnow().isoformat() + "Z"
    async with _get_db() as db:
        cursor = await db.execute(
            "UPDATE live_classes SET status='ended', ended_at=? "
            "WHERE status IN ('active','paused')",
            (now,),
        )
        await db.commit()
        return cursor.rowcount


async def purge_expired_sessions() -> int:
    """Delete all expired teacher sessions. Returns the number of rows deleted."""
    now = datetime.utcnow().isoformat() + "Z"
    async with _get_db() as db:
        cursor = await db.execute(
            "DELETE FROM teacher_sessions WHERE expires_at <= ?", (now,)
        )
        await db.commit()
        return cursor.rowcount


async def create_teacher_session(token: str, teacher_id: str, expires_at: datetime) -> None:
    async with _get_db() as db:
        await db.execute(
            """
            INSERT INTO teacher_sessions (token, teacher_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (token, teacher_id, datetime.utcnow().isoformat() + "Z", expires_at.isoformat() + "Z"),
        )
        await db.commit()


async def delete_teacher_session(token: str) -> None:
    async with _get_db() as db:
        await db.execute("DELETE FROM teacher_sessions WHERE token = ?", (token,))
        await db.commit()


async def invalidate_teacher_sessions(teacher_id: str, keep_token: str | None = None) -> None:
    async with _get_db() as db:
        if keep_token:
            await db.execute(
                "DELETE FROM teacher_sessions WHERE teacher_id = ? AND token != ?",
                (teacher_id, keep_token),
            )
        else:
            await db.execute("DELETE FROM teacher_sessions WHERE teacher_id = ?", (teacher_id,))
        await db.commit()


async def get_teacher_by_session(token: str) -> dict[str, Any] | None:
    now = datetime.utcnow().isoformat() + "Z"
    async with _get_db() as db:
        cursor = await db.execute(
            """
            SELECT t.*
            FROM teacher_sessions s
            JOIN teachers t ON t.id = s.teacher_id
            WHERE s.token = ? AND s.expires_at > ? AND t.is_active = 1
            """,
            (token, now),
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def get_student_by_username(username: str) -> dict[str, Any] | None:
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM students WHERE lower(username) = lower(?) LIMIT 1",
            (username.strip(),),
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def student_username_available(username: str) -> bool:
    clean = username.strip().lower()
    if not clean:
        return False
    async with _get_db() as db:
        return not await _student_username_exists(db, clean)


async def create_registered_student(
    name: str,
    username: str,
    pin_hash: str,
    pin_visible: str,
    language: str,
    grade_level: str | int,
) -> dict[str, Any]:
    student_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    grade = normalize_grade_level(grade_level)
    age = age_for_grade(grade)
    async with _get_db() as db:
        await db.execute(
            """
            INSERT INTO students (
                id, name, age, language, grade_level, username,
                password_hash, pin_visible, is_registered, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                student_id,
                name.strip(),
                age,
                language,
                grade,
                username.strip().lower(),
                pin_hash,
                pin_visible,
                now,
            ),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM students WHERE id = ?", (student_id,))
        row = await cursor.fetchone()
    return _row_to_dict(row)


async def list_students_for_admin(include_pin: bool = True) -> list[dict[str, Any]]:
    async with _get_db() as db:
        columns = "id, name, username, pin_visible, age, language, grade_level, created_at, last_active"
        if not include_pin:
            columns = "id, name, username, age, language, grade_level, created_at, last_active"
        cursor = await db.execute(f"SELECT {columns} FROM students ORDER BY name ASC")
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def reset_student_pin(student_id: str, pin_hash: str, pin_visible: str) -> dict[str, Any] | None:
    async with _get_db() as db:
        await db.execute(
            "UPDATE students SET password_hash = ?, pin_visible = ?, is_registered = 1 WHERE id = ?",
            (pin_hash, pin_visible, student_id),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT id, name, username, pin_visible, age, language, grade_level, created_at, last_active FROM students WHERE id = ?",
            (student_id,),
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def create_session(student_id: str) -> dict[str, Any]:
    """Create a new learning session for a student and return it as a dict."""
    session_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        await db.execute(
            """
            INSERT INTO sessions (id, student_id, started_at)
            VALUES (?, ?, ?)
            """,
            (session_id, student_id, now),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
    logger.info("Created session id={} student_id={}", session_id, student_id)
    return _row_to_dict(row)


async def end_session(session_id: str) -> None:
    """Mark a session as ended by recording the current timestamp."""
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        await db.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ?",
            (now, session_id),
        )
        await db.commit()


async def increment_message_count(session_id: str) -> None:
    """Atomically increment the message_count field of a session."""
    async with _get_db() as db:
        await db.execute(
            "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?",
            (session_id,),
        )
        await db.commit()


async def add_message(
    session_id: str,
    role: str,
    content: str | None,
    tool_name: str | None = None,
) -> dict[str, Any]:
    """Persist one chat message for a tutoring session."""
    message_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        await db.execute(
            """
            INSERT INTO messages (id, session_id, role, content, tool_name, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, session_id, role, content or "", tool_name, now),
        )
        await db.commit()
    return {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "content": content or "",
        "tool_name": tool_name,
        "timestamp": now,
    }


async def get_session_messages(session_id: str) -> list[dict[str, Any]]:
    """Return all saved messages for one tutoring session in display order."""
    async with _get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, session_id, role, content, tool_name, timestamp
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC, id ASC
            """,
            (session_id,),
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_student_chat_sessions(student_id: str) -> list[dict[str, Any]]:
    """Return saved AI Tutor sessions for one student, newest first."""
    async with _get_db() as db:
        cursor = await db.execute(
            """
            SELECT
                s.id,
                s.student_id,
                s.started_at,
                s.ended_at,
                s.message_count,
                (
                    SELECT m.content
                    FROM messages m
                    WHERE m.session_id = s.id
                      AND m.role IN ('user', 'assistant')
                      AND trim(COALESCE(m.content, '')) != ''
                    ORDER BY m.timestamp DESC, m.id DESC
                    LIMIT 1
                ) AS preview,
                (
                    SELECT m.timestamp
                    FROM messages m
                    WHERE m.session_id = s.id
                    ORDER BY m.timestamp DESC, m.id DESC
                    LIMIT 1
                ) AS last_message_at
            FROM sessions s
            WHERE s.student_id = ?
              AND EXISTS (
                  SELECT 1
                  FROM messages m
                  WHERE m.session_id = s.id
                    AND m.role IN ('user', 'assistant')
              )
            ORDER BY COALESCE(last_message_at, s.started_at) DESC
            LIMIT 30
            """,
            (student_id,),
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_session_for_student(session_id: str, student_id: str) -> dict[str, Any] | None:
    """Return a session only when it belongs to the given student."""
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE id = ? AND student_id = ?",
            (session_id, student_id),
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def record_quiz_result(
    student_id: str,
    quiz_id: str | None = None,
    session_id: str | None = None,
    topic: str = "",
    question_text: str = "",
    student_answer: str = "",
    correct: bool = False,
    score: float = 0.0,
) -> dict[str, Any]:
    """Persist a single quiz answer and return the stored record as a dict."""
    result_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        await db.execute(
            """
            INSERT INTO quiz_results
                (id, student_id, quiz_id, session_id, topic, question_text, student_answer, correct, score, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                student_id,
                quiz_id,
                session_id,
                topic,
                question_text,
                student_answer,
                1 if correct else 0,
                score,
                now,
            ),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT * FROM quiz_results WHERE id = ?", (result_id,)
        )
        row = await cursor.fetchone()
    return _row_to_dict(row)


async def update_mastery(
    student_id: str,
    topic: str,
    new_score: float,
) -> dict[str, Any]:
    """
    Update topic mastery using average score across completed topic quiz summaries.

    The incoming new_score is for the current completed quiz attempt and is
    combined with prior summary attempts for this topic.

    Returns: {"mastery": float, "level": str, "previous_level": str}
    """
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT mastery, level FROM topic_mastery WHERE student_id = ? AND topic = ?",
            (student_id, topic),
        )
        existing = await cursor.fetchone()

        if existing:
            old_mastery: float = existing["mastery"]
            previous_level: str = existing["level"]
        else:
            old_mastery = 0.0
            previous_level = "beginner"

        summary_cursor = await db.execute(
            """
            SELECT COUNT(*) AS cnt, COALESCE(SUM(score), 0.0) AS total
            FROM quiz_results
            WHERE student_id = ?
              AND topic = ?
              AND question_text LIKE '%:summary'
            """,
            (student_id, topic),
        )
        summary_row = await summary_cursor.fetchone()
        prior_count = int(summary_row["cnt"] or 0) if summary_row else 0
        prior_total = float(summary_row["total"] or 0.0) if summary_row else 0.0

        new_mastery = (prior_total + float(new_score)) / max(1, prior_count + 1)
        new_level = _mastery_level(new_mastery)

        if existing:
            await db.execute(
                """
                UPDATE topic_mastery
                SET mastery = ?, level = ?, last_updated = ?
                WHERE student_id = ? AND topic = ?
                """,
                (new_mastery, new_level, now, student_id, topic),
            )
        else:
            await db.execute(
                """
                INSERT INTO topic_mastery (student_id, topic, mastery, level, last_updated)
                VALUES (?, ?, ?, ?, ?)
                """,
                (student_id, topic, new_mastery, new_level, now),
            )

        await db.commit()

    logger.info(
        "Mastery update student={} topic={} {:.2f} → {:.2f} ({} → {})",
        student_id,
        topic,
        old_mastery,
        new_mastery,
        previous_level,
        new_level,
    )
    return {
        "mastery": round(new_mastery, 4),
        "level": new_level,
        "previous_level": previous_level,
    }


async def get_topic_mastery(student_id: str, topic: str) -> float:
    """Return current mastery for a student's topic, or 0.0 if no record exists."""
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT mastery FROM topic_mastery WHERE student_id = ? AND topic = ?",
            (student_id, topic),
        )
        row = await cursor.fetchone()
    return float(row["mastery"]) if row else 0.0


async def get_seen_question_ids(
    student_id: str,
    topic: str,
    question_ids: list[str],
) -> set[str]:
    """
    Return the subset of question_ids already attempted by this student for this topic.

    Used to reduce mastery inflation when the same activity is repeated.
    """
    unique_ids = [q.strip() for q in question_ids if q and q.strip()]
    if not unique_ids:
        return set()

    placeholders = ",".join("?" for _ in unique_ids)
    params: list[Any] = [student_id, topic, *unique_ids]
    sql = f"""
        SELECT DISTINCT question_text
        FROM quiz_results
        WHERE student_id = ?
          AND topic = ?
          AND question_text IN ({placeholders})
    """

    async with _get_db() as db:
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()

    return {str(r["question_text"]) for r in rows}


async def get_all_students() -> list[dict[str, Any]]:
    """Return all students with their latest mastery summary."""
    from collections import defaultdict
    async with _get_db() as db:
        cursor = await db.execute("SELECT * FROM students ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        students = [_row_to_dict(r) for r in rows]
        if not students:
            return students
        ph = ",".join("?" * len(students))
        m_cursor = await db.execute(
            f"SELECT student_id, topic, mastery, level FROM topic_mastery WHERE student_id IN ({ph}) ORDER BY mastery DESC",
            [s["id"] for s in students],
        )
        mastery_by_student: dict[str, list[dict]] = defaultdict(list)
        for r in await m_cursor.fetchall():
            mastery_by_student[r["student_id"]].append(_row_to_dict(r))
        for student in students:
            student["mastery_summary"] = mastery_by_student.get(student["id"], [])
    return students


async def get_student_mastery(student_id: str) -> list[dict[str, Any]]:
    """Return all topic mastery records for a given student."""
    async with _get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM topic_mastery
            WHERE student_id = ?
            ORDER BY mastery DESC
            """,
            (student_id,),
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_dashboard_data() -> dict[str, Any]:
    """
    Aggregate dashboard data for the teacher view.

    Returns:
        {
            "students": [...],
            "total_sessions": int,
            "topics_by_struggle": [...]
        }
    """
    from collections import defaultdict
    async with _get_db() as db:
        cursor = await db.execute("SELECT * FROM students ORDER BY created_at DESC")
        student_rows = await cursor.fetchall()
        students = [_row_to_dict(r) for r in student_rows]

        if students:
            ph = ",".join("?" * len(students))
            m_cursor = await db.execute(
                f"SELECT student_id, topic, mastery, level FROM topic_mastery WHERE student_id IN ({ph}) ORDER BY mastery DESC",
                [s["id"] for s in students],
            )
            mastery_by_student: dict[str, list[dict]] = defaultdict(list)
            for r in await m_cursor.fetchall():
                mastery_by_student[r["student_id"]].append(_row_to_dict(r))
            for student in students:
                student["mastery_summary"] = mastery_by_student.get(student["id"], [])

        count_cursor = await db.execute("SELECT COUNT(*) AS cnt FROM sessions")
        count_row = await count_cursor.fetchone()
        total_sessions: int = count_row["cnt"]

        struggle_cursor = await db.execute(
            """
            SELECT topic, AVG(mastery) AS avg_mastery, COUNT(*) AS student_count
            FROM topic_mastery
            GROUP BY topic
            ORDER BY avg_mastery ASC
            LIMIT 10
            """
        )
        struggle_rows = await struggle_cursor.fetchall()
        topics_by_struggle = [_row_to_dict(r) for r in struggle_rows]

    return {
        "students": students,
        "total_sessions": total_sessions,
        "topics_by_struggle": topics_by_struggle,
    }


async def get_teacher_students() -> list[dict[str, Any]]:
    """Return all students with mastery avg, last_active, and alert flags."""
    from collections import defaultdict
    async with _get_db() as db:
        cursor = await db.execute("SELECT * FROM students ORDER BY name ASC")
        rows = await cursor.fetchall()
        students = [_row_to_dict(r) for r in rows]

        if not students:
            return students

        now = datetime.utcnow()
        student_ids = [s["id"] for s in students]
        ph = ",".join("?" * len(student_ids))

        # Batch-fetch all mastery in one query instead of N queries
        m_cursor = await db.execute(
            f"SELECT student_id, topic, mastery, level FROM topic_mastery WHERE student_id IN ({ph})",
            student_ids,
        )
        all_mastery = await m_cursor.fetchall()
        mastery_by_student: dict[str, list[dict]] = defaultdict(list)
        for r in all_mastery:
            mastery_by_student[r["student_id"]].append(_row_to_dict(r))

        # Batch stuck_on_topic: students with low-mastery topics that have >= 3 attempts
        stuck_cursor = await db.execute(
            f"""
            SELECT qr.student_id
            FROM quiz_results qr
            JOIN topic_mastery tm ON tm.student_id = qr.student_id AND tm.topic = qr.topic
            WHERE qr.student_id IN ({ph}) AND tm.mastery < 0.40
            GROUP BY qr.student_id, qr.topic
            HAVING COUNT(*) >= 3
            """,
            student_ids,
        )
        stuck_set = {r["student_id"] for r in await stuck_cursor.fetchall()}

        # Batch level_dropped: fetch recent quiz scores ordered by time
        lv_cursor = await db.execute(
            f"""
            SELECT student_id, topic, score
            FROM quiz_results
            WHERE student_id IN ({ph})
            ORDER BY student_id, topic, timestamp DESC
            """,
            student_ids,
        )
        lv_rows = await lv_cursor.fetchall()
        scores_by_key: dict[tuple[str, str], list[float]] = defaultdict(list)
        for r in lv_rows:
            key = (r["student_id"], r["topic"])
            if len(scores_by_key[key]) < 4:
                scores_by_key[key].append(float(r["score"]))

        level_dropped_set: set[str] = set()
        for (sid, _topic), scores in scores_by_key.items():
            if len(scores) >= 4:
                recent_avg = sum(scores[:2]) / 2
                older_avg = sum(scores[2:4]) / 2
                if _mastery_level(recent_avg * 0.3) < _mastery_level(older_avg * 0.3):
                    level_dropped_set.add(sid)

        for student in students:
            sid = student["id"]
            mastery_list = mastery_by_student.get(sid, [])
            student["mastery_summary"] = mastery_list
            student["mastery_avg"] = (
                round(sum(m["mastery"] for m in mastery_list) / len(mastery_list), 4)
                if mastery_list else 0.0
            )

            alerts: list[str] = []

            last_active = _parse_db_datetime_utc(student.get("last_active"))
            if last_active is None or (now - last_active) > timedelta(days=3):
                alerts.append("inactive_3_days")

            if sid in stuck_set:
                alerts.append("stuck_on_topic")

            if sid in level_dropped_set:
                alerts.append("level_dropped")

            student["alerts"] = list(set(alerts))

        students.sort(key=lambda s: (0 if s["alerts"] else 1, s["name"]))

    return students


async def get_heatmap_data() -> dict[str, Any]:
    """Return heatmap grid: students × topics with mastery float and color."""
    def _color(val: float | None) -> str:
        if val is None:
            return "grey"
        if val < 0.40:
            return "red"
        if val < 0.75:
            return "amber"
        return "green"

    async with _get_db() as db:
        s_cursor = await db.execute("SELECT id, name FROM students ORDER BY name")
        student_rows = await s_cursor.fetchall()

        t_cursor = await db.execute(
            "SELECT DISTINCT topic FROM topic_mastery ORDER BY topic"
        )
        topic_rows = await t_cursor.fetchall()
        topics = [r["topic"] for r in topic_rows]

        # Batch-fetch all mastery in one query instead of N×M queries
        all_mastery_cursor = await db.execute("SELECT student_id, topic, mastery FROM topic_mastery")
        mastery_lookup: dict[tuple[str, str], float] = {}
        for r in await all_mastery_cursor.fetchall():
            mastery_lookup[(r["student_id"], r["topic"])] = r["mastery"]

        grid: list[list[dict | None]] = []
        for sr in student_rows:
            row_cells: list[dict | None] = []
            for topic in topics:
                val = mastery_lookup.get((sr["id"], topic))
                row_cells.append({"value": val, "color": _color(val)})
            grid.append(row_cells)

    return {
        "topics": topics,
        "students": [{"id": r["id"], "name": r["name"]} for r in student_rows],
        "grid": grid,
    }


async def get_student_progress(student_id: str) -> dict[str, Any] | None:
    """Return per-student progress: mastery by topic, recent quizzes, sessions, level."""
    student = await get_student(student_id)
    if student is None:
        return None

    async with _get_db() as db:
        m_cursor = await db.execute(
            "SELECT * FROM topic_mastery WHERE student_id = ? ORDER BY mastery DESC",
            (student_id,),
        )
        mastery_rows = await m_cursor.fetchall()
        mastery_by_topic = [_row_to_dict(r) for r in mastery_rows]

        q_cursor = await db.execute(
            """SELECT * FROM quiz_results WHERE student_id = ?
               ORDER BY timestamp DESC LIMIT 10""",
            (student_id,),
        )
        quiz_rows = await q_cursor.fetchall()
        recent_quizzes = [_row_to_dict(r) for r in quiz_rows]

        sess_cursor = await db.execute(
            """SELECT id, started_at, ended_at, message_count, topics_covered
               FROM sessions WHERE student_id = ? ORDER BY started_at DESC LIMIT 10""",
            (student_id,),
        )
        sess_rows = await sess_cursor.fetchall()
        sessions = [_row_to_dict(r) for r in sess_rows]

    overall_mastery = (
        sum(m["mastery"] for m in mastery_by_topic) / len(mastery_by_topic)
        if mastery_by_topic else 0.0
    )
    level = _mastery_level(overall_mastery)

    return {
        "student": student,
        "mastery_by_topic": mastery_by_topic,
        "recent_quizzes": recent_quizzes,
        "sessions": sessions,
        "level": level,
        "last_active": student.get("last_active"),
    }


async def create_material(
    title: str,
    subject: str | None,
    file_path: str,
    uploaded_by: str | None = None,
    student_id: str | None = None,
    target_language: str | None = None,
    source_language: str | None = None,
    detected_confidence: float | None = None,
    material_type: str | None = None,
    page_count: int | None = None,
    preview: str | None = None,
    faiss_indexed: bool = False,
) -> dict[str, Any]:
    """Insert a materials row and return it."""
    mat_id = str(uuid.uuid4())[:16].replace("-", "")
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        await db.execute(
            """
            INSERT INTO materials (
                id, title, subject, file_path, uploaded_by, student_id,
                target_language, source_language, detected_confidence,
                material_type, page_count, preview, faiss_indexed, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mat_id,
                title,
                subject,
                file_path,
                uploaded_by,
                student_id,
                target_language,
                source_language,
                detected_confidence or 0,
                material_type,
                page_count or 1,
                preview,
                1 if faiss_indexed else 0,
                now,
            ),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM materials WHERE id = ?", (mat_id,))
        row = await cursor.fetchone()
    return _row_to_dict(row)


async def get_material(material_id: str) -> dict[str, Any] | None:
    """Return a single material record by id (alias for get_material_by_id)."""
    return await get_material_by_id(material_id)


async def get_student_with_profile(student_id: str) -> dict[str, Any] | None:
    """Return student record combined with topic_mastery list."""
    student = await get_student(student_id)
    if student is None:
        return None
    student["topic_mastery"] = await get_student_mastery(student_id)
    return student


async def update_material_translation(
    material_id: str,
    translated_text: str,
    faiss_indexed: bool = True,
    target_language: str | None = None,
) -> None:
    """Persist the completed translation text and record whether vector indexing ran."""
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        await db.execute(
            """
            UPDATE materials
            SET translated_text = ?,
                faiss_indexed = ?,
                target_language = COALESCE(?, target_language),
                updated_at = ?
            WHERE id = ?
            """,
            (translated_text, 1 if faiss_indexed else 0, target_language, now, material_id),
        )
        await db.commit()


async def update_material_tutor_summary(
    material_id: str,
    tutor_summary: str,
) -> None:
    """Persist the AI tutor overview generated for a Feature 1 material."""
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        await db.execute(
            """
            UPDATE materials
            SET tutor_summary = ?, updated_at = ?
            WHERE id = ?
            """,
            (tutor_summary, now, material_id),
        )
        await db.commit()


async def append_material_tutor_history(
    material_id: str,
    question: str,
    answer: str,
) -> None:
    """Append one persisted tutor Q&A pair for a Feature 1 material."""
    import json as _json

    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT tutor_history FROM materials WHERE id = ?",
            (material_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return
        try:
            history = _json.loads(row["tutor_history"] or "[]")
            if not isinstance(history, list):
                history = []
        except (_json.JSONDecodeError, TypeError):
            history = []
        history.extend(
            [
                {"type": "question", "content": question},
                {"type": "answer", "content": answer},
            ]
        )
        await db.execute(
            """
            UPDATE materials
            SET tutor_history = ?, updated_at = ?
            WHERE id = ?
            """,
            (_json.dumps(history, ensure_ascii=False), now, material_id),
        )
        await db.commit()


async def get_all_materials(teacher_only: bool = False) -> list[dict[str, Any]]:
    """Return materials ordered by created_at desc. teacher_only=True filters to student_id IS NULL."""
    async with _get_db() as db:
        base_select = """
            SELECT m.*,
                   COALESCE(t.username, m.uploaded_by) AS uploaded_by_username,
                   t.display_name AS uploaded_by_display_name
            FROM materials m
            LEFT JOIN teachers t ON t.id = m.uploaded_by
        """
        if teacher_only:
            cursor = await db.execute(
                base_select + " WHERE m.student_id IS NULL ORDER BY m.created_at DESC"
            )
        else:
            cursor = await db.execute(base_select + " ORDER BY m.created_at DESC")
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def create_teacher_quiz(
    title: str,
    subject: str | None,
    questions: list[dict],
    assigned_to: str = "all",
    created_by: str | None = None,
) -> dict[str, Any]:
    """Insert a teacher_quizzes row and return it."""
    import json as _json
    quiz_id = str(uuid.uuid4())[:16].replace("-", "")
    async with _get_db() as db:
        await db.execute(
            """INSERT INTO teacher_quizzes (id, title, subject, questions, assigned_to, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (quiz_id, title, subject, _json.dumps(questions), assigned_to, created_by),
        )
        await db.commit()
        cursor = await db.execute(
            """
            SELECT q.*,
                   COALESCE(t.username, q.created_by) AS created_by_username,
                   t.display_name AS created_by_display_name
            FROM teacher_quizzes q
            LEFT JOIN teachers t ON t.id = q.created_by
            WHERE q.id = ?
            """,
            (quiz_id,),
        )
        row = await cursor.fetchone()
    result = _row_to_dict(row)
    result["questions"] = _json.loads(result["questions"])
    return result


async def get_all_teacher_quizzes() -> list[dict[str, Any]]:
    """Return all teacher quizzes, questions parsed from JSON."""
    import json as _json
    async with _get_db() as db:
        cursor = await db.execute(
            """
            SELECT q.*,
                   COALESCE(t.username, q.created_by) AS created_by_username,
                   t.display_name AS created_by_display_name
            FROM teacher_quizzes q
            LEFT JOIN teachers t ON t.id = q.created_by
            ORDER BY q.created_at DESC
            """
        )
        rows = await cursor.fetchall()
    result = []
    for r in rows:
        d = _row_to_dict(r)
        d["questions"] = _json.loads(d["questions"])
        result.append(d)
    return result


async def get_teacher_quiz(quiz_id: str) -> dict[str, Any] | None:
    """Return a single teacher quiz by id, questions parsed."""
    import json as _json
    async with _get_db() as db:
        cursor = await db.execute(
            """
            SELECT q.*,
                   COALESCE(t.username, q.created_by) AS created_by_username,
                   t.display_name AS created_by_display_name
            FROM teacher_quizzes q
            LEFT JOIN teachers t ON t.id = q.created_by
            WHERE q.id = ?
            """,
            (quiz_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    d = _row_to_dict(row)
    d["questions"] = _json.loads(d["questions"])
    return d


async def get_teacher_quizzes_for_student(student_id: str) -> list[dict[str, Any]]:
    """Return teacher quizzes assigned to this student or 'all'."""
    import json as _json
    async with _get_db() as db:
        cursor = await db.execute(
            """SELECT q.*,
                      COALESCE(t.username, q.created_by) AS created_by_username,
                      t.display_name AS created_by_display_name
               FROM teacher_quizzes q
               LEFT JOIN teachers t ON t.id = q.created_by
               WHERE q.assigned_to = 'all'
                  OR (',' || q.assigned_to || ',') LIKE ?
               ORDER BY q.created_at DESC""",
            (f"%,{student_id},%",),
        )
        rows = await cursor.fetchall()
    result = []
    for r in rows:
        d = _row_to_dict(r)
        d["questions"] = _json.loads(d["questions"])
        # verify exact student_id match when not 'all'
        if d["assigned_to"] != "all":
            ids = [x.strip() for x in d["assigned_to"].split(",")]
            if student_id not in ids:
                continue
        result.append(d)
    return result


async def get_subjects() -> list[str]:
    """Return all distinct non-null subjects from teacher materials and teacher_quizzes."""
    async with _get_db() as db:
        cursor = await db.execute("""
            SELECT DISTINCT subject FROM (
                SELECT subject FROM materials
                  WHERE subject IS NOT NULL AND subject != '' AND student_id IS NULL
                UNION
                SELECT subject FROM teacher_quizzes
                  WHERE subject IS NOT NULL AND subject != ''
            ) ORDER BY subject
        """)
        rows = await cursor.fetchall()
    return [r["subject"] for r in rows]


async def get_material_by_id(material_id: str) -> dict[str, Any] | None:
    """Return a single material record by id."""
    async with _get_db() as db:
        cursor = await db.execute("SELECT * FROM materials WHERE id = ?", (material_id,))
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def get_feature1_sessions(student_id: str) -> list[dict[str, Any]]:
    """Return saved Translate & Learn sessions for one student, newest first."""
    async with _get_db() as db:
        cursor = await db.execute(
            """
            SELECT
                id,
                title,
                subject,
                student_id,
                target_language,
                source_language,
                detected_confidence,
                material_type,
                page_count,
                preview,
                substr(translated_text, 1, 320) AS translated_preview,
                substr(tutor_summary, 1, 320) AS tutor_preview,
                faiss_indexed,
                created_at,
                updated_at
            FROM materials
            WHERE student_id = ?
              AND translated_text IS NOT NULL
              AND trim(translated_text) != ''
            ORDER BY COALESCE(updated_at, created_at) DESC
            """,
            (student_id,),
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_feature1_session(student_id: str, material_id: str) -> dict[str, Any] | None:
    """Return one saved Translate & Learn session for one student."""
    async with _get_db() as db:
        cursor = await db.execute(
            """
            SELECT *
            FROM materials
            WHERE id = ?
              AND student_id = ?
            """,
            (material_id, student_id),
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def get_materials_by_subject(subject: str) -> list[dict[str, Any]]:
    """Return materials for a given subject, newest first."""
    async with _get_db() as db:
        cursor = await db.execute(
            """
            SELECT m.*,
                   COALESCE(t.username, m.uploaded_by) AS uploaded_by_username,
                   t.display_name AS uploaded_by_display_name
            FROM materials m
            LEFT JOIN teachers t ON t.id = m.uploaded_by
            WHERE m.subject = ?
            ORDER BY m.created_at DESC
            """,
            (subject,),
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_teacher_quizzes_by_subject(subject: str, student_id: str) -> list[dict[str, Any]]:
    """Return teacher quizzes for a subject that are assigned to this student or 'all'."""
    import json as _json
    async with _get_db() as db:
        cursor = await db.execute(
            """SELECT q.*,
                      COALESCE(t.username, q.created_by) AS created_by_username,
                      t.display_name AS created_by_display_name
               FROM teacher_quizzes q
               LEFT JOIN teachers t ON t.id = q.created_by
               WHERE q.subject = ?
                 AND (q.assigned_to = 'all' OR q.assigned_to LIKE ?)
               ORDER BY q.created_at DESC""",
            (subject, f"%{student_id}%"),
        )
        rows = await cursor.fetchall()
    result = []
    for r in rows:
        d = _row_to_dict(r)
        d["questions"] = _json.loads(d["questions"])
        if d["assigned_to"] != "all":
            ids = [x.strip() for x in d["assigned_to"].split(",")]
            if student_id not in ids:
                continue
        result.append(d)
    return result


async def create_scheduled_class(
    title: str,
    subject: str | None,
    description: str | None,
    start_datetime: str,
    end_datetime: str,
    created_by: str | None = None,
) -> dict[str, Any]:
    class_id = str(uuid.uuid4())[:16].replace("-", "")
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        await db.execute(
            """INSERT INTO scheduled_classes
               (id, title, subject, description, start_datetime, end_datetime, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (class_id, title, subject, description, start_datetime, end_datetime, created_by, now),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM scheduled_classes WHERE id = ?", (class_id,))
        row = await cursor.fetchone()
    return _row_to_dict(row)


async def get_all_scheduled_classes() -> list[dict[str, Any]]:
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM scheduled_classes ORDER BY start_datetime ASC"
        )
        rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def get_scheduled_class(class_id: str) -> dict[str, Any] | None:
    async with _get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM scheduled_classes WHERE id = ?", (class_id,)
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def update_scheduled_class(class_id: str, **kwargs: Any) -> dict[str, Any] | None:
    allowed = {"title", "subject", "description", "start_datetime", "end_datetime"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return await get_scheduled_class(class_id)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [class_id]
    async with _get_db() as db:
        await db.execute(
            f"UPDATE scheduled_classes SET {set_clause} WHERE id = ?", values
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT * FROM scheduled_classes WHERE id = ?", (class_id,)
        )
        row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def delete_scheduled_class(class_id: str) -> None:
    async with _get_db() as db:
        await db.execute("DELETE FROM scheduled_classes WHERE id = ?", (class_id,))
        await db.commit()


async def get_teacher_settings() -> dict[str, str]:
    async with _get_db() as db:
        cursor = await db.execute("SELECT key, value FROM teacher_settings")
        rows = await cursor.fetchall()
    return {r["key"]: r["value"] for r in rows}


async def update_teacher_setting(key: str, value: str) -> None:
    now = datetime.utcnow().isoformat()
    async with _get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO teacher_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )
        await db.commit()


# ──────────────────────────────────────────────────────────────
# Allow running this file directly to initialise the schema
# ──────────────────────────────────────────────────────────────
async def count_recent_active_students(minutes: int = 10) -> int:
    """Count students who look connected based on recent activity or live sessions."""
    cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
    async with _get_db() as db:
        cursor = await db.execute(
            """
            SELECT COUNT(DISTINCT s.id) AS cnt
            FROM students s
            LEFT JOIN sessions sess ON sess.student_id = s.id
            WHERE s.last_active >= ?
               OR (sess.ended_at IS NULL AND sess.started_at >= ?)
            """,
            (cutoff, cutoff),
        )
        row = await cursor.fetchone()
    return int(row["cnt"] if row else 0)


if __name__ == "__main__":
    import asyncio

    asyncio.run(init_db())
    print("Database initialised.")
