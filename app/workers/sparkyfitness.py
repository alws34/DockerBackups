"""Worker exporting a SparkyFitness user's diary, library and settings via its REST API."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import ClassVar

import requests

from app.core.context import BackupContext, BackupError, BackupResult
from app.workers.base import BackupWorker, EnvVarSpec, fetch_json

# No endpoint reports an account's first entry, so history is read one year at a
# time from this year on (imports can backfill entries far before sign-up).
# Fallback when the account doesn't say when it was created; ~8 small calls per empty year.
_FIRST_YEAR = 2000


def _first_year(user: dict) -> int:
    """Year the account was created (nothing can be logged before it), else _FIRST_YEAR."""
    created = str(user.get("created_at") or user.get("createdAt") or "")
    year = created[:4]
    return (
        int(year)
        if year.isdigit() and _FIRST_YEAR <= int(year) <= date.today().year
        else _FIRST_YEAR
    )


_PAGE_SIZE = 100


def _exercise_sessions(get: Callable[..., dict | list]) -> list:
    """The whole exercise history, read page by page."""
    sessions: list = []
    n = 1
    while True:
        data = get("/v2/exercise-entries/history", page=str(n), pageSize=str(_PAGE_SIZE))
        sessions.extend(data["sessions"])
        if not data["pagination"]["hasMore"]:
            return sessions
        n += 1


class SparkyFitnessWorker(BackupWorker):
    """Export everything one SparkyFitness user logged, plus their foods, meals and settings."""

    worker_type: ClassVar[str] = "sparkyfitness"
    display_name: ClassVar[str] = "SparkyFitness"
    description: ClassVar[str] = (
        "Exports one user's food diary, exercise history, check-ins (weight, body "
        "measurements, water), sleep, mood, fasting, goals, custom foods, meals, "
        "exercises, workout and meal plans and preferences; photos and other users are "
        "not included."
    )
    env_var_specs: ClassVar[list[EnvVarSpec]] = [
        EnvVarSpec(
            key="SPARKYFITNESS_URL",
            label="SparkyFitness URL",
            description=(
                "Address you open SparkyFitness at in the browser "
                "(e.g. https://fitness.example.com)."
            ),
            secret=False,
            required=True,
        ),
        EnvVarSpec(
            key="SPARKYFITNESS_API_KEY",
            label="API Key",
            description=(
                "SparkyFitness Settings → Developer & Integrations → API Key Management → "
                "Generate New Key (expiry: Never). Exports the data of the user who made it."
            ),
            secret=True,
            required=True,
        ),
    ]

    def run(self, context: BackupContext) -> BackupResult:
        started_at = datetime.now()
        api = f"{self.require_env(context, 'SPARKYFITNESS_URL').rstrip('/')}/api"
        key = self.require_env(context, "SPARKYFITNESS_API_KEY")

        with requests.Session() as s:
            s.headers["x-api-key"] = key

            def get(path: str, **params: str) -> dict | list:
                return fetch_json(s, "GET", f"{api}{path}", params=params)

            def paged(
                path: str, field: str, total: str, page: str, size: str, **params: str
            ) -> list:
                items: list = []
                n = 1
                while True:
                    data = get(path, **params, **{page: str(n), size: str(_PAGE_SIZE)})
                    items.extend(data[field])
                    if not data[field] or len(items) >= int(data[total]):
                        return items
                    n += 1

            try:
                user = get("/identity/user")
            except BackupError as e:
                raise BackupError(
                    "Could not sign in to SparkyFitness: check SPARKYFITNESS_URL and that the "
                    f"API key exists, is enabled and has not expired ({e})"
                ) from e

            files: dict[str, object] = {
                "user": user,
                "profile": get("/identity/profiles"),
                "preferences": get("/user-preferences"),
                "custom_measurement_categories": get("/measurements/custom-categories"),
                "foods": paged(
                    "/foods/foods-paginated",
                    "foods",
                    "totalCount",
                    "currentPage",
                    "itemsPerPage",
                    foodFilter="mine",
                ),
                "meals": get("/meals", filter="mine"),
                "exercises": paged(
                    "/exercises",
                    "exercises",
                    "totalCount",
                    "currentPage",
                    "itemsPerPage",
                    ownershipFilter="mine",
                ),
                "workout_presets": paged("/workout-presets", "presets", "total", "page", "limit"),
                "workout_plan_templates": get("/workout-plan-templates"),
                "meal_plan_templates": get("/meal-plan-templates"),
                "goal_presets": get("/goal-presets"),
                "weekly_goal_plans": get("/weekly-goal-plans"),
                "meal_types": get("/meal-types"),
                "custom_nutrients": get("/custom-nutrients"),
                "water_containers": get("/water-containers"),
            }

            files["exercise_sessions"] = _exercise_sessions(get)

            categories = files["custom_measurement_categories"]
            ranged: dict[str, list] = {}
            goals: dict = {}
            today = date.today().isoformat()
            for year in range(_first_year(user), date.today().year + 2):  # +1: future-dated plans
                a, b = f"{year}-01-01", f"{year}-12-31"
                window = {
                    "food_entries": get(f"/food-entries/range/{a}/{b}"),
                    "check_in_measurements": get(
                        f"/measurements/check-in-measurements-range/{a}/{b}"
                    ),
                    "water_intake": get(f"/measurements/water-intake-range/{a}/{b}"),
                    "custom_measurements": [
                        m
                        for c in categories
                        for m in get(f"/measurements/custom-measurements-range/{c['id']}/{a}/{b}")
                    ],
                    "sleep": get("/sleep", startDate=a, endDate=b),
                    "mood": get("/mood", startDate=a, endDate=b),
                    "fasting": get(f"/fasting/history/range/{a}/{b}"),
                    "meal_plan": get("/meals/plan", startDate=a, endDate=b),
                }
                for k, v in window.items():
                    ranged.setdefault(k, []).extend(v)
                # Goals come back for every day asked for (defaults included), so only
                # ask for years the user actually logged something in.
                if any(window.values()) and a <= today:
                    goals.update(get("/goals/for-date", date=a, end_date=min(b, today)))
            files.update(ranged)
            files["goals_by_date"] = goals
        return self.archive_json(context, started_at, files)
