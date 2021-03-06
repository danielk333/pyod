#!/usr/bin/env python

'''

'''
import os
import pathlib

from pyod import RadarPair
from pyod import Orekit
from pyod import MCMCLeastSquares, OptimizeLeastSquares
from pyod import SourceCollection
from pyod import SourcePath
from pyod import PosteriorParameters
import pyod.plot as plots
from pyod.datetime import mjd2npdt
from pyod.coordinates import geodetic2ecef
from pyod.sources import RadarTracklet

import numpy as np
import matplotlib.pyplot as plt
from mpi4py import MPI
comm = MPI.COMM_WORLD

#reproducibility and "MPI" reasons
np.random.seed(1238764587)

orekit_data = '/home/danielk/IRF/IRF_GITLAB/orekit_build/orekit-data-master.zip'
results_data = str('.' / pathlib.Path(__file__).parents[0] / 'tmp_data' / 'mcmc_prior_update_results.h5')
init_data = str('.' / pathlib.Path(__file__).parents[0] / 'tmp_data' / 'mcmc_prior_update_init.h5')

state0 = np.array([-7100297.113,-3897715.442,18568433.707,86.771,-3407.231,2961.571])
t = np.linspace(0,1800/(3600*24),num=20)
mjd0 = 54952.08
dates = mjd2npdt(mjd0 + t)
params = dict(A= 0.1, m = 1.0)

steps = int(1e4)

r_err = 0.5e3
v_err = 0.1e3

ski_ecef = geodetic2ecef(69.34023844, 20.313166, 0.0)
kar_ecef = geodetic2ecef(68.463862, 22.458859, 0.0)
kai_ecef = geodetic2ecef(68.148205, 19.769894, 0.0)

# rx_list = [ski_ecef, kar_ecef, kai_ecef]
rx_list = [ski_ecef]

variables = ['x', 'y', 'z', 'vx', 'vy', 'vz']
dtype = [(name, 'float64') for name in variables]
state0_named = np.empty((1,), dtype=dtype)
true_state = np.empty((1,), dtype=dtype)
start_err = [5e3]*3 + [1e2]*3

step_arr = np.array([1e3,1e3,1e3,1e1,1e1,1e1], dtype=np.float64)
step = np.empty((1,), dtype=dtype)
for ind, name in enumerate(variables):
    state0_named[name] = state0[ind] + np.random.randn(1)*start_err[ind]
    true_state[name] = state0[ind]
    step[name] = step_arr[ind]

if os.path.isfile(results_data):
    results = PosteriorParameters.load_h5(results_data)
    init_results = PosteriorParameters.load_h5(init_data)
else:
    prop = RadarTracklet(
        orekit_data = orekit_data, 
        settings=dict(
            in_frame='ITRF',
            out_frame='ITRF',
            drag_force=False,
            radiation_pressure=False,
        )
    )

    source_data = []

    for rx in rx_list:
        data = dict(
            date = dates,
            date0 = mjd2npdt(mjd0),
            params = params,
            tx_ecef = ski_ecef,
            rx_ecef = rx,
        )
        radar = RadarPair(data, prop)
        sim_data = radar.evaluate(state0)

        radar_data = np.empty((len(t),), dtype=RadarTracklet.dtype)
        radar_data['date'] = dates
        radar_data['r'] = sim_data['r'] + np.random.randn(len(t))*r_err
        radar_data['v'] = sim_data['v'] + np.random.randn(len(t))*v_err
        radar_data['r_sd'] = np.full((len(t),), r_err, dtype=np.float64)
        radar_data['v_sd'] = np.full((len(t),), v_err, dtype=np.float64)

        source_data.append({
                'data': radar_data,
                'meta': dict(
                    tx_ecef = ski_ecef,
                    rx_ecef = rx,
                ),
                'index': 1,
            }
        )


    prior = [
        dict(
            variables = variables,
            distribution = 'multivariate_normal',
            params = dict(
                mean = state0,
                cov = np.diag(start_err)**2,
            ),
        ),
    ]

    paths = SourcePath.from_list(source_data, 'ram')

    sources = SourceCollection(paths = paths)
    if comm.rank == 0:
        sources.details()

    input_data_state = {
        'sources': sources,
        'Model': RadarPair,
        'date0': mjd2npdt(mjd0),
        'params': params,
    }

    post_init = OptimizeLeastSquares(
        data = input_data_state,
        variables = variables,
        start = state0_named,
        prior = prior,
        propagator = prop,
        method = 'Nelder-Mead',
        options = dict(
            maxiter = 10000,
            disp = False,
            xatol = 1e-3,
        ),
    )

    post_init.run()

    if comm.rank == 0:
        post_init.results.save(init_data)

    post = MCMCLeastSquares(
        data = input_data_state,
        variables = variables,
        start = post_init.results.MAP,
        prior = prior,
        propagator = prop,
        method = 'SCAM',
        method_options = dict(
            accept_max = 0.5,
            accept_min = 0.3,
            adapt_interval = 500,
        ),
        steps = steps,
        step = step,
        tune = 500,
    )

    post.run()

    if comm.rank != 0:
        exit()
    else:
        post.results.save(results_data)

    results = post.results

    plots.orbits(post, true=true_state)

    plots.residuals(post, [state0_named, true_state,results.MAP], ['Start', 'True', 'MAP'], ['-b', '-r', '-g'], absolute=False)
    plots.residuals(post, [state0_named, true_state,results.MAP], ['Start', 'True', 'MAP'], ['-b', '-r', '-g'], absolute=True)

    plt.show()

    exit()


print(results)

print('Prior error:')
for var in variables:
    print('{:<3}: {:.3f}'.format(var, (state0_named[var][0] - true_state[var][0])*1e-3))

print('Start error:')
for var in variables:
    print('{:<3}: {:.3f}'.format(var, (init_results.MAP[var][0] - true_state[var][0])*1e-3))

print('Posterior error:')
for var in variables:
    print('{:<3}: {:.3f}'.format(var, (results.MAP[var][0] - true_state[var][0])*1e-3))


print('Relative error change:')
for var in variables:
    pri_err_ = state0_named[var][0] - true_state[var][0]
    post_err_ = results.MAP[var][0] - true_state[var][0]
    print('{:<3}: {:.3f} %'.format(var, post_err_/pri_err_*100.0))


plots.autocorrelation(results, max_k=steps)
plots.trace(results, reference=true_state)
plots.scatter_trace(results, reference=true_state)

plt.show()
