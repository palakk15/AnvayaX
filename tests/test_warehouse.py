import json
import sqlite3
import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('import_archive',Path(__file__).resolve().parents[1]/'scripts/import_archive.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def test_warehouse_is_idempotent_and_rejects_silent_observation_revision(tmp_path):
    example=json.loads((Path(__file__).resolve().parents[1]/'site/data/validation-format.json').read_text())
    for file,key in [('forecasts.jsonl','forecast_example'),('observations.jsonl','observation_example'),('provenance.json','provenance_example')]:
        (tmp_path/file).write_text(json.dumps(example[key]))
    db=tmp_path/'archive.sqlite';module.ingest(tmp_path,db);module.ingest(tmp_path,db)
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT COUNT(*) FROM forecasts').fetchone()[0]==1
        assert connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==1
        assert connection.execute('PRAGMA foreign_key_check').fetchall()==[]
    example['observation_example']['value']=999
    (tmp_path/'observations.jsonl').write_text(json.dumps(example['observation_example']))
    with pytest.raises(ValueError,match='revision'):module.ingest(tmp_path,db)

def test_real_archive_requires_measurement_provenance(tmp_path):
    example=json.loads((Path(__file__).resolve().parents[1]/'site/data/validation-format.json').read_text())
    for key in ['forecast_example','observation_example']:example[key]['data_kind']='real'
    for file,key in [('forecasts.jsonl','forecast_example'),('observations.jsonl','observation_example'),('provenance.json','provenance_example')]:
        (tmp_path/file).write_text(json.dumps(example[key]))
    with pytest.raises(ValueError,match='measurement'):module.ingest(tmp_path,tmp_path/'real.sqlite')
