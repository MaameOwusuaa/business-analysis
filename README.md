# Business Insights — Universal Business Intelligence - AI-augmented machine learning analytics platform

Business Insights is a prototype of a generic analytics product a business can sign up for: it creates a workspace, brings in its data, checks and analyses it with SQL, trains a machine-learning model for the business problem it chooses, and uses the **Snwolley Agent API Toolkit** to turn the results into explanations, recommended actions and a management report.

The demo business is **Kasa Telecom**, a fictional Ghanaian mobile and fibre operator that wants to reduce customer churn. The platform itself is not tied to churn: business problems are configuration files, and a second use case (payment delinquency) plus a generic "upload a CSV and pick a target" flow run through the same pipeline.

```
Sign up / workspace  ->  Data (synthetic or CSV)  ->  SQL data quality + analysis  ->  ML (train, evaluate, explain, score)
        ->  Snwolley agent (insights, actions, Q&A, with guardrails)  ->  Dashboard + PDF report  ->  Monitoring (drift, audit)
```

## Quick start (macOS / Linux, Python 3.11+)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
mysql -uroot -p -e "CREATE DATABASE business_insights; CREATE USER 'insightforge'@'localhost' IDENTIFIED BY 'choose-a-password'; GRANT ALL ON business_insights.* TO 'insightforge'@'localhost';"
cp .env.example .env
# Edit DATABASE_URL in .env and replace the example password with your MySQL password.
python scripts/run_demo.py      # builds the whole flow end to end (about 1-2 minutes)
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 and use the demo login **demo@businessinsights.example / BusinessInsights#2026**, or create a new business workspace and generate data from the Data page. The frontend uses HTML, CSS, and JavaScript only. The authenticated workspace pages are also available as individual HTML files under `frontend/pages/` (Overview, Data, Analysis, Model, Predictions, Monitoring, Reports, AI analyst, and Settings); when served by FastAPI, open them at `/static/pages/<page>.html`.

`run_demo.py` creates the workspace, generates and loads the data, runs the SQL checks and analysis, trains the churn and payment-delinquency models, calls the agent for every insight type and writes a PDF report under `storage/`.

### Deploying on Render

1. Push this project to GitHub.
2. Create a cloud MySQL database (for example TiDB Cloud Serverless or Aiven) and copy its SQLAlchemy connection URL.
3. In Render, select **New +** → **Blueprint** and connect the repository. Render reads `render.yaml` automatically.
4. When prompted, set the `DATABASE_URL` environment variable to your cloud MySQL URL. Do not use `localhost` and do not commit `.env` or the password.
5. Deploy the service, then open its Render URL and visit `/health`.

The app is `render.yaml`'s web service, starts on Render's assigned `$PORT`, and stores uploaded files/models under the ephemeral `/tmp` directory. Free Render instances may sleep and lose ephemeral files when restarted, so models and reports should not be treated as permanent on the free plan. Use external object storage before production.

### MySQL connection

```bash
# MySQL 8 or MariaDB 10.6+; mysql-connector-python is installed from requirements.txt
mysql -uroot -p -e "CREATE DATABASE business_insights; CREATE USER 'insightforge'@'localhost' IDENTIFIED BY 'choose-a-password'; GRANT ALL ON business_insights.* TO 'insightforge'@'localhost';"
# Set DATABASE_URL=mysql+mysqlconnector://insightforge:<your-password>@localhost:3306/business_insights in .env
python scripts/run_demo.py
```

To repair the configured local application account without putting the MySQL administrator password in a command or chat, set a MySQL `DATABASE_URL` in `.env` and run `python scripts/setup_mysql_access.py`. The helper asks for the administrator password in a hidden terminal prompt, resets the password for the existing configured MySQL user, grants it access to the existing configured database, verifies the connection, and writes the generated app password back to `.env`. It does not create a new MySQL user or database. It requires a MySQL administrator account with permission to alter users and grant privileges.

Tables and views are created automatically. The DDL is also exported in `schema/schema_mysql.sql` and `schema/schema_sqlite.sql` (`python scripts/export_schema.py`). All SQL is written to run unchanged on MySQL/MariaDB and SQLite; it was tested on both (MariaDB 10.11 and SQLite 3.45).

### Connecting the real Snwolley Agent API Toolkit

Set these in `.env` (never in code):

