"""Minimal REST API so other systems (CRM, call-centre dialler, BI tools) can
consume predictions and AI insights.

Run:  uvicorn api.main:app --port 8000
Auth: a service token in INSIGHTFORGE_API_TOKEN, sent as "Authorization: Bearer <token>".
      If no token is configured the API refuses every data request (fails closed).
"""
from __future__ import annotations

import hmac
import secrets
import sys
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insightforge.agent.context import ContextError  # noqa: E402
from insightforge.agent.service import run_task  # noqa: E402
from insightforge.analysis import run_analysis, run_dq_checks, to_date  # noqa: E402
from insightforge.auth import AuthError, get_workspace, login, sign_up, update_workspace, INDUSTRIES  # noqa: E402
from insightforge.config import get_settings  # noqa: E402
from insightforge.db import get_engine, init_db, run_query  # noqa: E402
from insightforge.ingest import IngestError, get_dataset, list_datasets, load_csv_upload, load_synthetic  # noqa: E402
from insightforge.logging_setup import get_logger  # noqa: E402
from insightforge.ml.pipeline import PipelineError, latest_run, run_predictions, train_use_case  # noqa: E402
from insightforge.reporting.report import build_report  # noqa: E402
from insightforge.usecases import custom_use_case, load_use_cases, use_cases_for_source  # noqa: E402

log = get_logger("api")
app = FastAPI(title="Business Insights API", version="1.0.0")
SESSIONS: dict[str, object] = {}
ROOT = Path(__file__).resolve().parents[1]

class WebLogin(BaseModel):
    email: str
    password: str

class WebSignup(BaseModel):
    business_name: str
    industry: str = "Any industry"
    full_name: str
    email: str
    password: str
    objective: str = ""
    country: str = "Global"

class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    country: str | None = Field(default=None, min_length=1, max_length=60)
    industry: str | None = Field(default=None, min_length=1, max_length=100)
    objective: str = Field(default="", max_length=2000)

class DemoRequest(BaseModel):
    customers: int = Field(default=8000, ge=500, le=50000)

class TrainRequest(BaseModel):
    dataset_id: int
    use_case_id: str | None = None
    task_type: Literal["classification", "regression"] = "classification"
    target_column: str | None = None
    positive_class: str | None = None

class InsightRequest(BaseModel):
    task: Literal["data_quality_brief", "kpi_explainer", "model_explainer", "retention_actions", "executive_summary", "ask"]
    question: str | None = Field(default=None, max_length=500)
    use_case: str | None = None


def _session_user(request: Request):
    token = request.cookies.get("business_insights_session")
    user = SESSIONS.get(token or "") if token else None
    if not user:
        raise HTTPException(401, "Not logged in.")
    return user


def _set_session(response: Response, user) -> dict:
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = user
    response.set_cookie("business_insights_session", token, httponly=True, samesite="lax", max_age=60 * 60 * 12)
    return {"user_id": user.user_id, "workspace_id": user.workspace_id, "email": user.email,
            "full_name": user.full_name, "role": user.role, "workspace_name": user.workspace_name}


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def frontend() -> HTMLResponse:
    return HTMLResponse((ROOT / "frontend" / "index.html").read_text())


app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.post("/api/login")
def web_login(body: WebLogin, response: Response):
    try:
        user = login(body.email, body.password)
        return _set_session(response, user)
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from None


@app.post("/api/signup")
def web_signup(body: WebSignup, response: Response):
    try:
        user = sign_up(body.business_name, body.industry, body.full_name, body.email,
                       body.password, body.objective, body.country)
        return _set_session(response, user)
    except AuthError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/demo")
def web_demo(body: DemoRequest, response: Response):
    email = "demo@businessinsights.example"
    password = "BusinessInsights#2026"
    try:
        user = login(email, password)
    except AuthError:
        user = sign_up("Business Insights Demo", "Any industry", "Demo Administrator", email,
                       password, "Identify risk, understand drivers, and prioritize high-value actions.", "Global")
    if list_datasets(user.workspace_id).empty:
        load_synthetic(user.workspace_id, body.customers, 42, user_id=user.user_id)
    return _set_session(response, user)


