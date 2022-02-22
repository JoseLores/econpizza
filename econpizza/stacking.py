#!/bin/python
# -*- coding: utf-8 -*-

import os
import jax
import time
import numpy as np
from .shooting import find_path_linear
from grgrlib.jaxed import newton_jax

# set number of cores for XLA
os.environ["XLA_FLAGS"] = f"--xla_force_host_platform_device_count={os.cpu_count()}"


def find_stack(
    model,
    x0=None,
    shock=None,
    init_path=None,
    horizon=50,
    tol=None,
    maxit=None,
    use_linear_guess=True,
    use_linear_endpoint=None,
    parallel=False,
    verbose=True,
):

    st = time.time()

    stst = np.array(list(model["stst"].values()))
    nvars = len(model["variables"])
    func = model["func"]
    pars = np.array(list(model["parameters"].values()))
    shocks = model.get("shocks") or ()

    if tol is None:
        tol = 1e-8
    if maxit is None:
        maxit = 30

    ncores = os.cpu_count()
    if shock is None and (horizon - 1) % ncores:
        horizon += ncores - (horizon - 1) % ncores
    elif shock is not None and (horizon - 2) % ncores:
        horizon += ncores - (horizon - 2) % ncores

    x0 = np.array(list(x0)) if x0 is not None else stst
    x = np.ones((horizon + 1, nvars)) * stst
    x[0] = x0

    x_init, x_lin = find_path_linear(
        model, shock, horizon, x, use_linear_guess)

    if use_linear_endpoint is None:
        use_linear_endpoint = False if x_lin is None else True
    elif use_linear_endpoint and x_lin is None:
        print("(find_path_stacked:) Linear solution for the endpoint not available")
        use_linear_endpoint = False

    if init_path is not None:
        x_init[1: len(init_path)] = init_path[1:]

    zshock = np.zeros(len(shocks))
    tshock = np.copy(zshock)
    if shock is not None:
        tshock[shocks.index(shock[0])] = shock[1]

    endpoint = x_lin[-1] if use_linear_endpoint else stst

    if parallel:
        pfunc = jax.pmap(lambda x0, x1, x2: func(
            x0, x1, x2, stst, zshock, pars), in_axes=2)
        nshpe = (nvars, int((horizon-1-bool(shock))/ncores), ncores)
    else:
        def pfunc(x0, x1, x2): return func(x0, x1, x2, stst, zshock, pars)
        nshpe = (nvars, horizon-1-bool(shock))

    def stacked_func(x):

        if shock is None:
            X = jax.numpy.vstack(
                (x0, x.reshape((horizon - 1, nvars)), endpoint)).T

            out = pfunc(X[:, :-2].reshape(nshpe), X[:, 1:-
                        1].reshape(nshpe), X[:, 2:].reshape(nshpe)).flatten()
            out = pfunc(X[:, :-2].reshape(nshpe),
                        X[:, 1:-1].reshape(nshpe), X[:, 2:].reshape(nshpe))
        else:
            X = jax.numpy.vstack((x.reshape((horizon - 1, nvars)), endpoint)).T

            out_1st = func(x0, X[:, 0], X[:, 1], stst, tshock, pars)
            out_rst = pfunc(X[:, :-2].reshape(nshpe),
                            X[:, 1:-1].reshape(nshpe), X[:, 2:].reshape(nshpe))
            out = jax.numpy.hstack((out_1st, out_rst.flatten()))

        return out.flatten()

    if parallel:
        stacked_func = stacked_func
    else:
        stacked_func = jax.jit(stacked_func)

    if verbose:
        print("(find_path_stacked:) Solving stack (size: %s)..." %
              (horizon*nvars))

    res = newton_jax(
        stacked_func, x_init[1:-1].flatten(), maxit, tol, True, verbose)

    err = np.abs(res['fun']).max()
    x[1:-1] = res['x'].reshape((horizon - 1, nvars))

    mess = res['message']
    if err > tol:
        mess += " Max error is %1.2e." % np.abs(stacked_func(res['x'])).max()

    if verbose:
        duration = np.round(time.time() - st, 3)
        print("(find_path_stacked:) Stacking done after %s seconds. " %
              duration + mess)

    return x, x_lin, not res['success']
