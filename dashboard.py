"""Explore prepared forecast blends: ``streamlit run dashboard.py``.

This interface reads existing artifacts. Training and data preparation are explicit
CLI operations, never a side effect of opening or refreshing the dashboard.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from bharatblend.service import BlendService
from bharatblend.settings import local_settings
from bharatblend.dashboard_views import render_live, render_atlas, render_network, render_architecture

COLORS = ["#50e3c2", "#77abff", "#f5c578", "#c49cff", "#ff99ad"]
VARIABLES = {"rainfall": "Rainfall", "temperature": "Temperature", "wind_uv": "Wind"}


@st.cache_resource(show_spinner=False)
def load_service(data_dir: str) -> BlendService:
    return BlendService(data_dir)


def figure_style(figure: go.Figure, height: int = 300) -> go.Figure:
    figure.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#cfdfdc", "family": "sans-serif", "size": 12},
        colorway=COLORS,
        margin={"l": 12, "r": 12, "t": 24, "b": 20},
        legend={"orientation": "h", "y": -0.24},
        hoverlabel={"bgcolor": "#14312f", "font_color": "#ffffff"},
    )
    figure.update_xaxes(gridcolor="#1b3533", zerolinecolor="#31504d")
    figure.update_yaxes(gridcolor="#1b3533", zerolinecolor="#31504d")
    return figure


def plot(figure: go.Figure, *, height: int = 300) -> None:
    st.plotly_chart(figure_style(figure, height), width="stretch", config={"displayModeBar": False})


def numeric(value, suffix: str = "") -> str:
    if isinstance(value, (int, float)) and math.isfinite(value):
        return f"{value:,.1f}{suffix}"
    return "Unavailable"


def timestamp(value: str) -> str:
    try:
        instant = datetime.fromisoformat(value)
        return instant.astimezone(UTC).strftime("%d %b %Y, %H:%M UTC")
    except (TypeError, ValueError):
        return str(value)


def render_coverage(cases: list[dict], selected: str, service: BlendService, request: dict) -> None:
    matched = [case for case in cases if all(case[k] == request[k] for k in ("valid_start", "valid_end", "issuance_time"))]
    sites = {case["location_id"]: case for case in matched}
    rows = [site for site in sites.values() if "latitude" in site and "longitude" in site]
    if not rows:
        st.info("Coordinates are not available for these prepared cases.")
        return
    layers = ["Predictive mean"] + [f"Weight: {s}" for s in sorted(request.get("map_sources", []))]
    layer = st.selectbox("Map layer", layers)
    values, descriptions = [], []
    for row in rows:
        query = {k: v for k, v in request.items() if k != "map_sources"}
        query["location_id"] = row["location_id"]
        result = service.predict(query)
        if layer.startswith("Weight: "):
            source = layer.removeprefix("Weight: ")
            value = next((s["weight"] for s in result.get("sources", []) if s["source_id"] == source), 0)
            description = f"{source}: {value:.1%}"
        else:
            value = result.get("summary", {}).get("speed_mean", result.get("summary", {}).get("mean"))
            description = f"Predictive mean: {value:.2f}" if isinstance(value, (float, int)) else "Unavailable"
        values.append(value)
        descriptions.append(description)
    figure = go.Figure(go.Scatter(
        x=[row["longitude"] for row in rows],
        y=[row["latitude"] for row in rows],
        mode="markers+text",
        text=[row.get("location_name", row["location_id"]) for row in rows],
        textposition="top center",
        customdata=[[row["location_id"], description] for row, description in zip(rows, descriptions)],
        marker={
            "size": [17 if row["location_id"] == selected else 10 for row in rows],
            "color": values, "colorscale": "Viridis", "showscale": True,
            "line": {"color": "#a6f4df", "width": 1},
        },
        hovertemplate="%{text}<br>%{y:.2f}° N, %{x:.2f}° E<br>%{customdata[1]}<extra></extra>",
    ))
    figure.update_xaxes(title="Longitude (° E)", range=[67, 98], dtick=10)
    figure.update_yaxes(title="Latitude (° N)", range=[6, 38], scaleanchor="x", scaleratio=1)
    plot(figure, height=380)
    st.caption("Matched issuance and valid interval at recorded locations. Colors show predictions or learned mixture weights; no spatial interpolation.")


def render_weights(sources: list[dict]) -> None:
    figure = go.Figure(go.Bar(
        x=[row.get("weight", 0) for row in sources],
        y=[row["source_id"] for row in sources],
        orientation="h",
        marker_color=COLORS[:len(sources)] if len(sources) <= len(COLORS) else COLORS[0],
        text=[f"{row.get('weight', 0):.1%}" for row in sources],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="%{y}<br>Mixture weight: %{x:.1%}<extra></extra>",
    ))
    figure.update_xaxes(range=[0, 1.1], tickformat=".0%", title="Share of the predictive mixture")
    figure.update_yaxes(autorange="reversed")
    plot(figure, height=max(220, len(sources) * 48))
    st.caption("Weights describe this issued blend; they do not establish a model's general skill.")


def render_probabilities(result: dict) -> None:
    rows = sorted(result.get("probabilities", []), key=lambda row: row["threshold"])
    if not rows:
        st.info("No threshold probabilities were requested or available for this target.")
        return
    unit = result.get("summary", {}).get("units", "")
    chart = go.Figure(go.Scatter(
        x=[row["threshold"] for row in rows],
        y=[row["probability"] for row in rows],
        mode="lines+markers",
        name="Blended probability",
        line={"width": 3, "color": COLORS[0]},
        hovertemplate="Threshold %{x}<br>Exceedance probability: %{y:.1%}<extra></extra>",
    ))
    for source in result.get("sources", []):
        source_id = source["source_id"]
        if any(source_id in row.get("contributions", {}) for row in rows):
            chart.add_trace(go.Scatter(
                x=[row["threshold"] for row in rows],
                y=[row.get("contributions", {}).get(source_id) for row in rows],
                mode="lines+markers", name=f"{source_id} contribution", line={"dash": "dot"},
            ))
    chart.update_xaxes(title=f"Exceedance threshold ({unit})")
    chart.update_yaxes(title="Probability", range=[0, 1], tickformat=".0%")
    plot(chart, height=330)
    st.caption("Source contributions are weighted probabilities. Connecting lines guide the eye between evaluated thresholds.")


def render_distribution(result: dict) -> None:
    summary = result.get("summary", {})
    quantiles = summary.get("quantiles", {})
    if isinstance(quantiles, dict) and quantiles:
        pairs = []
        for key, value in quantiles.items():
            try:
                probability = float(str(key).removeprefix("p"))
                if probability > 1:
                    probability /= 100
                if isinstance(value, (int, float)) and 0 <= probability <= 1:
                    pairs.append((probability, value))
            except (ValueError, TypeError):
                continue
        if pairs:
            pairs.sort()
            figure = go.Figure(go.Scatter(
                x=[value for _, value in pairs], y=[probability for probability, _ in pairs],
                mode="lines+markers", name="Predictive quantiles", line={"color": COLORS[1], "width": 3},
                hovertemplate="Value %{x:.2f}<br>Quantile %{y:.0%}<extra></extra>",
            ))
            figure.update_xaxes(title=f"{'Wind speed' if result['target']['variable'] == 'wind_uv' else 'Forecast value'} ({summary.get('units', '')})")
            figure.update_yaxes(title="Cumulative probability", tickformat=".0%", range=[0, 1])
            plot(figure)
            st.caption("Quantiles reported by the predictive distribution. Segments between quantiles are visual guides.")
            return
    st.info("The prepared receipt does not include scalar distribution quantiles.")


def render_explanation(result: dict) -> None:
    sources = result.get("sources", [])
    rows = []
    for source in sources:
        terms = source.get("term_contributions", {})
        for term, value in terms.items():
            if isinstance(value, (int, float)):
                rows.append({"Source": source["source_id"], "Term": term, "Logit contribution": value})
    if rows:
        frame = pd.DataFrame(rows)
        figure = go.Figure()
        for i, source in enumerate(sources):
            subset = frame[frame["Source"] == source["source_id"]]
            figure.add_trace(go.Bar(
                x=subset["Term"], y=subset["Logit contribution"], name=source["source_id"],
                marker_color=COLORS[i % len(COLORS)],
            ))
        figure.update_layout(barmode="group")
        figure.update_yaxes(title="Contribution to gate logit")
        plot(figure, height=360)
        st.caption("These are the gate's reported terms for this request. Softmax converts source logits to mixture weights.")
    explanation = result.get("explanation", {})
    if explanation:
        st.json(explanation, expanded=False)
    if not rows and not explanation:
        st.info("A gate explanation is not available in this prepared artifact.")
    if sources:
        st.dataframe(pd.DataFrame([
            {"Source": row["source_id"], "Version": row.get("model_version"),
             "Weight": row.get("weight"), "Age (hours)": row.get("age_hours"),
             "Raw mean": row.get("raw_mean"), "Calibrated mean": row.get("calibrated_mean"),
             "Logit": row.get("logit")}
            for row in sources
        ]), hide_index=True, width="stretch")
    st.caption(f"Post-blend calibration adjustment: {result.get('calibration_adjustment', 'not reported')}")
    with st.expander("Evidence and provenance"):
        st.json(result.get("evidence", {}))


def render_verification(service: BlendService, variable: str) -> None:
    report = service.metrics()
    st.caption(f"Prepared verification · {report.get('split', 'split not specified')} · {report.get('data_kind', 'provenance not specified')}")
    st.caption("Saved scores describe the evaluated methods and cohorts. Changing forecast controls does not rerun verification.")
    for limitation in report.get("limitations", []):
        st.info(str(limitation))
    scores = report.get("variables", {}).get(variable, {})
    if not scores:
        st.info("No prepared verification results exist for this variable.")
        return
    st.caption(f"{scores.get('n_cases', 'Unknown number of')} verification cases")
    methods = scores.get("methods", {})
    if methods:
        st.dataframe(pd.DataFrame.from_dict(methods, orient="index").rename_axis("Method"),
                     width="stretch")
    for item in scores.get("reliability", []):
        bins = item.get("bins", [])
        if not bins:
            continue
        figure = go.Figure(go.Scatter(
            x=[0, 1], y=[0, 1], mode="lines", name="Perfect reliability",
            line={"dash": "dash", "color": "#638480"},
        ))
        figure.add_trace(go.Scatter(
            x=[row["forecast"] for row in bins], y=[row["observed"] for row in bins],
            customdata=[row["count"] for row in bins], mode="lines+markers", name="Prepared verification",
            marker={"size": 9, "color": COLORS[0]},
            hovertemplate="Forecast: %{x:.1%}<br>Observed: %{y:.1%}<br>Cases: %{customdata}<extra></extra>",
        ))
        figure.update_layout(title=f"Reliability · threshold {item['threshold']}")
        figure.update_xaxes(title="Mean forecast probability", range=[0, 1], tickformat=".0%")
        figure.update_yaxes(title="Observed frequency", range=[0, 1], tickformat=".0%")
        plot(figure)
    benchmark = service.benchmark()
    comparison = benchmark.get("variables", {}).get(variable)
    if comparison:
        st.markdown("#### Baseline research benchmark")
        st.caption(f"Run {benchmark['run_id']} · {benchmark['data_kind']} · identical held-out cases for every method")
        st.dataframe(pd.DataFrame.from_dict(comparison["methods"], orient="index").rename_axis("Method"),
                     width="stretch")
        st.caption(f"Reference selected on development data: {comparison['development_selected_baseline']}. "
                   "Research comparisons do not change the deployed gate.")
        with st.expander("Performance by region, season and lead time"):
            strata = comparison["stratified_proper_scores"]
            dimension = st.selectbox("Benchmark grouping", sorted({row["dimension"] for row in strata}))
            frame = pd.DataFrame([{**{"Group": row["value"], "Cases": row["n_cases"],
                                      "Issuances": row["n_issuances"]}, **row["methods"]}
                                  for row in strata if row["dimension"] == dimension])
            st.dataframe(frame, hide_index=True, width="stretch")
            st.caption("Lower proper scores are better. Small groups provide descriptive evidence only.")
        with st.expander("Paired uncertainty against the selected baseline"):
            st.json(comparison["paired_comparisons_vs_selected_baseline"], expanded=False)
            st.caption("Temporal blocks preserve spatial cases together. These pointwise intervals are not adjusted for multiple comparisons.")
        st.download_button("Download baseline benchmark", json.dumps(benchmark, indent=2, allow_nan=False),
                           file_name="bharatblend-benchmark.json", mime="application/json")


def render_experiments(service: BlendService) -> None:
    report = service.experiments()
    st.caption("Completed results and pending study specifications are labelled separately in the prepared report.")
    rows = report.get("results", report.get("experiments", []))
    if "results" in report:
        rows = rows + report.get("ablation_results", [])
    if isinstance(rows, dict):
        rows = [{"experiment": key, **value} if isinstance(value, dict) else
                {"experiment": key, "result": value} for key, value in rows.items()]
    if isinstance(rows, list) and rows:
        st.dataframe(pd.json_normalize(rows), hide_index=True, width="stretch")
    else:
        st.info("No tabular experiment results are available. Inspect the prepared experiment plan below.")
    with st.expander("Full experiment report"):
        st.json(report, expanded=False)
    st.download_button("Download experiment report", json.dumps(report, indent=2, allow_nan=False),
                       file_name="bharatblend-experiments.json", mime="application/json")


def render_receipt(result: dict) -> None:
    st.divider()
    st.download_button("Download explanation receipt", json.dumps(result, indent=2, allow_nan=False),
                       file_name=f"bharatblend-{result.get('receipt_id', 'receipt')}.json", mime="application/json")
    st.caption(f"Receipt {result.get('receipt_id', 'not recorded')} · {result.get('data_kind', 'provenance not specified')} data")


def main() -> None:
    st.set_page_config(page_title="BharatBlend · Forecast intelligence", page_icon="◈", layout="wide")
    st.markdown("""
    <style>
    .stApp {background: #091a1a; color: #e1eeeb;}
    [data-testid="stSidebar"] {background: #0d2322; border-right: 1px solid #24413d;}
    [data-testid="stHeader"] {background: rgba(9,26,26,.9);}
    .block-container {padding-top: 2.4rem; max-width: 1500px;}
    h1,h2,h3 {letter-spacing: -.025em; color: #effaf6 !important;}
    h1 {font-size: 2.8rem !important; font-weight: 650 !important;}
    [data-testid="stMetric"] {background: #102a28; padding: 20px; border: 1px solid #28433f; border-radius: 12px;}
    [data-testid="stMetricLabel"] {color: #adc6bf;}
    [data-testid="stMetricValue"] {color: #62e4c3;}
    [data-testid="stCaptionContainer"] {color: #a9c1ba;}
    [data-testid="stWidgetLabel"] p {color: #d3e3dc;}
    [data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="input"] input {
        background: #17312f; color: #e1eeeb; border-color: #36564f;}
    [data-baseweb="select"] svg {fill: #c4e4d8;}
    .brand-label {color: #61ddbf; font-size: .73rem; letter-spacing: .19em; font-weight: 700;}
    .demo-banner {border: 1px solid #81632e; background: #352c1a; color: #ffe2a2;
        padding: 13px 18px; border-radius: 10px; margin: 12px 0 24px; font-size: .9rem;}
    .stTabs [data-baseweb="tab-list"] {gap: 25px; border-bottom: 1px solid #29413d;}
    .stTabs [aria-selected="true"] {color: #64e5c5;}
    .stButton button, .stDownloadButton button {border-color: #3d6d61; color: #d6f4e9; background: #173b33;}
    </style>
    """, unsafe_allow_html=True)
    st.markdown('<div class="brand-label">BHARATBLEND / FORECAST INTELLIGENCE</div>', unsafe_allow_html=True)
    st.title("One forecast. Every source explained.")
    st.caption("Explore calibrated model mixtures, uncertainty, and the evidence behind each issued forecast.")

    with st.sidebar:
        page = st.radio('Workspace', ['Public forecasts', 'Blend laboratory', 'Model & source atlas', 'Architecture & PPT'])
        st.caption('Provider forecasts · transparent routing · source evidence')
    if page == 'Public forecasts':
        render_live()
        return
    if page == 'Model & source atlas':
        render_atlas()
        return
    if page == 'Architecture & PPT':
        render_architecture()
        return

    data_dir = local_settings().get("BHARATBLEND_DATA_DIR", "artifacts/demo")
    try:
        service = load_service(data_dir)
        status = service.status()
        cases = service.cases()
        registry = service.registry()
    except (FileNotFoundError, ValueError, OSError) as exc:
        st.error(f"Prepared forecasts are unavailable: {exc}")
        st.info("Prepare a workspace with the BharatBlend CLI, then reload this page.")
        st.code("bharatblend demo --output artifacts/demo")
        return

    if status.get("data_kind") == "synthetic":
        st.markdown('<div class="demo-banner"><strong>SYNTHETIC DEMONSTRATION</strong> · '
                    'Generated weather and observations. Scores demonstrate the workflow and do not establish real forecast skill.</div>',
                    unsafe_allow_html=True)
        st.sidebar.warning("SYNTHETIC DATA · demonstration only")
    else:
        st.caption(f"Workspace provenance: {status.get('data_kind', 'not specified')}")

    if not cases:
        st.info("This workspace has no forecast cases available.")
        return

    with st.sidebar:
        st.markdown("### Forecast workspace")
        variables = [variable for variable in VARIABLES if any(case["variable"] == variable for case in cases)]
        if not variables:
            st.info("No supported forecast variables are available.")
            return
        variable = st.selectbox("Variable", variables, format_func=VARIABLES.get)
        variable_cases = [case for case in cases if case["variable"] == variable]
        locations = {case["location_id"]: case.get("location_name", case["location_id"]) for case in variable_cases}
        location = st.selectbox("Location", list(locations), format_func=locations.get)
        location_cases = [case for case in variable_cases if case["location_id"] == location]
        location_cases.sort(key=lambda case: (case["valid_start"], case["issuance_time"]), reverse=True)
        case_index = st.selectbox("Forecast case", range(len(location_cases)),
                                  format_func=lambda i: f"{timestamp(location_cases[i]['valid_start'])} · issued {timestamp(location_cases[i]['issuance_time'])}")
        case = location_cases[case_index]
        track = st.selectbox("Blending track", ["interpretable", "accuracy"],
                             format_func=lambda value: "Interpretable gate" if value == "interpretable" else "Accuracy gate")
        defaults = {"rainfall": "64.5, 115.6, 204.5", "temperature": "35, 40", "wind_uv": "10, 15"}
        threshold_text = st.text_input("Exceedance thresholds", value=defaults[variable], key=f"thresholds_{variable}")
        st.caption("Comma-separated values in mm, °C, or m/s for the selected variable.")
        entries = registry.get("models", registry.get("sources", [])) if isinstance(registry, dict) else registry
        if isinstance(entries, dict):
            source_ids = list(entries)
        else:
            source_ids = sorted({row.get("source_id", row.get("id")) for row in entries
                                 if isinstance(row, dict) and (row.get("source_id") or row.get("id"))
                                 and row.get("kind", "forecast_expert") == "forecast_expert"
                                 and row.get("capabilities", {}).get(variable) is not False})
        source_ids = sorted(case.get("source_ids", status.get("source_ids", source_ids)))
        excluded = st.multiselect("Exclude sources", source_ids, help="Recompute the forecast using the remaining available sources.")
        if excluded:
            st.caption("Counterfactual forecast: source exclusions are included in the receipt.")
        if st.button("Reload prepared data", width="stretch"):
            load_service.clear()
            st.rerun()

    try:
        thresholds = sorted({float(value.strip()) for value in threshold_text.split(",") if value.strip()})
        if any(not math.isfinite(value) for value in thresholds):
            raise ValueError("Thresholds must be finite numbers.")
        if variable != "temperature" and any(value < 0 for value in thresholds):
            raise ValueError("Rainfall and wind-speed thresholds must be nonnegative.")
        if len(thresholds) > 32:
            raise ValueError("Use at most 32 thresholds.")
        request = {key: case[key] for key in ("location_id", "variable", "valid_start", "valid_end", "issuance_time")}
        request.update(track=track, excluded_sources=excluded, thresholds=thresholds)
        result = service.predict(request)
    except (ValueError, FileNotFoundError) as exc:
        st.error(f"Forecast unavailable: {exc}")
        return

    st.subheader(f"{locations[location]} · {VARIABLES[variable]}")
    st.caption(f"Valid {timestamp(case['valid_start'])} → {timestamp(case['valid_end'])} · Issued {timestamp(case['issuance_time'])}")
    for warning in result.get("warnings", []):
        st.warning(str(warning))
    if result.get("status") not in {"blended", "single_source"}:
        st.warning(f"Forecast status: {result.get('status', 'unavailable')}")
        st.json(result, expanded=False)
        render_receipt(result)
        return
    if result["status"] == "single_source":
        st.info("One source is available. This forecast uses its calibrated distribution with weight 100%.")

    summary = result.get("summary", {})
    columns = st.columns(4)
    unit = summary.get("units", "")
    mean = summary.get("speed_mean") if variable == "wind_uv" else summary.get("mean")
    columns[0].metric("Mean wind speed" if variable == "wind_uv" else "Predictive mean", numeric(mean, f" {unit}"))
    columns[1].metric("Median", numeric(summary.get("median"), f" {unit}"))
    columns[2].metric("Active sources", str(len(result.get("sources", []))))
    probabilities = result.get("probabilities", [])
    if probabilities:
        probability = probabilities[0]
        columns[3].metric(f"P(> {probability['threshold']:g} {unit})", f"{probability['probability']:.1%}")
    else:
        columns[3].metric("Track", "Interpretable" if track == "interpretable" else "Accuracy")
    if variable == "wind_uv" and isinstance(summary.get("mean"), list):
        u, v = summary["mean"]
        st.caption(f"Vector mean: u = {u:.2f} m/s, v = {v:.2f} m/s. Speed summaries describe the predictive wind-speed distribution.")

    network_tab, forecast_tab, explanation_tab, verification_tab, experiments_tab = st.tabs(
        ["Model routing network", "Forecast explorer", "Numerical evidence", "Verification", "Ablations & experiments"]
    )
    with network_tab:
        render_network(result, service, request)
    with forecast_tab:
        left, right = st.columns([1, 1.25], gap="large")
        with left:
            st.markdown("#### Forecast and weight maps")
            render_coverage(variable_cases, location, service, {**request, "map_sources": [s["source_id"] for s in result.get("sources", [])]})
        with right:
            st.markdown("#### Source mixture")
            render_weights(result.get("sources", []))
        left, right = st.columns(2, gap="large")
        with left:
            st.markdown("#### Threshold exceedance")
            render_probabilities(result)
        with right:
            st.markdown("#### Predictive distribution")
            render_distribution(result)
    with explanation_tab:
        st.markdown("#### The gate behind this forecast")
        render_explanation(result)
    with verification_tab:
        try:
            render_verification(service, variable)
        except (FileNotFoundError, ValueError) as exc:
            st.info(f"Prepared verification is unavailable: {exc}")
    with experiments_tab:
        try:
            render_experiments(service)
        except (FileNotFoundError, ValueError) as exc:
            st.info(f"Prepared experiments are unavailable: {exc}")

    render_receipt(result)


if __name__ == "__main__":
    main()
