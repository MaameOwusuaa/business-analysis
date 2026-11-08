"""Relational schema (SQLAlchemy Core) - portable across MySQL 8 / MariaDB and SQLite.

Two groups of tables:

* Platform tables - multi-tenant SaaS metadata (workspaces, users, datasets,
  model runs, predictions, AI outputs, audit log).
* Business ("raw / bronze") tables - the tenant's own operational data. Every
  row carries ``workspace_id`` so tenants are isolated by a mandatory filter.
  Raw tables deliberately accept imperfect data (duplicates, bad values); the
  cleaned "silver" layer is a set of SQL views (see ``sql/views.sql``).
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import mysql

metadata = MetaData()

# Large JSON/text payloads: TEXT on SQLite, LONGTEXT on MySQL.
LongText = Text().with_variant(mysql.LONGTEXT(), "mysql").with_variant(mysql.LONGTEXT(), "mariadb")

# --------------------------------------------------------------------------- platform
workspaces = Table(
    "workspaces",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False),
    Column("industry", String(60), nullable=False),
    Column("country", String(60), nullable=False, server_default="Ghana"),
    Column("objective", Text),
    Column("active_use_case", String(80)),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("workspace_id", Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("email", String(255), nullable=False, unique=True),
    Column("full_name", String(120), nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("role", String(20), nullable=False, server_default="owner"),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

datasets = Table(
    "datasets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("workspace_id", Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(120), nullable=False),
    Column("source", String(40), nullable=False),  # synthetic_telco | csv_upload
    Column("table_name", String(80)),  # for csv uploads
    Column("seed", Integer),
    Column("as_of_date", Date),
    Column("row_counts", Text),  # JSON
    Column("status", String(20), nullable=False, server_default="ready"),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

model_runs = Table(
    "model_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("workspace_id", Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("dataset_id", Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    Column("use_case_id", String(80), nullable=False),
    Column("task_type", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("best_model", String(60)),
    Column("metrics", LongText),  # JSON - headline metrics of selected model
    Column("details", LongText),  # JSON - comparison, drivers, monitoring, campaign simulation
    Column("artifact_path", String(255)),
    Column("score_snapshot", Date),
    Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

predictions = Table(
    "predictions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, ForeignKey("model_runs.id", ondelete="CASCADE"), nullable=False),
    Column("workspace_id", Integer, nullable=False),
    Column("entity_id", String(40), nullable=False),
    Column("score", Float, nullable=False),
    Column("risk_tier", String(10), nullable=False),
    Column("value", Float),
    Column("top_reasons", Text),
    Index("ix_predictions_run", "run_id", "risk_tier"),
)

ai_outputs = Table(
    "ai_outputs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("workspace_id", Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("run_id", Integer, ForeignKey("model_runs.id", ondelete="SET NULL")),
    Column("task", String(40), nullable=False),
    Column("provider", String(20), nullable=False),
    Column("prompt_hash", String(64), nullable=False),
    Column("context_json", LongText, nullable=False),
    Column("question", Text),
    Column("output_text", LongText),
    Column("validation", Text),  # JSON
    Column("status", String(20), nullable=False),  # verified | needs_review | blocked | error
    Column("latency_ms", Integer),
    Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("workspace_id", Integer),
    Column("user_id", Integer),
    Column("action", String(60), nullable=False),
    Column("detail", Text),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

# --------------------------------------------------------------------------- business (raw)
plans = Table(
    "plans",
    metadata,
    Column("workspace_id", Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True),
    Column("plan_id", String(10), primary_key=True),
    Column("plan_name", String(60), nullable=False),
    Column("plan_type", String(20), nullable=False),  # Prepaid | Postpaid | Fibre
    Column("monthly_fee", Float, nullable=False),  # GHS
    Column("data_gb", Float, nullable=False),
)

customers = Table(
    "customers",
    metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("workspace_id", Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
    Column("customer_id", String(12), nullable=False),
    Column("full_name", String(120)),  # PII
    Column("phone", String(20)),  # PII
    Column("email", String(160)),  # PII
    Column("gender", String(10)),
    Column("date_of_birth", Date),
    Column("region", String(40)),
    Column("city", String(60)),
    Column("segment", String(20)),  # Consumer | SME
    Column("acquisition_channel", String(30)),
    Column("signup_date", Date, nullable=False),
    UniqueConstraint("workspace_id", "customer_id", name="uq_customers_ws_cust"),
    Index("ix_customers_phone", "workspace_id", "phone"),
)

subscriptions = Table(
    "subscriptions",
    metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("workspace_id", Integer, nullable=False),
    Column("subscription_id", String(14), nullable=False),
    Column("customer_id", String(12), nullable=False),
    Column("plan_id", String(10), nullable=False),
    Column("start_date", Date, nullable=False),
    Column("end_date", Date),
    Column("status", String(12), nullable=False),
    Column("contract_type", String(20), nullable=False),
    Column("autopay", Boolean, nullable=False),
    Column("cancel_reason", String(40)),
    UniqueConstraint("workspace_id", "subscription_id", name="uq_subs_ws_sub"),
    ForeignKeyConstraint(
        ["workspace_id", "customer_id"],
        ["customers.workspace_id", "customers.customer_id"],
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "plan_id"], ["plans.workspace_id", "plans.plan_id"], ondelete="CASCADE"
    ),
    Index("ix_subs_customer", "workspace_id", "customer_id"),
)

monthly_usage = Table(
    "monthly_usage",
    metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("workspace_id", Integer, nullable=False),
    Column("usage_id", String(16), nullable=False),
    Column("subscription_id", String(14), nullable=False),
    Column("usage_month", Date, nullable=False),  # first day of month
    Column("data_gb_used", Float),
    Column("voice_minutes", Float),
    Column("sms_count", Integer),
    Column("dropped_calls", Integer),
    ForeignKeyConstraint(
        ["workspace_id", "subscription_id"],
        ["subscriptions.workspace_id", "subscriptions.subscription_id"],
        ondelete="CASCADE",
    ),
    Index("ix_usage_sub_month", "workspace_id", "subscription_id", "usage_month"),
)

payments = Table(
    "payments",
    metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("workspace_id", Integer, nullable=False),
    Column("payment_id", String(16), nullable=False),
    Column("subscription_id", String(14), nullable=False),
    Column("billing_month", Date, nullable=False),
    Column("due_date", Date, nullable=False),
    Column("amount_due", Float, nullable=False),
    Column("amount_paid", Float),
    Column("paid_date", Date),
    Column("payment_method", String(20)),
    ForeignKeyConstraint(
        ["workspace_id", "subscription_id"],
        ["subscriptions.workspace_id", "subscriptions.subscription_id"],
        ondelete="CASCADE",
    ),
    Index("ix_payments_sub_month", "workspace_id", "subscription_id", "billing_month"),
)

support_tickets = Table(
    "support_tickets",
    metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("workspace_id", Integer, nullable=False),
    Column("ticket_id", String(16), nullable=False),
    Column("customer_id", String(12), nullable=False),
    Column("opened_date", Date, nullable=False),
    Column("category", String(20), nullable=False),
    Column("resolution_hours", Float),
    Column("csat_score", Integer),
    ForeignKeyConstraint(
        ["workspace_id", "customer_id"],
        ["customers.workspace_id", "customers.customer_id"],
        ondelete="CASCADE",
    ),
    Index("ix_tickets_customer", "workspace_id", "customer_id", "opened_date"),
)

# Insert order (parents first) and delete order (children first).
BUSINESS_TABLES = [plans, customers, subscriptions, monthly_usage, payments, support_tickets]
