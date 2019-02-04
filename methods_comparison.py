import numpy as np


class SlowFeatureAnalysis(object):
    def __init__(self, n_components):
        self.n_components = n_components
        self.coef_ = None

    def fit(self, X):
        X_stan = X - X.mean(axis=1, keepdims=True)
        uX, sX, vhX = np.linalg.svd(X_stan)
        whiten = np.diag(1./sX).dot(uX.T)
        Xw = whiten.dot(X_stan)
        Xp = np.diff(Xw, axis=1)
        up, sp, vhp = np.linalg.svd(Xp, full_matrices=False)
        proj = up.T
        self.coef_ = proj.dot(whiten)[::-1][:self.n_components]

    def transform(self, X):
        if self.coef_ is None:
            raise ValueError
        return self.coef_.dot(X)

    def fit_transform(self, X):
        self.fit(X)
        return self.coef_.dot(X)