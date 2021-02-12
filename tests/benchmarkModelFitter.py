# -*- coding: utf-8 -*-
"""
Created on Tue Jul  7 14:24:09 2020

@author: hsauro
@author: joseph-hellerstein

Timing history

date         Version         numIteration    numProcess  Time (sec)
11/30/2020   1.0             10,000          5            11.29
11/30/2020   1.1             10,000          5           110.0
12/06/2020   1.1             10,000          5            18.1
02/03/2021*  1.3             10,000          5            33.6

*Using only leastsq, 100 function evaluations
"""

from SBstoat import modelFitter as mf
from SBstoat import _helpers
import SBstoat
from SBstoat import logs

import matplotlib
import numpy as np
import os
import time


IS_TEST = False
IS_PLOT = False
DIR = os.path.dirname(os.path.abspath(__file__))
BENCHMARK_PATH = os.path.join(DIR, "groundtruth_2_step_0_1.txt")
MODEL = """
    J1: S1 -> S2; k1*S1
    J2: S2 -> S3; k2*S2
   
    S1 = 1; S2 = 0; S3 = 0;
    k1 = 0; k2 = 0; 
"""
        

def main(numIteration):
    """
    Calculates the time to run iterations of the benchmark.

    Parameters
    ----------
    numIteration: int
    
    Returns
    -------
    float: time in seconds
    """
    logger = logs.Logger(logLevel=logs.LEVEL_STATUS, logPerformance=IS_TEST)
    optimizerMethod = _helpers.OptimizerMethod(SBstoat.METHOD_LEASTSQ,
          {"max_nfev": 100})
    fitter = mf.ModelFitter(MODEL, BENCHMARK_PATH,
          ["k1", "k2"], selectedColumns=['S1', 'S3'], isPlot=IS_PLOT,
          logger=logger,
          fitterMethods=[optimizerMethod],
          bootstrapMethods=[optimizerMethod],
          )
    fitter.fitModel()
    startTime = time.time()
    fitter.bootstrap(numIteration=numIteration, reportInterval=numIteration)
    elapsedTime = time.time() - startTime
    if IS_TEST:
        print(fitter.logger.formatPerformanceDF())
    fitter.plotFitAll()
    return elapsedTime
        

if __name__ == '__main__':
    if IS_PLOT:
        matplotlib.use('TkAgg')
    print("Elapsed time: %4.2f" % main(10000))
