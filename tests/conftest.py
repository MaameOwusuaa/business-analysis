import os
import sys
import tempfile
from pathlib import Path

import pytest

# Isolated storage + SQLite DB for the test session (set before importing the package).
_TMP = tempfile.mkdtemp(prefix="insightforge_tests_")
os.environ["STORAGE_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TMP) / 'test.db'}"
os.environ["SNWOLLEY_MODE"] = "mock"
os.environ["LOG_LEVEL"] = "WARNING"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session")
def workspace():
    from insightforge.auth import sign_up
    from insightforge.db import init_db
    from insightforge.ingest import load_synthetic

    init_db()
    user = sign_up("Test Telco", "Telecommunications", "Test Owner", "owner@test.example", "testpass123")
    ds_id = load_synthetic(user.workspace_id, n_customers=2000, seed=7)
    return {"user": user, "ws": user.workspace_id, "dataset_id": ds_id}


@pytest.fixture(scope="session")
def churn_run(workspace):
    from insightforge.ml.pipeline import train_use_case
    from insightforge.usecases import load_use_cases

    return train_use_case(workspace["ws"], workspace["dataset_id"], load_use_cases()["churn_90d"])