@app.get("/api/me")
def web_me(request: Request):
    user = _session_user(request)
    ws = get_workspace(user.workspace_id)
    return {"user_id": user.user_id, "workspace_id": user.workspace_id, "email": user.email,
            "full_name": user.full_name, "role": user.role, "workspace_name": user.workspace_name,
            "industry": ws.get("industry", ""), "country": ws.get("country", ""), "objective": ws.get("objective", "")}


@app.post("/api/logout")
def web_logout(request: Request, response: Response):
    token = request.cookies.get("business_insights_session")
    if token:
        SESSIONS.pop(token, None)
    response.delete_cookie("business_insights_session")
    return {"ok": True}


@app.get("/api/workspace")
def web_workspace(request: Request):
    user = _session_user(request)
    ws = get_workspace(user.workspace_id)
    datasets = list_datasets(user.workspace_id)
    items = []
    for row in datasets.to_dict(orient="records"):
        items.append({k: str(v) if k in {"as_of_date", "created_at"} and v is not None else v
                      for k, v in row.items() if k in {"id", "name", "source", "table_name", "as_of_date", "row_counts", "status"}})
    run = latest_run(user.workspace_id)
    model = None
    if run:
        model = {"run_id": run["id"], "use_case": run["use_case_id"], "task_type": run["task_type"],
                 "model": run["best_model"], "metrics": run["metrics"], "details": run["details"]}
    return {"workspace": ws, "industries": INDUSTRIES, "datasets": items, "model": model}


@app.put("/api/workspace")
def update_web_workspace(body: WorkspaceUpdate, request: Request):
    user = _session_user(request)
    values = {"objective": body.objective.strip()}
    if body.industry is not None:
        if body.industry not in INDUSTRIES:
            raise HTTPException(422, "Choose a listed industry.")
        values["industry"] = body.industry
    if body.name is not None:
        name = body.name.strip()
        if len(name) < 2:
            raise HTTPException(422, "Workspace name must be at least 2 characters.")
        values["name"] = name
    if body.country is not None:
        country = body.country.strip()
        if not country:
            raise HTTPException(422, "Country or region is required.")
        values["country"] = country
    update_workspace(user.workspace_id, **values)
    ws = get_workspace(user.workspace_id)
    # Keep the active session's workspace display name in sync.
    user.workspace_name = ws["name"]
    return {"workspace": ws}


def _api_value(value):
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@app.get("/api/analysis")
def web_analysis(request: Request):
    user = _session_user(request)
    datasets = list_datasets(user.workspace_id)
    telco = datasets[datasets["source"] == "synthetic_telco"] if not datasets.empty else datasets
    if telco.empty:
        return {"kind": "uploaded", "message": "SQL business analysis is available for the relational demo dataset. Your uploaded CSV is profiled on the Data page and can be modelled from the Model page.", "sections": [], "quality": []}
    ds = telco.sort_values("id").iloc[-1]
    as_of = to_date(ds["as_of_date"])
    result = run_analysis(user.workspace_id, as_of)
    sections = []
    for key, item in result.items():
        rows = item.data.head(100).to_dict(orient="records")
        sections.append({"id": key, "title": item.title, "question": item.meta.get("question", ""),
                         "columns": list(item.data.columns),
                         "rows": [{k: _api_value(v) for k, v in row.items()} for row in rows]})
    quality = run_dq_checks(user.workspace_id).drop(columns=["sql"], errors="ignore").to_dict(orient="records")
    quality = [{k: _api_value(v) for k, v in row.items()} for row in quality]
    return {"kind": "relational", "as_of": as_of.isoformat(), "sections": sections, "quality": quality}


