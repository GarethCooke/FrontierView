# FrontierView

A FastAPI service for evaluating model assumptions.

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
