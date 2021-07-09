#!/bin/python
# -*- coding: utf-8 -*-

import yaml
import re
import os
import tempfile
import numpy as np
from numpy import log, exp, sqrt
from numba import njit
from .steady_state import solve_stst, solve_linear


def eval_strs(vdict):

    if vdict is None:
        return None

    for v in vdict:
        if isinstance(vdict[v], str):
            vdict[v] = eval(vdict[v])

    return vdict


def parse(mfile, raise_errors=True, verbose=True):
    """parse yaml file. Warning: contains filthy code (eg. globals, exec, ...)"""

    f = open(mfile)
    mtxt = f.read()
    f.close()

    mtxt = mtxt.replace("^", "**")
    mtxt = re.sub(r"@ ?\n", " ", mtxt)
    model = yaml.safe_load(mtxt)

    defs = model.get("definitions")
    if defs is not None:
        for d in defs:
            exec(d, globals())

    evars = model["variables"]
    shocks = model.get("shocks") or ()
    par = eval_strs(model["parameters"])
    eqns = model["equations"]

    initvals = model["steady_state"].get("init_guesses")
    stst = eval_strs(model["steady_state"].get("fixed_values"))

    model["stst"] = stst

    if len(evars) != len(eqns):
        raise Exception(
            "Model has %s variables but %s equations." % (len(evars), len(eqns))
        )

    # collect number of foward and backward looking variables
    model["no_fwd"] = sum(var + "Prime" in "".join(model["equations"]) for var in evars)
    model["no_bwd"] = sum(var + "Lag" in "".join(model["equations"]) for var in evars)

    # check if each variable is defined in time t (only defining xSS does not give a valid root)
    for v in evars:
        v_in_eqns = [
            v in e.replace(v + "SS", "").replace(v + "Lag", "").replace(v + "Prime", "")
            for e in eqns
        ]
        if not np.any(v_in_eqns):
            raise Exception("Variable `%s` is not defined for time t." % v)

    # start compiling F
    for i, eqn in enumerate(eqns):
        if "=" in eqn:
            lhs, rhs = eqn.split("=")
            eqns[i] = "root_container[%s] = " % i + lhs + "- (" + rhs + ")"
        else:
            eqns[i] = "root_container[%s] = " % i + eqn

    eqns_aux = model.get("aux_equations")

    if not shocks:
        shock_str = ""
    elif len(shocks) > 1:
        shock_str = ", ".join(shocks) + " = shocks"
    else:
        shock_str = shocks[0] + " = shocks[0]"

    func_str = """def func_raw(XLag, X, XPrime, XSS, shocks, pars):\n %s\n %s\n %s\n %s\n %s\n %s\n root_container=np.empty(%s)\n %s\n %s\n return root_container""" % (
        ", ".join(v + "Lag" for v in evars) + " = XLag",
        ", ".join(evars) + " = X",
        ", ".join(v + "Prime" for v in evars) + " = XPrime",
        ", ".join(v + "SS" for v in evars) + " = XSS",
        shock_str,
        ", ".join(par.keys()) + " = pars",
        str(len(evars)),
        "\n ".join(eqns_aux) if eqns_aux else "",
        "\n ".join(eqns),
    )

    # get inital values to test the function
    init = np.ones(len(evars)) * 1.1

    if isinstance(init, dict):
        for v in initvals:
            init[evars.index(v)] = initvals[v]

    for v in stst:
        init[evars.index(v)] = stst[v]

    model["init"] = init

    # use a termporary file to get nice debug traces if things go wrong
    tmpf = tempfile.NamedTemporaryFile(mode="w", delete=False)

    tmpf.write(func_str)
    tmpf.close()

    exec(compile(open(tmpf.name).read(), tmpf.name, "exec"), globals())

    # try if function works on initvals. If it does, jit-compile it and remove tempfile
    func_raw(
        init, init, init, init, np.zeros(len(shocks)), np.array(list(par.values()))
    )
    func = njit(func_raw)
    os.unlink(tmpf.name)

    model["func"] = func
    model["func_str"] = func_str
    model["root_options"] = {}

    if verbose:
        print("(parse:) Parsing done.")

    solve_stst(model, raise_error=raise_errors, verbose=verbose)
    solve_linear(model, raise_error=raise_errors, verbose=verbose)

    return model
