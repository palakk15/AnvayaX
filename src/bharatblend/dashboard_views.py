"""Interactive reference, public forecast, and receipt explanation views."""
from __future__ import annotations

from datetime import UTC, datetime
from html import escape
import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from .catalogue import catalogue, export_catalogue_markdown
from .explainability import GroqNarrator, build_evidence, deterministic_narrative
from .live_data import CITIES, LIVE_MODELS, fetch_public_forecast, load_snapshot

PALETTE = ['#54e2c1', '#7aabff', '#d2a2ff', '#f7cc79', '#ff9dba']


def chart(fig, height=380, key=None):
    fig.update_layout(height=height, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font=dict(color='#d5e7e2', family='sans-serif'), colorway=PALETTE,
        margin=dict(l=22, r=22, t=45, b=35), hoverlabel=dict(bgcolor='#183530'),
        legend=dict(orientation='h', y=-0.18))
    fig.update_xaxes(gridcolor='#203d38', zerolinecolor='#3b5d54')
    fig.update_yaxes(gridcolor='#203d38', zerolinecolor='#3b5d54')
    st.plotly_chart(fig, width='stretch', key=key, config={'displaylogo': False})


def references(urls):
    for i, url in enumerate(urls):
        st.markdown(f'[{url.split("/")[2]} · reference {i+1}]({url})')


def network_figure(result, focus=None):
    """Layer graph with actual receipt weights. No decorative hidden neurons."""
    sources = result.get('sources', [])
    fig = go.Figure()
    if not sources:
        return fig
    profiles = {m['id']: m for m in catalogue()['models']}
    ys = np.linspace(0.85, 0.2, len(sources)) if len(sources) > 1 else [0.55]
    nx, ny, labels, colors, sizes, tips = [], [], [], [], [], []

    def node(x, y, label, color, size, tip):
        nx.append(x)
        ny.append(y)
        labels.append(label)
        colors.append(color)
        sizes.append(size)
        tips.append(tip)

    def edge(x, y, xx, yy, width, color, tip):
        fig.add_trace(go.Scatter(x=[x, xx], y=[y, yy], mode='lines',
            line=dict(width=width, color=color), text=[tip, tip], hovertemplate='%{text}<extra></extra>',
            showlegend=False))

    for i, (s, y) in enumerate(zip(sources, ys)):
        name = profiles.get(s['source_id'], {}).get('name', s['source_id'])
        color = PALETTE[i % len(PALETTE)] if focus in (None, s['source_id']) else '#425e59'
        name = name.replace(' Operational', '').replace('NOAA ', '')
        tip = (f"{escape(name)}<br>Version: {escape(s['model_version'])}<br>Weight: {s['weight']:.2%}"
               f"<br>Logit: {s['logit']:.5f}<br>Age: {s['age_hours']:.1f} h")
        node(0.05, y, escape(name), color, 18, tip)
        node(0.29, y, 'Calibrated', color, 13, f"{s['calibration_sample_count']} training cases")
        node(0.72, y, f"{s['weight']:.1%}", color, 18 + 27 * s['weight'], tip)
        edge(0.05, y, 0.29, y, 2, color, 'Raw forecast → calibrated distribution')
        edge(0.29, y, 0.50, 0.55, 1.5, color, 'Available source and context determine eligibility / routing')
        edge(0.50, 0.55, 0.72, y, 1 + 13 * s['weight'], color, f"Learned mixture share: {s['weight']:.2%}")
        edge(0.72, y, 0.95, 0.55, 1 + 13 * s['weight'], color, f"Weighted calibrated distribution: {s['weight']:.2%}")
    gate = result.get('explanation', {}).get('gate', 'gate')
    node(0.50, 0.55, 'Spatial attention' if gate == 'spatial_attention' else 'Additive gate<br>+ masked softmax',
         '#effaf6', 35, escape(gate))
    node(0.50, 0.06, 'Place · lead · season<br>regime · recent error', '#849fbd', 15,
         'Features derived from issuance-available context; no future observations')
    edge(0.50, 0.06, 0.50, 0.55, 2, '#849fbd', 'Context feature input')
    node(0.95, 0.55, 'Predictive<br>mixture', '#54e2c1', 39, 'Distribution, quantiles and event probabilities')
    fig.add_trace(go.Scatter(x=nx, y=ny, mode='markers+text', text=labels, textposition='top center',
        textfont=dict(size=11), customdata=tips, hovertemplate='%{customdata}<extra></extra>',
        marker=dict(size=sizes, color=colors, line=dict(width=2, color='#a3d9c9')), showlegend=False))
    fig.update_xaxes(visible=False, range=[-0.07, 1.08])
    fig.update_yaxes(visible=False, range=[-0.09, 1.04])
    fig.update_layout(title='Forecast routing network · actual source weights')
    return fig


