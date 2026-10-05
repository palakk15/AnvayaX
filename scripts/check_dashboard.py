"""Headless interaction smoke test against an already prepared workspace."""
from pathlib import Path

from streamlit.testing.v1 import AppTest


def select(app, label, value):
    widget = next(item for item in app.selectbox if item.label == label)
    widget.select(value).run(timeout=30)
    assert not app.exception, [e.value for e in app.exception]
    return app


app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py"), default_timeout=30).run()
assert not app.exception, [e.value for e in app.exception]
next(item for item in app.radio if item.label == 'Workspace').set_value('Blend laboratory').run(timeout=30)
assert not app.exception, [e.value for e in app.exception]
assert len(app.metric) == 4
select(app, 'Inspect a model in the network', 'gefs')
next(item for item in app.button if item.label == 'Recompute without the selected model').click().run(timeout=30)
assert not app.exception, [e.value for e in app.exception]
select(app, "Map layer", "Weight: gefs")
select(app, "Variable", "wind_uv")
select(app, "Blending track", "accuracy")
next(item for item in app.multiselect if item.label == "Exclude sources").select("gefs").run(timeout=30)
assert not app.exception, [e.value for e in app.exception]
assert len(app.metric) == 4
select(app, "Variable", "temperature")
assert len(app.metric) == 4
next(item for item in app.radio if item.label == 'Workspace').set_value('Model & source atlas').run(timeout=30)
assert not app.exception, [e.value for e in app.exception]
select(app, 'Model profile', 'pangu_weather')
select(app, 'Data-source profile', 'open_meteo_named_models')
next(item for item in app.radio if item.label == 'Workspace').set_value('Architecture & PPT').run(timeout=30)
assert not app.exception, [e.value for e in app.exception]
print("Dashboard passed: public page, routing network, map, wind, accuracy, exclusions, temperature, model/data atlas, presentation kit.")
