"""Compatibility tests for the read-only Session Lens API."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import Mock, patch


def _stamp(epoch: int) -> str:
    """Render an epoch as the local-time log stamp `_timestamp_from_log` parses.

    Fixture DB rows store raw epochs while Hermes log lines carry local-time
    strings; deriving the strings from the same epochs keeps the two aligned in
    every timezone (a literal stamp only lines up on a UTC runner).
    """
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S,000")


MODULE_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "_routes.py"
from dashboard import _classify as classify
from dashboard import _hermes_compat as hermes_compat
from dashboard import _routes as api


def tool_rows(name: str, arguments: dict | None = None, result: str = "Done!"):
    call_id = f"call-{name}-{abs(hash(json.dumps(arguments or {}, sort_keys=True)))}"
    calls = [{
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {})},
    }]
    return [
        {"role": "assistant", "tool_calls": json.dumps(calls)},
        {"role": "tool", "tool_call_id": call_id, "tool_name": name, "content": result},
    ]


SESSION_MODEL_USAGE_INSERT_SQL = """
    INSERT INTO session_model_usage (
        session_id,model,billing_provider,billing_mode,task,api_call_count,
        input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,
        reasoning_tokens,estimated_cost_usd,actual_cost_usd,cost_status,
        cost_source,first_seen,last_seen
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""
ASYNC_DELEGATION_INSERT_SQL = """
    INSERT INTO async_delegations (
        delegation_id,origin_session,parent_session_id,state,dispatched_at,
        completed_at,updated_at,delivery_state
    ) VALUES (?,?,?,?,?,?,?,?)
"""


class FakeSessionDB:
    path: Path

    def __init__(self, db_path: Path | None = None, read_only: bool = False):
        assert read_only is True, "Session Lens must never request a writable DB"
        self.db_path = Path(db_path) if db_path else self.path
        self.read_only = True
        self._conn = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

    def close(self):
        self._conn.close()

    def resolve_session_id(self, value):
        row = self._conn.execute("SELECT id FROM sessions WHERE id=?", (value,)).fetchone()
        return row[0] if row else None

    def search_messages(self, query, limit=20, fields=()):
        del fields
        term = query.replace('"', "").replace("*", "").strip().lower()
        rows = self._conn.execute(
            """
            SELECT session_id, role, substr(content,1,100) AS snippet
            FROM messages WHERE lower(coalesce(content,'')) LIKE ? LIMIT ?
            """,
            (f"%{term}%", limit),
        ).fetchall()
        return [dict(row) for row in rows]


class SessionLensApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.db_path = self.home / "state.db"
        self.original_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(self.home)
        connection = sqlite3.connect(self.db_path)
        connection.executescript(
            """
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (26);

            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                user_id TEXT,
                session_key TEXT,
                chat_id TEXT,
                chat_type TEXT,
                thread_id TEXT,
                display_name TEXT,
                origin_json TEXT,
                expiry_finalized INTEGER DEFAULT 0,
                model TEXT,
                model_config TEXT,
                system_prompt TEXT,
                parent_session_id TEXT,
                started_at REAL NOT NULL,
                ended_at REAL,
                end_reason TEXT,
                message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                reasoning_tokens INTEGER DEFAULT 0,
                cwd TEXT,
                git_branch TEXT,
                git_repo_root TEXT,
                billing_provider TEXT,
                billing_base_url TEXT,
                billing_mode TEXT,
                estimated_cost_usd REAL,
                actual_cost_usd REAL,
                cost_status TEXT,
                cost_source TEXT,
                pricing_version TEXT,
                title TEXT,
                api_call_count INTEGER DEFAULT 0,
                handoff_state TEXT,
                handoff_platform TEXT,
                handoff_error TEXT,
                compression_failure_cooldown_until REAL,
                compression_failure_error TEXT,
                compression_fallback_streak INTEGER NOT NULL DEFAULT 0,
                profile_name TEXT,
                rewind_count INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                compression_ineffective_count INTEGER NOT NULL DEFAULT 0,
                pinned INTEGER NOT NULL DEFAULT 0,
                system_prompt_hash TEXT,
                last_activity_at REAL,
                last_activity_description TEXT,
                last_activity_provenance TEXT,
                last_read_at REAL,
                git_metadata_generation INTEGER NOT NULL DEFAULT 0,
                title_source TEXT,
                hidden INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                tool_calls TEXT,
                tool_name TEXT,
                effect_disposition TEXT,
                timestamp REAL NOT NULL,
                token_count INTEGER,
                finish_reason TEXT,
                reasoning TEXT,
                reasoning_content TEXT,
                reasoning_details TEXT,
                codex_reasoning_items TEXT,
                codex_message_items TEXT,
                platform_message_id TEXT,
                observed INTEGER DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                compacted INTEGER NOT NULL DEFAULT 0,
                api_content TEXT,
                display_kind TEXT,
                display_metadata TEXT,
                _compressed_summary INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE session_model_usage (
                session_id TEXT NOT NULL,
                model TEXT NOT NULL,
                billing_provider TEXT NOT NULL DEFAULT '',
                billing_base_url TEXT NOT NULL DEFAULT '',
                billing_mode TEXT NOT NULL DEFAULT '',
                task TEXT NOT NULL DEFAULT '',
                api_call_count INTEGER NOT NULL DEFAULT 0,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost_usd REAL NOT NULL DEFAULT 0,
                actual_cost_usd REAL NOT NULL DEFAULT 0,
                cost_status TEXT,
                cost_source TEXT,
                first_seen REAL,
                last_seen REAL,
                PRIMARY KEY (session_id, model, billing_provider, billing_base_url, billing_mode, task)
            );

            CREATE TABLE async_delegations (
                delegation_id TEXT PRIMARY KEY,
                origin_session TEXT NOT NULL,
                origin_ui_session_id TEXT NOT NULL DEFAULT '',
                parent_session_id TEXT,
                state TEXT NOT NULL,
                dispatched_at REAL NOT NULL,
                completed_at REAL,
                updated_at REAL NOT NULL,
                event_json TEXT,
                result_json TEXT,
                delivery_state TEXT NOT NULL DEFAULT 'pending',
                delivery_attempts INTEGER NOT NULL DEFAULT 0,
                delivered_at REAL,
                owner_pid INTEGER,
                owner_started_at INTEGER,
                task_json TEXT,
                delivery_claim TEXT,
                delivery_claimed_at REAL,
                origin_session_id TEXT
            );

            CREATE VIRTUAL TABLE messages_fts USING fts5(content);
            CREATE VIRTUAL TABLE messages_fts_trigram USING fts5(content);
            """
        )
        connection.execute(
            """
            INSERT INTO sessions (
                id,source,model,started_at,ended_at,end_reason,message_count,tool_call_count,
                input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,
                cwd,billing_provider,billing_mode,estimated_cost_usd,actual_cost_usd,
                cost_status,cost_source,title,last_activity_at,api_call_count
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "session-1",
                "desktop",
                "provider/model-a",
                1_800_000_000,
                1_800_000_120,
                "cron_complete",
                4,
                2,
                1000,
                250,
                500,
                0,
                "C:\\work\\demo",
                "provider",
                "metered",
                0.012,
                None,
                "estimated",
                "pricing-table",
                "Plugin investigation",
                1_800_000_120,
                1,
            ),
        )
        connection.execute(
            SESSION_MODEL_USAGE_INSERT_SQL,
            (
                "session-1",
                "provider/model-a",
                "provider",
                "metered",
                "",
                1,
                1000,
                250,
                500,
                0,
                0,
                0.012,
                0,
                "estimated",
                "pricing-table",
                1_800_000_000,
                1_800_000_100,
            ),
        )
        calls = [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps({"path": "C:\\work\\demo\\app.py", "content": "secret"}),
                },
            },
            {
                "id": "call-2",
                "type": "function",
                "function": {
                    "name": "skill_view",
                    "arguments": json.dumps({"name": "hermes-desktop-plugins"}),
                },
            },
        ]
        connection.execute(
            "INSERT INTO messages (session_id,role,tool_calls,timestamp,active) VALUES (?,?,?,?,1)",
            ("session-1", "assistant", json.dumps(calls), 1_800_000_010),
        )
        connection.execute(
            """
            INSERT INTO messages (session_id,role,content,tool_call_id,tool_name,timestamp,active)
            VALUES (?,?,?,?,?,?,1)
            """,
            (
                "session-1",
                "tool",
                "Error: permission denied; api_key=should-not-leak",
                "call-1",
                "write_file",
                1_800_000_011,
            ),
        )
        connection.execute(
            """
            INSERT INTO messages (session_id,role,content,tool_call_id,tool_name,timestamp,active)
            VALUES (?,?,?,?,?,?,1)
            """,
            ("session-1", "tool", "Skill loaded", "call-2", "skill_view", 1_800_000_012),
        )
        connection.execute(
            "INSERT INTO messages (session_id,role,content,timestamp,active) VALUES (?,?,?,?,1)",
            ("session-1", "user", "Please build a plugin", 1_800_000_001),
        )
        connection.execute(
            "INSERT INTO messages (session_id,role,content,timestamp,active) VALUES (?,?,?,?,1)",
            (
                "session-1",
                "user",
                "[IMPORTANT: You are running as a scheduled cron job. DELIVERY: private scaffolding] hidden prompt",
                1_800_000_002,
            ),
        )
        connection.commit()
        connection.close()

        logs = self.home / "logs"
        logs.mkdir()
        (logs / "agent.log").write_text(
            f"{_stamp(1_800_000_000)} INFO [session-1] agent.conversation_loop: "
            "API call #1: model=provider/model-a provider=provider in=1000 out=250 total=1250 "
            "latency=2.5s cache=500/1000 (50%)\n"
            f"{_stamp(1_800_000_003)} INFO [session-1] agent.tool_executor: "
            "tool write_file failed (1.25s)\n"
            f"{_stamp(1_800_000_004)} ERROR [session-1] agent.conversation_loop: "
            "Error during OpenAI-compatible API call #2: HTTP 429 rate limit\n",
            encoding="utf-8",
        )
        (self.home / "gateway_state.json").write_text(
            json.dumps(
                {
                    "gateway_state": "running",
                    "pid": 123,
                    "updated_at": 1_800_000_100,
                    "platforms": {
                        "telegram": {"state": "connected", "needs_attention": False}
                    },
                }
            ),
            encoding="utf-8",
        )
        cron = self.home / "cron"
        cron.mkdir()
        (cron / "jobs.json").write_text(
            json.dumps(
                {
                    "jobs": [
                        {
                            "id": "job-1",
                            "name": "Daily report",
                            "prompt": "api_key=must-not-leak",
                            "enabled": True,
                            "schedule_display": "Daily at 09:00",
                            "next_run_at": 1_800_010_000,
                            "last_status": "ok",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        FakeSessionDB.path = self.db_path
        self.original_session_db = hermes_compat.SessionDB
        hermes_compat.SessionDB = FakeSessionDB

    def tearDown(self):
        hermes_compat.SessionDB = self.original_session_db
        api._log_file_cache.clear()
        api._ai_usage_cache = None
        api._ai_usage_last_success.clear()
        api._ai_models_cache.clear()
        api._session_classification_cache.clear()
        api._session_failure_cache.clear()
        if self.original_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self.original_home
        self.temp.cleanup()

    def test_runtime_telemetry_route_is_registered(self):
        routes = {
            (route.path, method)
            for route in api.router.routes
            for method in (route.methods or set())
        }
        self.assertIn(("/telemetry", "GET"), routes)

    def test_list_uses_recorded_cost_and_failure_first(self):
        payload = api._list_sessions_sync(
            days=0,
            query="",
            sort="failures",
            failures_only=False,
            include_archived=False,
            limit=50,
            offset=0,
        )
        self.assertEqual(payload["pagination"]["total"], 1)
        session = payload["sessions"][0]
        self.assertEqual(session["cost_kind"], "estimated")
        self.assertEqual(session["display_cost_usd"], 0.012)
        self.assertGreaterEqual(session["failure_count"], 1)
        self.assertEqual(session["total_tokens"], 1750)

    def test_detail_detects_failure_file_and_invoked_skill(self):
        detail = api._session_detail_sync("session-1")
        self.assertEqual(len(detail["failures"]), 1)
        self.assertIn("[redacted]", detail["failures"][0]["result_snippet"])
        self.assertNotIn("should-not-leak", detail["failures"][0]["result_snippet"])
        self.assertEqual(detail["skills"][0]["name"], "hermes-desktop-plugins")
        paths = {item["path"] for item in detail["files"]}
        self.assertIn("C:\\work\\demo\\app.py", paths)

    def test_digest_summarizes_periods_and_builds_markdown(self):
        payload = api._digest_sync(0)
        self.assertGreaterEqual(payload["totals"]["sessions"], 1)
        self.assertGreaterEqual(payload["totals"]["tool_calls"], payload["totals"]["tool_failures"])
        self.assertIsNone(payload["previous_period"])
        self.assertIn("# Session Lens digest", payload["markdown"])
        self.assertIn("## Totals", payload["markdown"])
        self.assertIn("## Models by recorded requests", payload["markdown"])
        self.assertNotIn("must-not-leak", payload["markdown"])

        week = api._digest_sync(7)
        self.assertIsNotNone(week["previous_period"])
        self.assertEqual(week["previous_totals"]["sessions"], 0)
        self.assertIn("(vs prior period)", week["markdown"])

    def test_quota_exhaust_forecast_math(self):
        now = time.time()
        reset_at = now + 0.83 * 7 * 86400
        exhaust = api._quota_exhaust_at("Weekly quota", reset_at, 31)
        self.assertIsNotNone(exhaust)
        self.assertAlmostEqual((exhaust - now) / 86400, 2.65, delta=0.1)
        self.assertIsNone(api._quota_exhaust_at("Weekly quota", reset_at, 15))
        self.assertIsNone(api._quota_exhaust_at("Balance", reset_at, 50))

    def test_projects_group_by_repo_directory_then_source(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO sessions (id, source, model, started_at, last_activity_at, ended_at,
                                      git_repo_root, input_tokens, output_tokens, actual_cost_usd, message_count)
                VALUES ('session-repo', 'desktop', 'provider/model-a', 1800000300, 1800000400, 1800000400,
                        'C:\work\demo-repo', 2000, 1000, 0.5, 3)
                """
            )
            connection.execute(
                """
                INSERT INTO sessions (id, source, model, started_at, last_activity_at,
                                      input_tokens, output_tokens, message_count)
                VALUES ('session-nodir', 'telegram', 'provider/model-a', 1800000500, 1800000600, 700, 300, 3)
                """
            )
            connection.commit()
        finally:
            connection.close()
        payload = api._projects_sync(0)
        kinds = {item["label"]: item for item in payload["projects"]}
        self.assertIn("demo-repo", kinds)
        self.assertEqual(kinds["demo-repo"]["kind"], "repo")
        self.assertEqual(kinds["demo-repo"]["recorded_cost_usd"], 0.5)
        self.assertIn("demo", kinds)
        self.assertEqual(kinds["demo"]["kind"], "directory")
        self.assertIn("telegram · no recorded directory", kinds)
        self.assertEqual(payload["totals"]["sessions_without_directory"], 1)

    def test_agent_runs_group_cron_sessions_by_title_prefix(self):
        connection = sqlite3.connect(self.db_path)
        try:
            for index, (end_reason, ended) in enumerate(
                [("cron_complete", True), ("error", True), ("cron_complete", True)]
            ):
                connection.execute(
                    """
                    INSERT INTO sessions (id, source, model, title, started_at, ended_at,
                                          end_reason, last_activity_at, input_tokens,
                                          output_tokens, estimated_cost_usd, message_count)
                    VALUES (?, 'cron', 'provider/model-a', ?, ?, ?, ?, ?, 100, 50, 0.02, 3)
                    """,
                    (
                        f"cron-run-{index}",
                        f"Nightly digest · Aug {10 + index}",
                        1_800_100_000 + index * 86400,
                        1_800_100_000 + index * 86400 + 120 if ended else None,
                        end_reason,
                        1_800_100_000 + index * 86400 + 120,
                    ),
                )
            connection.commit()
        finally:
            connection.close()
        payload = api._agent_runs_sync(0)
        jobs = {item["label"]: item for item in payload["jobs"]}
        self.assertIn("Nightly digest", jobs)
        job = jobs["Nightly digest"]
        self.assertEqual(job["runs_recorded"], 3)
        self.assertEqual(job["failed_runs"], 1)
        self.assertEqual(job["runs"][0]["outcome"], "completed")
        self.assertEqual(job["runs"][1]["outcome"], "failed")
        self.assertEqual(job["current_streak"], 1)
        self.assertAlmostEqual(job["avg_duration_seconds"], 120.0)

    def test_compression_summary_reports_distress_and_quiet_state(self):
        quiet = api._compression_sync()
        self.assertEqual(quiet["fallback_sessions"], 0)
        self.assertEqual(quiet["offenders"], [])
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO sessions (id, source, model, title, started_at, last_activity_at,
                                      compression_fallback_streak, compression_ineffective_count,
                                      compression_failure_error, message_count)
                VALUES ('session-squeeze', 'desktop', 'provider/model-a', 'Long build',
                        1800000700, 1800000800, 3, 2, 'token_limit password=abc123', 3)
                """
            )
            connection.commit()
        finally:
            connection.close()
        payload = api._compression_sync()
        self.assertEqual(payload["fallback_sessions"], 1)
        self.assertEqual(payload["ineffective_sessions"], 1)
        self.assertEqual(payload["failed_sessions"], 1)
        offender = payload["offenders"][0]
        self.assertEqual(offender["title"], "Long build")
        self.assertEqual(offender["fallback_streak"], 3)
        self.assertNotIn("abc123", offender["failure_error"])

    def test_health_names_the_serving_profile(self):
        # The fixture home is a temp dir with no "profiles" segment -> default.
        self.assertEqual(api._serving_profile_name(), "default")

    def test_attention_flags_runaway_and_reaped_sessions(self):
        connection = sqlite3.connect(self.db_path)
        try:
            now = time.time()
            connection.execute(
                """
                INSERT INTO sessions (id, source, model, started_at, last_activity_at,
                                      input_tokens, output_tokens, message_count)
                VALUES ('session-open', 'desktop', 'provider/model-a', ?, ?, 6000000, 500000, 4)
                """,
                (now - 3 * 86400, now - 2 * 86400),
            )
            connection.execute(
                """
                INSERT INTO sessions (id, source, model, started_at, ended_at,
                                      last_activity_at, end_reason, input_tokens,
                                      output_tokens, cache_read_tokens, message_count)
                VALUES ('session-reaped', 'desktop', 'provider/model-a', ?, ?, ?,
                        'startup_orphan_reap', 2000000, 1000000, 4000000, 9)
                """,
                (now - 10 * 86400, now - 3600, now - 3600),
            )
            connection.commit()
        finally:
            connection.close()

        payload = api._attention_sync(0)
        by_id = {item["id"]: item for item in payload["sessions"]}
        self.assertIn("session-open", by_id)
        self.assertEqual(by_id["session-open"]["severity"], "warning")
        self.assertIn("never closed", by_id["session-open"]["reason"])
        self.assertIn("session-reaped", by_id)
        self.assertIn("startup_orphan_reap", by_id["session-reaped"]["reason"])
        self.assertEqual(by_id["session-reaped"]["total_tokens"], 7000000)
        self.assertNotIn("session-1", by_id)
        self.assertEqual(payload["totals"]["open_sessions"], 1)
        self.assertEqual(payload["totals"]["reaped_sessions"], 1)

        scoped = api._attention_sync(0, start_at=time.time() - 60, end_at=time.time())
        scoped_ids = {item["id"] for item in scoped["sessions"]}
        self.assertIn("session-open", scoped_ids)
        self.assertNotIn("session-reaped", scoped_ids)

    def test_desktop_components_referenced_are_defined_or_imported(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        referenced = set(re.findall(r"jsxs?\(([A-Z]\w*)", source))
        defined = set(re.findall(r"(?m)^function ([A-Z]\w*)", source))
        imported = set()
        for block in re.findall(r"import \{([^}]*)\} from", source):
            imported.update(name.strip() for name in block.split(",") if name.strip())
        missing = referenced - defined - imported
        self.assertEqual(missing, set(), f"components used but never defined or imported: {sorted(missing)}")

    def test_benign_json_error_fields_are_not_failures(self):
        benign = (
            '{"output": "done", "exit_code": 0, "error": null}',
            '{"error": false}',
            '{"error": 0}',
            '{"error": ""}',
            '{"count": 3, "errors": null}',
        )
        real = (
            '{"error": "Search failed: rg: unrecognized flag"}',
            '{"errors": ["missing argument"]}',
            '{"output": "", "exit_code": 127, "error": null}',
        )
        for content in benign:
            self.assertFalse(
                api._is_failure(role="tool", content=content),
                f"benign payload misflagged: {content}",
            )
        for content in real:
            self.assertTrue(
                api._is_failure(role="tool", content=content),
                f"real failure missed: {content}",
            )

    def test_failure_sql_candidates_are_confirmed_by_shared_signature(self):
        corpus = (
            "0 errors",
            "error-free",
            "Traceback (most recent call last)",
            "process exited with code 2",
            "error_handler.py updated",
            "All tests passed",
        )
        connection = sqlite3.connect(self.db_path)
        try:
            for index, content in enumerate(corpus):
                connection.execute(
                    """
                    INSERT INTO messages (
                        session_id,role,content,tool_name,timestamp,active
                    ) VALUES (?,?,?,?,?,1)
                    """,
                    ("session-1", "tool", content, f"corpus-{index}", 1_800_000_020 + index),
                )
            connection.commit()
            connection.row_factory = sqlite3.Row
            candidates = {
                row["content"]
                for row in connection.execute(
                    f"SELECT content FROM messages m WHERE m.role='tool' AND {api._failure_sql('m')}"
                ).fetchall()
                if row["content"] in corpus
            }
        finally:
            connection.close()

        matches = {content for content in corpus if api._FAILURE_RE.search(content)}
        self.assertEqual(
            matches,
            {"Traceback (most recent call last)", "process exited with code 2"},
        )
        self.assertTrue(matches.issubset(candidates))

        detail = api._session_detail_sync("session-1")
        corpus_failures = [
            failure
            for failure in detail["failures"]
            if str(failure.get("name") or "").startswith("corpus-")
        ]
        self.assertEqual(len(corpus_failures), len(matches))
        for failure in corpus_failures:
            self.assertIsNotNone(api._FAILURE_RE.search(failure["result_snippet"]))

        session_list = api._list_sessions_sync(
            days=0,
            query="",
            sort="failures",
            failures_only=True,
            include_archived=False,
            limit=50,
            offset=0,
        )
        self.assertEqual(
            session_list["sessions"][0]["failure_count"],
            len(detail["failures"]),
        )
        self.assertEqual(api._overview_sync(0)["totals"]["failures"], len(detail["failures"]))
        tools = {item["name"]: item for item in api._tools_sync(0)["tools"]}
        for index, content in enumerate(corpus):
            self.assertEqual(tools[f"corpus-{index}"]["failures"], int(content in matches))

    def test_full_text_search_returns_bounded_snippet(self):
        payload = api._list_sessions_sync(
            days=0,
            query="plugin",
            sort="recent",
            failures_only=False,
            include_archived=False,
            limit=50,
            offset=0,
        )
        self.assertEqual(payload["pagination"]["total"], 1)
        self.assertIn("plugin", payload["sessions"][0]["search_snippet"].lower())

    def test_search_snippet_ids_are_queried_in_sql_safe_chunks(self):
        snippets = {f"missing-session-{index}": "match" for index in range(1801)}
        with patch.object(api, "_search_hits", return_value=snippets):
            payload = api._list_sessions_sync(
                days=0,
                query="match",
                sort="recent",
                failures_only=False,
                include_archived=False,
                limit=50,
                offset=0,
            )
        self.assertEqual(payload["sessions"], [])

    def test_aggregate_tools_and_skills_use_recorded_events(self):
        tools = api._tools_sync(0)
        by_name = {item["name"]: item for item in tools["tools"]}
        self.assertEqual(by_name["write_file"]["calls"], 1)
        self.assertEqual(by_name["write_file"]["failures"], 1)
        skills = api._skills_sync(0)
        self.assertEqual(skills["skills"][0]["name"], "hermes-desktop-plugins")
        self.assertEqual(skills["skills"][0]["view_count"], 1)

    def test_system_contract_is_read_only(self):
        system = api._system_sync()
        self.assertTrue(system["database"]["read_only"])
        self.assertEqual(system["privacy"]["mutation_endpoints"], 0)
        self.assertFalse(system["privacy"]["provider_credentials_returned_to_desktop"])
        self.assertEqual(system["database"]["schema_version"], 26)
        self.assertIn(system["capabilities"]["key_resolution"], {"unknown", "available", "unavailable"})

    def test_version_is_sourced_from_plugin_yaml_and_synced_to_manifest(self):
        root = MODULE_PATH.parents[1]
        plugin_version = next(
            line.split(":", 1)[1].strip()
            for line in (root / "plugin.yaml").read_text(encoding="utf-8").splitlines()
            if line.startswith("version:")
        )
        dashboard_manifest = json.loads((root / "dashboard" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(api.PLUGIN_VERSION, plugin_version)
        self.assertEqual(dashboard_manifest["version"], plugin_version)

    def test_fake_schema_matches_recorded_hermes_v26_fixture(self):
        # Regenerate this fixture from the installed read-only Hermes state.db
        # whenever Hermes bumps its schema version.
        fixture_path = Path(__file__).parent / "fixtures" / "schema_v26.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        keys = ("cid", "name", "type", "notnull", "default", "pk")
        connection = sqlite3.connect(self.db_path)
        try:
            for table, expected in fixture.items():
                actual = [
                    dict(zip(keys, row))
                    for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
                ]
                self.assertEqual(actual, expected, table)
        finally:
            connection.close()

    def test_account_usage_snapshot_is_normalised_and_secret_redacted(self):
        snapshot = SimpleNamespace(
            unavailable_reason=None,
            plan="Plus",
            windows=(
                SimpleNamespace(
                    label="Session",
                    used_percent=42.5,
                    reset_at="2027-01-20T12:00:00+00:00",
                    detail="5 hour window",
                ),
            ),
            details=("api_key=must-not-leak",),
        )
        payload = api._account_usage_payload("codex", snapshot)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["plan"], "Plus")
        self.assertEqual(payload["windows"][0]["percentage_remaining"], 57.5)
        self.assertIn("[redacted]", payload["details"][0])
        self.assertNotIn("must-not-leak", json.dumps(payload))

    def test_grok_windows_map_weekly_and_extra_credit_envelopes(self):
        windows = api._grok_windows_from_payloads(
            {
                "config": {
                    "creditUsagePercent": 42.5,
                    "currentPeriod": {"end": "2027-01-20T12:00:00Z"},
                }
            },
            {
                "config": {
                    "monthlyLimit": {"val": 5000},
                    "used": {"val": 1250},
                    "billingPeriodEnd": "2027-02-01T00:00:00Z",
                }
            },
        )
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0]["percentage_remaining"], 57.5)
        self.assertEqual(windows[1]["limit"], 50.0)
        self.assertEqual(windows[1]["used"], 12.5)
        self.assertEqual(windows[1]["remaining"], 37.5)
        self.assertEqual(windows[1]["unit"], "credits")

    def test_openrouter_keeps_key_usage_when_account_credits_need_management_key(self):
        payload = api._openrouter_payload(
            {
                "limit": 100,
                "limit_remaining": 74.5,
                "limit_reset": "monthly",
                "usage_daily": 1.25,
                "usage_weekly": 4.5,
                "usage_monthly": 25.5,
            },
            None,
            partial_message="Account credits require an OpenRouter management key",
        )
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["partial"])
        self.assertEqual(payload["windows"][0]["percentage_remaining"], 74.5)
        self.assertIn("$25.50 this month", payload["details"][0])

    def test_deepseek_balance_keeps_currency_and_breakdown(self):
        payload = api._deepseek_payload(
            {
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "110.00",
                        "granted_balance": "10.00",
                        "topped_up_balance": "100.00",
                    }
                ],
            }
        )
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["windows"][0]["remaining"], 110.0)
        self.assertEqual(payload["windows"][0]["unit"], "CNY")
        self.assertIn("10.00 CNY granted", payload["windows"][0]["detail"])

    def test_kimi_maps_weekly_rolling_and_parallel_limits(self):
        payload = api._kimi_payload(
            {
                "user": {"membership": {"level": "LEVEL_ADVANCED"}},
                "usage": {
                    "limit": "2048",
                    "used": "214",
                    "remaining": "1834",
                    "resetTime": "2027-01-09T15:23:13.716839300Z",
                },
                "limits": [
                    {
                        "window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                        "detail": {
                            "limit": "200",
                            "used": "139",
                            "remaining": "61",
                            "resetTime": "2027-01-06T13:33:02.717479433Z",
                        },
                    }
                ],
                "parallel": {"limit": 20},
            }
        )
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["plan"], "Advanced")
        self.assertEqual([item["label"] for item in payload["windows"]], ["Weekly quota", "5-hour rolling"])
        self.assertAlmostEqual(payload["windows"][1]["percentage_remaining"], 30.5)
        self.assertIn("Maximum parallel requests: 20", payload["details"])
        self.assertTrue(payload["windows"][0]["reset_at"].endswith("+00:00"))

    def test_zai_maps_only_five_hour_and_weekly_token_windows(self):
        payload = api._zai_payload(
            {
                "code": 200,
                "success": True,
                "data": {
                    "limits": [
                        {
                            "type": "TOKENS_LIMIT",
                            "unit": 3,
                            "number": 5,
                            "usage": 800_000_000,
                            "currentValue": 120_000_000,
                            "remaining": 680_000_000,
                            "percentage": 15,
                            "nextResetTime": 1_800_000_000_000,
                        },
                        {
                            "type": "TOKENS_LIMIT",
                            "unit": 6,
                            "number": 1,
                            "usage": 1_000_000_000,
                            "currentValue": 420_000_000,
                            "remaining": 580_000_000,
                            "percentage": 42,
                            "nextResetTime": 1_800_500_000_000,
                        },
                        {"type": "TIME_LIMIT", "unit": 5, "number": 1, "usage": 4000},
                    ]
                },
            }
        )
        self.assertEqual(payload["status"], "ok")
        self.assertEqual([item["label"] for item in payload["windows"]], ["5-hour rolling", "Weekly quota"])
        self.assertEqual(payload["windows"][0]["percentage_remaining"], 85.0)
        self.assertEqual(payload["windows"][0]["unit"], "tokens")
        self.assertIn("2027", payload["windows"][0]["reset_at"])

    def test_ai_usage_cache_preserves_last_success_as_stale(self):
        def ok(provider):
            return api._provider_payload(
                provider,
                status="ok",
                windows=[api._usage_window("Weekly", used_percent=25)],
            )

        first = {
            "_collect_codex_usage": Mock(return_value=ok("codex")),
            "_collect_anthropic_usage": Mock(return_value=ok("anthropic")),
            "_collect_nous_usage": Mock(return_value=ok("nous")),
            "_collect_openrouter_usage": Mock(return_value=ok("openrouter")),
            "_collect_deepseek_usage": Mock(return_value=ok("deepseek")),
            "_collect_grok_usage": Mock(return_value=ok("grok")),
            "_collect_kimi_usage": Mock(return_value=ok("kimi")),
            "_collect_zai_usage": Mock(return_value=ok("zai")),
        }
        with patch.multiple(api, **first):
            fresh = api._ai_usage_sync(True)
            cached = api._ai_usage_sync(False)
        self.assertEqual(fresh["summary"]["connected"], 8)
        self.assertTrue(cached["cached"])
        for collector in first.values():
            collector.assert_called_once()

        failing = {
            "_collect_codex_usage": Mock(return_value=ok("codex")),
            "_collect_anthropic_usage": Mock(return_value=ok("anthropic")),
            "_collect_nous_usage": Mock(return_value=ok("nous")),
            "_collect_openrouter_usage": Mock(return_value=ok("openrouter")),
            "_collect_deepseek_usage": Mock(return_value=ok("deepseek")),
            "_collect_grok_usage": Mock(
                return_value=api._provider_payload(
                    "grok", status="unavailable", message="temporary failure"
                )
            ),
            "_collect_kimi_usage": Mock(return_value=ok("kimi")),
            "_collect_zai_usage": Mock(return_value=ok("zai")),
        }
        with patch.multiple(api, **failing):
            refreshed = api._ai_usage_sync(True)
        grok = next(item for item in refreshed["providers"] if item["provider"] == "grok")
        self.assertEqual(grok["status"], "stale")
        self.assertTrue(grok["stale"])
        self.assertEqual(grok["last_error_status"], "unavailable")
        self.assertEqual(grok["windows"][0]["percentage_remaining"], 75.0)
        self.assertEqual(refreshed["summary"]["connected"], 7)
        self.assertEqual(refreshed["summary"]["needs_attention"], 1)

    def test_ai_usage_expired_login_does_not_reuse_stale_reading(self):
        api._ai_usage_last_success["grok"] = api._provider_payload(
            "grok",
            status="ok",
            windows=[api._usage_window("Weekly", used_percent=25)],
        )
        collectors = {
            "_collect_codex_usage": Mock(return_value=api._provider_payload("codex", status="not_configured")),
            "_collect_anthropic_usage": Mock(return_value=api._provider_payload("anthropic", status="not_configured")),
            "_collect_nous_usage": Mock(return_value=api._provider_payload("nous", status="not_configured")),
            "_collect_openrouter_usage": Mock(
                return_value=api._provider_payload("openrouter", status="not_configured")
            ),
            "_collect_deepseek_usage": Mock(return_value=api._provider_payload("deepseek", status="not_configured")),
            "_collect_grok_usage": Mock(return_value=api._provider_payload("grok", status="expired")),
            "_collect_kimi_usage": Mock(return_value=api._provider_payload("kimi", status="not_configured")),
            "_collect_zai_usage": Mock(return_value=api._provider_payload("zai", status="not_configured")),
        }
        with patch.multiple(api, **collectors):
            payload = api._ai_usage_sync(True)
        grok = next(item for item in payload["providers"] if item["provider"] == "grok")
        self.assertEqual(grok["status"], "expired")
        self.assertFalse(grok["stale"])
        self.assertNotIn("grok", api._ai_usage_last_success)

    def test_ai_usage_provider_failure_isolated_from_other_collectors(self):
        def explode():
            raise RuntimeError("api_key=must-not-leak")

        with patch.multiple(
            api,
            _collect_codex_usage=explode,
            _collect_anthropic_usage=Mock(
                return_value=api._provider_payload("anthropic", status="not_configured")
            ),
            _collect_nous_usage=Mock(return_value=api._provider_payload("nous", status="not_configured")),
            _collect_openrouter_usage=Mock(
                return_value=api._provider_payload("openrouter", status="not_configured")
            ),
            _collect_deepseek_usage=Mock(
                return_value=api._provider_payload("deepseek", status="not_configured")
            ),
            _collect_grok_usage=Mock(return_value=api._provider_payload("grok", status="not_configured")),
            _collect_kimi_usage=Mock(return_value=api._provider_payload("kimi", status="not_configured")),
            _collect_zai_usage=Mock(return_value=api._provider_payload("zai", status="not_configured")),
        ):
            payload = api._ai_usage_sync(True)
        codex = next(item for item in payload["providers"] if item["provider"] == "codex")
        self.assertEqual(codex["status"], "unavailable")
        self.assertNotIn("must-not-leak", json.dumps(codex))

    def test_ai_usage_probe_skips_collectors_for_unconfigured_providers(self):
        def ok(provider):
            return api._provider_payload(
                provider,
                status="ok",
                windows=[api._usage_window("Weekly", used_percent=25)],
            )

        collectors = {
            f"_collect_{provider}_usage": Mock(return_value=ok(provider))
            for provider in api._AI_USAGE_PROVIDER_ORDER
        }
        with patch.object(api, "_probe_usage_provider", side_effect=lambda p: p in {"codex", "openrouter"}):
            with patch.multiple(api, **collectors):
                payload = api._ai_usage_sync(True)
        collectors["_collect_codex_usage"].assert_called_once()
        collectors["_collect_openrouter_usage"].assert_called_once()
        for provider in ("anthropic", "nous", "deepseek", "grok", "kimi", "zai"):
            collectors[f"_collect_{provider}_usage"].assert_not_called()
        deepseek = next(item for item in payload["providers"] if item["provider"] == "deepseek")
        self.assertEqual(deepseek["status"], "not_configured")
        self.assertEqual(deepseek["message"], "No Hermes DeepSeek API key was found.")
        self.assertEqual(payload["summary"]["configured"], 2)
        self.assertEqual(payload["summary"]["connected"], 2)
        self.assertEqual(payload["summary"]["not_configured"], 6)

    def test_ai_usage_probe_is_conservative_outside_hermes(self):
        # Without Hermes modules the probes cannot prove absence, so every
        # provider stays eligible and its collector reports the real status.
        for provider in api._AI_USAGE_PROVIDER_ORDER:
            self.assertTrue(api._probe_usage_provider(provider), provider)

    def test_ai_usage_ui_hides_unconfigured_providers_behind_summary_line(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("provider.status !== 'not_configured'", source)
        self.assertIn("Also supported: ", source)
        self.assertIn("No AI providers are connected", source)
        self.assertIn("more supported", source)

    def test_attention_quota_notes_come_from_cached_usage_only(self):
        api._ai_usage_cache = None
        self.assertEqual(api._attention_sync(0)["quotas"], [])

        now = time.time()

        def window(label, used, reset_offset, kind="quota"):
            return api._usage_window(label, kind=kind, used_percent=used, reset_at=now + reset_offset)

        payload = {
            "providers": [
                api._provider_payload("codex", status="ok", windows=[window("Weekly quota", 95, 86_400)]),
                api._provider_payload("grok", status="ok", windows=[window("Weekly quota", 50, 86_400)]),
                api._provider_payload("kimi", status="ok", windows=[window("Weekly quota", 40, 6 * 86_400)]),
                api._provider_payload("zai", status="expired", windows=[window("Weekly quota", 99, 86_400)]),
                api._provider_payload(
                    "openrouter", status="ok", windows=[window("Account credits", 99, 86_400, kind="balance")]
                ),
            ],
            "generated_at": now,
        }
        api._ai_usage_cache = (now, payload)
        quotas = api._attention_sync(0)["quotas"]
        self.assertEqual([note["provider"] for note in quotas], ["codex", "kimi"])
        self.assertEqual(quotas[0]["severity"], "danger")
        self.assertEqual(quotas[0]["percent_used"], 95)
        self.assertEqual(quotas[1]["severity"], "warning")
        self.assertIsNotNone(quotas[1]["exhaust_at"])
        self.assertTrue(quotas[0]["id"].startswith("quota:codex:"))

        # An aged-out cache stops producing notes rather than lying quietly.
        api._ai_usage_cache = (now - api.QUOTA_ATTENTION_MAX_CACHE_AGE_SECONDS - 1, payload)
        self.assertEqual(api._attention_sync(0)["quotas"], [])

    def test_quota_alert_strip_ui_shows_on_other_tabs_and_dismisses_per_window(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("function QuotaAlertStrip", source)
        self.assertIn("ctx.storage.get('quotaAlertsDismissed')", source)
        # The strip is redundant on the AI Usage tab itself.
        self.assertIn("tab !== 'ai-usage'\n        ? jsx(QuotaAlertStrip", source)
        self.assertIn("Open AI Usage.", source)
        self.assertIn("It returns after the reset if the condition persists.", source)

    def test_ai_usage_ui_records_burn_history_and_forecasts_from_slope(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("ctx.storage.get('usageHistory')", source)
        # Cached responses repeat the same reading and must never be recorded.
        self.assertIn("if (!data || data.cached) return", source)
        # A percentage drop means the window reset; the series starts over.
        self.assertIn("pct < last[1] - 1", source)
        self.assertIn("function usageSlopeForecast", source)
        self.assertIn("recorded burn slope", source)
        self.assertIn("Usage burn sparkline", source)

    def test_trace_is_paginated_redacted_and_excludes_system_prompts(self):
        trace = api._trace_sync("session-1", 100, 0)
        kinds = {event["kind"] for event in trace["events"]}
        self.assertIn("user", kinds)
        self.assertIn("tool_call", kinds)
        self.assertIn("tool_result", kinds)
        self.assertFalse(trace["privacy"]["system_prompts_included"])
        self.assertFalse(trace["privacy"]["schedule_prompts_included"])
        combined = " ".join(event.get("content", "") for event in trace["events"])
        self.assertNotIn("should-not-leak", combined)
        self.assertNotIn("scheduled cron job", combined)
        self.assertNotIn("hidden prompt", combined)

    def test_runtime_telemetry_uses_local_log_metrics(self):
        telemetry = api._telemetry_sync(0, "session-1")
        self.assertEqual(telemetry["summary"]["api_calls"], 1)
        self.assertEqual(telemetry["summary"]["tool_runs"], 1)
        self.assertEqual(telemetry["summary"]["latency_p95_seconds"], 2.5)
        self.assertEqual(telemetry["summary"]["cache_hit_ratio"], 0.5)
        self.assertEqual(telemetry["tools"][0]["failed"], 1)

    def test_log_window_uses_all_parsed_lines_and_cache_is_lru_bounded(self):
        log_path = self.home / "logs" / "agent.log"
        original = log_path.read_text(encoding="utf-8")
        log_path.write_text(
            f"{_stamp(1_799_999_940)} INFO [session-1] agent.lifecycle: startup complete\n"
            + original
            + f"{_stamp(1_800_000_060)} INFO [session-1] agent.lifecycle: shutdown complete\n",
            encoding="utf-8",
        )
        api._log_file_cache.clear()
        payload = api._ai_models_sync(0, fresh=True)
        self.assertEqual(payload["coverage"]["log_start_at"], 1_799_999_940.0)
        self.assertEqual(payload["coverage"]["log_end_at"], 1_800_000_060.0)

        for index in range(11):
            rotated = self.home / "logs" / f"rotated-{index}.log"
            rotated.write_text(f"2027-01-15 09:00:{index:02d},000 INFO line\n", encoding="utf-8")
            api._parse_log_file(rotated)
        self.assertEqual(len(api._log_file_cache), 10)
        self.assertNotIn(str(self.home / "logs" / "rotated-0.log"), api._log_file_cache)

    def test_tools_scan_enforces_real_assistant_row_cap(self):
        calls = json.dumps([{
            "id": "call-cap",
            "type": "function",
            "function": {"name": "capped_tool", "arguments": "{}"},
        }])
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executemany(
                "INSERT INTO messages (session_id,role,tool_calls,timestamp,active) VALUES ('session-1','assistant',?,?,1)",
                ((calls, 1_800_000_030 + index) for index in range(50001)),
            )
            connection.commit()
        finally:
            connection.close()
        payload = api._tools_sync(0)
        capped = next(item for item in payload["tools"] if item["name"] == "capped_tool")
        self.assertTrue(payload["truncated"])
        self.assertEqual(capped["calls"], 50000)

    def test_session_task_type_uses_five_path_aware_types_in_precedence_order(self):
        self.assertEqual(api._session_task_type(tool_rows("delegate_task", {"prompt": "work"})), "Orchestration")
        self.assertEqual(api._session_task_type(tool_rows("write_file", {"path": "app.py"})), "Coding")
        self.assertEqual(
            api._session_task_type(tool_rows("web_search", {"query": "daily brief"})),
            "Analysis",
        )
        self.assertEqual(api._session_task_type(tool_rows("send_message", {"message": "hello"})), "General")
        self.assertEqual(api._session_task_type(tool_rows("write_file", {"path": "vault/note.md"})), "Writing")
        self.assertEqual(api._session_task_type(tool_rows("write_file", {"path": "plugins/demo/plugin.js"})), "Coding")
        self.assertEqual(
            api._session_task_type(tool_rows("terminal", {"command": "git add app.py && git commit -m done"})),
            "Coding",
        )
        self.assertEqual(api._session_task_type(tool_rows("terminal", {"command": "date"})), "General")
        self.assertEqual(api._session_task_type([]), "General")

    def test_read_only_git_commands_are_not_coding_but_mutations_and_runners_are(self):
        read_only = (
            "git status",
            "git log --oneline -5",
            "git diff --stat",
            "git show HEAD:app.py",
            "git blame app.py",
            "git branch --list",
            "gh pr view 42",
        )
        for command in read_only:
            with self.subTest(command=command):
                self.assertEqual(
                    api._session_task_type(tool_rows("exec_command", {"command": command})),
                    "General",
                )

        mutating = (
            "git commit -am fix",
            "git push origin main",
            "git stash pop",
            "gh pr merge 42",
            "pytest -q",
            "npm run build",
            "cargo test",
            "ruff check --fix .",
        )
        for command in mutating:
            with self.subTest(command=command):
                self.assertEqual(
                    api._session_task_type(tool_rows("exec_command", {"command": command})),
                    "Coding",
                )

    def test_session_task_type_classifies_mixed_research_by_saved_artifact(self):
        research = tool_rows("web_search", {"query": "provider release"})
        writing = tool_rows("write_file", {"path": "brief.md"})
        coding = tool_rows("write_file", {"path": "app.py"})
        self.assertEqual(api._session_task_type(research + writing), "Writing")
        self.assertEqual(api._session_task_type(research + coding), "Coding")

    def test_change_evidence_distinguishes_code_writing_and_commit_contents(self):
        writing = tool_rows("write_file", {"path": "vault/note.md"})
        coding = tool_rows("apply_patch", {"path": "app.py", "patch": "+ok"})
        markdown_commit = tool_rows(
            "terminal",
            {"command": "git commit -m docs"},
            "[main abc123] docs\n release-notes.md | 2 ++",
        )
        code_commit = tool_rows(
            "terminal",
            {"command": "git add app.py && git commit -m code"},
            "[main def456] code\n app.py | 2 ++",
        )

        self.assertTrue(api._writing_change_evidence(writing))
        self.assertIsNone(api._coding_change_evidence(writing))
        self.assertTrue(api._coding_change_evidence(coding))
        self.assertIsNone(api._writing_change_evidence(coding))
        self.assertEqual(api._session_task_type(markdown_commit), "Writing")
        self.assertTrue(api._writing_change_evidence(markdown_commit))
        self.assertEqual(api._session_task_type(code_commit), "Coding")
        self.assertTrue(api._coding_change_evidence(code_commit))
        self.assertEqual(
            api._acceptance_for_task(
                "Writing",
                {"closed": True, "resolved": True, "writing_change": True},
            ),
            (True, True),
        )

    def test_auxiliary_labels_are_separate_and_unscored(self):
        self.assertEqual(api._auxiliary_task_label("compression"), "Compression")
        self.assertEqual(api._auxiliary_task_label("title_generation"), "Title")
        self.assertEqual(api._auxiliary_task_label("vision"), "Vision")
        self.assertEqual(api._auxiliary_task_label("web_extract"), "Web extract")
        self.assertEqual(api._auxiliary_task_label("background_review"), "Review")
        self.assertEqual(api._auxiliary_task_label("approval"), "Approval")
        self.assertEqual(api._auxiliary_task_label("custom_aux_job"), "Custom Aux Job")
        self.assertEqual(api._auxiliary_task_label("analysis"), "Analysis job")
        self.assertEqual(api._acceptance_for_task("Title", {"closed": True, "resolved": True}), (False, False))
        self.assertEqual(api._acceptance_for_task("Orchestration", {"closed": True, "resolved": True}), (False, False))

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                SESSION_MODEL_USAGE_INSERT_SQL,
                (
                    "session-1", "provider/model-a", "provider", "metered", "title_generation",
                    1, 20, 5, 0, 0, 0, 0.0001, 0, "estimated", "test",
                    1_800_000_020, 1_800_000_030,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        model = api._ai_models_sync(0)["models"][0]
        self.assertNotIn("Title", {row["task_type"] for row in model["task_types"]})
        title = next(row for row in model["auxiliary_tasks"] if row["task_type"] == "Title")
        self.assertIsNone(title["first_attempt_acceptance_rate"])
        self.assertEqual(title["acceptance_basis"], "auxiliary job; acceptance is not scored")

    def test_ai_models_are_discovered_sorted_and_explicit_about_coverage(self):
        payload = api._ai_models_sync(0)
        self.assertEqual(payload["summary"]["models"], 1)
        model = payload["models"][0]
        self.assertEqual(model["model_id"], "provider/model-a")
        self.assertEqual(model["route_label"], "Provider API")
        self.assertEqual(model["requests"], 1)
        self.assertEqual(model["total_tokens"], 1750)
        self.assertEqual(model["cache_tokens"], 500)
        self.assertEqual(model["task_types"][0]["task_type"], "Coding")
        self.assertEqual(model["failures"]["rate_limits"], 1)
        self.assertEqual(model["failures"]["rate"], 0.5)
        self.assertEqual(model["failures"]["samples"], 2)
        self.assertEqual(model["failures"]["tool_failures"], 1)
        self.assertEqual(model["retry_switch_samples"], 1)
        self.assertEqual(model["task_types"][0]["first_attempt_acceptance_rate"], 0)
        self.assertEqual(model["acceptance_samples"], 1)
        self.assertIsNone(model["latency"]["ttft_p50_seconds"])
        self.assertEqual(model["latency"]["total_p50_seconds"], 2.5)
        self.assertFalse(payload["coverage"]["ttft_available"])

        outside_period = api._ai_models_sync(0, 1_800_001_000, 1_800_002_000)
        self.assertEqual([item["model_id"] for item in outside_period["models"]], ["provider/model-a"])
        self.assertEqual(outside_period["models"][0]["requests"], 0)
        self.assertEqual(outside_period["models"][0]["last_used_at"], 1_800_000_100)
        self.assertEqual(outside_period["summary"]["inventory_models"], 1)
        self.assertEqual(outside_period["summary"]["active_models"], 0)
        self.assertEqual(outside_period["summary"]["known_cost_models"], 0)

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE session_model_usage
                SET actual_cost_usd=0, estimated_cost_usd=0.012,
                    cost_status='actual', cost_source='provider'
                """
            )
            connection.commit()
        finally:
            connection.close()
        actual_zero = api._ai_models_sync(0)
        self.assertEqual(actual_zero["models"][0]["cost_kind"], "actual")
        self.assertEqual(actual_zero["models"][0]["cost_usd"], 0)

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DELETE FROM session_model_usage")
            connection.commit()
        finally:
            connection.close()
        session_fallback = api._ai_models_sync(0, 1_800_001_000, 1_800_002_000)
        self.assertEqual(session_fallback["models"][0]["last_used_at"], 1_800_000_120)

    def test_ai_models_cache_avoids_reclassifying_unchanged_history(self):
        api._ai_models_cache.clear()
        api._session_classification_cache.clear()
        with patch.object(classify, "_session_task_type", wraps=classify._session_task_type) as classifier:
            first = api._ai_models_sync(0)
            first_call_count = classifier.call_count
            second = api._ai_models_sync(0)
        self.assertGreater(first_call_count, 0)
        self.assertEqual(classifier.call_count, first_call_count)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])

    def test_ai_model_acceptance_is_task_specific_and_requires_coding_change_evidence(self):
        connection = sqlite3.connect(self.db_path)
        try:
            sessions = [
                ("coding-success", "provider/model-a", 1_800_000_200, "completed"),
                ("orchestration", "provider/model-b", 1_800_000_400, "completed"),
            ]
            for session_id, model, started_at, reason in sessions:
                connection.execute(
                    """
                    INSERT INTO sessions (
                        id,source,model,started_at,ended_at,end_reason,billing_provider,
                        billing_mode,last_activity_at,api_call_count
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (session_id, "desktop", model, started_at, started_at + 60, reason, "provider", "metered", started_at + 60, 1),
                )
                connection.execute(
                    SESSION_MODEL_USAGE_INSERT_SQL,
                    (session_id, model, "provider", "metered", "", 1, 100, 20, 0, 0, 0, 0.001, 0, "estimated", "test", started_at, started_at + 50),
                )

            patch_call = [{
                "id": "call-patch",
                "type": "function",
                "function": {"name": "apply_patch", "arguments": json.dumps({"path": "app.py", "patch": "+ok"})},
            }]
            connection.execute(
                "INSERT INTO messages (session_id,role,tool_calls,timestamp,active) VALUES (?,?,?,?,1)",
                ("coding-success", "assistant", json.dumps(patch_call), 1_800_000_210),
            )
            connection.execute(
                """
                INSERT INTO messages (session_id,role,content,tool_call_id,tool_name,timestamp,active)
                VALUES (?,?,?,?,?,?,1)
                """,
                ("coding-success", "tool", "Done!", "call-patch", "apply_patch", 1_800_000_211),
            )
            delegate_call = [{
                "id": "call-delegate",
                "type": "function",
                "function": {"name": "delegate_task", "arguments": json.dumps({"prompt": "Inspect the work"})},
            }]
            connection.execute(
                "INSERT INTO messages (session_id,role,tool_calls,timestamp,active) VALUES (?,?,?,?,1)",
                ("orchestration", "assistant", json.dumps(delegate_call), 1_800_000_410),
            )
            connection.execute(
                ASYNC_DELEGATION_INSERT_SQL,
                ("delegation-2", "orchestration", None, "completed", 1_800_000_401, 1_800_000_450, 1_800_000_450, "delivered"),
            )
            connection.commit()
        finally:
            connection.close()

        payload = api._ai_models_sync(0)
        models = {item["model_id"]: item for item in payload["models"]}
        coding = next(item for item in models["provider/model-a"]["task_types"] if item["task_type"] == "Coding")
        orchestration = models["provider/model-b"]["task_types"][0]
        self.assertEqual(coding["eligible_sessions"], 2)
        self.assertEqual(coding["accepted_sessions"], 1)
        self.assertEqual(coding["first_attempt_acceptance_rate"], 0.5)
        self.assertEqual(models["provider/model-a"]["accepted_tasks"], 1)
        self.assertEqual(orchestration["task_type"], "Orchestration")
        self.assertIsNone(orchestration["first_attempt_acceptance_rate"])
        self.assertEqual(orchestration["acceptance_basis"], "unavailable for this task type")

    def test_ai_model_retry_switch_respects_roles_and_same_model_prompt_resends(self):
        connection = sqlite3.connect(self.db_path)
        try:
            rows = [
                ("different-roles", "provider/model-a", "", 1_800_001_000),
                ("different-roles", "provider/model-b", "approval", 1_800_001_000),
                ("same-role-switch", "provider/model-c", "", 1_800_001_200),
                ("same-role-switch", "provider/model-d", "", 1_800_001_200),
                ("prompt-resend", "provider/model-e", "", 1_800_001_400),
            ]
            for session_id in {row[0] for row in rows}:
                started_at = next(row[3] for row in rows if row[0] == session_id)
                connection.execute(
                    """
                    INSERT INTO sessions (
                        id,source,model,started_at,ended_at,end_reason,billing_provider,
                        billing_mode,last_activity_at,api_call_count
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (session_id, "desktop", "provider/model-a", started_at, started_at + 100, "completed", "provider", "metered", started_at + 100, 1),
                )
            for session_id, model, task, started_at in rows:
                connection.execute(
                    SESSION_MODEL_USAGE_INSERT_SQL,
                    (session_id, model, "provider", "metered", task, 1, 100, 20, 0, 0, 0, 0.001, 0, "estimated", "test", started_at + 5, started_at + 90),
                )
            repeated = "Please inspect the provider configuration and explain the result"
            connection.execute(
                "INSERT INTO messages (session_id,role,content,timestamp,active) VALUES (?,?,?,?,1)",
                ("prompt-resend", "user", repeated, 1_800_001_401),
            )
            connection.execute(
                "INSERT INTO messages (session_id,role,content,timestamp,active) VALUES (?,?,?,?,1)",
                ("prompt-resend", "user", repeated + ".", 1_800_001_500),
            )
            connection.commit()
        finally:
            connection.close()

        models = {item["model_id"]: item for item in api._ai_models_sync(0)["models"]}
        self.assertEqual(models["provider/model-b"]["retry_switch_rate"], 0)
        self.assertEqual(models["provider/model-c"]["retry_switch_rate"], 1)
        self.assertEqual(models["provider/model-d"]["retry_switch_rate"], 1)
        self.assertEqual(models["provider/model-e"]["retry_switch_rate"], 1)

    def test_ai_model_failures_include_attempt_errors_but_keep_tool_failures_separate(self):
        log_path = self.home / "logs" / "agent.log"
        log_path.write_text(
            log_path.read_text(encoding="utf-8")
            + f"{_stamp(1_800_000_005)} WARNING [session-1] agent.conversation_loop: "
            + "API call failed (attempt 1/1) error_type=APITimeout provider=provider "
            + "base_url=https://example.test model=provider/model-a summary=timeout\n",
            encoding="utf-8",
        )
        api._log_file_cache.clear()
        model = api._ai_models_sync(0)["models"][0]
        self.assertEqual(model["failures"]["rate_limits"], 1)
        self.assertEqual(model["failures"]["timeouts"], 1)
        self.assertEqual(model["failures"]["errors"], 0)
        self.assertEqual(model["failures"]["tool_failures"], 1)
        self.assertEqual(model["failures"]["tool_calls"], 2)
        self.assertAlmostEqual(model["failures"]["rate"], 2 / 3)

    def test_ai_model_tool_call_denominator_is_reported(self):
        payload = api._ai_models_sync(0)
        model = payload["models"][0]
        self.assertGreaterEqual(
            model["failures"]["tool_calls"], model["failures"]["tool_failures"]
        )
        coverage = payload["coverage"]
        self.assertEqual(coverage["recorded_tool_calls"], 2)
        self.assertEqual(
            coverage["recorded_tool_calls"],
            coverage["attributed_tool_calls"] + coverage["unattributed_tool_calls"],
        )

    def test_work_reliability_uses_task_outcomes_and_confidence_adjustment(self):
        counts = api._work_reliability_counts(
            [
                {"status": "clean"},
                {"status": "recovered"},
                {"status": "completed"},
                {"status": "unrecovered"},
                {"status": "unknown"},
                {"status": "switched_away"},
                {"status": "excluded"},
            ],
            sample_threshold=5,
        )
        self.assertEqual(counts["eligible_tasks"], 4)
        self.assertEqual(counts["completed_tasks"], 3)
        self.assertEqual(counts["clean_completions"], 1)
        self.assertEqual(counts["recovered_tasks"], 1)
        self.assertEqual(counts["unrecovered_failures"], 1)
        self.assertEqual(counts["unknown_tasks"], 1)
        self.assertEqual(counts["switched_away_tasks"], 1)
        self.assertEqual(counts["excluded_tasks"], 1)
        self.assertEqual(counts["completion_rate"], 0.75)
        self.assertEqual(counts["clean_completion_rate"], 0.25)
        self.assertEqual(counts["recovery_rate"], 0.5)
        self.assertFalse(counts["rank_eligible"])
        self.assertGreater(counts["failure_rate_upper_bound_95"], counts["unrecovered_failure_rate"])

    def test_ai_model_work_reliability_distinguishes_recovery_terminal_failure_and_switches(self):
        log_specs = []
        connection = sqlite3.connect(self.db_path)
        try:
            sessions = [
                ("recovered-task", "provider/model-r", "completed", "2027-01-15 09:00:00,000"),
                ("unrecovered-task", "provider/model-u", "failed", "2027-01-15 09:10:00,000"),
                ("clean-task", "provider/model-c", "completed", "2027-01-15 09:20:00,000"),
                ("switched-task", "provider/model-b", "completed", "2027-01-15 09:30:00,000"),
                ("tool-only-task", "provider/model-t", "failed", "2027-01-15 09:40:00,000"),
            ]
            for session_id, model, outcome, stamp in sessions:
                started_at = api._timestamp_from_log(stamp)
                connection.execute(
                    """
                    INSERT INTO sessions (
                        id,source,model,started_at,ended_at,end_reason,billing_provider,
                        billing_mode,last_activity_at,api_call_count
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (session_id, "desktop", model, started_at, started_at + 60, outcome, "provider", "metered", started_at + 60, 2),
                )
                if session_id == "switched-task":
                    usage = [
                        ("provider/model-x", started_at + 2, started_at + 20),
                        ("provider/model-b", started_at + 25, started_at + 50),
                    ]
                else:
                    usage = [(model, started_at + 2, started_at + 50)]
                for usage_model, first_seen, last_seen in usage:
                    connection.execute(
                        SESSION_MODEL_USAGE_INSERT_SQL,
                        (session_id, usage_model, "provider", "metered", "", 1, 100, 20, 0, 0, 0, 0.001, 0, "estimated", "test", first_seen, last_seen),
                    )

            tool_started = api._timestamp_from_log("2027-01-15 09:40:00,000")
            connection.execute(
                """
                INSERT INTO messages (session_id,role,content,tool_call_id,tool_name,timestamp,active)
                VALUES (?,?,?,?,?,?,1)
                """,
                ("tool-only-task", "tool", "Error: command failed", "tool-only", "terminal", tool_started + 30),
            )
            connection.commit()

            log_specs = [
                "2027-01-15 09:00:10,000 WARNING [recovered-task] agent.conversation_loop: API call failed (attempt 1/2) error_type=APITimeout provider=provider base_url=https://example.test model=provider/model-r summary=timeout\n",
                "2027-01-15 09:00:20,000 INFO [recovered-task] agent.conversation_loop: API call #2: model=provider/model-r provider=provider in=100 out=20 total=120 latency=1.0s\n",
                "2027-01-15 09:10:10,000 INFO [unrecovered-task] agent.conversation_loop: API call #1: model=provider/model-u provider=provider in=100 out=20 total=120 latency=1.0s\n",
                "2027-01-15 09:10:20,000 WARNING [unrecovered-task] agent.conversation_loop: API call failed (attempt 1/1) error_type=APITimeout provider=provider base_url=https://example.test model=provider/model-u summary=timeout\n",
                "2027-01-15 09:20:10,000 INFO [clean-task] agent.conversation_loop: API call #1: model=provider/model-c provider=provider in=100 out=20 total=120 latency=1.0s\n",
                "2027-01-15 09:30:10,000 INFO [switched-task] agent.conversation_loop: API call #1: model=provider/model-x provider=provider in=100 out=20 total=120 latency=1.0s\n",
                "2027-01-15 09:30:40,000 INFO [switched-task] agent.conversation_loop: API call #2: model=provider/model-b provider=provider in=100 out=20 total=120 latency=1.0s\n",
                "2027-01-15 09:40:10,000 INFO [tool-only-task] agent.conversation_loop: API call #1: model=provider/model-t provider=provider in=100 out=20 total=120 latency=1.0s\n",
            ]
        finally:
            connection.close()

        log_path = self.home / "logs" / "agent.log"
        log_path.write_text(log_path.read_text(encoding="utf-8") + "".join(log_specs), encoding="utf-8")
        api._log_file_cache.clear()
        with patch.object(api, "_plugin_settings", return_value={"rate_sample_threshold": 1}):
            models = {item["model_id"]: item for item in api._ai_models_sync(0)["models"]}

        recovered = models["provider/model-r"]["work_reliability"]
        self.assertEqual(recovered["eligible_tasks"], 1)
        self.assertEqual(recovered["recovered_tasks"], 1)
        self.assertEqual(recovered["unrecovered_failures"], 0)
        self.assertEqual(recovered["recovery_rate"], 1)

        unrecovered = models["provider/model-u"]["work_reliability"]
        self.assertEqual(unrecovered["eligible_tasks"], 1)
        self.assertEqual(unrecovered["unrecovered_failures"], 1)
        self.assertEqual(unrecovered["completion_rate"], 0)
        self.assertGreater(unrecovered["rank"], recovered["rank"])

        clean = models["provider/model-c"]["work_reliability"]
        self.assertEqual(clean["clean_completions"], 1)
        self.assertEqual(clean["completion_rate"], 1)
        self.assertEqual(clean["by_route"][0]["label"], "Provider API")

        switched_from = models["provider/model-x"]["work_reliability"]
        switched_to = models["provider/model-b"]["work_reliability"]
        self.assertEqual(switched_from["switched_away_tasks"], 1)
        self.assertEqual(switched_from["eligible_tasks"], 0)
        self.assertEqual(switched_to["completed_tasks"], 1)
        self.assertEqual(switched_to["clean_completions"], 0)

        tool_only = models["provider/model-t"]["work_reliability"]
        self.assertEqual(tool_only["eligible_tasks"], 0)
        self.assertEqual(tool_only["unknown_tasks"], 1)
        self.assertEqual(models["provider/model-t"]["failures"]["tool_failures"], 1)

    def test_ai_models_ui_keeps_early_quota_neutral_and_enforces_sample_floor(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("elapsed !== null && elapsed < 10", source)
        self.assertIn("early in period", source)
        self.assertIn("Number(model.accepted_tasks) >= 10", source)
        self.assertIn("insufficient data", source)
        self.assertIn("Tool failures", source)

    def test_ai_model_rate_threshold_and_route_mapping_are_configurable(self):
        with patch.object(api, "_plugin_settings", return_value={"rate_sample_threshold": 7}):
            payload = api._ai_models_sync(0)
        self.assertEqual(payload["coverage"]["rate_sample_threshold"], 7)

        historical = api._historical_route_mappings(
            {
                "gpt-5.6-luna": {
                    "routes_map": {
                        "recorded": {"provider": "openai-codex", "label": "OpenAI OAuth"}
                    }
                },
                "gpt-5.6-terra-pro": {
                    "routes_map": {
                        "unknown": {"provider": "unknown", "label": "Unknown API"}
                    }
                },
            }
        )
        self.assertEqual(historical["gpt-5.6-*"], "OpenAI OAuth")
        unknown = {"provider": "unknown", "label": "Unknown API"}
        inferred = api._apply_route_mapping("gpt-5.6-terra-pro", unknown, {}, historical)
        configured = api._apply_route_mapping(
            "gpt-5.6-terra-pro",
            unknown,
            {"gpt-5.6-*": "Custom route"},
            historical,
        )
        unmapped = api._apply_route_mapping("other-model", unknown, {}, historical)
        self.assertEqual(inferred["label"], "OpenAI OAuth")
        self.assertEqual(inferred["mapping_source"], "historical")
        self.assertEqual(inferred["provider"], "openai-codex")
        self.assertTrue(inferred["oauth"])
        self.assertTrue(inferred["subscription"])
        self.assertEqual(inferred["quota_provider"], "codex")
        self.assertEqual(configured["label"], "Custom route")
        self.assertEqual(configured["mapping_source"], "config")
        self.assertEqual(unmapped["label"], "Unmapped (edit in config)")

    def test_ai_models_ui_guards_rate_samples_and_zero_request_log_metrics(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("of ${formatCount(samples)} ${sampleNoun}", source)
        self.assertIn("${formatCount(count)}/${formatCount(samples)}", source)
        self.assertIn("leftAdequate ? -1 : 1", source)
        self.assertIn("if (!leftAdequate) return left.index - right.index", source)
        self.assertIn("activity outside selected period; see the provenance note", source)
        self.assertIn("Unmapped (edit in config)", source)
        self.assertIn("Reliability rank #${formatCount(reliability.rank)}", source)
        self.assertIn("Not rankable yet", source)
        self.assertIn("risk ≤ ${formatPercent(bound)}", source)
        self.assertIn("of ${formatCount(toolCalls)} tool calls", source)
        self.assertIn("Work ledger: scores completed main-role tasks", source)
        self.assertIn("at this pace, empty ~${formatShortDate(quota.exhaustAt)}", source)
        self.assertIn("API attempt failure rate", source)

    def test_custom_period_is_inclusive_by_start_and_exclusive_by_end(self):
        start = 1_799_999_999
        end = 1_800_000_001
        sessions = api._list_sessions_sync(
            days=0,
            start_at=start,
            end_at=end,
            query="",
            sort="recent",
            failures_only=False,
            include_archived=False,
            limit=50,
            offset=0,
        )
        self.assertEqual(sessions["pagination"]["total"], 1)
        self.assertEqual(api._overview_sync(0, start, end)["totals"]["sessions"], 1)
        self.assertEqual(len(api._tools_sync(0, start, end)["tools"]), 2)
        self.assertEqual(len(api._skills_sync(0, start, end)["skills"]), 1)
        self.assertEqual(api._profiles_sync(0, start, end)["totals"]["sessions"], 1)

        outside_start = 1_800_000_001
        outside_end = 1_800_001_000
        self.assertEqual(api._overview_sync(0, outside_start, outside_end)["totals"]["sessions"], 0)
        self.assertEqual(api._tools_sync(0, outside_start, outside_end)["tools"], [])
        self.assertEqual(api._skills_sync(0, outside_start, outside_end)["skills"], [])
        self.assertEqual(api._profiles_sync(0, outside_start, outside_end)["totals"]["sessions"], 0)

    def test_custom_period_filters_runtime_logs_and_rejects_reverse_ranges(self):
        log_time = 1_800_000_000.0
        included = api._telemetry_sync(0, "session-1", log_time, log_time + 4)
        excluded = api._telemetry_sync(0, "session-1", log_time + 4, log_time + 8)
        self.assertEqual(included["summary"]["api_calls"], 1)
        self.assertEqual(included["summary"]["tool_runs"], 1)
        self.assertEqual(excluded["summary"]["api_calls"], 0)
        with self.assertRaises(api.HTTPException) as context:
            api._period_bounds(0, 20, 10)
        self.assertEqual(context.exception.status_code, 422)

    def test_outcomes_do_not_label_stale_unended_sessions_as_running(self):
        stale = api._session_outcome({"ended_at": None, "last_activity_at": 1})
        active = api._session_outcome({"ended_at": None, "last_activity_at": api.time.time()})
        self.assertEqual(stale["outcome"], "open")
        self.assertEqual(active["outcome"], "running")

    def test_operations_sources_are_read_only_and_prompt_safe(self):
        profiles = api._profiles_sync(0)
        self.assertEqual(profiles["totals"]["profiles"], 1)
        gateway = api._gateway_sync()
        self.assertEqual(gateway["gateways"][0]["state"], "running")
        schedules = api._schedules_sync()
        self.assertEqual(schedules["totals"]["jobs"], 1)
        self.assertNotIn("prompt", schedules["schedules"][0])
        self.assertNotIn("must-not-leak", json.dumps(schedules))


if __name__ == "__main__":
    unittest.main()