```
SNWOLLEY_MODE=live
SNWOLLEY_BASE_URL=https://<host provided by Npontu>
SNWOLLEY_API_KEY=<key provided by Npontu>
SNWOLLEY_AGENT_ID=<optional>
SNWOLLEY_CHAT_PATH=/v1/agent/chat           # endpoint path
SNWOLLEY_RESPONSE_PATH=output               # where the answer text is in the JSON, e.g. choices.0.message.content
```

The client sends `{"messages": [...system, user...], "agent_id", "temperature", "max_tokens", "metadata"}` with `Authorization: Bearer <key>`, retries on 429/5xx with back-off, and times out after `SNWOLLEY_TIMEOUT_S`. The toolkit's specification was not available while building this, so the request and response mapping is isolated in two small methods (`_payload` and `_extract` in `insightforge/agent/client.py`); adapting to the real contract is a change there and in `.env` only. Until a key is configured, **mock mode** uses a deterministic template writer with the same request/response contract so the whole product can be demonstrated and tested. If the live call fails, the app shows a clearly labelled template answer (switch off with `SNWOLLEY_FALLBACK_TO_MOCK=false`).

### REST API

```bash
export INSIGHTFORGE_API_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
uvicorn api.main:app --port 8000
curl -H "Authorization: Bearer $INSIGHTFORGE_API_TOKEN" "localhost:8000/v1/workspaces/1/predictions?tier=High&limit=20"
```

Endpoints: `GET /health`, `GET /v1/workspaces/{id}/model`, `GET /v1/workspaces/{id}/predictions`, `POST /v1/workspaces/{id}/insights` (`{"task": "ask", "question": "..."}`). Interactive docs at `/docs`.

### Tests and notebooks

```bash
pip install -r requirements.txt
pytest -q                                            # 29 tests: data generation, auth, SQL, pipeline, AI guardrails, live-client contract
python scripts/build_notebooks.py && jupyter nbconvert --to notebook --execute --inplace notebooks/*.ipynb
```

## What is where

| Requirement | Where |
|---|---|
| Business sign-up / login / workspace | `insightforge/auth.py`, app sign-up tab and *Overview* page |
| Choose or define the business problem | `insightforge/usecases/*.yaml` (registry), app *Model* page; custom CSV targets via `custom_use_case` |
| Synthetic data + schema | `insightforge/datagen/telco.py`, `scripts/generate_data.py`, `data/generated/*.csv` + `manifest.json`, `insightforge/schema.py`, `schema/*.sql`, `docs/schema.md` |
| SQL: data-quality checks | `insightforge/sql/dq_checks.sql` (14 named checks) |
| SQL: cleaning layer | `insightforge/sql/views.sql` (silver views; `ROW_NUMBER()` de-duplication, standardisation, PII removal) |
| SQL: business analysis | `insightforge/sql/analysis.sql` (CTEs, `LEAD`/`LAG`, moving-average windows, conditional aggregation, subqueries), `insightforge/sql/model_insights.sql` |
| SQL: features and labels | `insightforge/sql/features/`, `insightforge/sql/labels/` |
| Analysis and findings | `notebooks/01_sql_analysis.ipynb`, app *Data quality & SQL* page |
| ML pipeline | `insightforge/ml/pipeline.py`, `insightforge/ml/features.py`, `notebooks/02_ml_pipeline.ipynb` |
| Monitoring | `insightforge/ml/monitoring.py` (PSI), app *Monitoring* page, `docs/architecture.md` |
| Snwolley integration | `insightforge/agent/` (`client.py`, `context.py`, `guardrails.py`, `service.py`, `mock_writer.py`) |
| Dashboard and report | `frontend/`, `api/`, `insightforge/reporting/`; sample report `docs/business_report.pdf` |
| Architecture note | `docs/architecture.md` / `docs/architecture_note.pdf` |
| REST API | `api/main.py` |

## How the pieces work

**Data.** The generator simulates 8,000 customers month by month (January 2023 to September 2026): usage, dropped calls, support tickets with CSAT, monthly bills paid on time, late or not at all, and a churn hazard driven by contract, tenure, autopay, price sensitivity and accumulated bad experiences. Two market events are built in - a 15% prepaid price rise in October 2025 and a competitor promotion in Greater Accra from April to July 2026 - and leavers usually cut their usage before going. Realistic errors are then injected (duplicate registrations, inconsistent region labels, missing values, double-posted bills, negative or impossible meter readings, out-of-range CSAT, status/date inconsistencies). Same seed, same bytes.