def render_network(result, service, request):
    evidence = build_evidence(result, catalogue())
    sources = evidence['sources']
    if not sources:
        st.info('No eligible sources for this request.')
        return
    names = {s['source_id']: s['name'] for s in sources}
    focus = st.selectbox('Inspect a model in the network', list(names), format_func=names.get)
    chart(network_figure(result, focus), 510, 'forecast_network')
    st.caption('Edge width shows the actual mixture share. This is the application routing graph. '
               'The additive gate is not a deep neural network; a spatial-attention challenger is used only when selected in the receipt.')
    chosen = next(s for s in sources if s['source_id'] == focus)
    st.markdown(f"#### Why {chosen['name']} received {chosen['weight']:.2%}")
    st.write(chosen['selection_reason'])
    st.caption(f"Actual record version: {chosen['model_version']} · initialized {chosen['initialization_time']} · "
               f"available {chosen['available_at']} · calibration cases {chosen['calibration_sample_count']}")
    left, right = st.columns(2)
    with left:
        if evidence['exact_logit_explanation']:
            terms = chosen['term_contributions']
            ranked = sorted(terms, key=lambda k: abs(terms[k]), reverse=True)
            kept = ranked[:9]
            values = [terms[k] for k in kept]
            labels = kept.copy()
            if len(ranked) > 9:
                labels.append('All remaining terms')
                values.append(sum(terms[k] for k in ranked[9:]))
            fig = go.Figure(go.Waterfall(x=labels + ['Total logit'], y=values + [0],
                measure=['relative'] * len(values) + ['total'],
                increasing=dict(marker_color=PALETTE[0]), decreasing=dict(marker_color='#ff9dba'),
                totals=dict(marker_color=PALETTE[1])))
            fig.update_layout(title='Exact feature contributions to this logit')
            chart(fig, 390, 'logit_waterfall')
            st.caption(f"Reconstruction error: {chosen['logit_reconstruction_error']:.2e}. "
                       'A positive term raises this source logit; relative differences determine its weight.')
        else:
            st.info('The spatial gate exposes weights and exact probability contributions, but no exact additive feature reasons.')
    with right:
        if evidence['exact_logit_explanation'] and len(sources) > 1:
            ref = st.selectbox('Compare logit against', [s for s in names if s != focus], format_func=names.get)
            other = next(s for s in sources if s['source_id'] == ref)
            features = set(chosen['term_contributions']) | set(other['term_contributions'])
            diffs = {k: chosen['term_contributions'].get(k, 0) - other['term_contributions'].get(k, 0) for k in features}
            top = sorted(diffs, key=lambda k: abs(diffs[k]))[-10:]
            fig = go.Figure(go.Bar(y=top, x=[diffs[k] for k in top], orientation='h',
                marker_color=[PALETTE[0] if diffs[k] >= 0 else '#ff9dba' for k in top]))
            fig.update_layout(title=f'Signed advantage over {names[ref]}')
            chart(fig, 390, 'pairwise_logit')
            st.caption(f"All feature differences sum to {sum(diffs.values()):+.5f}. "
                       'This equals the log of the ratio of their mixture weights before numerical underflow.')
        else:
            st.write('Pairwise reasons need at least two sources and an additive gate.')
    if evidence['exact_logit_explanation']:
        groups = sorted({term.split('.')[0] for s in sources for term in s['term_contributions']})
        values = [[sum(v for k, v in s['term_contributions'].items() if k.split('.')[0] == g)
                   for g in groups] for s in sources]
        fig = go.Figure(go.Heatmap(z=values, x=groups, y=[s['name'] for s in sources],
            colorscale='RdBu', zmid=0, colorbar=dict(title='Logit'), hovertemplate='%{y}<br>%{x}: %{z:.4f}<extra></extra>'))
        fig.update_layout(title='Feature-family influence across all active models')
        chart(fig, 330, 'feature_heatmap')
    if result.get('probabilities'):
        threshold = st.selectbox('Probability flow threshold', [p['threshold'] for p in result['probabilities']])
        p = next(p for p in result['probabilities'] if p['threshold'] == threshold)
        ids = [s['source_id'] for s in sources]
        fig = go.Figure(go.Sankey(node=dict(label=[names[i] for i in ids] + [f'P(> {threshold:g}) = {p["probability"]:.1%}'],
            color=PALETTE[:len(ids)] + ['#ecfff6'], pad=20),
            link=dict(source=list(range(len(ids))), target=[len(ids)] * len(ids),
                      value=[p['contributions'][i] for i in ids])))
        chart(fig, 300, 'probability_sankey')
        st.caption('Each flow is weight × calibrated source exceedance probability. Flows add exactly to the displayed event probability.')
    with st.expander('Every model: used or excluded, and why', expanded=False):
        st.dataframe(pd.DataFrame([{'Model': row['name'], 'Status': row['status'],
            'Reason': ' '.join(row['reasons'])} for row in result.get('eligibility', [])]), hide_index=True, width='stretch')
    with st.expander('Exact context values supplied to the gate'):
        st.dataframe(pd.DataFrame([{'Feature': name, 'Value before scaling': value}
            for name, value in evidence['context_feature_values'].items()]), hide_index=True, width='stretch')
        st.caption('The gate applies training-fitted scaling, clipping and spline bases to these inputs. '
                   'Recent skill is delayed normalized absolute error; it is not an accuracy percentage.')
    if st.button('Recompute without the selected model', key='network_remove'):
        changed = dict(request, excluded_sources=sorted(set(request.get('excluded_sources', [])) | {focus}))
        alternate = service.predict(changed)
        st.session_state['network_counterfactual'] = (result['receipt_id'], focus, alternate)
    stored = st.session_state.get('network_counterfactual')
    if stored and stored[0] == result['receipt_id'] and stored[1] == focus:
        alternate = stored[2]
        if alternate['status'] == 'unavailable':
            st.info('Removing this source leaves no eligible forecast.')
        else:
            after = {s['source_id']: s['weight'] for s in alternate['sources']}
            frame = pd.DataFrame([{'Model': s['name'], 'Original share': s['weight'],
                                   'Share after removal': after.get(s['source_id'], 0)} for s in sources])
            st.dataframe(frame, hide_index=True, width='stretch')
            st.write({'original_prediction': result['summary'], 'prediction_after_removal': alternate['summary']})
            st.caption('Recomputed source-removal sensitivity, including context changes. This is not causal attribution.')
    st.markdown('#### Explanation assistant')
    st.markdown(deterministic_narrative(evidence))
    try:
        narrator = GroqNarrator()
        config = narrator.status()
        st.caption(f"Groq: {config['key_count']} configured key(s) · narrator model {config['model']}")
        st.caption('Generate sends the selected forecast’s evidence to Groq. Keys remain on the server. '
                   'AI narration supplements the numerical evidence above.')
        if st.button('Generate Groq explanation', disabled=not config['configured']):
            with st.spinner('Explaining the selected forecast evidence…'):
                st.session_state['groq_narration'] = (result['receipt_id'], narrator.explain(evidence))
        stored = st.session_state.get('groq_narration')
        if stored and stored[0] == result['receipt_id']:
            answer = stored[1]
            st.caption(f"{answer['provider']} · {answer.get('model') or 'local evidence'}")
            if answer.get('reason'):
                st.info(answer['reason'])
            st.markdown(answer['text'])
            st.download_button('Download narration', json.dumps(answer, indent=2), 'explanation.json', 'application/json')
        if not config['configured']:
            st.info('Add your key list to .env using .env.example. Exact local explanations already work.')
    except ValueError as exc:
        st.error(str(exc))
    st.download_button('Download structured explanation evidence', json.dumps(evidence, indent=2),
                       'explanation-evidence.json', 'application/json')


