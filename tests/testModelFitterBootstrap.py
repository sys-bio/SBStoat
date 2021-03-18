# -*- coding: utf-8 -*-
"""
Created on Aug 19, 2020

@author: hsauro
@author: joseph-hellerstein
"""

import SBstoat
from SBstoat import _modelFitterBootstrap as mfb
from SBstoat.modelStudy import ModelStudy
from SBstoat import logs
from SBstoat.namedTimeseries import NamedTimeseries
from tests import _testHelpers as th
from SBstoat.observationSynthesizer import  \
      ObservationSynthesizerRandomErrors
from tests import _testConstants as tcn

import matplotlib
import numpy as np
import os
import time
import unittest

#matplotlib.use('TkAgg')



def remove(ffile):
    if os.path.isfile(ffile):
        os.remove(ffile)

IGNORE_TEST = True
IS_PLOT = True
TIMESERIES = th.getTimeseries()
DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(DIR, "testModelFitterBootstrap.log")
LOGGER = logs.Logger()
if IGNORE_TEST:
    # Write log to std output
    FITTER = th.getFitter(cls=mfb.ModelFitterBootstrap)
else:
    FITTER = th.getFitter(cls=mfb.ModelFitterBootstrap, logger=LOGGER)
FITTER.fitModel()
NUM_ITERATION = 10
FILE_SERIALIZE = os.path.join(DIR, "modelFitterBootstrap.pcl")
FILES = [FILE_SERIALIZE]
MEAN_UNIFORM = 0.5  # Mean of uniform distribution
STD_UNIFORM = np.sqrt(1.0/12)  # Standard deviation of uniform

remove(LOG_FILE)  # Clean log file on each run



