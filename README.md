# Submission Agent

Small commercial submission intake and triage app for underwriting teams.

This app demonstrates a six-step workflow: upload a PDF, extract key submission fields with Gemini, add supplemental context, check basic guidelines, route the file, and prepare an underwriter handoff. It also supports a demo scenario where a normalized class code can make a submission look clean even when source evidence suggests it should have been escalated.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# add GEMINI_API_KEY
.venv/bin/uvicorn triage.server:app --port 8004 --reload
```

Open `http://localhost:8004`.

Extraction is Gemini-only. If `GEMINI_API_KEY` is missing or Gemini returns an invalid response, the graph stops at Step 2 and shows an extraction error. Gemini calls use the public REST API through `httpx`.

For a simple data-flow explanation, see `ARCHITECTURE.md`.

Demo-ready PDF samples are available in `samples/`.