**SQL.** Raw tables keep data as received. Fourteen data-quality checks quantify every problem. Silver views fix them and drop personal data. Business questions (KPIs, monthly churn trend, regional churn, churn by contract/tenure/payment/support/usage trend, cancellation reasons, support quality) and the model's feature and label sets are all SQL, parameterised by snapshot date.

**Machine learning.** Target: a customer active at the snapshot cancels within 90 days. Features use only information available at the snapshot (no leakage). Training uses four earlier quarterly snapshots and testing uses the latest fully observed one (out-of-time), which mimics real use. Candidates: a no-skill baseline, logistic regression, random forest and gradient boosting, selected on PR-AUC because churners are about 7% of customers. Outputs: comparison table, lift/capture curve, ROC, permutation-importance drivers with direction, a per-customer "why flagged" list, a retention-campaign simulation against random targeting, current scores with risk tiers and revenue at risk, and a PSI drift baseline.

Results on the default data (seed 42): gradient boosting ROC-AUC 0.78 and PR-AUC 0.34 versus 0.07 for the baseline; contacting the riskiest 15% reaches 54% of churners (3.6x random). The strongest drivers are falling voice and data usage, month-to-month contracts, short tenure and no autopay.

**AI augmentation.** The Snwolley agent is called from inside the workflow, never as a detached chatbot: a data-quality brief on the SQL page, a "what changed" KPI explanation, a model explanation, recommended actions for the flagged segments, an executive summary for the report, and grounded Q&A over the workspace's results. Controls: context packs contain only aggregates (no rows, names, phones, e-mails or IDs) and are scanned before sending; the prompt separates data from instructions; every number in the answer is matched back to the context and unmatched figures are flagged; personal data in the output blocks it; overclaiming is flagged; everything is logged in `ai_outputs` with its verification status, which is shown next to each output in the app and in the PDF.

## Assumptions

- The business is a subscription operator with one subscription per customer; churn means the subscription ends.
- Campaign economics (15% contact capacity, GHS 15 per contact, GHS 60 incentive, 30% save rate, 12-month value) are illustrative and set in `insightforge/usecases/churn_90d.yaml`.
- The Snwolley toolkit is reachable over HTTPS with bearer-token authentication and returns JSON; the exact field names are configurable.
- Amounts are in Ghana cedis (GHS).

## Limitations

- The data is synthetic. Patterns are realistic by design but the model's accuracy says more about the simulation than about any real operator.
- The mock agent is template-based; it demonstrates the contract and guardrails, not generative quality. Live outputs depend on the toolkit.
- The number-grounding check verifies figures, not reasoning: a sentence can quote correct numbers and still draw a weak conclusion, which is why outputs are labelled for human review.
- Browser sessions are stored in HTTP-only cookies for the prototype; production should replace them with a hardened session or authentication service. The REST API uses a single service token rather than per-workspace tokens.
- Uploaded CSVs get profiling and modelling but not the relational analysis that the telco schema gets.
- No hyper-parameter tuning; models use sensible defaults.

## With more time

- Calibrate save rates and costs with a randomised control-group pilot, then optimise the contact threshold for profit.
- Hyper-parameter search with time-series cross-validation, probability calibration, and SHAP explanations per customer.
- Scheduled monthly scoring and outcome back-filling (Airflow/dbt), with performance tracked automatically as labels arrive.
- Event streaming (Kafka) for near-real-time risk updates, Grafana dashboards for operations, Elasticsearch for searching AI outputs and tickets.
- Per-workspace API keys, role-based access in the app, row-level security in the database.
- A field-mapping step so businesses can map their own relational tables to the canonical schema instead of uploading a flat file.

## Project layout

```
api/                 FastAPI service
frontend/            HTML, CSS, and JavaScript web interface
data/generated/      generated CSVs + manifest (seed 42)
docs/                architecture note, schema/ERD, sample business report
insightforge/        the platform package
  agent/             Snwolley client, context builder, guardrails, service, mock writer
  datagen/           synthetic data generator
  ml/                pipeline, feature engineering, drift monitoring
  reporting/         charts and PDF report
  sql/               views, data-quality checks, analysis, features, labels, model insights
  usecases/          use-case registry (YAML)
notebooks/           01 SQL analysis, 02 ML pipeline (executed)
schema/              exported DDL for MySQL and SQLite
scripts/             run_demo, generate_data, export_schema, build_notebooks, docs builders
tests/               pytest suite
```
