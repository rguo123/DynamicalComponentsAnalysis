import numpy as np
import scipy.stats

import torch
dtype = torch.float
device = torch.device("cuda:0")
torch.set_default_tensor_type(torch.cuda.DoubleTensor)



def calc_cov_from_cross_cov_mats(cross_cov_mats):
    """Calculates the N*num_lags-by-N*num_lags spatiotemporal covariance matrix
    based on num_lags N-by-N cross-covariance matrices. This function is
    'autograd-safe' since is does not use array assignment, only Python list appending.
    Parameters
    ----------
    cross_cov_mats : np.ndarray, shape (num_lags, N, N)
        Cross-covariance matrices: cross_cov_mats[dt] is the
        cross-covariance between X(t) and X(t+dt), where each
        of X(t) and X(t+dt) is a N-dimensional vector.
    Returns
    -------
    cov : np.ndarray, shape (N*num_lags, N*num_lags)
        Big covariance matrix, stationary in time by construction.
    """

    N = cross_cov_mats[0].shape[0]
    num_lags = len(cross_cov_mats)

    cross_cov_mats_repeated = []
    for i in range(num_lags):
        for j in range(num_lags):
            if i > j:
                cross_cov_mats_repeated.append(cross_cov_mats[abs(i-j)])
            else:
                cross_cov_mats_repeated.append(cross_cov_mats[abs(i-j)].t())

    cov_tensor = torch.reshape(torch.stack(cross_cov_mats_repeated), (num_lags, num_lags, N, N))
    cov = torch.cat([torch.cat([cov_ii_jj for cov_ii_jj in cov_ii], dim=1) for cov_ii in cov_tensor])

    return cov


def calc_pi_from_cov(cov_2T):
    """Calculates the mutual information ("predictive information"
    or "PI") between variables  {1,...,T} and {T+1,...,2*T}, which
    are jointly Gaussian with covariance matrix cov_2T.
    Parameters
    ----------
    cov_2T : np.ndarray, shape (2*T, 2*T)
        Covariance matrix.
    Returns
    -------
    PI : float
        Mutual information in bits.
    """

    half = int(cov_2T.shape[0]/2)

    cov_T = cov_2T[:half, :half]
    logdet_T = torch.logdet(cov_T)
    logdet_2T = torch.logdet(cov_2T)
    PI = (2. * logdet_T - logdet_2T) / np.log(2.)

    return PI


def calc_pi_from_cross_cov_mats(cross_cov_mats, proj=None):
    """Calculates predictive information for a spatiotemporal Gaussian
    process with num_lags-1 N-by-N cross-covariance matrices.
    Parameters
    ----------
    cross_cov_mats : np.ndarray, shape (num_lags, N, N)
        Cross-covariance matrices: cross_cov_mats[dt] is the
        cross-covariance between X(t) and X(t+dt), where each
        of X(t) and X(t+dt) is a N-dimensional vector.
    proj: np.ndarray, shape (N, d), optional
        If provided, the N-dimensional data are projected onto a d-dimensional
        basis given by the columns of proj. Then, the mutual information is
        computed for this d-dimensional timeseries.
    Returns
    -------
    PI : float
        Mutual information in bits.
    """

    T = cross_cov_mats.shape[0] // 2
    if isinstance(proj, torch.Tensor):
        d = proj.shape[1]
    else:
        d = cross_cov_mats.shape[1] #or cross_cov_mats.shape[2]

    cross_cov_mats_proj = []
    if isinstance(proj, torch.Tensor):
        for i in range(2*T):
            cross_cov = cross_cov_mats[i]
            cross_cov_proj = torch.mm(proj.t(), torch.mm(cross_cov, proj))
            cross_cov_mats_proj.append(cross_cov_proj)
    else:
        cross_cov_mats_proj = cross_cov_mats

    cov_2T = calc_cov_from_cross_cov_mats(cross_cov_mats_proj)
    PI = calc_pi_from_cov(cov_2T)

    return PI


