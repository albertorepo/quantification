from os.path import basename

import dispy
import numpy as np

import functools

from quantification.utils.errors import ClusterException
from quantification.utils.validation import split


def setup(data_file):
    global X, y
    import numpy as np
    with open(data_file, 'rb') as fh:
        data = np.load(fh)
        X = data['X']
        y = data['y']
    return 0


def wrapper(clf, train, test, pos_class):
    from sklearn.metrics import confusion_matrix
    import numpy as np
    mask = (y[train] == pos_class)
    y_bin_train = np.ones(y[train].shape, dtype=np.int)
    y_bin_train[~mask] = 0
    clf.fit(X[train,], y_bin_train)

    mask = (y[test] == pos_class)
    y_bin_test = np.ones(y[test].shape, dtype=np.int)
    y_bin_test[~mask] = 0

    return confusion_matrix(y_bin_test, clf.predict(X[test]))


def cleanup():
    global X, y
    del X, y


def cv_confusion_matrix(clf, X, pos_class, data_file, folds=50):
    cv_iter = split(X, folds)
    cms = []
    cluster = dispy.SharedJobCluster(wrapper,
                                     depends=[data_file],
                                     reentrant=True,
                                     setup=functools.partial(setup, basename(data_file)),
                                     cleanup=cleanup,
                                     scheduler_node='dhcp015.aic.uniovi.es')
    try:
        jobs = []
        for train, test in cv_iter:
            job = cluster.submit(clf, train, test, pos_class)
            jobs.append(job)
        cluster.wait()
        for job in jobs:
            if job.exception:
                raise ClusterException(job.exception + job.ip_addr)
            cms.append(job.result)
    except KeyboardInterrupt:
        cluster.close()
    finally:
        cluster.print_status()
        cluster.close()
    return np.array(cms)
