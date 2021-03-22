# -*- coding: utf-8 -*-
"""
 Created on March 19, 2021

@author: joseph-hellerstein

Running a single thread of bootstrapping.
"""

from SBstoat._modelFitterCore import ModelFitterCore
from SBstoat._bootstrapResult import BootstrapResult
from SBstoat.timeseriesStatistic import TimeseriesStatistic
from SBstoat.observationSynthesizer import  \
      ObservationSynthesizerRandomizedResiduals
from SBstoat._parallelRunner import AbstractRunner
from SBstoat.logs import Logger

import time
import multiprocessing
import numpy as np
import sys

MAX_CHISQ_MULT = 5
PERCENTILES = [2.5, 97.55]  # Percentile for confidence limits
IS_REPORT = True
ITERATION_MULTIPLIER = 10  # Multiplier to calculate max bootsrap iterations
ITERATION_PER_PROCESS = 5  # Numer of iterations handled by a process
MAX_TRIES = 10  # Maximum number of tries to fit
MAX_ITERATION_TIME = 10.0


class RunnerArgument():
    """ Arguments passed to _runBootstrap. """

    def __init__(self, fitter,
                 numIteration:int=10,
                 synthesizerClass=ObservationSynthesizerRandomizedResiduals,
                 _loggerPrefix="",
                 **kwargs: dict):
        # Same the antimony model, not roadrunner bcause of Pickle
        self.fitter = fitter.copy(isKeepLogger=True)
        self.numIteration  = numIteration
        self.synthesizerClass = synthesizerClass
        self._loggerPrefix = _loggerPrefix
        self.kwargs = kwargs


class BootstrapRunner(AbstractRunner):

    def __init__(self, runnerArgument):
        """
        Parameters
        ----------
        runnerArgument: RunnerArgument

        Notes
        -----
        1. Uses METHOD_LEASTSQ for fitModel iterations.
        """
        super().__init__()
        #
        self.lastErr = ""
        self.fitter = runnerArgument.fitter
        self.numIteration = runnerArgument.numIteration
        self.kwargs = runnerArgument.kwargs
        self.synthesizerClass = runnerArgument.synthesizerClass
        self.logger = self.fitter.logger
        self._isDone = not self._fitInitial()
        self.columns = self.fitter.selectedColumns
        # Initializations for bootstrap loop
        if not self.isDone:
            fittedTS = self.fitter.fittedTS.subsetColumns(self.columns, isCopy=False)
            self.synthesizer = self.synthesizerClass(
                  observedTS=self.fitter.observedTS.subsetColumns(
                  self.columns, isCopy=False),
                  fittedTS=fittedTS,
                  **self.kwargs)
            self.numSuccessIteration = 0
            if self.fitter.minimizerResult is None:
                self.fitter.fitModel()
            self.baseChisq = self.fitter.minimizerResult.redchi
            self.curIteration = 0
            self.fd = self.logger.getFileDescriptor()
            self.baseFittedStatistic = TimeseriesStatistic(
                  self.fitter.observedTS.subsetColumns(
                  self.fitter.selectedColumns, isCopy=False))

    def report(self, id=None):
        if True:
            return
        if id is None:
            self._startTime = time.time()
        else:
            elapsed = time.time() - self._startTime
            print("%s: %2.3f" % (id, elapsed))

    @property
    def numWorkUnit(self):
        return self.numIteration

    @property
    def isDone(self):
        return self._isDone

    def run(self):
        """
        Runs the bootstrap.

        Returns
        -------
        BootstrapResult
        """
        def mkNullResult():
            fittedStatistic = TimeseriesStatistic(
                  self.fitter.observedTS[self.fitter.selectedColumns])
            return BootstrapResult(self.fitter, 0, {}, fittedStatistic)
        #
        if self.isDone:
            return
        # Set up logging for this run
        if self.fd is not None:
            sys.stderr = self.fd
            sys.stdout = self.fd
        isSuccess = False
        bootstrapError = 0
        self.report()
        for idx in range(ITERATION_MULTIPLIER):
            newObservedTS = self.synthesizer.calculate()
            self.report("newObservedTS")
            # Construct a new fitter
            newFitter = ModelFitterCore(self.fitter.roadrunnerModel,
                  newObservedTS,
                  parametersToFit=self.fitter.parametersToFit,
                  selectedColumns=self.fitter.selectedColumns,
                  # Use bootstrap methods for fitting
                  fitterMethods=self.fitter._bootstrapMethods,
                  parameterLowerBound=self.fitter.lowerBound,
                  parameterUpperBound=self.fitter.upperBound,
                  logger=self.logger,
                  isPlot=self.fitter._isPlot)
            self.report("newFitter")
            # Try fitting
            try:
                newFitter.fitModel(params=self.fitter.params)
                self.report("newFitter.fit")
            except Exception as err:
                # Problem with the fit.
                msg = "modelFitterBootstrap. Fit failed on iteration %d."  \
                      % iteration
                self.logger.error(msg, err)
                bootstrapError += 1
                continue
            # Check if the fit is of sufficient quality
            if newFitter.minimizerResult.redchi > MAX_CHISQ_MULT*self.baseChisq:
                # msg = "Fit has high chisq: %2.2f on iteration %d."
                # self.logger.exception(msg
                #      % (newFitter.minimizerResult.redchi, iteration))
                continue
            if newFitter.params is None:
                continue
            isSuccess = True
            self.report("break")
            break
        # Create the result
        if isSuccess:
            self.numSuccessIteration += 1
            parameterDct = {k: [v] for k, v 
                  in newFitter.params.valuesdict().items()}
            fittedStatistic = self.baseFittedStatistic.copy()
            fittedStatistic.accumulate(newFitter.fittedTS.subsetColumns(
                  self.fitter.selectedColumns, isCopy=False))
            bootstrapResult = BootstrapResult(self.fitter,
                  self.numSuccessIteration, parameterDct,
                  fittedStatistic, bootstrapError=bootstrapError)
        else:
            bootstrapResult = mkNullResult()
            self._isDone = True
        # Close the logging file
        if self.fd is not None:
            if not self.fd.closed:
                self.fd.close()
        # See if completed work
        if self.numSuccessIteration >= self.numIteration:
            self._isDone = True
        return bootstrapResult

    def _fitInitial(self):
        """
        Do the initial fit.

        Returns
        -------
        bool
            successful fit
        """
        isSuccess = False
        for _ in range(MAX_TRIES):
            try:
                self.fitter.fitModel()  # Initialize model
                isSuccess = True
                break
            except Exception as err:
                self.lastErr = err
                msg = "Could not do initial fit"
                self.logger.error(msg,  err)
        return isSuccess
