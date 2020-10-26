# -*- coding: utf-8 -*-
"""
 Created on August 18, 2020

@author: joseph-hellerstein

Core logic of model fitter. Does not include plots.
"""

from SBstoat.namedTimeseries import NamedTimeseries, TIME, mkNamedTimeseries
import SBstoat.timeseriesPlotter as tp
from SBstoat import namedTimeseries
from SBstoat import rpickle
from SBstoat import _helpers

import collections
import copy
import lmfit
import numpy as np
import pandas as pd
import random
import roadrunner
import tellurium as te
import typing

# Constants
PARAMETER_LOWER_BOUND = 0
PARAMETER_UPPER_BOUND = 10
#  Minimizer methods
METHOD_BOTH = "both"
METHOD_DIFFERENTIAL_EVOLUTION = "differential_evolution"
METHOD_LEASTSQ = "leastsqr"
MAX_CHISQ_MULT = 5
PERCENTILES = [2.5, 97.55]  # Percentile for confidence limits
INDENTATION = "  "
NULL_STR = ""
IS_REPORT = False


##############################
class ParameterSpecification(object):

    def __init__(self, lower=None, value=None, upper=None):
        self.lower = lower
        self.value = value
        self.upper = upper


class ModelFitterCore(rpickle.RPickler):

    def __init__(self, modelSpecification, observedData, parametersToFit,
                 selectedColumns=None, method=METHOD_BOTH,
                 parameterLowerBound=PARAMETER_LOWER_BOUND,
                 parameterUpperBound=PARAMETER_UPPER_BOUND,
                 parameterDct={},
                 fittedDataTransformDct={},
                 isPlot=True
                 ):
        """
        Parameters
        ---------
        modelSpecification: ExtendedRoadRunner/str
            roadrunner model or antimony model
        observedData: NamedTimeseries/str
            str: path to CSV file
        parametersToFit: list-str/None
            parameters in the model that you want to fit
            if None, no parameters are fit
        selectedColumns: list-str
            species names you wish use to fit the model
            default: all columns in observedData
        parameterLowerBound: float
            lower bound for the fitting parameters
        parameterUpperBound: float
            upper bound for the fitting parameters
        parameterDct: dict
            key: parameter name
            value: ParameterSpecification
        fittedDataTransformDct: dict
            key: column in selectedColumns
            value: function of the data in selectedColumns;
                   input: NamedTimeseries
                   output: array for the values of the column
        method: str
            method used for minimization

        Usage
        -----
        f = ModelFitter(roadrunnerModel, "observed.csv", ['k1', 'k2'])
        """
        self.modelSpecification = modelSpecification
        self.parametersToFit = parametersToFit
        self.lowerBound = parameterLowerBound
        self.upperBound = parameterUpperBound
        self.parameterDct = dict(parameterDct)
        self.observedTS = observedData
        if self.observedTS is not None:
            self.observedTS = mkNamedTimeseries(observedData)
        self.fittedDataTransformDct = fittedDataTransformDct
        if (selectedColumns is None) and (self.observedTS is not None):
            selectedColumns = self.observedTS.colnames
        self.selectedColumns = selectedColumns
        self._method = method
        self._isPlot = isPlot
        self._plotter = tp.TimeseriesPlotter(isPlot=self._isPlot)
        self._plotFittedTS = None  # Timeseries that is plotted
        # The following are calculated during fitting
        self.roadrunnerModel = None
        self.minimizer = None  # lmfit.minimizer
        self.minimizerResult = None  # Results of minimization
        self.params = None  # params property in lmfit.minimizer
        if self.observedTS is not None:
            self.fittedTS = self.observedTS.copy()  # Initialization of columns
        self.residualsTS = None  # Residuals for selectedColumns
        self.bootstrapResult = None  # Result from bootstrapping
        # Validation checks
        self._validateFittedDataTransformDct()
    
    @classmethod
    def rpConstruct(cls):
        """
        Overrides rpickler.rpConstruct to create a method that
        constructs an instance without arguments.
        
        Returns
        -------
        Instance of cls
        """
        return cls(None, None, None)

    def _validateFittedDataTransformDct(self):
        if self.fittedDataTransformDct is not None:
            keySet = set(self.fittedDataTransformDct.keys())
            selectedColumnsSet = self.selectedColumns
            if (keySet is not None) and (selectedColumnsSet is not None):
                excess = set(keySet).difference(selectedColumnsSet)
                if len(excess) > 0:
                    msg = "Columns not in selectedColumns: %s"  % str(excess)
                    raise ValueError(excess)

    def _transformFittedTS(self, data):
        """
        Updates the fittedTS taking into account required transformations.
 
        Parameters
        ----------
        data: np.ndarray
 
        Results
        ----------
        NamedTimeseries
        """
        fittedTS = NamedTimeseries(array=data[:, :],
              colnames=self.fittedTS.allColnames)
        for column, func in self.fittedDataTransformDct.items():
            if func is not None:
                fittedTS[column] = func(fittedTS)
        return fittedTS
        
    @staticmethod
    def addParameter(parameterDct: dict,
          name: str, lower: float, upper: float, value: float):
        """
        Adds a parameter to a list of parameters.

        Parameters
        ----------
        parameterDct: parameter dictionary to agument
        name: parameter name
        lower: lower range of parameter value
        upper: upper range of parameter value
        value: initial value
        
        Returns
        -------
        dict
        """
        parameterDct[name] = ParameterSpecification(
              lower=lower, upper=upper, value=value)

    def copy(self):
        """
        Creates a copy of the model fitter.
        Preserves the user-specified settings and the results
        of bootstrapping.
        """
        if not isinstance(self.modelSpecification, str):
            modelSpecification = self.modelSpecification.getAntimony()
        else:
            modelSpecification = self.modelSpecification
        newModelFitter = self.__class__(
              copy.deepcopy(modelSpecification),
              self.observedTS.copy(),
              copy.deepcopy(self.parametersToFit),
              selectedColumns=copy.deepcopy(self.selectedColumns),
              method=self._method,
              parameterLowerBound=self.lowerBound,
              parameterUpperBound=self.upperBound,
              parameterDct=copy.deepcopy(self.parameterDct),
              fittedDataTransformDct=copy.deepcopy(self.fittedDataTransformDct),
              isPlot=self._isPlot)
        if self.bootstrapResult is not None:
            newModelFitter.bootstrapResult = self.bootstrapResult.copy()
            newModelFitter.params = newModelFitter.bootstrapResult.params
        else:
            newModelFitter.bootstrapResult = None
            newModelFitter.params = self.params
        return newModelFitter

    def _initializeRoadrunnerModel(self):
        """
        Sets self.roadrunnerModel.
        """
        if isinstance(self.modelSpecification,
              te.roadrunner.extended_roadrunner.ExtendedRoadRunner):
            self.roadrunnerModel = self.modelSpecification
        elif isinstance(self.modelSpecification, str):
            self.roadrunnerModel = te.loada(self.modelSpecification)
        else:
            msg = 'Invalid model.'
            msg = msg + "\nA model must either be a Roadrunner model "
            msg = msg + "an Antimony model."
            raise ValueError(msg)

    def simulate(self, params=None, startTime=None, endTime=None, numPoint=None):
        """
        Runs a simulation. Defaults to parameter values in the simulation.

        Parameters
        ----------
        params: lmfit.Parameters
        startTime: float
        endTime: float
        numPoint: int

        Return
        ------
        NamedTimeseries
        """
        def set(default, parameter):
            # Sets to default if parameter unspecified
            if parameter is None:
                return default
            else:
                return parameter
        #
        startTime = set(self.observedTS.start, startTime)
        endTime = set(self.observedTS.end, endTime)
        numPoint = set(len(self.observedTS), numPoint)
        if self.roadrunnerModel is None:
            self._initializeRoadrunnerModel()
        self.roadrunnerModel.reset()
        if params is not None:
          # Parameters have been specified
          self._setupModel(params)
        data = self.roadrunnerModel.simulate(startTime, endTime, numPoint)
        # Select the required columns
        columnIndices = [i for i in range(len(data.colnames))
              if data.colnames[i][1:-1] in self.fittedTS.allColnames]
        columnIndices.insert(0, 0)
        data = data[:, columnIndices]
        fittedTS = self._transformFittedTS(data)
        return fittedTS
        

    def _simulate(self, **kwargs):
        """
        Runs a simulation.

        Parameters
        ----------
        kwargs: dict

        Instance Variables Updated
        --------------------------
        self.fittedTS
        """
        self.fittedTS = self.simulate(**kwargs)

    def _residuals(self, params)->np.ndarray:
        """
        Compute the residuals between objective and experimental data

        Parameters
        ----------
        kwargs: dict
            arguments for simulation

        Instance Variables Updated
        --------------------------
        self.residualsTS

        Returns
        -------
        1-d ndarray of residuals
        """
        self._simulate(params=params)
        cols = self.selectedColumns
        if self.residualsTS is None:
            self.residualsTS = self.observedTS.subsetColumns(cols)
        self.residualsTS[cols] = self.observedTS[cols] - self.fittedTS[cols]
        residuals = self.residualsTS.flatten()
        return residuals

    def fitModel(self, params:lmfit.Parameters=None,
          max_nfev:int=100):
        """
        Fits the model by adjusting values of parameters based on
        differences between simulated and provided values of
        floating species.

        Parameters
        ----------
        params: starting values of parameters
        max_nfev: maximum number of function evaluations

        Example
        -------
        f.fitModel()
        """
        self._initializeRoadrunnerModel()
        if self.parametersToFit is None:
            # Compute fit and residuals for base model
            self.params = None
        else:
            if params is None:
                params = self.mkParams()
            residuals_DE = self.observedTS.flatten()
            residuals_LS = residuals_DE
            params_DE = None
            params_LS = None
            # Fit the model to the data
            # Use two algorithms:
            #   Global differential evolution to get us close to minimum
            #   A local Levenberg-Marquardt to getsus to the minimum
            isMinimized = False
            if self._method in [METHOD_BOTH, METHOD_DIFFERENTIAL_EVOLUTION]:
                minimizer = lmfit.Minimizer(self._residuals, params,
                      max_nfev=max_nfev)
                self.minimizerResult = minimizer.minimize(
                      method=METHOD_DIFFERENTIAL_EVOLUTION,
                      max_nfev=max_nfev)
                params_DE = self.minimizerResult.params
                residuals_DE = self._residuals(params=params_DE)
                isMinimized = True
            if self._method in [METHOD_BOTH, METHOD_LEASTSQ]:
                minimizer = lmfit.Minimizer(self._residuals, params,
                      max_nfev=max_nfev)
                self.minimizerResult = minimizer.minimize(
                      method=METHOD_LEASTSQ,
                      max_nfev=max_nfev)
                params_LS = self.minimizerResult.params
                residuals_LS = self._residuals(params=params_LS)
                isMinimized = True
            if not isMinimized:
                raise ValueError("Invalid method specified: %s" % self._method)
            if np.std(residuals_DE) <= np.std(residuals_LS):
                self.params = params_DE
            else:
                if params_LS is not None:
                    self.params = params_LS
                else:
                    self.params = params_DE
            self.minimizer = minimizer
            if not self.minimizer.success:
                msg = "*** Minimizer failed for this model and data."
                raise ValueError(msg)
        # Ensure that residualsTS and fittedTS match the parameters
        _ = self._residuals(params=self.params)

    def getFittedModel(self):
        """
        Provides the roadrunner model with fitted parameters

        Returns
        -------
        ExtendedRoadrunner
        """
        self._checkFit()
        self.roadrunnerModel.reset()
        self._setupModel(self.params)
        return self.roadrunnerModel

    def _setupModel(self, params):
        """
        Sets up the model for use based on the parameter parameters

        Parameters
        ----------
        params: lmfit.Parameters

        """
        pp = params.valuesdict()
        for parameter in self.parametersToFit:
            self.roadrunnerModel.model[parameter] = pp[parameter]

    def mkParams(self, parameterDct:dict=None)->lmfit.Parameters:
        """
        Constructs lmfit parameters based on specifications.

        Parameters
        ----------
        parameterDct: key=name, value=ParameterSpecification
        
        Returns
        -------
        lmfit.Parameters
        """
        def get(value, base_value, multiplier):
            if value is not None:
                return value
            return base_value*multiplier
        #
        if parameterDct is None:
            parameterDct = self.parameterDct
        params = lmfit.Parameters()
        for parameterName in self.parametersToFit:
            if parameterName in parameterDct.keys():
              specification = parameterDct[parameterName]
              value = get(specification.value, specification.value, 1.0)
              lower = get(specification.lower, specification.value, 0.9)
              upper = get(specification.upper, specification.value, 1.1)
              params.add(parameterName, value=value, min=lower, max=upper)
            else:
                value = np.mean([self.lowerBound, self.upperBound])
                params.add(parameterName, value=value,
                      min=self.lowerBound, max=self.upperBound)
        return params

    def _checkFit(self):
        if self.params is None:
            raise ValueError("Must use fitModel before using this method.")

    def serialize(self, path):
        """
        Serialize the model to a path.

        Parameters
        ----------
        path: str
            File path
        """
        newModelFitter = self.copy()
        with open(path, "wb") as fd:
            rpickle.dump(newModelFitter, fd)

    @classmethod
    def deserialize(cls, path):
        """
        Deserialize the model from a path.

        Parameters
        ----------
        path: str
            File path

        Return
        ------
        ModelFitter
            Model is initialized.
        """
        with open(path, "rb") as fd:
            fitter = rpickle.load(fd)
        fitter._initializeRoadrunnerModel()
        return fitter

