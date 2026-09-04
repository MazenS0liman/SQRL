"""Sklearn-compatible tabular model classes.

The classes in this module intentionally inherit from their sklearn
counterparts. This keeps them compatible with ``clone``, ``cross_validate``
and joblib while giving the application a stable model namespace.
"""

from sklearn.ensemble import (
	AdaBoostClassifier,
	AdaBoostRegressor,
	ExtraTreesClassifier,
	ExtraTreesRegressor,
	GradientBoostingClassifier,
	GradientBoostingRegressor,
	RandomForestClassifier,
	RandomForestRegressor,
)
from sklearn.linear_model import Lasso, LogisticRegression, Ridge
from sklearn.neighbors import KNeighborsClassifier, KNeighborsRegressor
from sklearn.svm import SVC, SVR
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor


class LogisticRegressionModel(LogisticRegression):
	pass


class RidgeModel(Ridge):
	pass


class LassoModel(Lasso):
	pass


class RandomForestClassifierModel(RandomForestClassifier):
	pass


class RandomForestRegressorModel(RandomForestRegressor):
	pass


class GradientBoostingClassifierModel(GradientBoostingClassifier):
	pass


class GradientBoostingRegressorModel(GradientBoostingRegressor):
	pass


class ExtraTreesClassifierModel(ExtraTreesClassifier):
	pass


class ExtraTreesRegressorModel(ExtraTreesRegressor):
	pass


class AdaBoostClassifierModel(AdaBoostClassifier):
	pass


class AdaBoostRegressorModel(AdaBoostRegressor):
	pass


class DecisionTreeClassifierModel(DecisionTreeClassifier):
	pass


class DecisionTreeRegressorModel(DecisionTreeRegressor):
	pass


class SVCModel(SVC):
	pass


class SVRModel(SVR):
	pass


class KNeighborsClassifierModel(KNeighborsClassifier):
	pass


class KNeighborsRegressorModel(KNeighborsRegressor):
	pass


try:
	from xgboost import XGBClassifier, XGBRegressor
except ImportError:
	XGBClassifierModel = None
	XGBRegressorModel = None
else:
	class XGBClassifierModel(XGBClassifier):
		pass

	class XGBRegressorModel(XGBRegressor):
		pass


try:
	from lightgbm import LGBMClassifier, LGBMRegressor
except ImportError:
	LGBMClassifierModel = None
	LGBMRegressorModel = None
else:
	class LGBMClassifierModel(LGBMClassifier):
		pass

	class LGBMRegressorModel(LGBMRegressor):
		pass


__all__ = [
	"LogisticRegressionModel",
	"RidgeModel",
	"LassoModel",
	"RandomForestClassifierModel",
	"RandomForestRegressorModel",
	"GradientBoostingClassifierModel",
	"GradientBoostingRegressorModel",
	"ExtraTreesClassifierModel",
	"ExtraTreesRegressorModel",
	"AdaBoostClassifierModel",
	"AdaBoostRegressorModel",
	"DecisionTreeClassifierModel",
	"DecisionTreeRegressorModel",
	"SVCModel",
	"SVRModel",
	"KNeighborsClassifierModel",
	"KNeighborsRegressorModel",
	"XGBClassifierModel",
	"XGBRegressorModel",
	"LGBMClassifierModel",
	"LGBMRegressorModel",
]