@app.get("/api/monitoring")
def web_monitoring(request: Request):
    user = _session_user(request)
    run = latest_run(user.workspace_id)
    if not run:
        return {"available": False, "message": "Train a model to create a monitoring baseline."}
    details = run.get("details") or {}
    score = details.get("score_drift") or {}
    return {"available": True, "run_id": run["id"], "model": run.get("best_model"),
            "task_type": run.get("task_type"), "score_drift": score,
            "feature_drift": details.get("drift", []), "scoring_summary": details.get("scoring_summary", {}),
            "snapshot": str(run.get("score_snapshot") or "")}


@app.post("/api/demo/data")
def generate_demo_data(request: Request):
    user = _session_user(request)
    ds_id = load_synthetic(user.workspace_id, 8000, 42, user_id=user.user_id)
    return {"dataset_id": ds_id}


@app.post("/api/datasets/upload")
def upload_dataset(request: Request, file: UploadFile = File(...)):
    user = _session_user(request)
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Please upload a CSV file.")
    try:
        df = pd.read_csv(file.file)
        ds_id = load_csv_upload(user.workspace_id, df, file.filename, user.user_id)
        ds = get_dataset(ds_id, user.workspace_id)
        table = run_query(f"SELECT * FROM {ds['table_name']} LIMIT 500")
        profile = [{"column": c, "type": str(table[c].dtype), "missing_pct": round(float(table[c].isna().mean() * 100), 2),
                    "distinct": int(table[c].nunique())} for c in table.columns]
        preview = table.head(20).fillna("").astype(str).to_dict(orient="records")
        return {"dataset_id": ds_id, "columns": list(table.columns), "profile": profile, "preview": preview}
    except (IngestError, pd.errors.ParserError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/model/train")
def train_model(body: TrainRequest, request: Request):
    user = _session_user(request)
    ds = get_dataset(body.dataset_id, user.workspace_id)
    try:
        if ds["source"] == "synthetic_telco":
            if body.use_case_id:
                uc = load_use_cases().get(body.use_case_id)
            else:
                uc = use_cases_for_source("synthetic_telco")[0]
            if not uc:
                raise HTTPException(404, "Unknown use case.")
            out = train_use_case(user.workspace_id, ds["id"], uc, user.user_id, positive_class=None)
        else:
            if not body.target_column:
                raise HTTPException(422, "A target column is required for uploaded data.")
            uc = custom_use_case(ds, body.target_column, body.task_type)
            out = train_use_case(user.workspace_id, ds["id"], uc, user.user_id,
                                 positive_class=body.positive_class)
        update_workspace(user.workspace_id, active_use_case=uc.id)
        return {"run_id": out.run_id, "model": out.details.get("best_model"),
                "metrics": out.metrics, "details": out.details}
    except (PipelineError, IngestError) as exc:
        raise HTTPException(400, str(exc)) from None


@app.get("/api/use-cases")
def web_use_cases(request: Request, dataset_id: int):
    user = _session_user(request)
    ds = get_dataset(dataset_id, user.workspace_id)
    items = [{"id": u.id, "name": u.name, "description": u.description}
             for u in use_cases_for_source("synthetic_telco")]
    if ds["source"] == "synthetic_telco":
        df = run_query("SELECT * FROM customers WHERE workspace_id = :ws LIMIT 500",
                       {"ws": user.workspace_id})
        profile = [{"column": c, "type": str(df[c].dtype),
                    "missing_pct": round(float(df[c].isna().mean() * 100), 2),
                    "distinct": int(df[c].nunique())} for c in df.columns if c != "workspace_id"]
        preview = df.drop(columns=["workspace_id"], errors="ignore").head(20).fillna("").astype(str).to_dict(orient="records")
        return {"kind": "synthetic", "items": items, "profile": profile, "preview": preview,
                "columns": list(df.columns)}
    df = run_query(f"SELECT * FROM {ds['table_name']} LIMIT 500")
    return {"kind": "uploaded", "columns": list(df.columns), "preview": df.head(20).fillna("").astype(str).to_dict(orient="records")}


@app.get("/api/predictions")
def web_predictions(request: Request, limit: int = Query(500, ge=1, le=5000)):
    user = _session_user(request)
    run = latest_run(user.workspace_id)
    if not run:
        raise HTTPException(404, "Train a model first.")
    df = run_predictions(run["id"], user.workspace_id)
    return {"run_id": run["id"], "items": df.head(limit).fillna("").astype(object).where(pd.notna(df.head(limit)), None).to_dict(orient="records")}


@app.post("/api/insights")
def web_insights(body: InsightRequest, request: Request):
    user = _session_user(request)
    run = latest_run(user.workspace_id)
    try:
        res = run_task(body.task, user.workspace_id, run_id=run["id"] if run else None,
                       dataset_id=run["dataset_id"] if run else None, question=body.question,
                       user_id=user.user_id)
        return {"status": res.status, "provider": res.provider, "text": res.text, "validation": res.validation}
    except ContextError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/report")
def web_report(request: Request):
    user = _session_user(request)
    run = latest_run(user.workspace_id)
    if not run:
        raise HTTPException(409, "Train a model before generating a report.")
    try:
        path = build_report(user.workspace_id, run["id"], user.full_name, user_id=user.user_id)
    except (ValueError, ContextError) as exc:
        raise HTTPException(409, str(exc)) from None
    return FileResponse(path, media_type="application/pdf", filename="business-insights-report.pdf")


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().api_token
    if not expected:
        raise HTTPException(503, "API token not configured on the server.")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Invalid or missing token.")


def _run(workspace_id: int, use_case: str | None) -> dict | None:
    """Latest completed run for the requested use case (default: the workspace's active one)."""
    return latest_run(workspace_id, use_case or get_workspace(workspace_id).get("active_use_case"))


@app.get("/health")
def health() -> dict:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        db = "ok"
    except Exception:
        db = "unavailable"
    return {"status": "ok" if db == "ok" else "degraded", "database": db, "agent_mode": get_settings().snwolley_mode}


@app.get("/v1/workspaces/{workspace_id}/model", dependencies=[Depends(require_token)])
def model_summary(workspace_id: int, use_case: str | None = None) -> dict:
    run = _run(workspace_id, use_case)
    if not run:
        raise HTTPException(404, "No completed model run.")
    d = run["details"]
    return {"run_id": run["id"], "use_case": run["use_case_id"], "model": run["best_model"], "metrics": run["metrics"],
            "score_snapshot": str(run["score_snapshot"]), "scoring_summary": d.get("scoring_summary"),
            "top_drivers": [{"feature": x["label"], "importance": x["importance"]} for x in d.get("drivers", [])[:10]]}


@app.get("/v1/workspaces/{workspace_id}/predictions", dependencies=[Depends(require_token)])
def predictions(workspace_id: int, use_case: str | None = None, tier: Literal["High", "Medium", "Low"] | None = None,
                limit: int = Query(100, ge=1, le=5000)) -> dict:
    run = _run(workspace_id, use_case)
    if not run:
        raise HTTPException(404, "No completed model run.")
    df = run_predictions(run["id"], workspace_id)
    if tier:
        df = df[df["risk_tier"] == tier]
    return {"run_id": run["id"], "count": int(len(df)), "items": df.head(limit).to_dict(orient="records")}


@app.post("/v1/workspaces/{workspace_id}/insights", dependencies=[Depends(require_token)])
def insights(workspace_id: int, body: InsightRequest) -> dict:
    run = _run(workspace_id, body.use_case)
    if body.task == "ask" and not body.question:
        raise HTTPException(422, "A question is required for task 'ask'.")
    try:
        res = run_task(body.task, workspace_id, run_id=run["id"] if run else None,
                       dataset_id=run["dataset_id"] if run else None, question=body.question)
    except ContextError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"id": res.id, "status": res.status, "provider": res.provider, "text": res.text, "validation": res.validation}
