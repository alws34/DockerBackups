# SparkyFitness

Worker type: `sparkyfitness` · Method: REST API (API key, read-only calls)

## What's Backed Up

Everything one user logged, as JSON files in one `.tar.gz`:

| File                                 | Contents                                                              |
|--------------------------------------|-----------------------------------------------------------------------|
| `user.json`, `profile.json`          | Account e-mail and role; name, birth date, gender, target weight      |
| `preferences.json`                   | Units, timezone, BMR/TDEE algorithms and other settings               |
| `food_entries.json`                  | The food diary: every entry with meal type, quantity, unit and nutrients |
| `exercise_sessions.json`             | Full exercise history: workouts and single entries with sets, reps, weight, duration, calories |
| `check_in_measurements.json`         | Weight, body measurements (neck, waist, hips), body fat, height, steps |
| `water_intake.json`                  | Water drunk per day (ml)                                              |
| `custom_measurement_categories.json`, `custom_measurements.json` | Your own measurement types and their values |
| `sleep.json`, `mood.json`, `fasting.json` | Sleep entries, mood entries, fasting logs                        |
| `goals_by_date.json`                 | The goals that applied on each day of the years you logged anything   |
| `goal_presets.json`, `weekly_goal_plans.json` | Saved goal presets and weekly goal plans                     |
| `foods.json`                         | Your foods (custom and saved from food providers) with default serving |
| `meals.json`                         | Your meal templates with their foods                                  |
| `meal_plan.json`, `meal_plan_templates.json` | Planned meals and meal plan templates                         |
| `exercises.json`                     | Your custom exercises                                                 |
| `workout_presets.json`, `workout_plan_templates.json` | Workout presets and plans                            |
| `meal_types.json`, `custom_nutrients.json`, `water_containers.json` | Your meal types, nutrients and drink sizes |

History is read one year at a time from 2000 to the end of next year, because
the API has no "first entry" date. Empty years cost a few quick requests.

**Not included:**

- Progress and check-in **photos**, profile pictures and food images.
- **Other users** of the same server, and data shared with you through family
  access. Each user needs their own key (add one SparkyFitness instance per user).
- Medications, symptoms, cycle and pregnancy tracking, raw data synced from
  Garmin/Fitbit/Withings etc. beyond what lands in the diary and check-ins,
  extra serving variants of a food (only its default serving), and the
  grouping of diary items into logged meals.
- Integration secrets, AI provider keys and server settings.

## Setup

1. In SparkyFitness open **Settings → Developer & Integrations → API Key
   Management**, enter a description, pick **Expires In: Never** and click
   **Generate New Key**. Copy the key; it is shown only once.
2. Add SparkyFitness in the web GUI and fill in the URL and key (they are
   stored in `.env`).
3. Use the address you open SparkyFitness at in the browser (the web
   front-end forwards `/api` to the server).

The key acts as the user who created it and can also write; the worker only
reads. The server allows 100 API-key checks per minute by default, which one
backup stays well under.

## `.env` Variables

| Variable                | Required | Notes                                              |
|-------------------------|----------|----------------------------------------------------|
| `SPARKYFITNESS_URL`     | Yes      | e.g. `https://fitness.example.com`                 |
| `SPARKYFITNESS_API_KEY` | Yes      | Key from Settings → Developer & Integrations       |

## Restoring

This export is a **readable reference**, not a file SparkyFitness can import
as-is. The quickest way back in is SparkyFitness's own CSV importers under
**Settings → Profile & Account → Data Import**:

- **Food Diary (CSV)** takes `date, meal_type, meal_name, food_name, brand,
  quantity, unit` plus nutrient columns (`calories`, `protein`, `carbs`,
  `fat`, …). `food_entries.json` has the same fields (`entry_date` → `date`,
  `brand_name` → `brand`), so a few lines of `jq` or Python turn it into that
  CSV. Its nutrient values are per `serving_size`/`serving_unit`; import a
  few days first and compare totals before importing everything.
- **Food Database (CSV)** for `foods.json`, and **Check-in & Health Data
  (CSV)** for measurements, sleep, water and mood. Both let you map columns
  when importing.

Exercise history, meals, goals, plans and preferences have no importer:
re-create them by hand, or with a short script against the same API, mapping
old food and exercise IDs to the new ones.

For a whole-server restore, an admin's database backup (**Admin → Backup
Settings**) is more complete; this export is a per-user copy that doesn't
depend on Postgres.
