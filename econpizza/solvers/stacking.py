# -*- coding: utf-8 -*-

import os
import jax
import time
import jax.numpy as jnp
from grgrjax import newton_jax_jit, val_and_jacrev
from ..parser.build_functions import build_aggr_het_agent_funcs, get_stst_derivatives
from ..utilities.jacobian import get_stst_jacobian, get_jac_and_value_sliced
from ..utilities.newton import newton_for_jvp, newton_for_banded_jac


def find_path_stacking(
    model,
    shock=None,
    x0=None,
    horizon=300,
    use_solid_solver=False,
    verbose=True,
    raise_errors=True,
    **newton_args
):
    """Find the expected trajectory given an initial state.

    Parameters
    ----------
    model : dict
        model dict or PizzaModel instance
    shock : tuple, optional
        shock in period 0 as in `(shock_name_as_str, shock_size)`
    x0 : array, optional
        initial state
    horizon : int, optional
        number of periods until the system is assumed to be back in the steady state. Defaults to 300
    verbose : bool, optional
        degree of verbosity. 0/`False` is silent
    raise_errors : bool, optional
        whether to raise errors as exceptions, or just inform about them. Defaults to `True`
    newton_args : optional
        any additional arguments to be passed on to the solver

    Returns
    -------
    x : array
        array of the trajectory
    flag : bool
        returns False if the solver was successful, else True
    """

    st = time.time()

    # get variables
    stst = jnp.array(list(model["stst"].values()))
    nvars = len(model["variables"])
    pars = jnp.array(list(model["parameters"].values()))
    shocks = model.get("shocks") or ()

    # get initial guess
    x0 = jnp.array(list(x0)) if x0 is not None else stst
    x_stst = jnp.ones((horizon + 1, nvars)) * stst
    x_init = x_stst.at[0].set(x0)

    # deal with shocks if any
    shock_series = jnp.zeros((horizon-1, len(shocks)))
    if shock is not None:
        try:
            shock_series = shock_series.at[0,
                                           shocks.index(shock[0])].set(shock[1])
        except ValueError:
            raise ValueError(f"Shock '{shock[0]}' is not defined.")

    if not model.get('distributions'):

        if model['new_model_horizon'] != horizon:
            # get transition function
            func_eqns = model['context']["func_eqns"]
            jav_func_eqns = val_and_jacrev(func_eqns, (0, 1, 2))
            jav_func_eqns_partial = jax.tree_util.Partial(
                jav_func_eqns, XSS=stst, pars=pars, distributions=[], decisions_outputs=[])
            model['jav_func'] = jav_func_eqns_partial
            # mark as compiled
            model['new_model_horizon'] = horizon

        # actual newton iterations
        jav_func_eqns_partial = model['jav_func']
        x_out, flag, mess = newton_for_banded_jac(
            jav_func_eqns_partial, nvars, horizon, x_init, shock_series, verbose, **newton_args)

    else:
        if model['new_model_horizon'] != horizon:
            # get derivatives via AD and compile functions
            zero_shocks = jnp.zeros_like(shock_series).T
            build_aggr_het_agent_funcs(
                model, nvars, pars, stst, zero_shocks, horizon)

            if not use_solid_solver:
                # get steady state partial jacobians
                derivatives = get_stst_derivatives(
                    model, nvars, pars, stst, x_stst, zero_shocks, horizon, verbose)
                # accumulate steady stat jacobian
                get_stst_jacobian(model, derivatives, horizon, nvars, verbose)

            # mark as compiled
            model['new_model_horizon'] = horizon

        # get jvp function and steady state jacobian
        jvp_partial = jax.tree_util.Partial(
            model['context']['jvp_func'], x0=x0, shocks=shock_series.T)
        if not use_solid_solver:
            jacobian = model['jac_factorized']
            # actual newton iterations
            x, flag, mess = newton_for_jvp(
                jvp_partial, jacobian, x_init, verbose, **newton_args)
        else:
            # define function returning value and jacobian calculated in slices
            value_and_jac_func = get_jac_and_value_sliced(
                (horizon-1)*nvars, jvp_partial, newton_args)
            x, _, _, flag = newton_jax_jit(
                value_and_jac_func, x_init[1:-1].flatten(), **newton_args)
            mess = ''
        x_out = x_init.at[1:-1].set(x.reshape((horizon - 1, nvars)))

    # some informative print messages
    duration = time.time() - st
    result = 'done' if not flag else 'FAILED'
    mess = f"(find_path:) Stacking {result} ({duration:1.3f}s). " + mess
    if flag and raise_errors:
        raise Exception(mess)
    elif verbose:
        print(mess)

    return x_out, flag
