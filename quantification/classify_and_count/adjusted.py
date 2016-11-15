from copy import deepcopy

import numpy as np

from quantification.classify_and_count.base import BaseClassifyAndCountModel, predict_wrapper_per_clf
from quantification.utils.base import merge, mean_of_non_zero
from quantification.utils.parallelism import ClusterParallel
from quantification.utils.validation import split, cross_validation_score


# TODO: Make a common class and extend it

class BinaryAdjustedCount(BaseClassifyAndCountModel):
    def __init__(self):
        super(BinaryAdjustedCount, self).__init__()
        self.fpr_, self.tpr_ = None, None

    def _predict(self, X):
        parallel = ClusterParallel(predict_wrapper_per_clf, self.estimators_, {'X': X}, local=True)  # TODO: Fix this
        predictions = parallel.retrieve()
        maj = np.apply_along_axis(lambda x: np.argmax(np.bincount(x)),
                                  axis=0,
                                  arr=predictions.astype('int'))
        freq = np.bincount(maj, minlength=len(self.classes_))
        relative_freq = freq / float(np.sum(freq))
        adjusted = self._adjust(relative_freq)
        return adjusted

    def fit(self, X, y, local=False):
        if not isinstance(X, list):
            clf = self._fit(X, y)
            self.tpr_, self.fpr_ = self._performance(X, y, clf, local)
            self.estimators_.append(clf)
        else:
            X, y = np.array(X), np.array(y)
            for sample in y:
                if len(np.unique(sample)) != 2:
                    raise ValueError('Number of classes must be 2 for a binary quantification problem')
            split_iter = split(X, len(X))
            parallel = ClusterParallel(fit_and_performance_wrapper, split_iter, {'X': X, 'y': y,
                                                                                 'quantifier': self, 'local': local},
                                       local=True)  # TODO: Fix this
            clfs, tpr, fpr = zip(*parallel.retrieve())
            self.estimators_.extend(clfs)
            self.tpr_ = np.mean(tpr)
            self.fpr_ = np.mean(fpr)
        self.classes_ = set(label for clf in self.estimators_ for label in clf.classes_)
        return self

    def _performance(self, X, y, clf, local, cv=3):
        confusion_matrix = np.mean(
            cross_validation_score(clf, X, y, cv, score="confusion_matrix", local=local), 0)
        tpr = confusion_matrix[0, 0] / float(confusion_matrix[0, 0] + confusion_matrix[1, 0])
        fpr = confusion_matrix[0, 1] / float(confusion_matrix[0, 1] + confusion_matrix[1, 1])
        return tpr, fpr

    def fit_and_performance(self, perf, train, X, y, local):
        clf = self._fit(X[train[0]], y[train[0]])
        tpr, fpr = self._performance(np.concatenate(X[perf]), np.concatenate(y[perf]), clf, local)
        return clf, tpr, fpr

    def _adjust(self, prob):
        return (prob - self.fpr_) / float(self.tpr_ - self.fpr_)


class MulticlassAdjustedCount(BaseClassifyAndCountModel):
    def __init__(self):
        super(MulticlassAdjustedCount, self).__init__()
        self.conditional_prob_ = None

    def _predict(self, X):
        parallel = ClusterParallel(predict_wrapper_per_clf, self.estimators_, {'X': X}, local=True)  # TODO: Fix this
        predictions = parallel.retrieve()
        maj = np.apply_along_axis(lambda x: np.argmax(np.bincount(x)),
                                  axis=0,
                                  arr=predictions.astype('int'))
        freq = np.bincount(maj, minlength=len(self.classes_))
        relative_freq = freq / float(np.sum(freq))

        def solve_adjustments(matrix, coefs):
            idxs = np.where(coefs != 0)[0]
            adjustment = np.linalg.lstsq(np.matrix.transpose(matrix[idxs[:, np.newaxis], idxs]),
                                         coefs[idxs])
            adjusted = deepcopy(coefs)
            adjusted[idxs] = adjustment[0]
            return coefs

        parallel = ClusterParallel(solve_adjustments, self.conditional_prob_, {'coefs': relative_freq}, local=True)
        adjusted = parallel.retrieve()
        adjusted = np.mean(adjusted, axis=0)
        return adjusted

    def _solve_adjustments(self, matrix, coefs):
        return np.linalg.solve(np.matrix.transpose(matrix), coefs)

    def fit(self, X, y, local=False):
        if not isinstance(X, list):
            clf = self._fit(X, y)
            self.estimators_.append(clf)
            self.classes_ = list(set(label for clf in self.estimators_ for label in clf.classes_))
            self.conditional_prob_ = self._performance(X, y, clf, local)
        else:
            X, y = np.array(X), np.array(y)
            split_iter = list(split(X, len(X)))

            parallel = ClusterParallel(fit_train_wrapper, split_iter, {'X': X, 'y': y, 'quantifier': self}, local=local)
            clfs = parallel.retrieve()
            self.estimators_.extend(clfs)

            self.classes_ = list(set(label for clf in self.estimators_ for label in clf.classes_))
            parallel = ClusterParallel(performance_wrapper, merge(split_iter, self.estimators_),
                                       {'X': X, 'y': y, 'quantifier': self,
                                        'local': True},
                                       local=local)  # TODO: Fix this
            self.conditional_prob_ = parallel.retrieve()
        return self

    def _performance(self, X, y, clf, local, cv=3):
        n_classes = len(self.classes_)
        confusion_matrix = np.mean(
            cross_validation_score(clf, X, y, cv, score="confusion_matrix", local=local, labels=self.classes_),
            0)
        conditional_prob = np.empty((n_classes, n_classes))
        for i in range(n_classes):
            if np.all(confusion_matrix[i] == 0.0):
                continue
            conditional_prob[i] = confusion_matrix[i] / np.sum(confusion_matrix[i])
        return conditional_prob

    def fit_and_performance(self, perf, train, X, y, local):
        clf = self._fit(X[train[0]], y[train[0]])
        conditional_prob = self._performance(np.concatenate(X[perf]), np.concatenate(y[perf]), clf, local)
        return clf, conditional_prob


def performance_wrapper(train, perf, clf, X, y, quantifier, local):
    return quantifier._performance(np.concatenate(X[perf]), np.concatenate(y[perf]), clf, local)


def fit_train_wrapper(train, perf, X, y, quantifier):
    return quantifier._fit(X[train[0]], y[train[0]])
