# FrontierView

A pre-trade market-impact analysis tool (Almgren–Chriss 2005). A single FastAPI
app serves a static HTML UI, a JSON compute API, and an optional LLM agent — all
from one origin, deployed as one Render service.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full picture (deployment, the
UI/server boundary, component diagrams, and the agent subsystem).

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn api.main:app --reload
```

API docs available at `http://localhost:8000/docs`.

## Structure

```
api/
  main.py        — FastAPI app, routes
  models.py      — Pydantic request/response schemas
model_assumptions.md — tracked modelling decisions
requirements.txt
```
