from __future__ import print_function

import quadprog
from abc import ABCMeta, abstractmethod
from tempfile import mkstemp

import numpy as np
import math
import six
import cvxpy
from sklearn.base import BaseEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix
from sklearn.model_selection import GridSearchCV, cross_val_score, cross_val_predict
from copy import deepcopy

from sklearn.utils import check_X_y

from quantification.metrics import distributed, model_score
from quantification.metrics.model_score import fpr, tpr
from quantification.utils.base import is_pd, nearest_pd, solve_hd


class BaseClassifyAndCountModel(six.with_metaclass(ABCMeta, BaseEstimator)):
    def __init__(self, estimator_class, estimator_params, estimator_grid, grid_params, b):
        if estimator_params is None:
            estimator_params = dict()
        if estimator_grid is None:
            estimator_grid = dict()
        if grid_params is None:
            grid_params = dict()
        self.b = b
        self.estimator_class = estimator_class
        self.estimator_params = estimator_params
        self.estimator_grid = estimator_grid
        self.grid_params = grid_params

    @abstractmethod
    def fit(self, X, y):
        """Fit a sample or a set of samples and combine them"""

    @abstractmethod
    def predict(self, X):
        """Predict the prevalence"""

    def _validate_estimator(self, default, default_params, default_grid):
        """Check the estimator."""
        if self.estimator_class is not None:
            clf = self.estimator_class
        else:
            clf = default
        if self.estimator_params is not None:
            clf.set_params(**self.estimator_params)
            if not self.estimator_grid:
                estimator = clf
            else:
                estimator = GridSearchCV(estimator=self.estimator_class, param_grid=self.estimator_grid,
                                         **self.grid_params)
        else:
            clf.set_params(**default_params)
            if not self.estimator_grid:
                estimator = clf
            else:
                estimator = GridSearchCV(estimator=clf, param_grid=default_grid, verbose=11)

        if estimator is None:
            raise ValueError('estimator cannot be None')

        return estimator

    def _make_estimator(self):
        """Build the estimator"""
        estimator = self._validate_estimator(default=LogisticRegression(), default_grid={'C': [0.1, 1, 10]},
                                             default_params=dict())
        return estimator

    def _persist_data(self, X, y):
        f, path = mkstemp()
        self.X_y_path_ = path + '.npz'
        np.savez(path, X=X, y=y)


