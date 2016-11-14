from nose.tools import assert_almost_equal
import numpy as np

from quantification.classify_and_count import ProbabilisticClassifyAndCount, ProbabilisticBinaryAdjustedCount
from quantification.tests.base import ModelTestCase


class TestProbabilisticClassifyAndCount(ModelTestCase):
    def test_fit_single_sample_of_binary_data(self):
        cc = ProbabilisticClassifyAndCount()
        X = self.binary_data.data[0]
        y = self.binary_data.target[0]
        cc.fit(X, y, local=True)
        predictions = cc.predict(X, local=True)
        assert_almost_equal(np.sum(predictions), 1)

    def test_fit_ensemble_of_binary_data(self):
        cc = ProbabilisticClassifyAndCount()
        X = self.binary_data.data
        y = self.binary_data.target
        cc.fit(X, y, local=True)
        predictions = cc.predict(X, local=True)
        assert_almost_equal(np.sum(predictions), len(y))

    def test_fit_single_sample_of_multiclass_data(self):
        cc = ProbabilisticClassifyAndCount()
        X = self.multiclass_data.data[0]
        y = self.multiclass_data.target[0]
        cc.fit(X, y, local=True)
        predictions = cc.predict(X, local=True)
        assert_almost_equal(np.sum(predictions), 1)

    def test_fit_ensemble_of_multiclass_data(self):
        cc = ProbabilisticClassifyAndCount()
        X = self.multiclass_data.data
        y = self.multiclass_data.target
        cc.fit(X, y, local=True)
        predictions = cc.predict(X, local=True)
        assert_almost_equal(np.sum(predictions), len(y))


class TestProbabilisticBinaryAdjustedCount(ModelTestCase):

    def test_fit_single_sample_of_binary_data(self):
        cc = ProbabilisticBinaryAdjustedCount()
        X = self.binary_data.data[0]
        y = self.binary_data.target[0]
        cc.fit(X, y, local=True)
        predictions = cc.predict(X, local=True)
        assert_almost_equal(np.sum(predictions), 1)

    def test_fit_ensemble_of_binary_data(self):
        cc = ProbabilisticBinaryAdjustedCount()
        X = self.binary_data.data
        y = self.binary_data.target
        cc.fit(X, y, local=True)
        predictions = cc.predict(X, local=True)
        assert_almost_equal(np.sum(predictions), len(y))