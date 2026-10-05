# BharatBlend

An auditable AI–NWP forecast-blending research prototype for India. The **Vercel frontend** compares current named public forecasts and explores a separate, explicitly **synthetic** trained-blend demonstration. The synthetic benchmark does not establish real-world forecast skill.

The sole presentation and technical guide is [ppt.md](ppt.md). It covers the proposed solution, scientific rationale, differentiators, architecture, challenges, feasibility, impact, model catalogue, validation protocol and slide-by-slide plan.

## Frontend

```powershell
npm run build
npm run dev
```

Open `http://127.0.0.1:4173`. On Vercel, import the existing private `SIH_PS_81` repository with the root directory as project root. `vercel.json` builds the static site from `site/` and deploys the real-data route in `api/`.

## Research application

See [ppt.md](ppt.md) for the full research setup, synthetic training, benchmark and export sequence. Check the frontend with `npm run test:web` and the Python research code with `.venv\Scripts\python -m pytest -q`.

The Python Streamlit/FastAPI app remains available for local research. `.env`, local artifacts, and credentials are ignored by Git. The optional Groq API keys are only for local natural-language narration; exact mathematical explanations need no key.