class BaseCC(BaseClassifyAndCountModel):
    """
        Multiclass Classify And Count method.

        It is meant to be trained once and be able to predict using the following methods:
            - Classify & Count
            - Adjusted Count
            - Probabilistic Classify & Count
            - Probabilistic Adjusted Count
            - HDy

        The idea is not to trained the classifiers more than once, due to the computational cost. In the training phase
        every single performance metric that is needed in predictions are computed, that is, FPR, TPR and so on.

        If you are only going to use one of the methods above, you can use the wrapper classes in this package.

        Parameters
        ----------
        b : integer, optional
            Number of bins to compute the distributions in the HDy method. If you are not going to use that method in the
            prediction phase, leave it as None. The training phase will be probably faster.

        estimator_class : object, optional
            An instance of a classifier class. It has to have fit and predict methods. It is highly advised to use one of
            the implementations in sklearn library. If it is leave as None, Logistic Regression will be used.

        estimator_params : dictionary, optional
            Additional params to initialize the classifier.

        estimator_grid : dictionary, optional
            During training phase, grid search is performed. This parameter should provided the parameters of the classifier
            that will be tested (e.g. estimator_grid={C: [0.1, 1, 10]} for Logistic Regression).

        strategy : string, optional
            Strategy to follow when aggregating.

        multiclass : string, optional
            One versus all or one vs one

        Attributes
        ----------
        estimator_class : object
            The underlying classifier+

        confusion_matrix_ : dictionary
            The confusion matrix estimated by cross-validation of the underlying classifier for each class

        tpr_ : dictionary
            True Positive Rate of the underlying classifier from the confusion matrix for each class

        fpr_ : dictionary
            False Positive Rate of the underlying classifier from the confusion matrix for each class

        tp_pa_ : dictionary
            True Positive Probability Average of the underlying classifier if it is probabilistic for each class

        fp_pa_ : dictionary
            False Positive Probability Average of the underlying classifier if it is probabilistic for each class

        train_dist_ : dictionary
            Distribution of the positive and negative samples for each bin in the training data for each class

        """

    def __init__(self, estimator_class=None, estimator_params=None, estimator_grid=None, grid_params=None, b=None,
                 strategy='macro', multiclass='ova'):
        super(BaseCC, self).__init__(estimator_class, estimator_params, estimator_grid,
                                     grid_params, b)
        self.strategy = strategy
        self.multiclass = multiclass

    def fit(self, X, y, cv=50, verbose=False, local=True):

        cv = np.min([cv, np.min(np.unique(y, return_counts=True)[1])])

        X, y = check_X_y(X, y, accept_sparse=True)

        self.classes_ = np.unique(y).tolist()
        n_classes = len(self.classes_)

        if not local:
            self._persist_data(X, y)

        if n_classes == 2:
            classes = self.classes_[1:]
        else:
            classes = self.classes_

        self.estimators_ = dict.fromkeys(classes)
        self.confusion_matrix_ = dict.fromkeys(classes)

        self.fpr_ = dict.fromkeys(classes)
        self.tpr_ = dict.fromkeys(classes)

        self.tp_pa_ = dict.fromkeys(classes)
        self.fp_pa_ = dict.fromkeys(classes)

        for pos_class in classes:
            if verbose:
                print("Class {}/{}".format(pos_class + 1, n_classes))
                print("\tFitting  classifier...")
            mask = (y == pos_class)
            y_bin = np.ones(y.shape, dtype=np.int)
            y_bin[~mask] = 0
            clf = self._make_estimator()
            clf = clf.fit(X, y_bin)
            if isinstance(clf, GridSearchCV):
                clf = clf.best_estimator_
            self.estimators_[pos_class] = deepcopy(clf)
            if verbose:
                print("\tComputing performance...")
            self._compute_performance(X, y_bin, pos_class, folds=cv, local=local, verbose=verbose)
        if self.b:
            if verbose:
                print("\tComputing distribution...")
        self._compute_distribution(X, y)

        return self

    def _compute_performance(self, X, y, pos_class, folds, local, verbose):

        if folds is None or folds == 1:
            self.tpr_[pos_class] = tpr(self.estimators_[pos_class], X, y)
            self.fpr_[pos_class] = fpr(self.estimators_[pos_class], X, y)
        else:
            fprs = cross_val_score(self.estimators_[pos_class], X, y, cv=folds, scoring=fpr)
            tprs = cross_val_score(self.estimators_[pos_class], X, y, cv=folds, scoring=tpr)
            self.tpr_[pos_class] = tprs.mean()
            self.fpr_[pos_class] = fprs.mean()

        try:
            predictions = self.estimators_[pos_class].predict_proba(X)
        except AttributeError:
            return

        self.tp_pa_[pos_class] = np.sum(predictions[y == self.estimators_[pos_class].classes_[1], 1]) / \
                                 np.sum(y == self.estimators_[pos_class].classes_[1])
        self.fp_pa_[pos_class] = np.sum(predictions[y == self.estimators_[pos_class].classes_[0], 1]) / \
                                 np.sum(y == self.estimators_[pos_class].classes_[0])

    def _compute_distribution(self, X, y):

        if not self.b:
            return

        n_classes = len(self.classes_)
        n_clfs = n_classes  # OvA
        self.train_dist_ = np.zeros((n_classes, self.b, n_clfs))

        if n_classes == 2:
            # If it is a binary problem, add the representation of the negative samples
            preds = cross_val_predict(self.estimators_[1], X, y, method="predict_proba")[:, 1]
            pos_preds = preds[y == 1]
            neg_preds = preds[y == 0]
            pos_pdf, _ = np.histogram(pos_preds, bins=self.b, range=(0., 1.))
            neg_pdf, _ = np.histogram(neg_preds, bins=self.b, range=(0., 1.))
            self.train_dist_ = np.vstack(
                [(neg_pdf / float(sum(y == 0)))[None, :, None], (pos_pdf / float(sum(y == 1)))[None, :, None]])
            self.train_dist_ = np.squeeze(self.train_dist_)
        else:
            for n_cls, cls in enumerate(self.classes_):
                for n_clf, (clf_cls, clf) in enumerate(self.estimators_.items()):
                    mask = (y == clf_cls)
                    y_bin = np.ones(y.shape, dtype=np.int)
                    y_bin[~mask] = 0
                    preds = cross_val_predict(clf, X, y_bin, method="predict_proba")[:, 1]
                    preds = preds[y==cls]
                    pdf, _ = np.histogram(preds, bins=self.b, range=(0., 1.))
                    self.train_dist_[n_cls, :, n_clf] = pdf / float(sum(y_bin))
            self.train_dist_ = self.train_dist_.reshape(n_classes, -1)

    def predict(self, X, method='cc'):
        if method == 'cc':
            return self._predict_cc(X)
        elif method == 'ac':
            return self._predict_ac(X)
        elif method == 'pcc':
            return self._predict_pcc(X)
        elif method == 'pac':
            return self._predict_pac(X)
        elif method == "hdy":
            return self._predict_hdy(X)
        else:
            raise ValueError("Invalid method %s. Choices are `cc`, `ac`, `pcc`, `pac`.", method)

    def _predict_cc(self, X):
        n_classes = len(self.classes_)
        if n_classes == 2:
            probabilities = np.zeros(1)
        else:
            probabilities = np.zeros(n_classes)

        for n, (cls, clf) in enumerate(self.estimators_.items()):
            predictions = clf.predict(X)
            freq = np.bincount(predictions, minlength=2)
            relative_freq = freq / float(np.sum(freq))
            probabilities[n] = relative_freq[1]

        if len(probabilities) < 2:
            probabilities = np.array([1 - probabilities[0], probabilities[0]])

        if np.sum(probabilities) == 0:
            return probabilities
        return probabilities / np.sum(probabilities)

    def _predict_ac(self, X):
        n_classes = len(self.classes_)
        if n_classes == 2:
            probabilities = np.zeros(1)
        else:
            probabilities = np.zeros(n_classes)
        for n, (cls, clf) in enumerate(self.estimators_.items()):
            predictions = clf.predict(X)
            freq = np.bincount(predictions, minlength=2)
            relative_freq = freq / float(np.sum(freq))
            adjusted = (relative_freq - self.fpr_[cls]) / float(self.tpr_[cls] - self.fpr_[cls])
            adjusted = np.nan_to_num(adjusted)
            probabilities[n] = np.clip(adjusted[1], 0, 1)

        if len(probabilities) < 2:
            probabilities = np.array([1 - probabilities[0], probabilities[0]])

        if np.sum(probabilities) == 0:
            return probabilities
        return probabilities / np.sum(probabilities)

    def _predict_pcc(self, X):
        n_classes = len(self.classes_)
        if n_classes == 2:
            probabilities = np.zeros(1)
        else:
            probabilities = np.zeros(n_classes)
        for n, (cls, clf) in enumerate(self.estimators_.items()):
            try:
                predictions = clf.predict_proba(X)
            except AttributeError:
                raise ValueError("Probabilistic methods like PCC or PAC cannot be used "
                                 "with hard (crisp) classifiers like %s", clf.__class__.__name__)

            p = np.mean(predictions, axis=0)
            probabilities[n] = p[1]

        if len(probabilities) < 2:
            probabilities = np.array([1 - probabilities[0], probabilities[0]])

        if np.sum(probabilities) == 0:
            return probabilities
        return probabilities / np.sum(probabilities)

    def _predict_pac(self, X):
        n_classes = len(self.classes_)
        if n_classes == 2:
            probabilities = np.zeros(1)
        else:
            probabilities = np.zeros(n_classes)
        for n, (cls, clf) in enumerate(self.estimators_.items()):
            try:
                predictions = clf.predict_proba(X)
            except AttributeError:
                raise ValueError("Probabilistic methods like PCC or PAC cannot be used "
                                 "with hard (crisp) classifiers like %s", clf.__class__.__name__)

            p = np.mean(predictions, axis=0)
            probabilities[n] = np.clip((p[1] - self.fp_pa_[cls]) / float(self.tp_pa_[cls] - self.fp_pa_[cls]), 0, 1)

        if len(probabilities) < 2:
            probabilities = np.array([1 - probabilities[0], probabilities[0]])

        if np.sum(probabilities) == 0:
            return probabilities
        return probabilities / np.sum(probabilities)

    def _predict_hdy(self, X):
        if not self.b:
            raise ValueError("If HDy predictions are in order, the quantifier must be trained with the parameter `b`")
        n_classes = len(self.classes_)

        if n_classes == 2:
            preds = self.estimators_[1].predict_proba(X)[:, 1]
            pdf, _ = np.histogram(preds, self.b, range=(0, 1))
            test_dist = pdf / float(X.shape[0])
            test_dist = np.expand_dims(test_dist, -1)


        else:
            test_dist = np.zeros((self.b, len(self.estimators_)))
            for n_clf, (clf_cls, clf) in enumerate(self.estimators_.items()):
                preds = clf.predict_proba(X)[:, 1]
                pdf, _ = np.histogram(preds, self.b, range=(0, 1))
                test_dist[:, n_clf] = pdf / float(X.shape[0])
            test_dist = test_dist.reshape(-1, 1)

        return solve_hd(self.train_dist_, test_dist, n_classes)



