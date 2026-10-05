"""Validate and transactionally ingest canonical forecasts/observations into a local SQLite warehouse.

Source metadata is mandatory. This is a local durable archive, not serverless filesystem storage.
"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from bharatblend.schemas import ForecastRecord, ObservationRecord, load_jsonl

def ingest(source: Path, database: Path):
    manifest=json.loads((source/'provenance.json').read_text(encoding='utf-8'))
    if not manifest.get('forecasts_source_uri') or not manifest.get('observations_source_uri'):
        raise ValueError('Forecast and observation source URIs are required')
    forecasts=load_jsonl(source/'forecasts.jsonl',ForecastRecord)
    observations=load_jsonl(source/'observations.jsonl',ObservationRecord)
    kinds={r.data_kind for r in forecasts+observations}
    if len(kinds)!=1: raise ValueError('Import real and synthetic archives into separate databases')
    if kinds=={'real'} and (not manifest.get('observation_dataset') or manifest.get('observation_kind')!='measurement'):
        raise ValueError('Real validation requires a named measurement dataset; reanalysis needs a separately labelled study')
    database.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(database) as db:
        db.executescript((Path(__file__).resolve().parents[1]/'database/schema.sql').read_text())
        existing={row[0] for row in db.execute('SELECT DISTINCT data_kind FROM forecasts UNION SELECT DISTINCT data_kind FROM observations')}
        if existing and existing!=kinds: raise ValueError('Cannot mix evidence kinds in one warehouse')
        for r in forecasts+observations:
            metadata=manifest.get('locations',{}).get(r.location_id,{})
            existing_location=db.execute('SELECT latitude,longitude FROM locations WHERE id=?',(r.location_id,)).fetchone()
            if existing_location and (abs(existing_location[0]-r.latitude)>1e-5 or abs(existing_location[1]-r.longitude)>1e-5):
                raise ValueError('Location ID coordinates changed; resolve explicitly')
            db.execute('INSERT OR IGNORE INTO locations VALUES (?,?,?,?,?,?,?,?,?)',(r.location_id,r.latitude,r.longitude,metadata.get('state'),metadata.get('district'),r.region,r.elevation_m,r.coastal_distance_km,json.dumps(metadata)))
        for f in forecasts:
            metadata=manifest.get('models',{}).get(f.source_id,{})
            db.execute('INSERT OR IGNORE INTO models(id,provider,model_type,metadata_json) VALUES (?,?,?,?)',(f.source_id,metadata.get('provider','Unspecified'),metadata.get('type','Unspecified'),json.dumps(metadata)))
            raw=f.model_dump_json();key=hashlib.sha256(raw.encode()).hexdigest()
            # Immutable records: a conflicting revision cannot silently overwrite issue-time evidence.
            if not db.execute('SELECT 1 FROM forecasts WHERE id=?',(key,)).fetchone():
                db.execute('INSERT INTO forecasts(id,model_id,location_id,variable,units,initialization_time,available_at,valid_start,valid_end,lead_hours,data_kind,source_uri,model_version,record_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(key,f.source_id,f.location_id,f.variable,f.units,f.initialization_time.isoformat(),f.available_at.isoformat(),f.valid_start.isoformat(),f.valid_end.isoformat(),(f.valid_start-f.initialization_time).total_seconds()/3600,f.data_kind,manifest['forecasts_source_uri'],f.model_version,raw))
        for o in observations:
            raw=o.model_dump_json();prior=db.execute('SELECT record_json FROM observations WHERE id=?',(o.observation_id,)).fetchone()
            if prior and prior[0]!=raw: raise ValueError('Observation revision requires a new observation_id')
            db.execute('INSERT OR IGNORE INTO observations(id,location_id,variable,units,valid_start,valid_end,available_at,quality,data_kind,source_uri,record_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(o.observation_id,o.location_id,o.variable,o.units,o.valid_start.isoformat(),o.valid_end.isoformat(),o.available_at.isoformat(),o.quality,o.data_kind,manifest['observations_source_uri'],raw))
    print(f'Imported {len(forecasts)} forecasts and {len(observations)} observations into {database}')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('--database',type=Path,default=Path('artifacts/warehouse.sqlite'));a=p.parse_args();ingest(a.source,a.database)
