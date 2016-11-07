import numpy as np
import six
from abc import ABCMeta, abstractmethod

import time
from sklearn.linear_model import LogisticRegression

from quantification.base import BasicModel


class BaseClassifyAndCountModel(six.with_metaclass(ABCMeta, BasicModel)):
    """Base class for C&C Models"""

    @abstractmethod
    def fit(self, X, y):
        """Fit model."""

    @abstractmethod
    def predict(self, X):
        """Predict using the classifier model"""


class ClassifyAndCount(BaseClassifyAndCountModel):
    """
    Ordinary classify and count method.

    Parameters
    ----------

    copy_X : boolean, optional, default True
        If True, X will be copied; else, it may be overwritten.
    """

    def __init__(self, estimator_class=None, copy_X=True, estimator_params=tuple()):
        self.estimator_class = estimator_class
        self.copy_X = copy_X
        self.estimator_params = estimator_params

    def _validate_estimator(self, default):
        """Check the estimator."""
        if self.estimator_class is not None:
            self.estimator = self.estimator_class
        else:
            self.estimator = default

        if self.estimator is None:
            raise ValueError('estimator cannot be None')

    def _make_estimator(self):
        self._validate_estimator(default=LogisticRegression())

        self.estimator.set_params(**dict((p, getattr(self, p))
                                         for p in self.estimator_params))

    def fit(self, X, y):

        self._make_estimator()
        self.estimator.fit(X, y)
        self._labels = np.unique(y)
        return self

    def predict(self, X):
        predictions = self.estimator.predict(X)
        counts = np.bincount(predictions)
        prevalence_count, prevalence_idx = np.max(counts), np.argmax(counts)
        return self._labels[prevalence_idx], prevalence_count/float(np.sum(counts))

if __name__ == '__main__':

    cc = ClassifyAndCount()
    from sklearn.datasets import load_iris
    X = []
    y = []
    X, y = load_iris(return_X_y=True)

    cc.fit(X, y)
    print cc.predict(X)

