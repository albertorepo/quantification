from abc import ABCMeta, abstractmethod
from itertools import chain

import numpy as np
import six
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder

from quantification.base import BasicModel
from quantification.utils.parallelism import ClusterParallel


class BaseClassifyAndCountModel(six.with_metaclass(ABCMeta, BasicModel)):
    """Base class for C&C Models"""

    def __init__(self, estimator_class=None, estimator_params=tuple()):
        self.estimator_class = estimator_class
        self.estimator_params = estimator_params
        self.estimators_ = []
        self.classes_= None

    @abstractmethod
    def _predict(self, X):
        """Predict using the classifier model"""

    @abstractmethod
    def fit(self, X, y):
        """Fit a set of models and combine them"""

    def predict(self, X, local=False):
        if not isinstance(X, list):
            return self._predict(X)

        parallel = ClusterParallel(predict_wrapper_per_sample, X, {'quantifier': self, 'local': local}, local=local)
        return parallel.retrieve().tolist()

    def _validate_estimator(self, default):
        """Check the estimator."""
        if self.estimator_class is not None:
            estimator = self.estimator_class
        else:
            estimator = default

        if estimator is None:
            raise ValueError('estimator cannot be None')
        return estimator

    def _make_estimator(self):
        estimator = self._validate_estimator(default=LogisticRegression())

        estimator.set_params(**dict((p, getattr(self, p))

                                    for p in self.estimator_params))
        return estimator

    def _fit(self, X, y):
        clf = self._make_estimator()
        clf.fit(X, y)
        return clf


class ClassifyAndCount(BaseClassifyAndCountModel):
    """
    Ordinary classify and count method.

    Parameters
    ----------

    """

    def _predict(self, X):
        parallel = ClusterParallel(predict_wrapper_per_clf, self.estimators_, {'X': X}, local=True)  # TODO: Fix this
        predictions = parallel.retrieve()

        maj = np.apply_along_axis(lambda x: np.argmax(np.bincount(x)),
                                  axis=0,
                                  arr=predictions.astype('int'))
        freq = np.bincount(maj, minlength=len(self.classes_))
        relative_freq = freq / float(np.sum(freq))
        return relative_freq

    def fit(self, X, y, local=False):
        if not isinstance(X, list):
            clf = self._fit(X, y)
            self.estimators_.append(clf)
        else:
            parallel = ClusterParallel(fit_wrapper, zip(X, y), {'quantifier': self}, local=local)
            clfs = parallel.retrieve()
            self.estimators_.extend(clfs)
        self.classes_ = set(label for clf in self.estimators_ for label in clf.classes_)
        return self


def predict_wrapper_per_sample(X, quantifier, local):
    return quantifier.predict(X, local=False)  # TODO: Fix this, please


def predict_wrapper_per_clf(clf, X):
    return clf.predict(X)


def fit_wrapper(X, y, quantifier):
    return quantifier._fit(X, y)
