from __future__ import annotations

import importlib

from django.test import SimpleTestCase


class SqliteSettingsTests(SimpleTestCase):
    def test_sqlite_settings_force_debug_for_local_staticfiles(self) -> None:
        sqlite_settings = importlib.import_module("codemaster_system.settings_sqlite")

        # Regression: ISSUE-001 - local sqlite QA served static assets as 404/html.
        # Found by /qa on 2026-05-15.
        # Report: .gstack/qa-reports/qa-report-codemaster-local-2026-05-15.md
        self.assertIs(sqlite_settings.DEBUG, True)

    def test_sqlite_settings_keep_file_storage_in_local_workspace(self) -> None:
        sqlite_settings = importlib.import_module("codemaster_system.settings_sqlite")

        # Regression: ISSUE-002 - tests tried to save uploaded files under /app.
        # Found by /qa on 2026-05-15.
        # Report: .gstack/qa-reports/qa-report-codemaster-local-2026-05-15.md
        self.assertEqual(sqlite_settings.MEDIA_ROOT, sqlite_settings.BASE_DIR / ".tmp_sqlite_media")
        self.assertEqual(sqlite_settings.STATIC_ROOT, sqlite_settings.BASE_DIR / ".tmp_sqlite_staticfiles")
