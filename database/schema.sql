PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS models (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, model_type TEXT NOT NULL,
 metadata_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS locations (
 id TEXT PRIMARY KEY, latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
 longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180), state TEXT, district TEXT,
 region TEXT, elevation_m REAL, coast_km REAL, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecasts (
 id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES models(id),
 location_id TEXT NOT NULL REFERENCES locations(id), variable TEXT NOT NULL, units TEXT NOT NULL,
 initialization_time TEXT NOT NULL, available_at TEXT NOT NULL, valid_start TEXT NOT NULL,
 valid_end TEXT NOT NULL, lead_hours REAL NOT NULL CHECK(lead_hours >= 0),
 data_kind TEXT NOT NULL CHECK(data_kind IN ('real','synthetic')),
 source_uri TEXT NOT NULL, model_version TEXT NOT NULL, record_json TEXT NOT NULL,
 ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(model_id,model_version,location_id,variable,initialization_time,valid_start,valid_end)
);
CREATE INDEX IF NOT EXISTS forecast_targets ON forecasts(location_id,variable,valid_start,valid_end);
CREATE TABLE IF NOT EXISTS observations (
 id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id), variable TEXT NOT NULL,
 units TEXT NOT NULL, valid_start TEXT NOT NULL, valid_end TEXT NOT NULL, available_at TEXT NOT NULL,
 quality TEXT NOT NULL CHECK(quality IN ('good','suspect','bad')),
 data_kind TEXT NOT NULL CHECK(data_kind IN ('real','synthetic')), source_uri TEXT NOT NULL,
 record_json TEXT NOT NULL, ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS observation_arrival ON observations(available_at,location_id,variable);
CREATE TABLE IF NOT EXISTS historical_errors (
 forecast_id TEXT NOT NULL REFERENCES forecasts(id), observation_id TEXT NOT NULL REFERENCES observations(id),
 error REAL, absolute_error REAL, squared_error REAL, computed_at TEXT NOT NULL,
 PRIMARY KEY(forecast_id,observation_id)
);
CREATE TABLE IF NOT EXISTS model_weights (
 id INTEGER PRIMARY KEY, forecast_id TEXT REFERENCES forecasts(id), location_id TEXT NOT NULL REFERENCES locations(id),
 model_id TEXT NOT NULL REFERENCES models(id), variable TEXT NOT NULL, valid_start TEXT NOT NULL,
 lead_hours REAL, issued_at TEXT NOT NULL, skill_cutoff TEXT NOT NULL, router_version TEXT NOT NULL,
 weight REAL NOT NULL CHECK(weight BETWEEN 0 AND 1), evidence_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weather_regimes (
 id INTEGER PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id), valid_start TEXT NOT NULL,
 issued_at TEXT NOT NULL, method TEXT NOT NULL, regime TEXT NOT NULL, confidence REAL,
 features_json TEXT NOT NULL, CHECK(confidence IS NULL OR confidence BETWEEN 0 AND 1)
);
CREATE TABLE IF NOT EXISTS validation_results (
 id TEXT PRIMARY KEY, data_kind TEXT NOT NULL, variable TEXT NOT NULL, filters_json TEXT NOT NULL,
 train_end TEXT NOT NULL, validation_end TEXT NOT NULL, test_start TEXT NOT NULL, test_end TEXT NOT NULL,
 metrics_json TEXT NOT NULL, sample_count INTEGER NOT NULL, computed_at TEXT NOT NULL,
 CHECK(train_end <= validation_end AND validation_end <= test_start)
);
CREATE TABLE IF NOT EXISTS extreme_events (
 id INTEGER PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id), issued_at TEXT NOT NULL,
 valid_start TEXT NOT NULL, valid_end TEXT NOT NULL, event_type TEXT NOT NULL, threshold REAL NOT NULL,
 units TEXT NOT NULL, probability REAL CHECK(probability BETWEEN 0 AND 1),
 probability_kind TEXT NOT NULL, contributions_json TEXT NOT NULL, data_kind TEXT NOT NULL
);