class CC(BaseCC):
    """
        Multiclass Classify And Count method.

        Just a wrapper to perform adjusted count without the need of every other single methods.
        The main difference with the general class is the `predict` method that enforces to use CC.
        """

    def predict(self, X, method='cc'):
        assert method == 'cc'
        return self._predict_cc(X)

    def _compute_performance(self, X, y, pos_class, folds, local, verbose):
        pass

    def _compute_distribution(self, X, y):
        pass


class AC(BaseCC):
    """
        Multiclass Adjusted Count method.

        Just a wrapper to perform adjusted count without the need of every other single methods.
        The main difference with the general class is the `predict` method that enforces to use AC.

        """

    def predict(self, X, method='ac'):
        assert method == 'ac'
        return self._predict_ac(X)

    def _compute_distribution(self, X, y):
        pass


class PCC(BaseCC):
    """
        Multiclass Probabilistic Classify And Count method.

        Just a wrapper to perform adjusted count without the need of every other single methods.
        The main difference with the general class is the `predict` method that enforces to use PCC."""

    def predict(self, X, method='pcc'):
        assert method == 'pcc'
        return self._predict_pcc(X)

    def _compute_performance(self, X, y, pos_class, folds, local, verbose):
        pass

    def _compute_distribution(self, X, y):
        pass