def ortho_reg_fn(V, lambda_param):
    """Regularization term which encourages the basis vectors in the
    columns of V to be orthonormal.
    Parameters
    ----------
    V : np.ndarray, shape (N, d)
        Matrix whose columns are basis vectors.
    lambda_param : float
        Regularization hyperparameter.
    Returns
    -------
    reg_val : float
        Value of regularization function.
    """

    d = V.shape[1]
    reg_val = lambda_param * torch.sum((torch.mm(V.t(), V) - torch.eye(d, dtype=torch.float64))**2)
    return reg_val


def build_loss(cross_cov_mats, d, lambda_param=10):
    """Constructs a loss function which gives the (negative) predictive information
    in the projection of multidimensional timeseries data X onto a d-dimensional
    basis, where predictive information is computed using a stationary Gaussian
    process approximation.
    Parameters
    ----------
    X : np.ndarray, shape (# time-steps, N)
        The multidimensional time series data from which the
        mutual information is computed.
    d: int
        Number of basis vectors onto which the data X are projected.
    lambda_param : float
        Regularization hyperparameter.
    Returns
    -------
    loss : function
       Loss function which accepts a (flattened) N-by-d matrix, whose
       columns are basis vectors, and outputs the negative predictive information
       corresponding to that projection (plus regularization term).
    """

    N = cross_cov_mats.shape[1] #or cross_cov_mats.shape[2]
    def loss(V_flat):

        V = V_flat.reshape(N, d)
        reg_val = ortho_reg_fn(V, lambda_param)
        return -calc_pi_from_cross_cov_mats(cross_cov_mats, V) + reg_val

    return loss


def run_cca(cross_cov_mats, d, init="random", method="BFGS", tol=1e-6, lambda_param=10, verbose=False):
    """Runs CCA on multidimensional timeseries data X to discover a projection
    onto a d-dimensional subspace which maximizes the complexity of the d-dimensional
    dynamics.
    Parameters
    ----------
    X : np.ndarray, shape (# time-steps, N)
        The multidimensional time series data.
    T: int
        Size of time windows accross which to compute mutual information.
    d: int
        Number of basis vectors onto which the data X are projected.
    init: string
        Options: "random", "PCA"
        Method for initializing the projection matrix.
    Returns
    -------
    V_opt: np.ndarray, shape (N, d)
        Projection matrix.
    """

    loss = build_loss(cross_cov_mats, d, lambda_param=lambda_param)
    grad_loss = grad(loss)

    N = cross_cov_mats.shape[1] #or cross_cov_mats.shape[2]

    if verbose:
        def callback(V_flat):
            loss_val = loss(V_flat)
            V = V_flat.reshape((N, d))
            reg_val = ortho_reg_fn(V, lambda_param)
            loss_no_reg = loss_val - reg_val
            pi = -loss_no_reg
            print("PI = " + str(np.round(pi, 4)) + " bits, reg = " + str(np.round(reg_part, 4)))
    else:
        callback = None

    if type(init) == str:
        if init == "random":
            V_init = np.random.normal(0, 1, (N, d))
            V_init = V_init / np.sqrt(np.sum(V_init**2, axis=0))
        if init == "random_ortho":
            V_init = scipy.stats.ortho_group.rvs(N)[:, :d]
        if init == "uniform":
            V_init = np.ones((N, d))/np.sqrt(N)
            V_init = V_init + np.random.normal(0, 1e-3, V_init.shape)
    elif type(init) == np.ndarray:
        V_init = init

    opt_result = scipy.optimize.minimize(loss, V_init.flatten(), method=method, jac=grad_loss, callback=callback, tol=tol)
    V_opt_flat = opt_result["x"]
    V_opt = V_opt_flat.reshape((N, d))

    #Orhtonormalize the basis prior to returning it
    V_opt = scipy.linalg.orth(V_opt)

    return V_opt