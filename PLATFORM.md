# Validation-driven SIH81 prototype

The public dashboard has eight primary sections: Overview, Live Forecast, Blended Forecast, Model Comparison, Weight Maps, Extreme Weather, Model Atlas and Data Health. The Weight Lab (trained neural challenger and exact additive router) is inside Weight Maps at `#maps/neural`. Validation, Evidence, Historical Skill and Operational Pipeline have no separate public pages. Comparison explanations and evidence-matching receipts are available within Model Comparison.

## Evidence contracts

| Layer | Implemented behaviour | Current limitation |
| --- | --- | --- |
| Live deterministic blend | Temperature and hourly precipitation weighted means; wind component blending; per-target missing-source renormalization | Six connected sources; adaptive agreement heuristic, not measured historical skill |
| Historical skill | MAE, RMSE, bias, correlation; threshold contingency metrics; ensemble Brier and reliability bins | Shipped archive contains 3,456 generated pairs at six sites; explicitly simulated |
| Contextual routing | Historical error, state/district/region, season, lead, forecast regime, recent 7/30/90-day error, disagreement, availability | Transparent experimental log-score router; coefficients fixed, strength selected on validation |
| Validation | Common source cohort; chronological development selection; three expanding-window test folds; arrival-time and target-window purges | Synthetic test scores do not establish operational India skill; no statistical significance claim |
| Model maps | Live six-source weights by sampled coordinate, weather variable and exact target hour; all-source weight matrix; ensemble magnitude/frequency layers; drag/wheel zoom and coverage receipts | Thirty-three India grid points plus the selected location are requested; only returned samples render, with no interpolated skill field |
| Extremes | Real NOAA GEFS member exceedance frequency for rain totals, temperature maxima, wind maxima; member distribution and P10/P50/P90 | Raw, uncalibrated; no cyclone tracks, official heatwave diagnosis or IMD warning ingestion |
| Data health | Missing values, independent provider failures, retrieval expiry, availability and unknown model initialization | Latest-series API cannot establish source-run age; current conditions are model estimates |
| Operations | Executed request-stage status, client refresh, modular adapters, warehouse schema and validated import/export | Hosted durable persistence and background scheduler planned, not active |

## Scientific definitions

The point router uses inverse historical RMSE in log space, with shrunk contextual corrections and a recent-error correction. A small explicitly heuristic disagreement penalty discourages extreme outliers; disagreement does not prove a model is wrong. Softmax is masked for missing forecasts. Historical scores require a common source cohort with at least 10 pairs, contextual corrections at least 5. Without that support, the router returns equal eligible weights. Strength 0, 0.5 or 1 is chosen using only development data. The existing trained neural router remains a separate research challenger and predicts weights, not replacement weather fields.

Every validation fold fits only labels whose target end **and observed availability** precede the fold cutoff. Development labels must arrive before the first test boundary. Later folds can learn from earlier test observations only after those observations arrive. The development-selected individual source is the improvement reference. Improvement percentage is withheld for synthetic archives; real values may be negative. No significance claim is made.

Issuance groups are split at 35% (initial training end) and 50% (validation end); the remaining half is tested in three sequential blocks. Splitting by issuance groups keeps sites and leads from the same run together. Percentages are fixed before inspecting scores.

Wind point metrics compare speed after vector blending against observed speed; router skill uses vector RMSE. Correlation is undefined for constant/insufficient series. POD = recall; FAR is false alarms divided by forecast events; CSI excludes correct negatives. Brier uses actual probability inputs only. Deterministic model spread is never turned into event probability. Raw ensemble probabilities require complete target windows and at least 10 usable members. All event thresholds are prototype screening choices, not official declarations.

Latest-series live inputs have no pinned model initialization. The interface distinguishes hours ahead from initialization lead and never uses these series to fabricate fixed-lead historical skill. Rainfall is previous-hour water-equivalent accumulation. GEFS events use complete 24-hour windows, displayed explicitly in IST; the first accumulation interval can already be partly elapsed at retrieval.

## Public comparison and planning maps

Comparison controls select only location, lead and season values present in the selected archive. The evaluation period (`period=all|7|30|90`) filters scored held-out cases relative to the latest target end in that archive. It is separate from the router's recent training lookback. All summary metrics and example cases use the filtered cohort; the coverage receipt shows before/after counts and target dates. A matched-case inspector shows each model's prediction, the identical outcome, absolute error, issuance time and outcome availability. Simulated evidence is labelled throughout.

Local map cutoffs use ERA5 daily reanalysis over 30 days ending seven days before the request date, cached per city and reference date for one day. Rain uses the 75th percentile of wet-day totals (at least 1 mm), requiring at least five wet days and 20 valid daily totals. Temperature and wind use the 90th percentile of daily maxima, requiring at least 20 valid values. Cutoffs are bounded to 10?50 mm, 30?42?C and 8?14 m/s respectively, then rounded to one decimal. Fallbacks are 10 mm, 35?C and 8 m/s, explicitly labelled when reference coverage or units are insufficient. Recent reanalysis is not station truth, a long-term normal or a vulnerability assessment.

Maps offer median amount, member frequency above a cutoff, severe screening and editable local cutoffs. Rain, heat and wind have separate values, units, colours and marker shapes. Exact coverage includes requested coordinates, complete/returned member counts, forecast windows, retrieval times and reference dates/day counts. Soft marker halos do not fill gaps between sampled points or imply verified skill. No-exceedance frequencies read ?None above?; dry or calm values remain numerically unchanged underneath. Equal genuine values are not artificially differentiated. Event probabilities remain from GEFS; six deterministic forecasts are not represented as six probability providers.

