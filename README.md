# Submission Triage Agent v2

Independent prototype for a small commercial submission intake and triage agent.

This app is intentionally separate from the older triage prototype and from other underwriting agents in the workspace. It demonstrates a six-step LangGraph workflow and a silent failure mode where a normalized class code can make a submission look clean even when source evidence suggests it should have been escalated.

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
