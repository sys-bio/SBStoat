"""
 Created on August 18, 2020

@author: joseph-hellerstein

Core logic of model fitter. Does not include plots.
"""

import SBstoat
import SBstoat._constants as cn
from SBstoat._optimizer import Optimizer
from SBstoat.namedTimeseries import NamedTimeseries, TIME, mkNamedTimeseries
from SBstoat.logs import Logger
import SBstoat.timeseriesPlotter as tp
from SBstoat import rpickle
from SBstoat import _helpers

import collections
import copy
import lmfit
import numpy as np
import tellurium as te
import typing

# Constants
PARAMETER_LOWER_BOUND = 0
PARAMETER_UPPER_BOUND = 10
MAX_CHISQ_MULT = 5
PERCENTILES = [2.5, 97.55]  # Percentile for confidence limits
LOWER_PARAMETER_MULT = 0.95
UPPER_PARAMETER_MULT = 1.05
LARGE_RESIDUAL = 1000000


##############################
class Parameter():

    def __init__(self, name, lower=PARAMETER_LOWER_BOUND,
              value=None, upper=PARAMETER_UPPER_BOUND):
        self.name = name
        self.lower = lower
        self.upper = upper
        self.value = value
        if value is None:
            self.value = (lower + upper)/2.0
        if self.value <= self.lower:
            self.lower = LOWER_PARAMETER_MULT*self.value
        if self.value >= self.upper:
            self.upper = UPPER_PARAMETER_MULT*self.value
        if np.isclose(self.lower, 0.0):
            self.lower = -0.001
        if np.isclose(self.upper, 0.0):
            self.upper = 0.001

    def __str__(self):
        return self.name

    def copy(self, name=None):
        if name is None:
            name = self.name
        return Parameter(name, lower=self.lower, upper=self.upper,
              value=self.value)


