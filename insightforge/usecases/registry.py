"""Use-case registry: business problems are configuration, not code."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import USECASE_DIR

DEFAULT_BUSINESS = {
    "action": "Targeted intervention",
    "target_top_pct": 0.15,
    "contact_cost_ghs": 10.0,
    "offer_cost_ghs": 0.0,
    "save_rate": 0.25,
    "value_months": 12,
}


@dataclass
class UseCase:
    id: str
    name: str
    description: str
    task_type: str  # classification | regression
    dataset_sources: list[str]
    features_sql: str | None = None
    label_sql: str | None = None
    feature_transform: str | None = None
    horizon_days: int = 90
    positive_label: str = "Positive outcome"
    outcome_noun: str = "outcome"
    entity_label: str = "record"
    n_train_snapshots: int = 4
    snapshot_step_months: int = 3
    value_column: str | None = None
    value_label: str | None = None
    business: dict = field(default_factory=dict)
    feature_labels: dict = field(default_factory=dict)
    # custom (uploaded CSV) use cases
    custom_table: str | None = None
    target_column: str | None = None
    id_column: str | None = None

    def __post_init__(self) -> None:
        if self.task_type not in {"classification", "regression"}:
            raise ValueError(f"Unsupported task_type {self.task_type!r}")
        self.business = {**DEFAULT_BUSINESS, **(self.business or {})}

    @property
    def is_snapshot_based(self) -> bool:
        return self.features_sql is not None and self.label_sql is not None

    def label(self, feature: str) -> str:
        return self.feature_labels.get(feature, feature.replace("_", " ").capitalize())


def load_use_cases(directory: Path = USECASE_DIR) -> dict[str, UseCase]:
    cases = {}
    for path in sorted(directory.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text())
        uc = UseCase(**spec)
        cases[uc.id] = uc
    return cases


def use_cases_for_source(source: str) -> list[UseCase]:
    return [uc for uc in load_use_cases().values() if source in uc.dataset_sources]


def custom_use_case(dataset: dict, target_column: str, task_type: str, id_column: str | None = None,
                    name: str | None = None) -> UseCase:
    """Build a use case on the fly for a business's own uploaded table."""
    return UseCase(
        id=f"custom_ds{dataset['id']}_{target_column}"[:80],
        name=name or f"Predict '{target_column}'",
        description=f"Custom model predicting '{target_column}' from the uploaded dataset '{dataset['name']}'.",
        task_type=task_type,
        dataset_sources=["csv_upload"],
        custom_table=dataset["table_name"],
        target_column=target_column,
        id_column=id_column,
        positive_label=f"{target_column} = positive class",
        outcome_noun=target_column,
    )
