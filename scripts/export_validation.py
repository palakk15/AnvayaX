"""Export exact-window canonical pairs; never relabel generated records as real."""
import argparse
import json
from pathlib import Path
import numpy as np
from bharatblend.dataset import join_cases
from bharatblend.schemas import ForecastRecord, ObservationRecord

PLACES = {'mumbai': ('Maharashtra','Mumbai'), 'pune': ('Maharashtra','Pune'), 'delhi': ('Delhi','New Delhi'), 'chennai': ('Tamil Nadu','Chennai'), 'guwahati': ('Assam','Kamrup Metropolitan'), 'jaipur': ('Rajasthan','Jaipur')}

def export(source, output, mode):
    forecasts = [ForecastRecord.model_validate_json(x) for x in (source/'forecasts.jsonl').read_text().splitlines() if x.strip()]
    observations = [ObservationRecord.model_validate_json(x) for x in (source/'observations.jsonl').read_text().splitlines() if x.strip()]
    kinds = {x.data_kind for x in forecasts+observations}
    if len(kinds) != 1: raise ValueError('An archive must have one data kind')
    manifest_path=source/'provenance.json'
    provenance=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    if kinds=={'real'} and (provenance.get('observation_kind')!='measurement' or not provenance.get('observation_dataset') or not provenance.get('observations_source_uri') or not provenance.get('forecasts_source_uri')):
        raise ValueError('Real validation requires source provenance and a named measurement dataset')
    rows=[]
    for c in join_cases(forecasts, observations, mode=mode):
        o=c.observation
        f=next(iter(c.forecasts.values()))
        month=o.valid_start.month
        state,district=PLACES.get(o.location_id, ('Unspecified','Unspecified'))
        if kinds=={'real'}:
            place=provenance.get('locations',{}).get(o.location_id,{})
            state,district=place.get('state','Unspecified'),place.get('district','Unspecified')
        members={k:np.atleast_1d(v.value).tolist() for k,v in c.forecasts.items()}
        predictions={k:np.mean(np.atleast_1d(v.value),axis=0).tolist() for k,v in c.forecasts.items()}
        # Regime features use forecasts only, never the verifying observation.
        centre=float(np.mean([np.linalg.norm(v) if isinstance(v,list) else v for v in predictions.values()]))
        regime=('heavy rainfall' if o.variable=='rainfall' and centre>64.5 else 'high temperature' if o.variable=='temperature' and centre>=40 else 'high wind' if o.variable=='wind_uv' and centre>=17.2 else 'monsoon calendar' if 6<=month<=9 else 'normal')
        rows.append(dict(id=c.key,data_kind=o.data_kind,location_id=o.location_id,state=state,district=district,region=o.region,latitude=o.latitude,longitude=o.longitude,elevation_m=o.elevation_m,coast_km=o.coastal_distance_km,variable=o.variable,units=o.units,issued_at=c.issuance_time.isoformat(),initialization_time=f.initialization_time.isoformat(),valid_start=o.valid_start.isoformat(),valid_end=o.valid_end.isoformat(),observed_at=o.available_at.isoformat(),lead_hours=(o.valid_start-f.initialization_time).total_seconds()/3600,month=month,season='winter' if month<=2 else 'pre-monsoon' if month<=5 else 'southwest monsoon' if month<=9 else 'post-monsoon',regime=regime,observed=o.value,predictions=predictions,members=members,observation_id=o.observation_id))
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(dict(schema_version=1,data_kind=next(iter(kinds)),source='Existing seeded research generator; no station observations' if kinds=={'synthetic'} else provenance['observation_dataset']+'; '+provenance['observations_source_uri'],provenance=provenance,join_mode=mode,cases=rows),separators=(',',':')),encoding='utf-8')
    print(f'Exported {len(rows)} paired cases to {output}')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('artifacts/research_v2'));p.add_argument('--output',type=Path,default=Path('server/data/research-validation.json'));p.add_argument('--mode',choices=['operational','hindcast'],default='operational');a=p.parse_args();export(a.source,a.output,a.mode)