class PAC(BaseCC):
    """
        Multiclass Probabilistic Adjusted Count method.

        Just a wrapper to perform adjusted count without the need of every other single methods.
        The main difference with the general class is the `predict` method that enforces to use PAC.
        """

    def predict(self, X, method='pac'):
        assert method == 'pac'
        return self._predict_pac(X)

    def _compute_distribution(self, X, y):
        pass


class FriedmanAC(BaseCC):

    def predict(self, X, method='fac'):

        n_classes = len(self.classes_)

        Up = np.zeros((len(X), n_classes))
        if n_classes == 2:
            Up = self.estimators_[1].predict_proba(X)
            Up = Up / Up.sum(axis=1, keepdims=True)
            Up = (Up > self.train_prevs).astype(np.int)
        else:
            for n_clf, (clf_cls, clf) in enumerate(self.estimators_.items()):
                Up[:, n_clf] = clf.predict_proba(X)[:, 1]

            Up = Up / Up.sum(axis=1, keepdims=True)
            Up = (Up > self.train_prevs).astype(np.int)

        U = Up.mean(axis=0)

        G = self.V.T.dot(self.V)
        if not is_pd(G):
            G = nearest_pd(G)
        a = U.dot(self.V)

        C = np.vstack([- np.ones((1, n_classes)), np.eye(n_classes)]).T
        b = np.array([-1] + [0] * n_classes, dtype=np.float)
        sol = quadprog.solve_qp(G=G,
                                a=a, C=C, b=b)

        p = sol[0]

        return p

    def _compute_distribution(self, X, y):
        n_classes = len(self.classes_)
        Vp = np.zeros((len(X), n_classes))
        self.train_prevs = np.unique(y, return_counts=True)[1] / len(X)

        if n_classes == 2:
            Vp = self.estimators_[1].predict_proba(X)
            Vp = Vp / Vp.sum(axis=1, keepdims=True)
            Vp = (Vp > self.train_prevs).astype(np.int)
        else:
            for n_clf, (clf_cls, clf) in enumerate(self.estimators_.items()):
                Vp[:, n_clf] = clf.predict_proba(X)[:, 1]

            Vp = Vp / Vp.sum(axis=1, keepdims=True)
            Vp = (Vp > self.train_prevs).astype(np.int)

        self.V = np.zeros((n_classes, n_classes))

        for cls in self.classes_:
            self.V[:, cls] = Vp[y == cls].mean(axis=0)

    def _compute_performance(self, X, y, pos_class, folds, local, verbose):
        pass