class ModelFitterCore(rpickle.RPickler):

    def __init__(self, modelSpecification, observedData,
          parametersToFit=None,
          selectedColumns=None,
          fitterMethods=None,
          numFitRepeat=1,
          bootstrapMethods=None,
          parameterLowerBound=PARAMETER_LOWER_BOUND,
          parameterUpperBound=PARAMETER_UPPER_BOUND,
          logger=Logger(),
          isPlot=True,
          _loggerPrefix="",
          # The following must be kept in sync with ModelFitterBootstrap.bootstrap
          numIteration:int=10,
          reportInterval:int=1000,
          maxProcess:int=None,
          serializePath:str=None,
          numRestart=0,
          ):
        """
        Constructs estimates of parameter values.

        Parameters
        ----------
        modelSpecification: ExtendedRoadRunner/str
            roadrunner model or antimony model
        observedData: NamedTimeseries/str
            str: path to CSV file
        parametersToFit: list-str/SBstoat.Parameter/None
            parameters in the model that you want to fit
            if None, no parameters are fit
        selectedColumns: list-str
            species names you wish use to fit the model
            default: all columns in observedData
        parameterLowerBound: float
            lower bound for the fitting parameters
        parameterUpperBound: float
            upper bound for the fitting parameters
        logger: Logger
        fitterMethods: str/list-str/list-OptimizerMethod
            method used for minimization in fitModel
        numFitRepeat: int
            number of times fitting is repeated for a method
        bootstrapMethods: str/list-str/list-OptimizerMethod
            method used for minimization in bootstrap
        numIteration: number of bootstrap iterations
        reportInterval: number of iterations between progress reports
        maxProcess: Maximum number of processes to use. Default: numCPU
        serializePath: Where to serialize the fitter after bootstrap
        numRestart: int
            number of times the minimization is restarted with random
            initial values for parameters to fit.

        Usage
        -----
        parametersToFit = [SBstoat.Parameter("k1", lower=1, upper=10, value=5),
                           SBstoat.Parameter("k2", lower=2, upper=6, value=3),
                          ]
        ftter = ModelFitter(roadrunnerModel, "observed.csv",
            parametersToFit=parametersToFit)
        fitter.fitModel()  # Do the fit
        fitter.bootstrap()  # Estimate parameter variance with bootstrap
        """
        if modelSpecification is not None:
            # Not the default constructor
            self._loggerPrefix = _loggerPrefix
            self.modelSpecification = modelSpecification
            self.parametersToFit = parametersToFit
            self.lowerBound = parameterLowerBound
            self.upperBound = parameterUpperBound
            self.bootstrapKwargs = dict(
                  numIteration=numIteration,
                  reportInterval=reportInterval,
                  maxProcess=maxProcess,
                  serializePath=serializePath,
                  )
            self._numFitRepeat = numFitRepeat
            self.observedTS = observedData
            if self.observedTS is not None:
                self.observedTS = mkNamedTimeseries(observedData)
            #
            if (selectedColumns is None) and (self.observedTS is not None):
                selectedColumns = self.observedTS.colnames
            self.selectedColumns = selectedColumns
            if self.observedTS is not None:
                self._observedArr = self.observedTS[self.selectedColumns].flatten()
            else:
                self._observedArr = None
            # Other internal state
            self._fitterMethods = ModelFitterCore.makeMethods(fitterMethods,
                  cn.METHOD_FITTER_DEFAULTS)
            self._bootstrapMethods = ModelFitterCore.makeMethods(bootstrapMethods,
                  cn.METHOD_BOOTSTRAP_DEFAULTS)
            if isinstance(self._bootstrapMethods, str):
                self._bootstrapMethods = [self._bootstrapMethods]
            self._isPlot = isPlot
            self._plotter = tp.TimeseriesPlotter(isPlot=self._isPlot)
            self._plotFittedTS = None  # Timeseries that is plotted
            self.logger = logger
            self._numRestart = numRestart
            # The following are calculated during fitting
            self.roadrunnerModel = None
            self.minimizer = None  # lmfit.minimizer
            self.minimizerResult = None  # Results of minimization
            self.params = None  # params property in lmfit.minimizer
            self.fittedTS = self.observedTS.copy(isInitialize=True)  # Initialize
            self.residualsTS = None  # Residuals for selectedColumns
            self.bootstrapResult = None  # Result from bootstrapping
            self._optimizer = None
        else:
            pass

    @staticmethod
    def makeMethods(methods, default):
        """
        Creates a method dictionary.

        Parameters
        ----------
        methods: str/list-str/dict
            method used for minimization in fitModel
            dict: key-method, value-optional parameters

        Returns
        -------
        list-OptimizerMethod
            key: method name
            value: dict of optional parameters
        """
        if methods is None:
            methods = default
        if isinstance(methods, str):
            if methods == cn.METHOD_BOTH:
                methods = cn.METHOD_FITTER_DEFAULTS
            else:
                methods = [methods]
        if isinstance(methods, list):
            if isinstance(methods[0], str):
                results = [_helpers.OptimizerMethod(method=m, kwargs={})
                      for m in methods]
            else:
                results = methods
        else:
            raise RuntimeError("Must be a list")
        trues = [isinstance(m, _helpers.OptimizerMethod) for m in results]
        if not all(trues):
            raise ValueError("Invalid methods: %s" % str(methods))
        return results

    @classmethod
    def mkParameters(cls, parametersToFit:list,
          logger:Logger=Logger(),
          lowerBound:float=PARAMETER_LOWER_BOUND,
          upperBound:float=PARAMETER_UPPER_BOUND)->lmfit.Parameters:
        """
        Constructs lmfit parameters based on specifications.

        Parameters
        ----------
        parametersToFit: list-Parameter/list-str
        logger: error logger
        lowerBound: lower value of range for parameters
        upperBound: upper value of range for parameters

        Returns
        -------
        lmfit.Parameters
        """
        if len(parametersToFit) == 0:
            raise RuntimeError("Must specify at least one parameter.")
        if logger is None:
            logger = logger()
        lmfitParameters = lmfit.Parameters()
        # Process each parameter
        for element in parametersToFit:
            # Get the lower bound, upper bound, and initial value for the parameter
            if not isinstance(element, SBstoat.Parameter):
                element = SBstoat.Parameter(element,
                      lower=lowerBound, upper=upperBound)
            lmfitParameters.add(element.name,
                  min=element.lower, max=element.upper, value=element.value)
        return lmfitParameters

    @classmethod
    def initializeRoadrunnerModel(cls, modelSpecification):
        """
        Sets self.roadrunnerModel.

        Parameters
        ----------
        modelSpecification: ExtendedRoadRunner/str

        Returns
        -------
        ExtendedRoadRunner
        """
        if isinstance(modelSpecification,
              te.roadrunner.extended_roadrunner.ExtendedRoadRunner):
            roadrunnerModel = modelSpecification
        elif isinstance(modelSpecification, str):
            roadrunnerModel = te.loada(modelSpecification)
        else:
            msg = 'Invalid model.'
            msg = msg + "\nA model must either be a Roadrunner model "
            msg = msg + "an Antimony model."
            raise ValueError(msg)
        return roadrunnerModel

    @classmethod
    def setupModel(cls, roadrunner, parameters, logger=Logger()):
        """
        Sets up the model for use based on the parameter parameters

        Parameters
        ----------
        roadrunner: ExtendedRoadRunner
        parameters: lmfit.Parameters
        logger Logger
        """
        pp = parameters.valuesdict()
        for parameter in pp.keys():
            try:
                roadrunner.model[parameter] = pp[parameter]
            except Exception as err:
                msg = "_modelFitterCore.setupModel: Could not set value for %s"  \
                      % parameter
                logger.error(msg, err)

    @classmethod
    def runSimulation(cls, parameters=None,
          roadrunner=None,
          startTime=0,
          endTime=5,
          numPoint=30,
          selectedColumns=None,
          returnDataFrame=True,
          _logger=Logger(),
          _loggerPrefix="",
          ):
        """
        Runs a simulation. Defaults to parameter values in the simulation.

        Parameters
       ----------
        roadrunner: ExtendedRoadRunner/str
            Roadrunner model
        parameters: lmfit.Parameters
            lmfit parameters
        startTime: float
            start time for the simulation
        endTime: float
            end time for the simulation
        numPoint: int
            number of points in the simulation
        selectedColumns: list-str
            output columns in simulation
        returnDataFrame: bool
            return a DataFrame
        _logger: Logger
        _loggerPrefix: str


        Return
        ------
        NamedTimeseries (or None if fail to converge)
        """
        if isinstance(roadrunner, str):
            roadrunner = cls.initializeRoadrunnerModel(roadrunner)
        else:
            roadrunner.reset()
        if parameters is not None:
            # Parameters have been specified
            cls.setupModel(roadrunner, parameters, logger=_logger)
        # Do the simulation
        if selectedColumns is not None:
            newSelectedColumns = list(selectedColumns)
            if TIME not in newSelectedColumns:
                newSelectedColumns.insert(0, TIME)
            try:
                data = roadrunner.simulate(startTime, endTime, numPoint,
                      newSelectedColumns)
            except Exception as err:
                _logger.error("Roadrunner exception: ", err)
                data = None
        else:
            try:
                data = roadrunner.simulate(startTime, endTime, numPoint)
            except Exception as err:
                _logger.exception("Roadrunner exception: %s", err)
                data = None
        if data is None:
            return data
        fittedTS = NamedTimeseries(namedArray=data)
        if returnDataFrame:
            result = fittedTS.to_dataframe()
        else:
            result = fittedTS
        return result

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

    def rpRevise(self):
        """
        Overrides rpickler.
        """
        if "logger" not in self.__dict__.keys():
            self.logger = Logger()

    def _adjustNames(self, antimonyModel:str, observedTS:NamedTimeseries)  \
          ->typing.Tuple[NamedTimeseries, list]:
        """
        Antimony exports can change the names of floating species
        by adding a "_" at the end. Check for this and adjust
        the names in observedTS.

        Return
        ------
        NamedTimeseries: newObservedTS
        list: newSelectedColumns
        """
        rr = te.loada(antimonyModel)
        dataNames = rr.simulate().colnames
        names = ["[%s]" % n for n in observedTS.colnames]
        missingNames = [n[1:-1] for n in set(names).difference(dataNames)]
        newSelectedColumns = list(self.selectedColumns)
        if len(missingNames) > 0:
            newObservedTS = observedTS.copy()
            self.logger.exception("Missing names in antimony export: %s"
                  % str(missingNames))
            for name in observedTS.colnames:
                missingName = "%s_" % name
                if name in missingNames:
                    newObservedTS = newObservedTS.rename(name, missingName)
                    newSelectedColumns.remove(name)
                    newSelectedColumns.append(missingName)
        else:
            newObservedTS = observedTS
        return newObservedTS, newSelectedColumns

    def copy(self, isKeepLogger=False):
        """
        Creates a copy of the model fitter.
        Preserves the user-specified settings and the results
        of bootstrapping.
        """
        if not isinstance(self.modelSpecification, str):
            try:
                modelSpecification = self.modelSpecification.getAntimony()
            except Exception as err:
                self.logger.error("Problem wth conversion to Antimony. Details:",
                      err)
                raise ValueError("Cannot proceed.")
            observedTS, selectedColumns = self._adjustNames(
                  modelSpecification, self.observedTS)
        else:
            modelSpecification = self.modelSpecification
            observedTS = self.observedTS.copy()
            selectedColumns = self.selectedColumns
        #
        if isKeepLogger:
            logger = self.logger
        elif self.logger is not None:
            logger = self.logger.copy()
        else:
            logger = None
        newModelFitter = self.__class__(
              copy.deepcopy(modelSpecification),
              observedTS,
              copy.deepcopy(self.parametersToFit),
              selectedColumns=selectedColumns,
              fitterMethods=self._fitterMethods,
              bootstrapMethods=self._bootstrapMethods,
              parameterLowerBound=self.lowerBound,
              parameterUpperBound=self.upperBound,
              logger=logger,
              isPlot=self._isPlot)
        if self.bootstrapResult is not None:
            newModelFitter.bootstrapResult = self.bootstrapResult.copy()
            newModelFitter.params = newModelFitter.bootstrapResult.params
        else:
            newModelFitter.bootstrapResult = None
            newModelFitter.params = self.params
        return newModelFitter

    def initializeRoadRunnerModel(self):
        """
        Sets self.roadrunnerModel.
        """
        self.roadrunnerModel = ModelFitterCore.initializeRoadrunnerModel(
              self.modelSpecification)

    def getDefaultParameterValues(self):
        """
        Obtain the original values of parameters.

        Returns
        -------
        list-SBstoat.Parameter
        """
        dct = {}
        self.initializeRoadRunnerModel()
        self.roadrunnerModel.reset()
        for parameterName in self.parametersToFit:
            dct[parameterName] = self.roadrunnerModel.model[parameterName]
        return dct

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
        def setValue(default, parameter):
            # Sets to default if parameter unspecified
            if parameter is None:
                return default
            return parameter
        #
        startTime = setValue(self.observedTS.start, startTime)
        endTime = setValue(self.observedTS.end, endTime)
        numPoint = setValue(len(self.observedTS), numPoint)
        #
        if self.roadrunnerModel is None:
            self.initializeRoadRunnerModel()
        #
        return ModelFitterCore.runSimulation(parameters=params,
              roadrunner=self.roadrunnerModel,
              startTime=startTime,
              endTime=endTime,
              numPoint=numPoint,
              selectedColumns=self.selectedColumns,
              _logger=self.logger,
              _loggerPrefix=self._loggerPrefix,
              returnDataFrame=False)

    def updateFittedAndResiduals(self, **kwargs)->np.ndarray:
        """
        Updates values of self.fittedTS and self.residualsTS
        based on self.params.

        Parameters
        ----------
        kwargs: dict
            arguments for simulation

        Instance Variables Updated
        --------------------------
        self.fittedTS
        self.residualsTS

        Returns
        -------
        1-d ndarray of residuals
        """
        self.fittedTS = self.simulate(**kwargs)  # Updates self.fittedTS
        residualsArr = self.calcResiduals(self.params)
        numRow = len(self.fittedTS)
        numCol = len(residualsArr)//numRow
        residualsArr = np.reshape(residualsArr, (numRow, numCol))
        cols = self.selectedColumns
        if self.residualsTS is None:
            self.residualsTS = self.observedTS.subsetColumns(cols)
        self.residualsTS[cols] = residualsArr

    def calcResiduals(self, params)->np.ndarray:
        """
        Compute the residuals between objective and experimental data
        Handle nan values in observedTS. This internal-only method
        is implemented to maximize efficieency.

        Parameters
        ----------
        params: lmfit.Parameters
            arguments for simulation

        Returns
        -------
        1-d ndarray of residuals
        """
        data = ModelFitterCore.runSimulation(parameters=params,
              roadrunner=self.roadrunnerModel,
              startTime=self.observedTS.start,
              endTime=self.observedTS.end,
              numPoint=len(self.observedTS),
              selectedColumns=self.selectedColumns,
              _logger=self.logger,
              _loggerPrefix=self._loggerPrefix,
              returnDataFrame=False)
        if data is None:
            residualsArr = np.repeat(LARGE_RESIDUAL, len(self._observedArr))
        else:
            residualsArr = self._observedArr - data.flatten()
            residualsArr = np.nan_to_num(residualsArr)
        return residualsArr

    def fitModel(self, params:lmfit.Parameters=None):
        """
        Fits the model by adjusting values of parameters based on
        differences between simulated and provided values of
        floating species.

        Parameters
        ----------
        params: lmfit.parameters
            starting values of parameters

        Example
        -------
        f.fitModel()
        """
        self.initializeRoadRunnerModel()
        if self.parametersToFit is not None:
            if params is None:
                params = self.mkParams()
            self.optimizer = Optimizer.optimize(self.calcResiduals, params,
                  self._fitterMethods, logger=self.logger,
                  numRestart=self._numRestart)
            self.params = self.optimizer.params.copy()
            self.minimizer = self.optimizer.minimizer
            self.minimizerResult = self.optimizer.minimizerResult
        # Ensure that residualsTS and fittedTS match the parameters
        self.updateFittedAndResiduals(params=self.params)

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

    def _setupModel(self, parameters):
        """
        Sets up the model for use based on the parameter parameters

        Parameters
        ----------
        parameters: lmfit.Parameters

        """
        ModelFitterCore.setupModel(self.roadrunnerModel, parameters,
              logger=self.logger)

    def mkParams(self, parametersToFit:list=None)->lmfit.Parameters:
        """
        Constructs lmfit parameters based on specifications.

        Parameters
        ----------
        parametersToFit: list-Parameter

        Returns
        -------
        lmfit.Parameters
        """
        if parametersToFit is None:
            parametersToFit = self.parametersToFit
        return ModelFitterCore.mkParameters(parametersToFit,
              logger=self.logger,
              lowerBound=self.lowerBound,
              upperBound=self.upperBound)

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
        fitter.initializeRoadRunnerModel()
        return fitter