Extreme Weather translates the member signals into public planning guidance. A 40?C screen alone does not declare a heat wave; local normals, terrain and persistence checks are absent. Wind is the maximum hourly modeled speed, not a gust forecast. Official IMD and NDMA links are provided. The prototype does not claim demonstrated signal improvement or calibration.

## Backend boundaries

```mermaid
flowchart TD
  UI[Dashboard and assistant] --> API[API handlers]
  API --> L[India location resolution]
  API --> A[Model adapters]
  A --> QC[Units, times, finite values, missingness]
  QC --> C[Forecast regime and current context]
  S[Versioned skill archive] --> R[Masked contextual router]
  C --> R
  R --> B[Scalar or vector blend]
  B --> U[Disagreement and explanations]
  E[Ensemble adapter] --> X[Raw event guidance]
  F[Issue-preserving forecasts] --> W[Local SQLite warehouse]
  O[Quality-controlled observations] --> W
  W --> V[Temporal validation]
  V --> S
```

`server/forecast.mjs` exposes adapters with `fetchForecast`, `getMetadata`, and `getAvailability`. `server/intelligence.mjs` produces live calculation receipts. `server/science/` contains pure metric, regime, router and validation logic. `server/skill-store.mjs` reads versioned snapshots. `api/intelligence.mjs` exposes `action=live`, `action=skill`, or `action=ensemble`. `site/platform.js` handles reusable controls, tables, charts and maps without fitting models in the browser.

## Import real validation data

1. Prepare `forecasts.jsonl`, `observations.jsonl`, and `provenance.json` using the canonical Pydantic contracts in `src/bharatblend/schemas.py`. The downloadable `site/data/validation-format.json` is an **example**, not actual data. Supply explicit units, coordinates, exact target windows, initialization, original availability, quality, dataset name and source URI. Wind forecasts are lists of `[u,v]` members. Never rename a source to a different model.
2. Real provenance requires `observation_kind: "measurement"`, `observation_dataset`, `forecasts_source_uri`, `observations_source_uri`. Include `locations` keyed by location ID with state/district labels. A reanalysis study needs a distinct evidence designation and is deliberately not accepted as station truth.
3. Validate and persist locally:

```powershell
.venv\Scripts\python.exe scripts/import_archive.py path/to/archive --database artifacts/real.sqlite
.venv\Scripts\python.exe scripts/export_validation.py --source path/to/archive --mode operational --output server/data/real-validation.json
npm run test:web
npm run build
```

The exporter reuses the existing exact-window join, coordinate checks, model training-cutoff exclusions and actual availability checks. It never converts a hindcast into an operational forecast. Imports are transactional and idempotent; immutable forecast collisions and silent observation revisions are rejected. Keep sensitive station data out of published exports unless its licence allows disclosure: the dashboard exposes paired values.

The hosted function reads the immutable snapshot at cold start. Publish a new snapshot by redeploying. This makes the prototype reproducible, not continuously trained. The live latest-series route deliberately continues its agreement heuristic until a compatible live issuance/lead mapping and validated promotion policy are connected.

## Scheduling and storage

`database/schema.sql` provides nine normalized tables: models, locations, forecasts, observations, historical_errors, model_weights, weather_regimes, validation_results and extreme_events. Original receipt JSON and source URIs are retained. Derived tables are designed for a future persistent worker; they are not shown as populated merely because the schema exists.

Current operation is request-driven with a 15-minute forecast cache and visible-page refresh. Ensemble requests cache for 30 minutes. No background cron job is registered, and the UI says so. For operations, use a durable database, archive each provider's exact run and publication time, fetch authorized observations, import, validate, generate a new skill snapshot, and promote only after temporal evaluation. A worker/cron must use a shared lock, bounded retries and idempotent run IDs before multi-instance deployment. Free provider quotas and shared caching must be budgeted before enabling frequent scheduled ingestion.

## Sources and restrictions

- [Open-Meteo forecast API](https://open-meteo.com/en/docs): named GFS, ECMWF IFS, AIFS, DWD ICON, ECCC GEM and JMA GSM feeds connected; attribution required, free hosted API for non-commercial use subject to quotas. Output sampling is not the native model grid/time step.
- [Ensemble API](https://open-meteo.com/en/docs/ensemble-api): GEFS seamless member endpoint tested with 31 members. Raw frequencies are not locally calibrated probabilities. Provider documentation lists native resolutions, forecast ranges and update frequencies.
- [Historical forecast](https://open-meteo.com/en/docs/historical-forecast-api): continuous stitched products are not a substitute for issue-preserving fixed-lead forecasts. Single-run archives require separate ingestion.
- [IMD](https://mausam.imd.gov.in/): official warning destination; no authenticated observation/alert feed connected. Do not scrape gated endpoints or imply official endorsement.
- [DataMeet India composite](https://github.com/datameet/maps/tree/master/Country): CC BY 4.0. Downloaded `india-composite.geojson`, simplified with 0.012-degree geometric tolerance and rounded to four decimals. Full-country display with island groups; not authoritative administrative boundaries.

## Vercel keys and packaging

Vercel project → **Settings → Environment Variables** → add exactly one variable, **`GROQ_API_KEY`**, with the Groq secret in **Value**. Select Production (and Preview only if using preview URLs), save, then redeploy. The server selects `openai/gpt-oss-120b` in code; `GROQ_API_KEYS` and `GROQ_MODEL` are not used by the Vercel app. Verify `https://YOUR-SITE/api/chat` reports `"configured":true`. This variable is server-only; never use a public/client prefix or paste the secret into frontend files. Public forecast and ensemble routes need no key under the provider's free-use terms.

`api/intelligence.mjs` includes `server/data/**` in its deployment bundle. Keep `.vercelignore`'s raw-data exclusion anchored as `/data/`; `data/` would also remove required nested public and server datasets. A build validates both static assets and the research snapshot.