class TestModelFitterBootstrap(unittest.TestCase):

    def setUp(self):
        if IGNORE_TEST:
            return
        self._init()

    def _init(self):
        self._remove()
        self.timeseries = TIMESERIES
        self.fitter = FITTER
        self.fitter.bootstrapResult = None

    def tearDown(self):
        self._remove()

    def _remove(self):
        for ffile in FILES:
            remove(ffile)

    def testRunBootstrap(self):
        if IGNORE_TEST:
            return
        self._init()
        NUM_ITERATION = 10
        MAX_DIFF = 4
        arguments = mfb._Arguments(self.fitter,
              synthesizerClass=ObservationSynthesizerRandomErrors,
              std=0.01)
        arguments.numIteration = NUM_ITERATION
        bootstrapResult = mfb._runBootstrap(arguments)
        self.assertEqual(bootstrapResult.numIteration, NUM_ITERATION)
        trues = [len(v)==NUM_ITERATION for _, v in
              bootstrapResult.parameterDct.items()]
        self.assertTrue(all(trues))
        # Test not too far from true values
        trues = [np.abs(np.mean(v) - th.PARAMETER_DCT[p]) <= MAX_DIFF
              for p, v in bootstrapResult.parameterDct.items()]
        self.assertTrue(all(trues))

    def checkParameterValues(self):
        dct = self.fitter.params.valuesdict()
        self.assertEqual(len(dct), len(self.fitter.parametersToFit))
        #
        for value in dct.values():
            self.assertTrue(isinstance(value, float))
        return dct

    def testGetMeanParameters(self):
        if IGNORE_TEST:
            return
        _ = self.checkParameterValues()
        #
        self.fitter.bootstrap(numIteration=5)
        _ = self.checkParameterValues()

    def testBoostrapTimeMultiprocessing(self):
        return
        if IGNORE_TEST:
            return
        print("\n")
        def timeIt(maxProcess):
            startTime = time.time()
            self.fitter.bootstrap(numIteration=10000,
                  reportInterval=1000, maxProcess=maxProcess)
            elapsed_time = time.time() - startTime
            print("%s processes: %3.2f" % (str(maxProcess), elapsed_time))
        #
        timeIt(None)
        timeIt(1)
        timeIt(2)
        timeIt(4)

    def testBootstrapSequential(self):
        if IGNORE_TEST:
            return
        self._init()
        self.fitter.bootstrap(numIteration=5,
              maxProcess=1,
              serializePath=FILE_SERIALIZE, isParallel=False)
        NUM_STD = 10
        result = self.fitter.bootstrapResult
        for p in self.fitter.parametersToFit:
            isLowerOk = result.parameterMeanDct[p]  \
                  - NUM_STD*result.parameterStdDct[p]  \
                  < th.PARAMETER_DCT[p]
            isUpperOk = result.parameterMeanDct[p]  \
                  + NUM_STD*result.parameterStdDct[p]  \
                  > th.PARAMETER_DCT[p]
            self.assertTrue(isLowerOk)
            self.assertTrue(isUpperOk)
        self.assertIsNotNone(self.fitter.bootstrapResult)
        #
        fitter = mfb.ModelFitterBootstrap.deserialize(FILE_SERIALIZE)
        self.assertIsNotNone(fitter.bootstrapResult)

    def testBoostrapParallel(self):
        # TESTING
        self._init()
        numIteration = 50
        self.fitter.bootstrap(numIteration=numIteration, isParallel=True)
        fitterLow = th.getFitter(cls=mfb.ModelFitterBootstrap,
            logger=LOGGER)
        import pdb; pdb.set_trace()
        # Filters more and so lower std
        fitterLow.bootstrap(numIteration=numIteration, filterSL=0.5)
        #
        stdLows = fitterLow.bootstrapResult.parameterStdDct.values()
        stdHighs = self.fitter.bootstrapResult.parameterStdDct.values()
        trues = [l <= h for l,h in zip(stdLows, stdHighs)]
        self.assertGreater(sum(trues), len(trues)*0.6)
        #
        meanHighs = self.fitter.bootstrapResult.parameterMeanDct.values()
        meanLows = fitterLow.bootstrapResult.parameterMeanDct.values()
        trues = [np.abs(l - h) < 0.5 for l,h in zip(meanLows, meanHighs)]
        if tcn.IGNORE_ACCURACY:
            return
        self.assertTrue(sum(trues) > len(trues)*0.6)

    def testGetParameter(self):
        if IGNORE_TEST:
            return
        self._init()
        self.fitter.bootstrap()
        NUM_STD = 10
        result = self.fitter.bootstrapResult
        for p in self.fitter.parametersToFit:
            isLowerOk = result.parameterMeanDct[p]  \
                  - NUM_STD*result.parameterStdDct[p]  \
                  < th.PARAMETER_DCT[p]
            isUpperOk = result.parameterMeanDct[p]  \
                  + NUM_STD*result.parameterStdDct[p]  \
                  > th.PARAMETER_DCT[p]
            self.assertTrue(isLowerOk)
            self.assertTrue(isUpperOk)
        self.assertIsNotNone(self.fitter.bootstrapResult)

    def testGetParameter1(self):
        if IGNORE_TEST:
            return
        # Smoke test
        self.fitter.bootstrap(numIteration=3)
        _ = self.fitter.getParameterMeans()
        _ = self.fitter.getParameterStds()

    def testFittedStd(self):
        if IGNORE_TEST:
            return
        #
        self.fitter.bootstrap(numIteration=3)
        stds = self.fitter.bootstrapResult.fittedStatistic.stdTS.flatten()
        for std in stds:
            self.assertTrue(isinstance(std, float))

    def testBootstrap3(self):
        if IGNORE_TEST:
            return
        self.fitter.bootstrap(numIteration=50,
              synthesizerClass=ObservationSynthesizerRandomErrors,
              reportInterval=10, maxProcess=2, std=0.02)
        result = self.fitter.bootstrapResult
        self.assertTrue(result is not None)

    def mkTimeSeries(self, values, name):
        numPoint = len(values)
        arr =  np.array([range(numPoint), values])
        arr = np.resize(arr, (2, numPoint))
        arr = arr.transpose()
        return NamedTimeseries(array=arr, colnames=["time", name])

    def runVirus(self, values):
        ANTIMONY_MODEL  = '''
            // Equations
            E1: T -> E ; beta*T*V ; // Target cells to exposed
            E2: E -> I ; kappa*E ;  // Exposed cells to infected
            E3: -> V ; p*I ;        // Virus production by infected cells
            E4: V -> ; c*V ;        // Virus clearance
            E5: I -> ; delta*I      // Death of infected cells

            // Parameters - from the Influenza article,

            beta = 3.2e-5;  // rate of transition of target(T) to exposed(E) cells, in units of 1/[V] * 1/day
            kappa = 4.0;    // rate of transition from exposed(E) to infected(I) cells, in units of 1/day
            delta = 5.2;    // rate of death of infected cells(I), in units of 1/day
            p = 4.6e-2;     // rate virus(V) producion by infected cells(I), in units of [V]/day
            c = 5.2;        // rate of virus clearance, in units of 1/day

            // Initial conditions
            T = 4E+8 // estimate of the total number of susceptible epithelial cells
                     // in upper respiratory tract)
            E = 0
            I = 0
            V = 0.75 // the dose of virus in TCID50 in Influenza experiment; could be V=0 and I = 20 instead for a natural infection

            // Computed values
            log10V := log10(V)

        '''
        dataSource = self.mkTimeSeries(values, "log10V")
        parametersToFit = [
              SBstoat.Parameter("beta", lower=0, upper=10e-5, value=3.2e-5),
              SBstoat.Parameter("kappa", lower=0,  upper=10, value=4.0),
              SBstoat.Parameter("delta", lower=0,  upper=10, value=5.2),
              SBstoat.Parameter("p", lower=0,  upper=1, value=4.6e-2),
              SBstoat.Parameter("c", lower=0,  upper=10, value=5.2),
              ]
        if IGNORE_TEST:
            logger = logs.Logger(logLevel=logs.LEVEL_MAX)
        else:
            logger = LOGGER
        study = ModelStudy(ANTIMONY_MODEL, [dataSource],
                    parametersToFit=parametersToFit,
                    selectedColumns=["log10V"],
                    doSerialize=False, useSerialized=False,
                    logger=logger)
        study.bootstrap(numIteration=10)
        fitter = list(study.fitterDct.values())[0]
        fitter = study.fitterDct["src_1"]
        for name in [p.name for p in parametersToFit]:
            if fitter.bootstrapResult is not None:
                value = fitter.bootstrapResult.params.valuesdict()[name]
                self.assertIsNotNone(value)

    def testBootstrapErrorOnException(self):
        if IGNORE_TEST:
            return
        values =  [3.5, 5.5, 6.5, 5.5, 3.5, 4.0, 0.0]
        self.runVirus(values)

    def testNanBug(self):
        if IGNORE_TEST:
            return
        values =  [np.nan, np.nan, np.nan, 7.880300e+00, 3.990000e+00,
                   2.496500e-15, 1.246900e+00, 3.142100e+00,
                   1.845400e+00, 2.693300e+00, 1.122200e+00, 2.020000e+00,
                   2.496500e-15, 3.167100e+00,
                   -2.493800e-02, -2.493800e-02, 2.496500e-15,
                   2.493800e-02, -2.493800e-02, 2.496500e-15,
                   2.496500e-15, 2.496500e-15, np.nan, np.nan, np.nan,
                   np.nan, np.nan, np.nan]
        self.runVirus(values)


if __name__ == '__main__':
    if IS_PLOT:
        matplotlib.use('TkAgg')
    unittest.main()
