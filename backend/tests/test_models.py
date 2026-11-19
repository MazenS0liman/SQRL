"""Tests for the sklearn-compatible model namespace."""
from __future__ import annotations

import numpy as np
import pytest
from sklearn.base import clone
from sklearn.datasets import make_classification, make_regression

from squirrel import models
from squirrel.models.classification import (
    AdaBoostClassifierModel,
    AdaBoostRegressorModel,
    DecisionTreeClassifierModel,
    DecisionTreeRegressorModel,
    ExtraTreesClassifierModel,
    ExtraTreesRegressorModel,
    GradientBoostingClassifierModel,
    GradientBoostingRegressorModel,
    KNeighborsClassifierModel,
    KNeighborsRegressorModel,
    LassoModel,
    LGBMClassifierModel,
    LGBMRegressorModel,
    LogisticRegressionModel,
    RandomForestClassifierModel,
    RandomForestRegressorModel,
    RidgeModel,
    SVCModel,
    SVRModel,
    XGBClassifierModel,
    XGBRegressorModel,
)


CLASSIFIERS = [
    LogisticRegressionModel,
    RandomForestClassifierModel,
    GradientBoostingClassifierModel,
    ExtraTreesClassifierModel,
    AdaBoostClassifierModel,
    DecisionTreeClassifierModel,
    KNeighborsClassifierModel,
    SVCModel,
]

REGRESSORS = [
    RidgeModel,
    LassoModel,
    RandomForestRegressorModel,
    GradientBoostingRegressorModel,
    ExtraTreesRegressorModel,
    AdaBoostRegressorModel,
    DecisionTreeRegressorModel,
    KNeighborsRegressorModel,
    SVRModel,
]


@pytest.fixture(scope="module")
def classification_data():
    return make_classification(
        n_samples=60,
        n_features=6,
        n_informative=4,
        n_redundant=0,
        n_classes=2,
        random_state=42,
    )


@pytest.fixture(scope="module")
def regression_data():
    return make_regression(
        n_samples=60,
        n_features=6,
        n_informative=4,
        noise=0.1,
        random_state=42,
    )


@pytest.mark.parametrize("model_type", CLASSIFIERS)
def test_classifier_fits_and_predicts(model_type, classification_data) -> None:
    X, y = classification_data
    model = model_type()

    model.fit(X, y)
    predictions = model.predict(X)

    assert len(predictions) == len(y)
    assert set(np.unique(predictions)) <= set(np.unique(y))
    assert isinstance(clone(model), model_type)


@pytest.mark.parametrize("model_type", REGRESSORS)
def test_regressor_fits_and_predicts(model_type, regression_data) -> None:
    X, y = regression_data
    model = model_type()

    model.fit(X, y)
    predictions = model.predict(X)

    assert len(predictions) == len(y)
    assert np.isfinite(predictions).all()
    assert isinstance(clone(model), model_type)


@pytest.mark.parametrize("model_type", CLASSIFIERS + REGRESSORS)
def test_model_classes_keep_the_sklearn_base(model_type) -> None:
    from sklearn.base import BaseEstimator

    assert issubclass(model_type, BaseEstimator)


@pytest.mark.skipif(XGBClassifierModel is None, reason="xgboost is not installed")
def test_xgboost_models_fit_and_predict(classification_data, regression_data) -> None:
    Xc, yc = classification_data
    classifier = XGBClassifierModel(n_estimators=5, max_depth=2)
    classifier.fit(Xc, yc)
    assert len(classifier.predict(Xc)) == len(yc)

    Xr, yr = regression_data
    regressor = XGBRegressorModel(n_estimators=5, max_depth=2)
    regressor.fit(Xr, yr)
    assert len(regressor.predict(Xr)) == len(yr)


@pytest.mark.skipif(LGBMClassifierModel is None, reason="lightgbm is not installed")
def test_lightgbm_models_fit_and_predict(classification_data, regression_data) -> None:
    Xc, yc = classification_data
    classifier = LGBMClassifierModel(n_estimators=5, verbose=-1)
    classifier.fit(Xc, yc)
    assert len(classifier.predict(Xc)) == len(yc)

    Xr, yr = regression_data
    regressor = LGBMRegressorModel(n_estimators=5, verbose=-1)
    regressor.fit(Xr, yr)
    assert len(regressor.predict(Xr)) == len(yr)


def test_models_package_exports_every_class() -> None:
    exported = set(models.__all__)

    for model_type in CLASSIFIERS + REGRESSORS:
        assert model_type.__name__ in exported

    # Optional libraries are exported as ``None`` when unavailable rather than
    # breaking the package import.
    assert "XGBClassifierModel" in exported
    assert "LGBMRegressorModel" in exported