def render_atlas():
    data = catalogue()
    st.subheader('Model & source atlas')
    st.caption(f"{len(data['models'])} model profiles · {len(data['datasets'])} data products · references reviewed {data['reviewed_on']}")
    st.info(data['note'])
    models_tab, sources_tab = st.tabs(['Weather models', 'Data sources'])
    with models_tab:
        query = st.text_input('Search model, provider or architecture')
        classes = st.multiselect('Model families', ['ai', 'nwp', 'hybrid'], default=['ai', 'nwp', 'hybrid'])
        models = [m for m in data['models'] if m['model_class'] in classes and
                  query.lower() in ' '.join(str(m[k]) for k in ('name', 'provider', 'architecture', 'id')).lower()]
        if models:
            selected = st.selectbox('Model profile', [m['id'] for m in models],
                                    format_func=lambda x: next(m['name'] for m in models if m['id'] == x))
            model = next(m for m in models if m['id'] == selected)
            st.markdown(f"### {model['name']}")
            st.write(model['description'])
            st.caption(f"{model['provider']} · {model['architecture']}")
            left, right = st.columns(2)
            with left:
                st.markdown('**Properties**')
                props = {'Product version': model.get('version'), 'Family': model['model_class'],
                         'Role': model['kind'], 'Ensemble': model.get('ensemble'), 'Grid': model.get('grid'),
                         'Training cutoff': model.get('training_end'), 'Checkpoint licence': model.get('weights_license'),
                         'Code licence': model.get('code_license'), 'Data licence': model.get('data_license'),
                         'Integration state': model['status']}
                st.dataframe(pd.DataFrame([{'Property': k, 'Value': 'Unknown / not verified' if v is None else str(v)}
                                           for k, v in props.items()]), hide_index=True, width='stretch')
            with right:
                st.markdown('**Advantages for integration**')
                for point in model['pros']:
                    st.write('• ' + point)
                st.markdown('**Limitations / tradeoffs**')
                for point in model['cons']:
                    st.write('• ' + point)
                st.caption(model['assessment_basis'])
                st.markdown('**Direct references**')
                references(model['evidence_urls'])
            st.write('**Prerequisites:** ' + '; '.join(model['integration_requirements']))
            for note in model.get('notes', []):
                st.caption(note)
            with st.expander('Complete model metadata, compute and access properties'):
                st.json(model)
            variables = ['rainfall', 'temperature', 'wind_uv', 'gust']
            fig = go.Figure(go.Heatmap(z=[[{True: 1, False: 0, None: 0.5}[m['capabilities'].get(v)] for v in variables]
                                         for m in models], x=variables, y=[m['name'] for m in models],
                text=[[{True: 'Supported', False: 'Absent', None: 'Unknown / checkpoint-dependent'}[m['capabilities'].get(v)]
                       for v in variables] for m in models],
                colorscale=[[0, '#594247'], [0.49, '#594247'], [0.5, '#53616d'], [0.99, '#53616d'], [1, '#38bda0']],
                zmin=0, zmax=1, showscale=False, hovertemplate='%{y}<br>%{x}: %{text}<extra></extra>'))
            fig.update_layout(title='Documented variable inventory · support is not a skill score')
            chart(fig, max(320, len(models) * 27), 'capability_matrix')
        else:
            st.info('No models match these filters.')
    with sources_tab:
        selected = st.selectbox('Data-source profile', [d['id'] for d in data['datasets']],
            format_func=lambda x: next(d['name'] for d in data['datasets'] if d['id'] == x))
        source = next(d for d in data['datasets'] if d['id'] == selected)
        st.markdown(f"### {source['name']}")
        st.write(source['description'])
        st.write(f"**Project role:** {source['project_role']}")
        st.write(f"**Coverage:** {source['coverage']}")
        st.write(f"**Access:** {source.get('access')} · **Format:** {source.get('format', 'Product-specific')}")
        st.write(f"**Local state:** {source['local_status']}")
        if source.get('endpoint'):
            st.markdown(f"[Provider data endpoint]({source['endpoint']})")
        left, right = st.columns(2)
        with left:
            st.markdown('**Constraints and interpretation**')
            for point in source['constraints']:
                st.write('• ' + point)
        with right:
            st.markdown('**Quality checks**')
            for point in source['quality_checks']:
                st.write('• ' + point)
        references(source['evidence_urls'])
        with st.expander('Complete data-source metadata'):
            st.json(source)
        st.dataframe(pd.DataFrame([{'Source': d['name'], 'Type': d['kind'], 'Format': d.get('format', 'Product-specific'),
            'Local state': d['local_status']} for d in data['datasets']]), hide_index=True, width='stretch')
    st.download_button('Download complete model and source reference', export_catalogue_markdown(),
                       'MODEL_AND_DATA_SOURCES.md', 'text/markdown')
    st.download_button('Download machine-readable catalogue', json.dumps(data, indent=2), 'catalogue.json', 'application/json')


