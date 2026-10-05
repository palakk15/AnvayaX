# Anvaya X

An auditable AI–NWP forecast-blending research prototype for India. The **Vercel frontend** searches Indian places, compares current named public forecasts in IST, and explores a separate, explicitly **synthetic** trained-blend demonstration. The 35-profile model atlas documents additional candidates; an atlas entry does not mean a model is already integrated or locally validated.

The sole presentation and technical guide is [ppt.md](ppt.md). It covers the proposed solution, scientific rationale, differentiators, architecture, challenges, feasibility, impact, model catalogue, validation protocol and slide-by-slide plan.

## Frontend

```powershell
npm run build
npm run dev
```

Open `http://127.0.0.1:4173`. On Vercel, import the existing private `SIH_PS_81` repository with the root directory as project root. `vercel.json` builds the static site from `site/` and deploys the forecast and India-location search routes in `api/`. The live desk uses Open-Meteo's named GFS, IFS and AIFS series; it does not apply the synthetic gate to those real values.

## Research application

See [ppt.md](ppt.md) for the full research setup, synthetic training, benchmark and export sequence. Check the frontend with `npm run test:web` and the Python research code with `.venv\Scripts\python -m pytest -q`.

The Python Streamlit/FastAPI app remains available for local research. `.env`, local artifacts, and credentials are ignored by Git. The optional Groq API keys are only for local natural-language narration; exact mathematical explanations need no key.
