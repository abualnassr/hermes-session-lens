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
from datetime import datetime, timezone
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
from dashboard import _services as services


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

    def _search_total(self, query):
        payload = api._list_sessions_sync(
            days=0,
            query=query,
            sort="failures",
            failures_only=False,
            include_archived=False,
            limit=50,
            offset=0,
        )
        return payload["pagination"]["total"]

    def _make_beta_profile(self):
        import shutil

        beta_dir = self.home / "profiles" / "beta"
        beta_dir.mkdir(parents=True)
        beta_db = beta_dir / "state.db"
        shutil.copyfile(self.db_path, beta_db)
        connection = sqlite3.connect(beta_db)
        connection.execute("UPDATE sessions SET id='beta-session-1', title='Beta work'")
        connection.execute("UPDATE messages SET session_id='beta-session-1'")
        connection.execute("UPDATE session_model_usage SET session_id='beta-session-1'")
        connection.commit()
        connection.close()
        return beta_db

    def test_profile_scope_discovery_and_param_parsing(self):
        self._make_beta_profile()
        self.assertEqual(api._discovered_profiles(), ["default", "beta"])
        self.assertIsNone(api._parse_profiles_param(""))
        self.assertEqual(api._parse_profiles_param("all"), ["default", "beta"])
        self.assertEqual(api._parse_profiles_param("beta,unknown"), ["beta"])
        self.assertIsNone(api._parse_profiles_param("unknown"))

    def test_profile_scope_unions_sessions_across_profiles(self):
        self._make_beta_profile()
        list_kwargs = dict(
            days=0, query="", sort="recent", failures_only=False,
            include_archived=False, limit=50, offset=0,
        )
        payload = api._scoped_call("all", api._list_sessions_sync, **list_kwargs)
        by_id = {item["id"]: item.get("profile") for item in payload["sessions"]}
        self.assertEqual(by_id, {"session-1": "default", "beta-session-1": "beta"})
        self.assertEqual(payload["pagination"]["total"], 2)

        beta_only = api._scoped_call("beta", api._list_sessions_sync, **list_kwargs)
        self.assertEqual([item["id"] for item in beta_only["sessions"]], ["beta-session-1"])
        self.assertEqual(beta_only["sessions"][0]["profile"], "beta")

        # The scope always resets, even after the scoped call returns.
        self.assertIsNone(api._get_profile_scope())

        detail = api._scoped_call("beta", api._session_detail_sync, "beta-session-1")
        self.assertEqual(detail["session"]["id"], "beta-session-1")

    def test_profile_scope_search_and_query_syntax_span_profiles(self):
        self._make_beta_profile()
        list_kwargs = dict(
            days=0, sort="recent", failures_only=False,
            include_archived=False, limit=50, offset=0,
        )
        titled = api._scoped_call("all", api._list_sessions_sync, query="beta work", **list_kwargs)
        self.assertEqual([item["id"] for item in titled["sessions"]], ["beta-session-1"])
        self.assertEqual(
            api._scoped_call("all", api._list_sessions_sync, query="title:beta", **list_kwargs)["pagination"]["total"],
            1,
        )

    def test_query_syntax_field_predicates_filter_sessions(self):
        self.assertEqual(self._search_total("model:model-a"), 1)
        self.assertEqual(self._search_total("model:model-z"), 0)
        self.assertEqual(self._search_total("project:demo"), 1)
        self.assertEqual(self._search_total("source:desktop"), 1)
        self.assertEqual(self._search_total("provider:provider"), 1)
        self.assertEqual(self._search_total("failed:yes"), 1)
        self.assertEqual(self._search_total("failed:no"), 0)
        self.assertEqual(self._search_total("tokens:>1k"), 1)
        self.assertEqual(self._search_total("tokens:>2k"), 0)
        self.assertEqual(self._search_total("cost:<1"), 1)
        self.assertEqual(self._search_total("cost:>1"), 0)

    def test_query_syntax_combines_terms_and_free_text(self):
        self.assertEqual(self._search_total("project:demo failed:yes"), 1)
        self.assertEqual(self._search_total("project:demo failed:no"), 0)
        self.assertEqual(self._search_total('title:"plugin investigation"'), 1)
        self.assertEqual(self._search_total("plugin model:model-a"), 1)
        self.assertEqual(self._search_total("plugin model:model-z"), 0)

    def test_query_syntax_unknown_fields_stay_free_text(self):
        # A Windows path's drive letter must not be swallowed as a field.
        self.assertEqual(self._search_total("C:\\work"), 1)
        free_text, terms = api._parse_session_query("note: fix D:\\repo model:model-a")
        self.assertEqual(terms, [("model", "model-a")])
        self.assertIn("D:\\repo", free_text)
        self.assertIn("note:", free_text)

    def test_tools_group_by_mcp_server_with_log_metrics(self):
        self.assertEqual(api._tool_group("mcp__brave__search"), ("mcp", "brave", "search"))
        self.assertEqual(api._tool_group("terminal"), ("builtin", "built-in", "terminal"))

        connection = sqlite3.connect(self.db_path)
        calls = [{"id": "call-9", "type": "function", "function": {"name": "mcp__brave__search", "arguments": "{}"}}]
        connection.execute(
            "INSERT INTO messages (session_id,role,tool_calls,timestamp,active) VALUES (?,?,?,?,1)",
            ("session-1", "assistant", json.dumps(calls), 1_800_000_020),
        )
        connection.execute(
            "INSERT INTO messages (session_id,role,content,tool_call_id,tool_name,timestamp,active) VALUES (?,?,?,?,?,?,1)",
            ("session-1", "tool", "r" * 1200, "call-9", "mcp__brave__search", 1_800_000_021),
        )
        connection.commit()
        connection.close()
        log_path = self.home / "logs" / "agent.log"
        log_path.write_text(
            log_path.read_text(encoding="utf-8")
            + f"{_stamp(1_800_000_021)} INFO [session-1] agent.tool_executor: "
            "tool mcp__brave__search completed (0.50s, 1200 chars)\n",
            encoding="utf-8",
        )

        payload = api._tools_sync(days=0)
        groups = {(group["kind"], group["name"]): group for group in payload["groups"]}
        brave = groups[("mcp", "brave")]
        self.assertEqual(brave["tool_count"], 1)
        self.assertEqual(brave["calls"], 1)
        self.assertEqual(brave["context_chars"], 1200)
        self.assertEqual(brave["context_tokens_estimate"], 300)
        self.assertAlmostEqual(brave["latency_p50_seconds"], 0.5)
        self.assertEqual(len(brave["trend"]), 7)
        builtin = groups[("builtin", "built-in")]
        self.assertEqual(builtin["tool_count"], 2)
        self.assertEqual(payload["totals"]["mcp_servers"], 1)
        by_name = {tool["name"]: tool for tool in payload["tools"]}
        self.assertEqual(by_name["mcp__brave__search"]["group"], "brave")
        self.assertEqual(by_name["mcp__brave__search"]["short_name"], "search")
        self.assertAlmostEqual(by_name["write_file"]["latency_p50_seconds"], 1.25)

    def test_ui_tools_view_shows_group_inventory(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("Tools & MCP servers", source)
        self.assertIn("Context weight", source)
        self.assertIn("rows: data.groups || []", source)
        self.assertIn("'Trend (7d)'", source)

    def test_ui_profile_scope_picker_wired_into_requests(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("function ProfileScopePicker", source)
        self.assertIn("let activeProfilesParam", source)
        self.assertIn("if (activeProfilesParam) merged.profiles = activeProfilesParam", source)
        self.assertIn("ctx.storage.get('profileScope')", source)
        self.assertIn("queryClient.invalidateQueries({ queryKey: [PLUGIN_ID] })", source)
        # The old static chip must not linger next to the picker.
        self.assertNotIn("children: `data: ${servingProfile} profile`", source)

    def test_ui_account_cards_use_base_provider_for_icon_and_refresh(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("usageProviderIcons[provider.base_provider || provider.provider]", source)
        self.assertIn("onRefresh(provider.base_provider || provider.provider)", source)

    def test_ui_search_hint_documents_query_syntax(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("try model:opus failed:yes", source)
        self.assertIn("tokens:>500k", source)
        self.assertIn("failed:yes|no", source)

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

    def test_digest_folds_budgets_attribution_and_services(self):
        now = time.time()
        reset_iso = datetime.fromtimestamp(now + 3 * 86400, tz=timezone.utc).isoformat()
        usage = {
            "providers": [
                {
                    "provider": "openrouter",
                    "label": "OpenRouter",
                    "status": "ok",
                    "windows": [
                        {
                            "kind": "quota",
                            "label": "Weekly quota",
                            "percentage_used": 40,
                            "reset_at": reset_iso,
                            "attribution": {
                                "basis": "window",
                                "tokens": 1000,
                                "sessions": 2,
                                "cost_usd": 1.5,
                                "by_project": [
                                    {"label": "demo", "share_percent": 69.8, "tokens": 698},
                                    {"label": "noise", "share_percent": 0.2, "tokens": 2},
                                    {"label": "2 other", "other": True, "share_percent": 30.0, "tokens": 300},
                                ],
                                "by_model": [{"label": "gpt-x", "share_percent": 100.0, "tokens": 1000}],
                                "by_session": [],
                            },
                        },
                        {
                            "kind": "balance",
                            "label": "Monthly usage",
                            "used": 126.44,
                            "limit": 250,
                            "unit": "USD",
                            "attribution": {
                                "basis": "trailing_7d",
                                "tokens": 500,
                                "sessions": 1,
                                "cost_usd": 18.92,
                                "by_project": [],
                                "by_model": [],
                                "by_session": [],
                                "explained": {"account_used": 126.44, "unit": "USD", "local_cost_usd": 18.92, "percent": 15.0},
                            },
                        },
                        {"kind": "balance", "label": "Credits", "remaining": 12.5, "unit": "USD"},
                    ],
                },
                {"provider": "zai", "label": "Z.AI", "status": "not_configured", "windows": []},
            ]
        }
        services_payload = {
            "cards": [
                services._service_payload(
                    "firecrawl",
                    status="ok",
                    plan="Standard",
                    windows=[services._usage_window("Credits", kind="balance", remaining=4512, limit=5000, unit="credits")],
                ),
                services._service_payload("brightdata", status="forbidden", message="Token lacks the balance permission."),
                services._service_payload(
                    "firecrawl",
                    status="ok",
                    account="topped",
                    windows=[services._usage_window("Credits", kind="balance", remaining=10752, limit=1000, unit="credits",
                                                     detail="Plan 1,000 credits per period · balance includes top-ups beyond the plan")],
                ),
                services._service_payload("monid", status="ok", details=["Month to date: 2.50 USD across 2 runs"]),
                services._service_payload("scrapecreators", status="not_configured", message="No key."),
            ],
            "inventory": [],
            "summary": {"configured": 5, "monitored": 2, "attention": 1, "unreadable": 2},
        }
        budgets_payload = {
            "month": {"start": now - 5 * 86400, "end": now + 20 * 86400, "days_remaining": 20.0},
            "entries": [
                {
                    "id": "openrouter", "label": "OpenRouter", "cap_usd": 150.0, "spend_usd": 126.44,
                    "spend_source": "account", "projected_usd": 180.0, "percent_of_cap": 84.3,
                    "status": "at_risk", "cross_at": now + 4 * 86400, "account_stale": False,
                },
                {
                    "id": "nous", "label": "Nous Portal", "cap_usd": None, "spend_usd": 3.1,
                    "spend_source": "local", "projected_usd": 6.0, "percent_of_cap": None,
                    "status": "no_cap", "cross_at": None, "account_stale": False,
                },
            ],
            "total": {
                "id": "all", "label": "All providers", "cap_usd": 300.0, "spend_usd": 129.54,
                "spend_source": "mixed", "projected_usd": 186.0, "percent_of_cap": 43.2,
                "status": "ok", "cross_at": None, "account_stale": False,
            },
            "notes": [{"id": "budget:openrouter:2026-09"}],
        }
        with patch.object(api, "_ai_usage_sync", return_value=usage), \
             patch.object(api, "_services_sync", return_value=services_payload) as services_sync, \
             patch.object(api, "_budgets_sync", return_value=budgets_payload) as budgets_sync:
            payload = api._digest_sync(7, budgets="openrouter:150, all:300")
        services_sync.assert_called_once_with()
        budgets_sync.assert_called_once_with({"openrouter": 150.0, "all": 300.0})
        markdown = payload["markdown"]

        # Budgets: one line per entry plus the total, cap status in words, before quota windows.
        self.assertIn("## Monthly spend", markdown)
        self.assertIn("(20 days left)", markdown)
        self.assertIn("- OpenRouter: $126.44 month to date (account figure), projected $180.00 by month end — cap $150.00: on pace to exceed ~", markdown)
        self.assertIn("- Nous Portal: $3.10 month to date (local records), projected $6.00 by month end — no cap set", markdown)
        self.assertIn("- All providers: $129.54 month to date (account figures plus local records), projected $186.00 by month end — cap $300.00: within cap (43% used)", markdown)
        self.assertLess(markdown.index("## Monthly spend"), markdown.index("## Quota windows"))
        self.assertEqual(payload["budgets"]["notes"], [{"id": "budget:openrouter:2026-09"}])
        self.assertNotIn("definition", payload["budgets"])

        # Quota window carries its attribution as a nested bullet; the "other" bucket is not named.
        self.assertIn("- OpenRouter Weekly quota: 40% used", markdown)
        self.assertIn("  - local share: demo 70% · top model gpt-x", markdown)
        self.assertNotIn("2 other", markdown)
        self.assertNotIn("noise", markdown)
        self.assertEqual(payload["quota_windows"][0]["attribution"]["top_projects"][0]["label"], "demo")

        # Money window becomes a spend window with the explained share; a pure balance does not.
        self.assertIn("## Spend windows", markdown)
        self.assertIn("- OpenRouter Monthly usage: $126.44 used of $250.00", markdown)
        self.assertIn("  - local sessions explain $18.92 of $126.44 (15%); the rest came from other machines, tools, or profiles (trailing 7 days — window span not readable)", markdown)
        self.assertEqual([item["label"] for item in payload["spend_windows"]], ["Monthly usage"])

        # Services: readings for ok cards, the reason for cards needing attention, the tally, no not_configured rows.
        self.assertIn("## Service balances", markdown)
        self.assertIn("- Firecrawl: 4,512 credits remaining of 5,000 · plan Standard", markdown)
        self.assertIn("- Firecrawl · topped: 10,752 credits remaining (Plan 1,000 credits per period · balance includes top-ups beyond the plan)", markdown)
        self.assertIn("- Bright Data: needs attention — Token lacks the balance permission.", markdown)
        self.assertIn("- Monid: Month to date: 2.50 USD across 2 runs", markdown)
        self.assertNotIn("ScrapeCreators", markdown)
        self.assertIn("- 5 configured · 2 monitored · 1 need attention · 2 without a readable usage API", markdown)
        self.assertEqual(payload["services"]["summary"]["monitored"], 2)
        self.assertEqual([row["provider"] for row in payload["services"]["rows"]], ["firecrawl", "brightdata", "firecrawl:topped", "monid"])

    def test_digest_stays_quiet_without_budgets_services_or_attribution(self):
        usage = {"providers": [{"provider": "codex", "label": "Codex", "status": "ok", "windows": [
            {"kind": "quota", "label": "Weekly quota", "percentage_used": 10, "reset_at": None}]}]}
        empty_budgets = {"month": {"start": None, "days_remaining": 0}, "entries": [], "total": {"id": "all", "spend_usd": 0, "cap_usd": None}, "notes": []}
        with patch.object(api, "_ai_usage_sync", return_value=usage), \
             patch.object(api, "_services_sync", side_effect=RuntimeError("no services")), \
             patch.object(api, "_budgets_sync", return_value=empty_budgets):
            payload = api._digest_sync(0)
        markdown = payload["markdown"]
        self.assertIn("- Codex Weekly quota: 10% used", markdown)
        for heading in ("## Monthly spend", "## Spend windows", "## Service balances"):
            self.assertNotIn(heading, markdown)
        self.assertIsNone(payload["services"])
        self.assertEqual(payload["spend_windows"], [])

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

    def test_anthropic_usage_payload_mirrors_hermes_window_parsing(self):
        from dashboard._providers import anthropic as anthropic_provider

        payload = anthropic_provider._anthropic_usage_payload(
            {
                "five_hour": {"utilization": 0.42, "resets_at": "2027-01-15T21:00:00+00:00"},
                "seven_day": {"utilization": 25, "resets_at": "2027-01-19T07:00:00+00:00"},
                "extra_usage": {"is_enabled": True, "used_credits": 1.5, "monthly_limit": 20, "currency": "USD"},
            }
        )
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(
            [(w["label"], w["percentage_used"]) for w in payload["windows"]],
            [("Current session", 42.0), ("Current week", 25.0)],
        )
        self.assertEqual(payload["details"], ["Extra usage: 1.50 / 20.00 USD"])

    def test_anthropic_api_key_shadowing_falls_back_to_stored_oauth(self):
        from dashboard._providers import anthropic as anthropic_provider

        shadowed = patch.object(
            anthropic_provider, "_resolve_anthropic_oauth", return_value=("sk-ant-api-key", False)
        )
        ok = api._provider_payload("anthropic", status="ok")
        expired = api._provider_payload("anthropic", status="expired", message="rejected")

        # Claude Code's own token is preferred: it stays fresh through use.
        with shadowed:
            with patch.object(anthropic_provider, "_resolve_anthropic_claude_code_oauth", return_value="cc-token"):
                with patch.object(anthropic_provider, "_resolve_anthropic_pool_oauth", return_value="pool-token"):
                    with patch.object(anthropic_provider, "_collect_anthropic_direct", return_value=ok) as direct:
                        result = anthropic_provider._collect_anthropic_usage()
        direct.assert_called_once_with("cc-token")
        self.assertIs(result, ok)

        # A rejected Claude Code token falls through to the pool login.
        with shadowed:
            with patch.object(anthropic_provider, "_resolve_anthropic_claude_code_oauth", return_value="cc-token"):
                with patch.object(anthropic_provider, "_resolve_anthropic_pool_oauth", return_value="pool-token"):
                    with patch.object(
                        anthropic_provider, "_collect_anthropic_direct", side_effect=[expired, ok]
                    ) as direct:
                        result = anthropic_provider._collect_anthropic_usage()
        self.assertEqual(direct.call_count, 2)
        direct.assert_any_call("pool-token")
        self.assertIs(result, ok)

        # No Claude Code credentials: the pool login alone still works.
        with shadowed:
            with patch.object(anthropic_provider, "_resolve_anthropic_claude_code_oauth", return_value=""):
                with patch.object(anthropic_provider, "_resolve_anthropic_pool_oauth", return_value="pool-token"):
                    with patch.object(anthropic_provider, "_collect_anthropic_direct", return_value=ok) as direct:
                        result = anthropic_provider._collect_anthropic_usage()
        direct.assert_called_once_with("pool-token")
        self.assertIs(result, ok)

        # Every stored login rejected: surface the first failure.
        with shadowed:
            with patch.object(anthropic_provider, "_resolve_anthropic_claude_code_oauth", return_value="cc-token"):
                with patch.object(anthropic_provider, "_resolve_anthropic_pool_oauth", return_value="cc-token"):
                    with patch.object(anthropic_provider, "_collect_anthropic_direct", return_value=expired) as direct:
                        result = anthropic_provider._collect_anthropic_usage()
        direct.assert_called_once_with("cc-token")  # identical tokens tried once
        self.assertIs(result, expired)

        # API key but no saved OAuth login: explain the requirement plainly.
        with shadowed:
            with patch.object(anthropic_provider, "_resolve_anthropic_claude_code_oauth", return_value=""):
                with patch.object(anthropic_provider, "_resolve_anthropic_pool_oauth", return_value=""):
                    result = anthropic_provider._collect_anthropic_usage()
        self.assertEqual(result["status"], "not_configured")
        self.assertIn("Sign in with Claude in Hermes", result["message"])

    def test_zai_no_coding_plan_is_nothing_to_monitor_not_a_fault(self):
        payload = api._zai_payload({"code": 500, "msg": "当前用户不存在coding plan", "success": False})
        self.assertEqual(payload["status"], "not_configured")
        self.assertIn("no Coding Plan subscription", payload["message"])

        generic = api._zai_payload({"code": 500, "msg": "internal error", "success": False})
        self.assertEqual(generic["status"], "unavailable")
        self.assertIn("internal error", generic["message"])

    def test_anthropic_multi_account_cards_from_pool(self):
        from dashboard._providers import anthropic as anthropic_provider

        accounts = [
            {"label": "work", "token": "tok-primary"},
            {"label": "personal", "token": "tok-personal"},
        ]
        ok = api._provider_payload("anthropic", status="ok")
        with patch.object(anthropic_provider, "_anthropic_pool_oauth_accounts", return_value=accounts):
            with patch.object(anthropic_provider, "_collect_anthropic_direct", return_value=dict(ok)) as direct:
                cards = anthropic_provider._anthropic_account_cards(["tok-primary"])
        direct.assert_called_once_with("tok-personal")
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["provider"], "anthropic:1")
        self.assertEqual(card["base_provider"], "anthropic")
        self.assertEqual(card["account"], "personal")
        self.assertTrue(card["account_extra"])
        self.assertIn("personal", card["label"])

    def test_ai_usage_flattens_account_cards_without_inflating_summary(self):
        def ok(provider):
            return api._provider_payload(
                provider,
                status="ok",
                windows=[api._usage_window("Weekly", used_percent=25)],
            )

        anthropic_result = ok("anthropic")
        extra = ok("anthropic")
        extra.update(
            {
                "provider": "anthropic:1",
                "base_provider": "anthropic",
                "account": "personal",
                "account_extra": True,
                "label": "Anthropic Claude · personal",
            }
        )
        anthropic_result["extra_accounts"] = [extra]
        collectors = {
            "_collect_codex_usage": Mock(return_value=ok("codex")),
            "_collect_anthropic_usage": Mock(return_value=anthropic_result),
            "_collect_nous_usage": Mock(return_value=ok("nous")),
            "_collect_openrouter_usage": Mock(return_value=ok("openrouter")),
            "_collect_deepseek_usage": Mock(return_value=ok("deepseek")),
            "_collect_grok_usage": Mock(return_value=ok("grok")),
            "_collect_kimi_usage": Mock(return_value=ok("kimi")),
            "_collect_zai_usage": Mock(return_value=ok("zai")),
        }
        with patch.multiple(api, **collectors):
            payload = api._ai_usage_sync(True)
        ids = [item["provider"] for item in payload["providers"]]
        self.assertIn("anthropic:1", ids)
        self.assertEqual(ids.index("anthropic:1"), ids.index("anthropic") + 1)
        # The extra card never rides along inside the primary payload.
        base_card = payload["providers"][ids.index("anthropic")]
        self.assertNotIn("extra_accounts", base_card)
        # Summary counts base providers only: 8 providers, not 9.
        self.assertEqual(payload["summary"]["providers"], 8)
        self.assertEqual(payload["summary"]["connected"], 8)

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
        self.assertIn("No monitorable AI providers are connected", source)
        self.assertIn("more supported", source)

    def test_ai_usage_single_provider_refresh_merges_into_cached_payload(self):
        def ok(provider, used):
            return api._provider_payload(
                provider,
                status="ok",
                windows=[api._usage_window("Weekly", used_percent=used)],
            )

        first = {
            f"_collect_{provider}_usage": Mock(return_value=ok(provider, 25))
            for provider in api._AI_USAGE_PROVIDER_ORDER
        }
        with patch.multiple(api, **first):
            api._ai_usage_sync(True)
        cached_at = api._ai_usage_cache[0]

        second = {
            f"_collect_{provider}_usage": Mock(return_value=ok(provider, 60))
            for provider in api._AI_USAGE_PROVIDER_ORDER
        }
        with patch.multiple(api, **second):
            payload = api._ai_usage_sync(True, "grok")
        second["_collect_grok_usage"].assert_called_once()
        for provider in api._AI_USAGE_PROVIDER_ORDER:
            if provider != "grok":
                second[f"_collect_{provider}_usage"].assert_not_called()
        by_provider = {item["provider"]: item for item in payload["providers"]}
        self.assertEqual(by_provider["grok"]["windows"][0]["percentage_used"], 60.0)
        self.assertEqual(by_provider["codex"]["windows"][0]["percentage_used"], 25.0)
        self.assertFalse(payload["cached"])
        # Refreshing one card must not extend the whole payload's lifetime.
        self.assertEqual(api._ai_usage_cache[0], cached_at)

    def test_ai_usage_attaches_local_seven_day_usage_per_provider(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                SESSION_MODEL_USAGE_INSERT_SQL,
                ("session-kimi", "kimi/k2", "kimi-coding", "metered", "", 2, 1000, 200, 0, 0, 0, 0.05, 0,
                 "estimated", "test", 1_800_000_000, 1_800_000_100),
            )
            connection.execute(
                SESSION_MODEL_USAGE_INSERT_SQL,
                ("session-kimi-cn", "kimi/k2", "kimi-coding-cn", "metered", "", 1, 500, 100, 0, 0, 0, 0.02, 0,
                 "estimated", "test", 1_800_000_000, 1_800_000_100),
            )
            connection.commit()
        finally:
            connection.close()
        recorded = api._usage_recorded_7d()
        self.assertEqual(recorded["kimi"]["tokens"], 1800)
        self.assertEqual(recorded["kimi"]["sessions"], 2)
        self.assertAlmostEqual(recorded["kimi"]["cost_usd"], 0.07)
        self.assertNotIn("codex", recorded)

        collectors = {
            f"_collect_{provider}_usage": Mock(
                return_value=api._provider_payload(provider, status="ok",
                                                  windows=[api._usage_window("Weekly", used_percent=10)])
            )
            for provider in api._AI_USAGE_PROVIDER_ORDER
        }
        with patch.multiple(api, **collectors):
            payload = api._ai_usage_sync(True)
        kimi = next(item for item in payload["providers"] if item["provider"] == "kimi")
        self.assertEqual(kimi["recorded_7d"]["tokens"], 1800)
        codex = next(item for item in payload["providers"] if item["provider"] == "codex")
        self.assertIsNone(codex["recorded_7d"])

    def test_ai_usage_ui_orders_by_urgency_with_countdowns_and_local_line(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("function usageUrgency", source)
        self.assertIn("usageUrgency(b) - usageUrgency(a)", source)
        self.assertIn("function formatCountdown", source)
        self.assertIn("Recorded locally: ", source)
        self.assertIn("Refresh only ${provider.label}", source)
        self.assertIn("provider=${encodeURIComponent(provider)}", source)

    def test_ai_usage_reports_configured_but_unsupported_registry_providers(self):
        # Outside Hermes the registry is unreachable and the list is empty.
        self.assertEqual(api._usage_unsupported_configured(), [])

        with patch.object(
            api,
            "_hermes_configured_provider_ids",
            return_value=["deepseek", "alibaba", "alibaba-coding-plan", "qwen-oauth", "nvidia", "my-custom-llm"],
        ):
            entries = api._usage_unsupported_configured()
        labels = [entry["label"] for entry in entries]
        # deepseek is covered by a collector; Qwen ids dedupe to one label.
        self.assertEqual(labels, ["My Custom Llm", "NVIDIA", "Qwen"])

            # And the payload carries the list for the UI footer.
        collectors = {
            f"_collect_{provider}_usage": Mock(
                return_value=api._provider_payload(provider, status="not_configured")
            )
            for provider in api._AI_USAGE_PROVIDER_ORDER
        }
        with patch.object(api, "_hermes_configured_provider_ids", return_value=["nvidia"]):
            with patch.multiple(api, **collectors):
                payload = api._ai_usage_sync(True)
        self.assertEqual(payload["hermes_configured_unsupported"], [{"id": "nvidia", "label": "NVIDIA"}])

    def test_ai_usage_ui_names_unmonitorable_configured_providers(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("Configured in Hermes, not yet monitorable: ", source)
        self.assertIn("hermes_configured_unsupported", source)
        self.assertIn("No monitorable AI providers are connected", source)

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
        # Sparklines were removed at the user's request (0.19.0); history
        # still feeds the slope forecast.
        self.assertNotIn("UsageSparkline", source)

    def test_refresh_buttons_spin_with_animated_icon(self):
        source = (MODULE_PATH.parents[1] / "desktop" / "plugin.js").read_text(encoding="utf-8")
        self.assertIn("function SpinIcon", source)
        self.assertIn("session-lens-spin", source)
        # The Codicon "sync~spin" modifier rendered a blank box in Hermes.
        self.assertNotIn("sync~spin", source)

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


    def test_usage_attribution_ranks_local_sessions_inside_window(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO sessions (id, source, model, title, started_at, last_activity_at, git_repo_root, message_count)
                VALUES ('session-opus', 'desktop', 'claude-opus-5', 'Big refactor', 1800000300, 1800000900, 'C:\\work\\demo-repo', 3)
                """
            )
            connection.execute(
                SESSION_MODEL_USAGE_INSERT_SQL,
                ("session-opus", "claude-opus-5", "anthropic-oauth", "subscription", "", 3, 6000, 1500, 0, 0, 0, 0, 0,
                 "included", "test", 1_800_000_300, 1_800_000_900),
            )
            connection.execute(
                SESSION_MODEL_USAGE_INSERT_SQL,
                ("session-1", "claude-sonnet-5", "anthropic", "metered", "", 1, 1000, 500, 0, 0, 0, 0.5, 0,
                 "estimated", "test", 1_800_000_000, 1_800_000_100),
            )
            # Older than the window: must never count.
            connection.execute(
                "INSERT INTO sessions (id, source, model, started_at, last_activity_at, message_count) "
                "VALUES ('session-old', 'desktop', 'claude-opus-5', 1799000000, 1799000100, 1)"
            )
            connection.execute(
                SESSION_MODEL_USAGE_INSERT_SQL,
                ("session-old", "claude-opus-5", "anthropic-oauth", "subscription", "", 1, 99000, 1000, 0, 0, 0, 0, 0,
                 "included", "test", 1_799_000_000, 1_799_000_100),
            )
            connection.commit()
        finally:
            connection.close()
        now = 1_800_001_000
        reset_at = datetime.fromtimestamp(now + 3 * 86_400, timezone.utc).isoformat()
        card = {
            "windows": [
                api._usage_window("Current week", used_percent=60, reset_at=reset_at),
                api._usage_window("Opus week", used_percent=40, reset_at=reset_at),
                api._usage_window("Monthly extra usage", used=10, limit=20, unit="USD", reset_at=reset_at),
                api._usage_window("Credits", kind="balance", remaining=5, unit="USD"),
            ]
        }
        with patch.object(api.time, "time", return_value=now):
            api._attach_usage_attribution("anthropic", card)
        week, opus, extra, credits = [window["attribution"] for window in card["windows"]]

        self.assertEqual(week["basis"], "window")
        self.assertEqual(week["since"], now + 3 * 86_400 - 7 * 86_400)
        self.assertEqual(week["sessions"], 2)
        self.assertEqual(week["tokens"], 9000)
        self.assertAlmostEqual(week["cost_usd"], 0.5)
        top = week["by_session"][0]
        self.assertEqual(top["id"], "session-opus")
        self.assertEqual(top["label"], "Big refactor")
        self.assertEqual(top["model"], "claude-opus-5")
        self.assertEqual(top["search"], "id:session-opus")
        self.assertAlmostEqual(top["share_percent"], 83.3)
        self.assertEqual(week["by_session"][1]["search"], "id:session-1")
        project = week["by_project"][0]
        self.assertEqual((project["label"], project["kind"], project["sessions"]), ("demo-repo", "repo", 1))
        self.assertEqual(project["search"], 'project:"C:\\work\\demo-repo"')
        self.assertEqual(week["by_project"][1]["label"], "demo")
        self.assertEqual([row["label"] for row in week["by_model"]], ["claude-opus-5", "claude-sonnet-5"])
        self.assertNotIn("explained", week)

        # Model-family windows count only that family.
        self.assertEqual(opus["model_family"], "opus")
        self.assertEqual((opus["sessions"], opus["tokens"]), (1, 7500))

        # A money window compares local recorded cost against the account figure.
        self.assertEqual(extra["explained"], {"account_used": 10.0, "unit": "USD", "local_cost_usd": 0.5, "percent": 5.0})

        # No reset/duration: trailing 7 days, declared as such, still excludes the old session.
        self.assertEqual(credits["basis"], "trailing_7d")
        self.assertIsNone(credits["until"])
        self.assertEqual(credits["sessions"], 2)

    def test_quota_window_duration_recognises_session_windows(self):
        self.assertEqual(api._quota_window_duration_seconds("Current session"), 5 * 3600.0)
        self.assertEqual(api._quota_window_duration_seconds("Session"), 5 * 3600.0)
        self.assertEqual(api._quota_window_duration_seconds("Current week"), 7 * 86400.0)
        self.assertEqual(api._quota_window_duration_seconds("3-hour window"), 3 * 3600.0)
        self.assertIsNone(api._quota_window_duration_seconds("Account credits"))
        source = Path(__file__).resolve().parents[1].joinpath("desktop", "plugin.js").read_text(encoding="utf-8")
        self.assertIn("if (text.includes('session')) return 5 * 3600", source)

    def test_usage_attribution_skips_extra_account_cards_and_tolerates_db_errors(self):
        extra = {"account_extra": True, "windows": [api._usage_window("Weekly", used_percent=5)]}
        api._attach_usage_attribution("anthropic", extra)
        self.assertNotIn("attribution", extra["windows"][0])
        card = {"windows": [api._usage_window("Weekly", used_percent=5)]}
        with patch.object(api, "_usage_attribution_rows", side_effect=RuntimeError("db gone")):
            api._attach_usage_attribution("anthropic", card)
        self.assertNotIn("attribution", card["windows"][0])

    def test_ai_usage_attribution_is_wired_into_plugin_source(self):
        source = Path(__file__).resolve().parents[1].joinpath("desktop", "plugin.js").read_text(encoding="utf-8")
        self.assertIn("function UsageAttribution(", source)
        self.assertIn("jsx(UsageAttribution, { window, onDrill })", source)
        self.assertIn("What consumed ${scope}", source)
        self.assertIn("onDrill: drillToSessions", source[source.index("jsx(AIUsageView, {"):])
        self.assertIn("the rest came from other machines, tools, or profiles on this account", source)


    def test_tools_context_weight_is_priced_per_session_route(self):
        connection = sqlite3.connect(self.db_path)
        try:
            # Subscription session: tokens count against a quota, never dollars.
            connection.execute(
                "INSERT INTO sessions (id, source, model, started_at, last_activity_at, billing_provider, "
                "billing_mode, cost_status, message_count) VALUES ('session-sub', 'desktop', 'claude-opus-5', "
                "1800000500, 1800000600, 'anthropic-oauth', 'subscription_included', 'included', 3)"
            )
            rows = [
                ("session-1", "tool", "x" * 4000, "call-ctx", "mcp__ctx__fetch", 1_800_000_050),
                ("session-1", "assistant", "thinking", None, None, 1_800_000_051),
                ("session-1", "assistant", "done", None, None, 1_800_000_052),
                ("session-sub", "tool", "y" * 800, "call-sub", "mcp__ctx__fetch", 1_800_000_550),
                ("session-sub", "assistant", "ok", None, None, 1_800_000_551),
            ]
            for session_id, role, content, call_id, tool_name, timestamp in rows:
                connection.execute(
                    "INSERT INTO messages (session_id,role,content,tool_call_id,tool_name,timestamp,active) "
                    "VALUES (?,?,?,?,?,?,1)",
                    (session_id, role, content, call_id, tool_name, timestamp),
                )
            connection.commit()
        finally:
            connection.close()

        def pricing(model, provider, base_url, billing_mode, cost_status):
            if provider == "anthropic-oauth":
                return {"kind": "included", "input_per_token": None, "cache_read_per_token": None, "source": None}
            return {"kind": "priced", "input_per_token": 1e-6, "cache_read_per_token": 1e-7, "source": "test"}

        with patch.object(api, "_context_pricing", side_effect=pricing):
            payload = api._tools_sync(days=0)
        tool = next(item for item in payload["tools"] if item["name"] == "mcp__ctx__fetch")
        self.assertEqual(tool["context_chars"], 4800)
        self.assertEqual(tool["context_tokens_estimate"], 1200)
        # 1000 tokens at $1/M enter once; re-sent on two later calls at $0.1/M.
        self.assertAlmostEqual(tool["context_cost_usd"], 0.001)
        self.assertEqual(tool["carried_tokens_estimate"], 2200)
        self.assertAlmostEqual(tool["carried_cost_usd"], 0.0002)
        self.assertEqual(tool["context_pricing"], "mixed")
        self.assertAlmostEqual(tool["context_priced_share"], 4000 / 4800, places=3)
        self.assertAlmostEqual(tool["context_included_share"], 800 / 4800, places=3)
        group = next(item for item in payload["groups"] if item["name"] == "ctx")
        self.assertEqual(group["context_chars"], 4800)
        self.assertAlmostEqual(group["context_cost_usd"], 0.001)
        self.assertEqual(group["context_pricing"], "mixed")
        self.assertGreaterEqual(payload["totals"]["context_cost_usd"], 0.001)
        self.assertIn("context_chars", payload["totals"])

    def test_context_pricing_marks_subscription_routes_included_without_lookup(self):
        api._context_pricing_cache.clear()
        with patch.dict("sys.modules", {"agent": None, "agent.usage_pricing": None}):
            included = api._context_pricing("gpt-5.3-codex", "openai-codex", "", "subscription_included", "included")
            self.assertEqual(included["kind"], "included")
            unpriced = api._context_pricing("mystery/model", "custom", "http://localhost:1234/v1", "", "unknown")
            self.assertEqual(unpriced["kind"], "unpriced")
        self.assertIn(("mystery/model", "custom", "http://localhost:1234/v1"), api._context_pricing_cache)


    def test_budgets_param_parsing_is_forgiving(self):
        parsed = api._parse_budgets_param("openrouter:150, all:300,bad,zai:abc,:5,anthropic:0,Kimi:12.345")
        self.assertEqual(parsed, {"openrouter": 150.0, "all": 300.0, "kimi": 12.35})
        self.assertEqual(api._parse_budgets_param(""), {})

    def test_budgets_project_month_end_from_pace_and_prefer_account_spend(self):
        now = 1_800_000_000  # 2027-01-15 local-ish; the exact date only needs a mid-month position
        month_start, month_end = api._month_bounds(now)
        self.assertLess(month_start, now)
        self.assertGreater(month_end, now)
        connection = sqlite3.connect(self.db_path)
        try:
            # OpenRouter: $7 this month, $3.50 of it in the last 7 days → $0.50/day.
            connection.execute(
                SESSION_MODEL_USAGE_INSERT_SQL,
                ("session-1", "deepseek/x", "openrouter", "metered", "", 1, 100, 10, 0, 0, 0, 3.5, 0,
                 "estimated", "test", month_start + 60, month_start + 120),
            )
            connection.execute(
                SESSION_MODEL_USAGE_INSERT_SQL,
                ("session-1", "deepseek/y", "openrouter", "metered", "", 1, 100, 10, 0, 0, 0, 3.5, 0,
                 "estimated", "test", now - 3 * 86_400, now - 3 * 86_400 + 60),
            )
            # Nous: $2 this month, nothing recent.
            connection.execute(
                SESSION_MODEL_USAGE_INSERT_SQL,
                ("session-1", "nous/h", "nous", "metered", "", 1, 100, 10, 0, 0, 0, 2.0, 0,
                 "estimated", "test", month_start + 60, month_start + 120),
            )
            connection.commit()
        finally:
            connection.close()
        days_remaining = (month_end - now) / 86_400

        with patch.object(api.time, "time", return_value=now):
            payload = api._budgets_sync({"openrouter": 10, "nous": 100, "all": 8})
        by_id = {item["id"]: item for item in payload["entries"]}
        openrouter = by_id["openrouter"]
        self.assertEqual(openrouter["spend_source"], "local")
        self.assertEqual(openrouter["spend_usd"], 7.0)
        self.assertAlmostEqual(openrouter["pace_daily_usd"], 0.5)
        self.assertAlmostEqual(openrouter["projected_usd"], round(7.0 + 0.5 * days_remaining, 2), places=2)
        self.assertEqual(openrouter["status"], "at_risk" if 7.0 + 0.5 * days_remaining >= 10 else "ok")
        nous = by_id["nous"]
        self.assertEqual(nous["projection_basis"], "linear")
        self.assertEqual(nous["status"], "ok")
        # setUp's own session adds $0.012 under the unmapped "provider" key.
        total = payload["total"]
        self.assertEqual(total["spend_usd"], 9.01)
        self.assertEqual(total["status"], "over")
        self.assertEqual(total["spend_source"], "local")
        note_ids = [note["id"] for note in payload["notes"]]
        self.assertTrue(any(item.startswith("budget:all:") for item in note_ids))
        self.assertEqual(payload["notes"][0]["severity"], "danger")
        self.assertIn("$9.01 spent against a $8.00 monthly cap", payload["notes"][0]["reason"])

        # A cached account figure outranks local records for that provider.
        cached = {
            "providers": [
                {
                    "provider": "openrouter",
                    "status": "ok",
                    "fetched_at": now,
                    "account_spend": {"daily": 1.0, "weekly": 14.0, "monthly": 126.44, "unit": "USD"},
                }
            ],
            "generated_at": now,
        }
        api._ai_usage_cache = (now, cached)
        with patch.object(api.time, "time", return_value=now):
            payload = api._budgets_sync({"openrouter": 250})
        openrouter = next(item for item in payload["entries"] if item["id"] == "openrouter")
        self.assertEqual(openrouter["spend_source"], "account")
        self.assertEqual(openrouter["spend_usd"], 126.44)
        self.assertEqual(openrouter["local_spend_usd"], 7.0)
        self.assertAlmostEqual(openrouter["pace_daily_usd"], 2.0)
        self.assertEqual(payload["total"]["spend_source"], "mixed")
        self.assertEqual(api._budget_attention_notes("openrouter:250"), payload["notes"])
        self.assertEqual(api._budget_attention_notes(""), [])

    def test_attention_payload_carries_budget_notes(self):
        with patch.object(api, "_budget_attention_notes", return_value=[{"id": "budget:all:2026-09"}]) as notes:
            payload = api._attention_sync(0, budgets="all:1")
        notes.assert_called_once_with("all:1")
        self.assertEqual(payload["budgets"], [{"id": "budget:all:2026-09"}])
        routes = {route.path for route in api.router.routes}
        self.assertIn("/budgets", routes)

    def test_openrouter_exposes_account_spend(self):
        payload = api._openrouter_payload(
            {"limit": 100, "limit_remaining": 74.5, "usage_daily": 1.25, "usage_weekly": 4.5, "usage_monthly": 25.5},
            None,
        )
        self.assertEqual(payload["account_spend"], {"daily": 1.25, "weekly": 4.5, "monthly": 25.5, "unit": "USD"})

    def test_budgets_are_wired_into_plugin_source(self):
        source = Path(__file__).resolve().parents[1].joinpath("desktop", "plugin.js").read_text(encoding="utf-8")
        self.assertIn("function BudgetsSection(", source)
        self.assertIn("ctx.storage.set('budgets', budgets)", source)
        self.assertIn("activeBudgetsParam = budgetsParam", source)
        self.assertIn("pageAttentionQuery.data?.budgets", source)
        self.assertIn("jsx(BudgetsSection, {", source)


    def _write_services_home(self):
        (self.home / ".env").write_text(
            "\n".join(
                [
                    "# OPENROUTER_API_KEY=commented-out",
                    "OPENROUTER_API_KEY=llm-key-belongs-to-providers",
                    "FIRECRAWL_API_KEY=fc-main",
                    "export FIRECRAWL_API_KEY_N8N=fc-n8n",
                    "FIRECRAWL_API_URL=https://api.firecrawl.dev",
                    "BRAVE_SEARCH_API_KEY=brave",
                    "TELEGRAM_BOT_TOKEN=tg",
                    "WEIRD_SERVICE_API_KEY=x",
                    "SCRAPECREATORS_API_KEY=sc",
                ]
            ),
            encoding="utf-8",
        )
        (self.home / "config.yaml").write_text(
            "\n".join(
                [
                    "model: something",
                    "mcp_servers:",
                    "  firecrawl:",
                    "    command: npx",
                    "    enabled: true",
                    "  brightdata:",
                    "    url: https://mcp.brightdata.com/mcp?token=secret",
                    "    enabled: true",
                    "  custom-thing:",
                    "    url: https://tools.example.com/mcp",
                    "    enabled: false",
                    "other_setting: 1",
                ]
            ),
            encoding="utf-8",
        )

    def test_services_inventory_reads_env_names_mcp_servers_and_clis_only(self):
        self._write_services_home()
        with patch.object(services.shutil, "which", side_effect=lambda name: r"C:\npm\monid.cmd" if name == "monid" else None):
            inventory = services._services_inventory()
        self.assertNotIn("openrouter", inventory)
        firecrawl = inventory["firecrawl"]
        self.assertEqual(firecrawl["kind"], "service")
        self.assertTrue(firecrawl["adapter"])
        self.assertEqual(firecrawl["sources"], ["env:FIRECRAWL_API_KEY", "env:FIRECRAWL_API_KEY_N8N", "mcp:firecrawl"])
        self.assertEqual(firecrawl["accounts"], ["n8n"])
        self.assertEqual(firecrawl["mcp"], {"transport": "stdio", "enabled": True, "tool_count": None})
        brightdata = inventory["brightdata"]
        self.assertEqual(brightdata["sources"], ["mcp:brightdata (mcp.brightdata.com)"])
        self.assertNotIn("secret", json.dumps(brightdata))
        brave = inventory["brave"]
        self.assertFalse(brave["adapter"])
        self.assertIn("will not spend", brave["note"])
        self.assertEqual(inventory["telegram"]["sources"], ["env:TELEGRAM_BOT_TOKEN"])
        custom = inventory["custom-thing"]
        self.assertEqual((custom["kind"], custom["mcp"]["enabled"], custom["mcp"]["transport"]), ("mcp", False, "http"))
        weird = inventory["weird_service"]
        self.assertEqual((weird["kind"], weird["label"], weird["adapter"]), ("key", "Weird Service", False))
        monid = inventory["monid"]
        self.assertEqual(monid["sources"], ["cli:monid"])
        self.assertEqual(monid["cli_path"], r"C:\npm\monid.cmd")
        # Values are never part of the inventory.
        self.assertNotIn("fc-main", json.dumps(inventory))

    def test_service_adapters_parse_verified_response_shapes(self):
        firecrawl = services._firecrawl_payload(
            {"success": True, "data": {"remainingCredits": 10778, "planCredits": 1000,
                                        "billingPeriodStart": "2026-08-29T09:32:35.884Z", "billingPeriodEnd": "2026-09-29T09:32:35.884Z"}},
            account="n8n",
        )
        self.assertEqual(firecrawl["provider"], "firecrawl:n8n")
        self.assertTrue(firecrawl["account_extra"])
        window = firecrawl["windows"][0]
        self.assertEqual((window["kind"], window["remaining"], window["limit"], window["unit"]), ("balance", 10778.0, 1000.0, "credits"))
        self.assertIsNone(window["percentage_used"])
        self.assertIn("top-ups", window["detail"])
        self.assertTrue(window["reset_at"].startswith("2026-09-29"))
        low = services._firecrawl_payload({"success": True, "data": {"remaining_credits": 250, "plan_credits": 1000}})
        self.assertEqual(low["windows"][0]["percentage_used"], 75.0)

        scrape = services._scrapecreators_payload({"success": True, "creditCount": 6441, "message": "You have 6441 credits remaining."})
        self.assertEqual(scrape["windows"][0]["remaining"], 6441.0)

        mail = services._agentmail_payload({"message_count": [{"timestamp": "2026-09-01T21:04:43.917Z", "value": 28}],
                                            "inbox_count": [{"timestamp": "2026-09-01T21:04:43.917Z", "value": 3}]})
        self.assertEqual(mail["status"], "ok")
        self.assertEqual(mail["windows"], [])
        self.assertIn("28 messages · 3 inboxes", mail["details"][0])

        bright = services._brightdata_payload({"balance": 12.5, "pending_costs": 1.25})
        self.assertEqual(bright["windows"][0]["remaining"], 12.5)
        self.assertEqual(bright["details"], ["pending costs: 1.25"])

        now = 1_800_000_000
        month_start = services._monid_month_start(now)
        iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")
        runs = {"items": [
            {"providerName": "Exa", "cost": {"value": 0.5, "currency": "USD"}, "createdAt": iso(now - 3600)},
            {"providerName": "Apify", "cost": {"value": 2.0, "currency": "USD"}, "createdAt": iso(max(month_start + 60, now - 6 * 86400))},
            {"providerName": "Old", "cost": {"value": 9.0, "currency": "USD"}, "createdAt": iso(month_start - 86400)},
        ]}
        monid = services._monid_payload({"balance": {"value": 11, "currency": "USD"}, "held": {"value": 0, "currency": "USD"},
                                         "notes": ["Update available: 0.1.6 → 0.1.7."]}, runs, now)
        self.assertEqual(monid["windows"][0]["remaining"], 11.0)
        self.assertEqual(monid["account_spend"]["monthly"], 2.5)
        self.assertEqual(monid["account_spend"]["daily"], 0.5)
        self.assertIn("Month to date: 2.50 USD across 2 runs", monid["details"][0])
        self.assertIn("Top: Apify 2.00 · Exa 0.50", monid["details"][1])
        self.assertIn("Update available", monid["details"][-1])

    def test_services_sync_flattens_accounts_and_keeps_unreadable_rows(self):
        self._write_services_home()
        services._services_cache = None
        services._services_last_success.clear()
        primary = services._service_payload("firecrawl", status="ok", windows=[services._usage_window("Credits", kind="balance", remaining=5, unit="credits")])
        primary["extra_accounts"] = [services._service_payload("firecrawl", status="expired", message="rejected", account="n8n")]
        collectors = {
            "firecrawl": Mock(return_value=primary),
            "scrapecreators": Mock(return_value=services._service_payload("scrapecreators", status="unavailable", message="timeout")),
            "brightdata": Mock(return_value=services._service_payload("brightdata", status="forbidden", message="no permission")),
            "monid": Mock(return_value=services._service_payload("monid", status="ok", windows=[])),
        }
        with patch.dict(services._SERVICE_COLLECTORS, collectors, clear=True), \
             patch.object(services.shutil, "which", side_effect=lambda name: "monid" if name == "monid" else None):
            payload = services._services_sync(fresh=True)
        ids = [card["provider"] for card in payload["cards"]]
        self.assertEqual(ids, ["firecrawl", "firecrawl:n8n", "scrapecreators", "brightdata", "monid"])
        rows = {row["id"]: row for row in payload["inventory"]}
        self.assertEqual(rows["firecrawl"]["status"], "monitored")
        self.assertEqual(rows["brightdata"]["status"], "attention")
        self.assertEqual(rows["brightdata"]["note"], "no permission")
        self.assertEqual(rows["brave"]["status"], "unreadable")
        self.assertEqual(rows["custom-thing"]["status"], "unreadable")
        self.assertEqual(payload["summary"], {"configured": len(rows), "monitored": 2, "attention": 2, "unreadable": 4})
        # Cached payload serves without collectors; budgets can read it without a fetch.
        collectors["monid"].reset_mock()
        again = services._services_sync()
        self.assertTrue(again["cached"])
        collectors["monid"].assert_not_called()
        self.assertIsNotNone(services._services_cached_payload())
        services._services_cache = None

    def test_budgets_pick_up_service_account_spend(self):
        now = 1_800_000_000
        services._services_cache = (now, {"cards": [{"provider": "monid", "status": "ok", "fetched_at": now,
                                                     "account_spend": {"daily": 0.5, "weekly": 2.5, "monthly": 2.5, "unit": "USD"}}],
                                          "inventory": [], "generated_at": now})
        try:
            with patch.object(api.time, "time", return_value=now):
                payload = api._budgets_sync({"monid": 20})
        finally:
            services._services_cache = None
        monid = next(item for item in payload["entries"] if item["id"] == "monid")
        self.assertEqual((monid["label"], monid["spend_source"], monid["spend_usd"]), ("Monid", "account", 2.5))
        self.assertEqual(monid["status"], "ok")

    def test_services_are_wired_into_plugin_source(self):
        source = Path(__file__).resolve().parents[1].joinpath("desktop", "plugin.js").read_text(encoding="utf-8")
        self.assertIn("function ServicesSection(", source)
        self.assertIn("jsx(ServicesSection, {", source)
        self.assertIn("'/services?fresh=true'", source)
        self.assertIn("Everything configured", source)
        routes = {route.path for route in api.router.routes}
        self.assertIn("/services", routes)


    def test_plugin_source_parses_as_an_es_module(self):
        """`node --check` silently skips .js files that contain `import`; check as .mjs."""
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        source = Path(__file__).resolve().parents[1].joinpath("desktop", "plugin.js")
        copy = self.home / "plugin-syntax-check.mjs"
        copy.write_bytes(source.read_bytes())
        completed = subprocess.run([node, "--check", str(copy)], capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr[-800:])


if __name__ == "__main__":
    unittest.main()
