"""Provider reference catalogue, distinct from a workspace's trained source registry."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

# Advantages/limitations below are engineering assessments of integration suitability,
# not measured local forecast rankings. Unknown hardware and version facts stay unknown.
PROFILES = {
    'gfs': ('NOAA / NCEP', 'Physical atmosphere model with an FV3 dynamical core',
            'Global numerical guidance for surface and upper-air weather.',
            ['Public forecast access', 'Physical comparator for AI outputs'],
            ['Deterministic output needs probabilistic calibration', 'Grid and accumulation steps require decoding']),
    'gefs': ('NOAA / NCEP', 'Perturbed physical forecast ensemble',
             'Multiple atmospheric forecasts represent uncertainty in initial conditions and model evolution.',
             ['Member spread supports uncertainty estimation', 'Retrospective product supports calibration studies'],
             ['Members share model errors', 'Retrospective and operational configurations differ']),
    'ifs_control': ('ECMWF', 'Integrated Forecasting System numerical control forecast',
             'Control product from the physical IFS forecast system.',
             ['Independent provider comparison', 'Public GRIB products'],
             ['A control alone does not quantify uncertainty', 'Do not conflate open control with historical HRES']),
    'ifs_ens': ('ECMWF', 'Integrated Forecasting System ensemble',
             'Perturbed IFS forecast members for probabilistic guidance.',
             ['Enables event-frequency comparisons', 'Retains multiple weather scenarios'],
             ['Large archive and member bookkeeping', 'Local reliability still needs observation verification']),
    'aifs_single': ('ECMWF', 'Graph-transformer AI forecasting system',
             'Learned deterministic global weather evolution; exact checkpoint is recorded separately.',
             ['AI and physics comparison from one provider', 'Published weights and model card'],
             ['One trajectory needs calibration for uncertainty', 'Shared initialization can correlate errors with IFS']),
    'aifs_ens': ('ECMWF', 'Probabilistic AI forecasting system',
             'Ensemble AI product; version and training cutoff must be independently pinned.',
             ['Probabilistic AI comparator', 'Multiple sampled trajectories'],
             ['Checkpoint-specific training boundary required', 'Do not transfer Single calibration to ENS']),
    'graphcast_operational': ('Google DeepMind; NOAA/CIRA archive', 'Multiscale graph neural network',
             'Operational checkpoint maps atmospheric states through a multiscale mesh to future weather.',
             ['Named public checkpoint', 'Native precipitation in the NOAA archive'],
             ['Checkpoint and initialization both affect comparability', 'No local India skill established here']),
    'pangu_weather': ('Huawei research; NOAA/CIRA archive', '3D Earth-specific transformer',
             'Atmospheric forecasting architecture with hierarchical temporal prediction.',
             ['Different neural architecture for comparison', 'Public implementation and reforecast route'],
             ['No native precipitation field', 'Surface and pressure-level channels must be distinguished']),
    'fourcastnet_v2_small': ('NVIDIA; NOAA/CIRA archive', 'Spherical Fourier neural operator family',
             'Spectral neural weather model; this entry refers specifically to the NOAA v2-small product.',
             ['Alternative spectral representation', 'Public reforecast route for supported variables'],
             ['No native precipitation in this archive product', 'Do not borrow capabilities from v1 or v3']),
    'fourcastnet_3': ('NVIDIA', 'Stochastic spherical neural operator',
             'Probabilistic global weather model with sampled ensemble trajectories.',
             ['Open model card and weights', 'Designed for ensemble generation'],
             ['No native precipitation in the cited release', 'Requires its own input preprocessing and benchmark']),
    'aurora_original': ('Microsoft Research', 'Atmospheric foundation model / 3D transformer',
             'Original weather checkpoint with temperature, wind and pressure surface channels.',
             ['Reusable atmospheric representation', 'Public model implementation'],
             ['Original weather checkpoint has no native rainfall', 'Fine-tuned variants have different capabilities']),
    'aurora_1_5': ('Microsoft Research', 'Atmospheric foundation model, expanded output variant',
             'Version 1.5 expands the weather variable inventory and temporal output.',
             ['Precipitation-capable version', 'Public model catalogue'],
             ['Training cutoff needs exact checkpoint evidence', 'New fields require separate unit and interval audits']),
    'aurora_1_5_ensemble': ('Microsoft Research', 'Ensemble foundation-model variant',
             'Ensemble extension of Aurora 1.5; distinct from its deterministic product.',
             ['Supports probabilistic comparisons', 'Shared documented model family'],
             ['Member archive not integrated here', 'Do not reuse deterministic verification results']),
    'gencast': ('Google DeepMind', 'Conditional diffusion ensemble on a spherical mesh',
             'Generates sampled weather trajectories; this entry pins the research checkpoint.',
             ['Generative uncertainty representation', 'Explicit research checkpoint boundary'],
             ['Native twelve-hour totals must retain their windows', 'Research and operational checkpoints differ']),
    'weathernext_2': ('Google DeepMind', 'Functional generative network ensemble',
             'WeatherNext generation with open-weight and managed data access routes.',
             ['Sampled ensemble trajectories', 'Published code and checkpoint route'],
             ['Managed feeds and weights have distinct terms', 'Archive entitlement is not implied by a model card']),
    'weathernext_3': ('Google DeepMind', 'Managed AI ensemble product',
             'Provider weather product with product-dependent delivery and spatial resolution.',
             ['Frequent provider updates', 'Multiple forecast scenarios'],
             ['No local checkpoint is installed here', 'Account, exact fields and historical access require verification']),
    'neuralgcm_standard': ('Google Research / NeuralGCM', 'Differentiable dynamics plus learned physical parameterizations',
             'Hybrid general circulation model; standard checkpoint emphasizes atmospheric state evolution.',
             ['Physics and learning in one model', 'Useful hybrid research comparator'],
             ['No direct precipitation for standard checkpoint', 'Atmospheric levels do not imply 2 m / 10 m diagnostics']),
    'neuralgcm_precipitation': ('Google Research / NeuralGCM', 'Hybrid dynamics with precipitation-specific learned components',
             'Coarse stochastic precipitation checkpoint, distinct from standard NeuralGCM.',
             ['Precipitation-specific hybrid comparison', 'Ensemble research capability'],
             ['2.8-degree product is coarse for local India work', 'Accumulate only over documented intervals']),
    'ncum_global': ('NCMRWF', 'Global Unified Model numerical forecasting',
             'India-provider global physical guidance; current configuration needs provider confirmation.',
             ['India-provider operational context', 'Useful independent candidate'],
             ['Machine-readable archive access unverified', 'Historical resolution is not current configuration evidence']),
    'ncum_regional': ('NCMRWF', 'Regional Unified Model numerical forecasting',
             'Regional physical guidance with a configured limited-area domain.',
             ['Regional-domain candidate', 'Potential terrain-sensitive comparison'],
             ['Boundary conditions influence results', 'Provider archive and licensing not connected']),
    'neps_global': ('NCMRWF', 'Global ensemble prediction system',
             'Global ensemble candidate associated with NCMRWF numerical guidance.',
             ['India-provider ensemble comparison', 'Members can represent scenario spread'],
             ['Current member count and cycle unverified', 'No matched archive loaded']),
    'neps_regional': ('NCMRWF', 'Regional ensemble prediction system',
             'Limited-area ensemble candidate; exact setup is a provider-specific product.',
             ['Regional probabilistic research option', 'Supports member-based comparisons if acquired'],
             ['Domain and boundary version must be pinned', 'No public archive entitlement assumed']),
    'laxmi': ('Authors of arXiv:2609.03210', 'Observation-trained AIFS-CRPS graph-transformer research model',
             'Research precipitation model using observation-based rainfall training targets.',
             ['Addresses precipitation training-target quality', 'Relevant rainfall research hypothesis'],
             ['Downloadable checkpoint access unverified', 'Paper results are not this application’s measured performance']),
    'jua_ept_2': ('Jua', 'Commercial Earth-system AI forecast product',
             'EPT-2 deterministic forecast service; EPT-2e is a separate ensemble offering.',
             ['Provider-supported delivery option', 'Documented product API'],
             ['Subscription and field entitlements apply', 'Wind speed/direction need explicit vector conversion']),
    'wrf': ('NCAR / WRF community', 'Configurable regional numerical weather model',
             'Software pipeline requiring domain, physics, initial state and boundary conditions.',
             ['Explicit domain and physics choices', 'Regional experiment control'],
             ['Compute and configuration burden', 'Software installation is not a forecast archive']),
    'aifs_wrf': ('Proposed Anvaya X research integration', 'AIFS boundary forcing into configured WRF',
             'Proposed coupling experiment; no operational source or completed integration exists here.',
             ['Testable AI-to-regional-physics hypothesis', 'Can isolate boundary-forcing choices in an experiment'],
             ['Required boundary-field completeness unproven', 'No measured skill or working coupled run']),
    'corrdiff': ('NVIDIA', 'Conditional diffusion regional downscaler',
             'Generates regional fine-scale fields conditioned on coarser weather information.',
             ['Stochastic downscaling framework', 'Potential resolution-refinement experiment'],
             ['Not an independent global forecaster', 'Checkpoint domain cannot be assumed to transfer to India']),
}

DATA_DETAILS = {
    'noaa_gfs': ('Issued physical-model forecasts', 'Global; selected GRIB delivery grid',
                 'Operational comparison and future calibrated expert', 'https://noaa-gfs-bdp-pds.s3.amazonaws.com/'),
    'noaa_gefs': ('Issued ensemble forecasts', 'Global; grid varies by file and lead',
                  'Member-level probability and spread evaluation', 'https://noaa-gefs-pds.s3.amazonaws.com/'),
    'noaa_gefs_v12_reforecast': ('Retrospective ensemble experiments', 'Global, 2000–2019 product',
                  'Version-consistent calibration research', 'https://noaa-gefs-retrospective.s3.amazonaws.com/'),
    'noaa_aiwp': ('AI model reforecasts', 'Global; inspect each NetCDF object',
                  'AI/NWP matched archive research', 'https://noaa-oar-mlwp-data.s3.amazonaws.com/'),
    'ecmwf_open': ('Issued IFS / AIFS forecasts', 'Global, product-specific GRIB grids',
                  'Independent physical and AI comparisons', 'https://data.ecmwf.int/forecasts/'),
    'imd_daily_rainfall': ('Gauge-derived daily precipitation analysis', 'India, 0.25-degree grid',
                  'Daily rainfall verification with matching accumulation windows', None),
    'imerg_early': ('Satellite precipitation retrieval', 'Global half-hourly estimates',
                  'Lower-latency rainfall monitoring; preserve retrieval uncertainty', None),
    'imerg_late': ('Satellite precipitation retrieval', 'Global half-hourly estimates',
                  'Delayed monitoring with more input availability', None),
    'imerg_final': ('Retrospective satellite/gauge-adjusted precipitation', 'Global half-hourly estimates',
                  'Retrospective rainfall verification; release time matters', None),
    'noaa_isd': ('Quality-flagged station observations', 'Individual stations; audit India coverage',
                  'Independent temperature and wind checks at supported stations', 'https://www.ncei.noaa.gov/data/global-hourly/access/'),
    'imd_stations': ('Provider station observations', 'India stations and product-specific coverage',
                  'Independent local verification after access and QC review', 'https://dsp.imdpune.gov.in/'),
    'era5': ('Model-assisted reanalysis', 'Global historical atmospheric reconstruction',
                  'Secondary reference and training context; not independent station truth', 'https://cds.climate.copernicus.eu/'),
}


def config_root() -> Path:
    bundled = Path(__file__).parent / 'configs'
    return bundled if bundled.is_dir() else Path(__file__).resolve().parents[2] / 'configs'


def catalogue() -> dict:
    root = config_root()
    models = json.loads((root / 'model_registry.json').read_text(encoding='utf-8'))['models']
    datasets = json.loads((root / 'data_registry.json').read_text(encoding='utf-8'))['datasets']
    for model in models:
        provider, architecture, description, pros, cons = PROFILES[model['id']]
        model.update(provider=provider, architecture=architecture, description=description, pros=pros, cons=cons,
            assessment_basis='Engineering assessment of integration suitability; not a measured skill ranking.',
            local_inference_installed=False,
            compute_profile=('Configured regional model run needed' if model['kind'] == 'configured_forecast_pipeline'
                             else 'Provider outputs can be ingested without running the upstream model locally'),
            hardware_requirements='Exact checkpoint memory, runtime and throughput have not been measured here.',
            integration_requirements=['Pin product version and initialization source',
                'Match variable, units, target interval and location', 'Record original availability time',
                'Audit training cutoff and chronological evaluation', 'Calibrate on matched observations before promotion'])
        if model['id'] in ('wrf', 'aifs_wrf'):
            model['evidence_urls'] = [u for u in model['evidence_urls'] if '/wrf/' not in u] + [
                'https://www2.mmm.ucar.edu/wrf/site/documentation/users_guide/overview.html']
        if model['id'] == 'fourcastnet_v2_small':
            model['evidence_urls'].append('https://noaa-oar-mlwp-data.s3.amazonaws.com/README.txt')
        model['reference_review'] = ('Provider page available; current technical details not extractable in this review'
                                     if model['id'].startswith(('ncum_', 'neps_')) else 'Primary provider or author reference reviewed')
    for dataset in datasets:
        description, coverage, role, endpoint = DATA_DETAILS[dataset['id']]
        dataset.update(description=description, coverage=coverage, project_role=role, endpoint=endpoint,
            local_status='Reference catalogue; not a matched training archive',
            quality_checks=['Missing values and QC flags', 'Units and accumulation start/end',
                'Location/station identity and spatial support', 'Publication/revision timestamp',
                'Version, checksum and licence provenance'])
        dataset['reference_review'] = ('Page could not be retrieved in this review; reference retained'
                                      if dataset['id'] == 'imd_daily_rainfall' else 'Primary provider reference reviewed')
    datasets.append({
        'id': 'open_meteo_named_models', 'name': 'Open-Meteo named-model forecast API', 'kind': 'forecast',
        'description': 'Provider-served NOAA GFS, ECMWF IFS and AIFS model forecasts.',
        'coverage': 'Selected Indian cities; three days of hourly provider output',
        'project_role': 'Public forecast explorer; not input to the existing trained gate',
        'variables': ['temperature_2m', 'precipitation', 'wind_speed_10m', 'wind_direction_10m'],
        'source_ids': ['gfs', 'ifs_control', 'aifs_single'], 'format': 'JSON',
        'access': 'Named-model HTTP GET; free endpoint for eligible non-commercial use',
        'endpoint': 'https://api.open-meteo.com/v1/forecast',
        'evidence_urls': ['https://open-meteo.com/en/docs', 'https://open-meteo.com/en/docs/gfs-api',
                          'https://open-meteo.com/en/docs/ecmwf-api', 'https://open-meteo.com/en/terms'],
        'data_license': 'CC BY 4.0 attribution; API access subject to provider plan terms',
        'local_status': 'Adapter implemented; individual download receipts establish retrieval evidence',
        'access_verified': True, 'archive_verified': False, 'quality_audited': False,
        'constraints': ['Latest series does not pin a single initialization or checkpoint',
                        'Hourly values may be interpolated and elevation-adjusted',
                        'Total precipitation includes rain, showers and snow water equivalent',
                        'Not station observations and not a calibrated blended forecast'],
        'quality_checks': ['Response size bound', 'Exact requested model fields and units',
                           'UTC contiguous timestamps', 'Finite values and missing-value preservation', 'SHA-256 receipt'],
        'reference_review': 'Documentation reviewed and Delhi HTTP request verified on 2026-09-26',
    })
    return deepcopy({'reviewed_on': '2026-09-26', 'models': models, 'datasets': datasets,
        'scope': 'All 27 candidates in this project and all 13 registered data-source products; not every weather model worldwide.',
        'note': 'Descriptions are provider references. Actual forecast participation comes from a request receipt.'})


def export_catalogue_markdown() -> str:
    data = catalogue()
    lines = ['# Anvaya X model and data-source reference', '', f"Reviewed: {data['reviewed_on']}", '',
             data['scope'], '', data['note'], '',
             'Advantages and limitations are engineering assessments, not a local performance ranking. '
             'Unverified values stay unknown. All machine-readable properties are included below.']
    for kind, entries in [('Models', data['models']), ('Data sources', data['datasets'])]:
        lines += ['', f'## {kind}', '']
        for entry in entries:
            lines += [f"### {entry['name']} (`{entry['id']}`)", '', entry.get('description', ''), '']
            for key, value in entry.items():
                if key in ('id', 'name', 'description', 'evidence_urls'):
                    continue
                rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                lines.append(f'- **{key.replace("_", " ")}**: {rendered if value is not None else "Unknown / not verified"}')
            lines += ['', 'References: ' + ' · '.join(f'[Source {i+1}]({u})' for i, u in enumerate(entry['evidence_urls'])), '']
    return '\n'.join(lines)