def render_live():
    st.subheader('Public forecast observatory')
    st.write('Compare the latest downloaded forecasts from NOAA GFS, ECMWF IFS and ECMWF AIFS.')
    city = st.selectbox('Forecast city', list(CITIES))
    if st.button('Fetch latest provider forecasts', type='primary'):
        try:
            with st.spinner('Retrieving named model forecasts and saving the source receipt…'):
                fetch_public_forecast(city)
            st.success('Provider response downloaded and verified.')
        except (requests.RequestException, ValueError, OSError):
            st.error('Provider download unavailable. Any existing snapshot remains available below.')
    try:
        snapshot = load_snapshot(city)
    except (ValueError, OSError):
        st.error('The cached response is missing or failed its integrity check. Fetch it again.')
        return
    if snapshot is None:
        st.info('No downloaded forecast for this city yet. Fetch the provider data to populate the charts.')
        return
    retrieved = datetime.fromisoformat(snapshot['retrieved_at'])
    age = max(0, (datetime.now(UTC) - retrieved).total_seconds() / 3600)
    st.caption(f"PROVIDER FORECASTS · downloaded {retrieved:%d %b %Y, %H:%M UTC} · snapshot age {age:.1f} hours")
    if age > 6:
        st.warning('This is a saved snapshot older than six hours. Fetch a newer forecast before treating it as current.')
    frame = pd.DataFrame(snapshot['rows'])
    frame['valid_time'] = pd.to_datetime(frame['valid_time'], utc=True)
    columns = st.columns(3)
    now = pd.Timestamp.now(tz='UTC')
    available = frame[frame['valid_time'] >= now]
    if available.empty:
        st.warning('All forecast times in this snapshot are in the past. Refresh for current guidance.')
    for column, (model, info) in zip(columns, LIVE_MODELS.items()):
        subset = available[available['model'] == model]
        value = subset.iloc[0]['temperature_2m'] if len(subset) else None
        column.metric(info['name'], f'{value:.1f} °C' if value is not None and pd.notna(value) else 'Unavailable')
    st.caption('Cards show the first future forecast hour. These are individual provider predictions; no learned blend is applied.')
    fields = {'temperature_2m': ('2 m temperature', '°C'), 'precipitation': ('Hourly total precipitation', 'mm'),
              'wind_speed_10m': ('10 m wind speed', 'm/s')}
    field = st.selectbox('Weather variable', list(fields), format_func=lambda k: fields[k][0])
    label, unit = fields[field]
    chosen = st.multiselect('Compare provider models', list(LIVE_MODELS), default=list(LIVE_MODELS),
                           format_func=lambda k: LIVE_MODELS[k]['name'])
    if not chosen:
        st.info('Select at least one provider model to compare.')
        return
    selected = frame[frame['model'].isin(chosen)]
    fig = go.Figure()
    for model in chosen:
        subset = selected[selected['model'] == model]
        fig.add_trace(go.Scatter(x=subset['valid_time'], y=subset[field], mode='lines',
            name=LIVE_MODELS[model]['name'], connectgaps=False, line=dict(width=2.5)))
    fig.update_layout(title=f'{city} · {label}', yaxis_title=unit, xaxis_title='Valid time (UTC)')
    chart(fig, 430, 'live_timeseries')
    pivot = selected.pivot(index='valid_time', columns='model', values=field).reindex(columns=chosen)
    left, right = st.columns(2)
    with left:
        fig = go.Figure(go.Heatmap(z=pivot.T.values, x=pivot.index, y=[LIVE_MODELS[m]['name'] for m in chosen],
            colorscale='Viridis', colorbar=dict(title=unit), hovertemplate='%{y}<br>%{x}<br>%{z:.2f}<extra></extra>'))
        fig.update_layout(title='Model × forecast-hour field')
        chart(fig, 310, 'live_heatmap')
    with right:
        # Require all selected models before computing a comparison range.
        complete = pivot.notna().all(axis=1)
        high, low = pivot.max(axis=1).where(complete), pivot.min(axis=1).where(complete)
        fig = go.Figure(go.Scatter(x=pivot.index, y=high, mode='lines', line=dict(width=0), name='Maximum'))
        fig.add_trace(go.Scatter(x=pivot.index, y=low, fill='tonexty', fillcolor='rgba(84,226,193,.22)',
            mode='lines', line=dict(width=0), name='Minimum'))
        fig.add_trace(go.Scatter(x=pivot.index, y=pivot.mean(axis=1).where(complete), name='Unweighted mean',
            line=dict(color=PALETTE[0])))
        fig.update_layout(title='Between-model range and unweighted mean', yaxis_title=unit)
        chart(fig, 310, 'live_range')
    st.caption('The shaded range is disagreement among selected products, not a calibrated confidence interval. '
               'Hours missing any selected model are omitted from the range and mean.')
    st.markdown('#### Which models supplied these values?')
    fig = go.Figure()
    ys = np.linspace(.8, .2, len(chosen))
    for i, (model, y) in enumerate(zip(chosen, ys)):
        color = PALETTE[i]
        fig.add_trace(go.Scatter(x=[.05, .48, .93], y=[y, y, .5], mode='lines+markers+text',
            text=[LIVE_MODELS[model]['name'], 'Provider hourly fields', 'Comparison' if i == 0 else ''],
            textposition='top center', marker=dict(size=[23, 14, 28]), line=dict(color=color, width=2),
            name=LIVE_MODELS[model]['provider'], showlegend=False))
    fig.update_xaxes(visible=False, range=[-.12, 1.1])
    fig.update_yaxes(visible=False, range=[0, 1])
    chart(fig, 270, 'live_provider_network')
    st.caption('All selected sources are displayed for comparison. No model was ranked or selected using observed accuracy. '
               'The latest-series API does not expose a pinned checkpoint or single initialization cycle.')
    st.markdown(f"{snapshot['attribution']} [API documentation]({snapshot['documentation_url']}) · [Terms]({snapshot['license_url']})")
    for model in chosen:
        st.markdown(f"[{LIVE_MODELS[model]['name']} — product documentation]({LIVE_MODELS[model]['docs']})")
    with st.expander('Provenance, processing notes and exact request'):
        st.write({k: v for k, v in snapshot.items() if k != 'rows'})
    st.download_button('Download provider forecast and receipt', json.dumps(snapshot, indent=2),
                       f'{city.lower()}-provider-forecast.json', 'application/json')
    st.download_button('Download forecast CSV', frame.to_csv(index=False), f'{city.lower()}-forecast.csv', 'text/csv')


def render_architecture():
    st.subheader('Architecture & presentation kit')
    st.write('The presentation plan, architecture and model evidence are consolidated in one guide.')
    path = Path('ppt.md')
    if path.exists():
        content = path.read_text(encoding='utf-8')
        st.download_button('Download ppt.md', content, path.name, 'text/markdown')
        st.markdown(content)
