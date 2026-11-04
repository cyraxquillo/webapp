import logging
import re
import warnings
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
import sklearn

from api.models import Employee, Evaluation

ARTIFACT_DIR = Path(__file__).resolve().parent
MODEL_SOURCE = "api/ml/rf_model.pkl"
logger = logging.getLogger(__name__)
_model_version = sklearn.__version__
_load_error = None
rf_model: Any | None = None
le_department: Any | None = None
le_target: Any | None = None
feature_columns: Any | None = None


def _load_artifact(filename: str) -> Any:
    path = ARTIFACT_DIR / filename
    return joblib.load(path) if path.is_file() else None


try:
    with warnings.catch_warnings(record=True) as load_warnings:
        warnings.simplefilter("always")
        rf_model = _load_artifact("rf_model.pkl")
        le_department = _load_artifact("le_department.pkl")
        le_target = _load_artifact("le_target.pkl")
        feature_columns = _load_artifact("feature_columns.pkl")
except (OSError, ValueError, ImportError) as error:
    rf_model = le_department = le_target = feature_columns = None
    _load_error = str(error)
    logger.exception(
        "Unable to load prediction model artifacts from %s",
        ARTIFACT_DIR,
    )
else:
    trained_versions: set[str] = set()
    for warning_record in load_warnings:
        warning_text = str(warning_record.message)
        version_match = re.search(
            r"from version ([\d.]+) when using version",
            warning_text,
        )
        if version_match:
            trained_versions.add(version_match.group(1))
    if trained_versions:
        _model_version = sorted(trained_versions)[0]
        logger.warning(
            "Model artifacts were trained with scikit-learn %s; runtime is %s",
            _model_version,
            sklearn.__version__,
        )
    if rf_model is None or le_department is None or le_target is None or feature_columns is None:
        _load_error = "One or more required .pkl artifacts are missing"
        logger.error(
            "Prediction model artifacts are incomplete in %s: %s",
            ARTIFACT_DIR, _load_error,
        )
    else:
        model_feature_names = list(getattr(rf_model, "feature_names_in_", feature_columns))
        logger.info(
            "Loaded prediction model: source=%s type=%s n_estimators=%s "
            "trained_sklearn_version=%s runtime_sklearn_version=%s "
            "n_features=%s feature_names=%s",
            (ARTIFACT_DIR / "rf_model.pkl").resolve(),
            f"{type(rf_model).__module__}.{type(rf_model).__qualname__}",
            getattr(rf_model, "n_estimators", None),
            _model_version,
            sklearn.__version__,
            getattr(rf_model, "n_features_in_", len(model_feature_names)),
            model_feature_names,
        )


MODEL_VERSION = _model_version


class ModelUnavailableError(RuntimeError):
    """Raised when trained model artifacts are unavailable."""


class UnknownDepartmentError(ValueError):
    """Raised when the model does not recognize an employee's department."""


def loaded_model_artifacts() -> tuple[Any, Any, Any, Any]:
    if rf_model is None or le_department is None or le_target is None or feature_columns is None:
        reason = f" ({_load_error})" if _load_error else ""
        raise ModelUnavailableError(
            f"Random Forest model artifacts are unavailable{reason}; add the trained .pkl files to api/ml"
        )
    return rf_model, le_department, le_target, feature_columns


def supported_department_names() -> tuple[str, ...]:
    _, department_encoder, _, _ = loaded_model_artifacts()
    return tuple(str(name) for name in department_encoder.classes_)


def build_feature_vector(employee: Employee, latest_evaluation: Evaluation) -> pd.DataFrame:
    model, department_encoder, _, columns = loaded_model_artifacts()

    try:
        department_encoded = int(department_encoder.transform([employee.department.name])[0])
    except ValueError as error:
        recognized = ", ".join(supported_department_names())
        raise UnknownDepartmentError(
            f"Department '{employee.department.name}' is not supported by the trained "
            f"model. Supported departments: {recognized}."
        ) from error

    attendance_rate = employee.attendance_rate if employee.attendance_rate is not None else 0.0
    if attendance_rate > 1:
        attendance_rate /= 100

    def to_five_point_rating(score: float) -> float:
        rating = score / 20 if score > 5 else score
        return max(1.0, min(5.0, float(rating)))

    values: dict[str, float | int] = {
        "attendance_rate": attendance_rate,
        "productivity_score": latest_evaluation.productivity_score,
        "quality_of_work": to_five_point_rating(latest_evaluation.quality_score),
        "teamwork_score": to_five_point_rating(latest_evaluation.teamwork_score),
        "communication_score": to_five_point_rating(latest_evaluation.communication_score),
        "tenure_months": employee.tenure_months,
        "training_hours": employee.training_hours if employee.training_hours is not None else 0.0,
        "department_encoded": department_encoded,
    }
    ordered_columns = list(getattr(model, "feature_names_in_", columns))
    if list(columns) != ordered_columns:
        raise ValueError("The feature columns artifact does not match the trained Random Forest model")
    missing = [column for column in ordered_columns if column not in values]
    if missing:
        raise ValueError(f"The trained model expects unsupported features: {', '.join(missing)}")
    return pd.DataFrame([[values[column] for column in ordered_columns]], columns=ordered_columns)


def predict_performance(employee: Employee, latest_evaluation: Evaluation) -> dict[str, Any]:
    model, _, target_encoder, _ = loaded_model_artifacts()
    feature_vector = build_feature_vector(employee, latest_evaluation)
    predicted_value = model.predict(feature_vector)[0]
    category = target_encoder.inverse_transform([predicted_value])[0]
    probabilities = model.predict_proba(feature_vector)[0]
    confidence = round(float(max(probabilities)), 4)
    feature_importances = {
        column: float(importance)
        for column, importance in zip(feature_vector.columns, model.feature_importances_)
    }
    return {
        "predicted_category": str(category),
        "confidence_score": confidence,
        "feature_importances": feature_importances,
        "model_source": MODEL_SOURCE,
        "model_version": MODEL_VERSION,
    }
