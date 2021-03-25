"""
 Created on August 18, 2020

@author: joseph-hellerstein

Core logic of model fitter. Does not include plots.
"""

import SBstoat
from SBstoat._parameterManager import Parameter
import SBstoat._constants as cn
from SBstoat._optimizer import Optimizer
from SBstoat.namedTimeseries import NamedTimeseries, TIME, mkNamedTimeseries
from SBstoat.logs import Logger
import SBstoat.timeseriesPlotter as tp
from SBstoat import rpickle
from SBstoat import _helpers

import copy
import lmfit
import numpy as np
import tellurium as te
import typing

# Constants
MAX_CHISQ_MULT = 5
PERCENTILES = [2.5, 97.55]  # Percentile for confidence limits
LARGE_RESIDUAL = 1000000


class ModelFitterCore(rpickle.RPickler):

    def __init__(self, modelSpecification, observedData,
          # The following must be kept in sync with ModelFitterBootstrap.bootstrap
          parametersToFit=None, # Must be first kw for backwards compatibility
          bootstrapMethods=None,
          endTime=None,
          fitterMethods=None,
          logger=Logger(),
          _loggerPrefix="",
          isPlot=True,
          maxProcess:int=None,
          numFitRepeat=1,
          numIteration:int=10,
          numPoint=None,
          numRestart=0,
          parameterLowerBound=cn.PARAMETER_LOWER_BOUND,
          parameterUpperBound=cn.PARAMETER_UPPER_BOUND,
          selectedColumns=None,
          serializePath:str=None,
          isParallel=True,
          isProgressBar=True,
          ):
        """
        Constructs estimates of parameter values.

        Parameters
        ----------
        endTime: float
            end time for the simulation
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
        numPoint: int
            number of time points in the simulation
        maxProcess: Maximum number of processes to use. Default: numCPU
        serializePath: Where to serialize the fitter after bootstrap
        numRestart: int
            number of times the minimization is restarted with random
            initial values for parameters to fit.
        isParallel: bool
            run in parallel where possible
        isProgressBar: bool
            display the progress bar

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
            self._maxProcess = maxProcess
            self.bootstrapKwargs = dict(
                  numIteration=numIteration,
                  serializePath=serializePath,
                  )
            self._numFitRepeat = numFitRepeat
            self.selectedColumns = selectedColumns
            self.observedTS, self.selectedColumns = self._updateObservedTS(
                  mkNamedTimeseries(observedData))
            #
            self.selectedColumns = [c.strip() for c in self.selectedColumns]
            self.numPoint = numPoint
            if self.numPoint is None:
                self.numPoint = len(self.observedTS)
            self.endTime = endTime
            if self.endTime is None:
                self.endTime = self.observedTS.end
            # Other internal state
            self._fitterMethods = ModelFitterCore.makeMethods(fitterMethods,
                  cn.METHOD_FITTER_DEFAULTS)
            self._bootstrapMethods = ModelFitterCore.makeMethods(bootstrapMethods,
                  cn.METHOD_BOOTSTRAP_DEFAULTS)
            if isinstance(self._bootstrapMethods, str):
                self._bootstrapMethods = [self._bootstrapMethods]
            self._isPlot = isPlot
            self._plotter = tp.TimeseriesPlotter(isPlot=self._isPlot)
            self.logger = logger
            self._numRestart = numRestart
            self._isParallel = isParallel
            self._isProgressBar = isProgressBar
            self._selectedIdxs = None
            # The following are calculated during fitting
            self.roadrunnerModel = None
            self.minimizerResult = None  # Results of minimization
            self.fittedTS = self.observedTS.copy(isInitialize=True)  # Initialize
            self.residualsTS = None  # Residuals for selectedColumns
            self.bootstrapResult = None  # Result from bootstrapping
            self.optimizer = None
            self.suiteFitterParams = None  # Result from a suite fitter
            #
        else:
            pass
 
    def _updateSelectedIdxs(self):
        resultTS = self.simulate()
        if resultTS is not None:
            self._selectedIdxs =  \
                  ModelFitterCore.selectCompatibleIndices(resultTS[TIME],
                  self.observedTS[TIME])
        else:
            self._selectedIdxs = list(range(self.numPoint))   

    def _updateObservedTS(self, newObservedTS, isCheck=True):
        """
        Changes just the observed timeseries. The new timeseries must have
        the same shape as the old one. Also used on initialization,
        in which case, the check should be disabled.

        Parameters
        ----------
        newObservedTS: NamedTimeseries
        isCheck: Bool
            verify that new timeseries as the same shape as the old one

        Returns
        -------
        NamedTimeseries
            ObservedTS
        list-str
            selectedColumns
        """
        if isCheck:
            isError = False
            if not isinstance(newObservedTS, NamedTimeseries):
                isError = True
            if "observedTS" in self.__dict__.keys():
                isError = isError  \
                      or (not self.observedTS.equalSchema(newObservedTS))
            if isError:
                raise RuntimeError("Timeseries argument: incorrect type or shape.")
        #
        self.observedTS = newObservedTS
        if (self.selectedColumns is None) and (self.observedTS is not None):
            self.selectedColumns = self.observedTS.colnames
        if self.observedTS is None:
            self._observedArr = None
        else:
            self._observedArr = self.observedTS[self.selectedColumns].flatten()
        #
        return self.observedTS, self.selectedColumns

    @property
    def params(self):
        params = None
        #
        if params is None:
            if self.suiteFitterParams is not None:
                params = self.suiteFitterParams
        if self.bootstrapResult is not None:
            if self.bootstrapResult.params is not None:
                params = self.bootstrapResult.params
        if params is None:
            if self.optimizer is not None:
                params = self.optimizer.params
        return params

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
          lowerBound:float=cn.PARAMETER_LOWER_BOUND,
          upperBound:float=cn.PARAMETER_UPPER_BOUND)->lmfit.Parameters:
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
          modelSpecification=None,
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
        modelSpecification: ExtendedRoadRunner/str
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
        roadrunnerModel = modelSpecification
        if isinstance(modelSpecification, str):
            roadrunnerModel = cls.initializeRoadrunnerModel(roadrunnerModel)
        else:
            roadrunnerModel.reset()
        if parameters is not None:
            # Parameters have been specified
            cls.setupModel(roadrunnerModel, parameters, logger=_logger)
        # Do the simulation
        if selectedColumns is not None:
            newSelectedColumns = list(selectedColumns)
            if TIME not in newSelectedColumns:
                newSelectedColumns.insert(0, TIME)
            try:
                data = roadrunnerModel.simulate(startTime, endTime, numPoint,
                      newSelectedColumns)
            except Exception as err:
                _logger.error("Roadrunner exception: ", err)
                data = None
        else:
            try:
                data = roadrunnerModel.simulate(startTime, endTime, numPoint)
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

    def clean(self):
        """
        Cleans the object so that it can be pickled.
        """
        self.roadrunnerModel = None
        return self

    def copy(self, isKeepLogger=False, isKeepOptimizer=False):
        """
        Creates a copy of the model fitter.
        Preserves the user-specified settings and the results
        of bootstrapping.
        
        Parameters
        ----------
        isKeepLogger: bool
        isKeepOptimizer: bool
        isMinimalCopy: bool
            copy minimal context for bootstrap
        
        Returns
        -------
        ModelFitter
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
        if self.optimizer is not None:
            if isKeepOptimizer:
                newModelFitter.optimizer = self.optimizer.copyResults()
        if self.bootstrapResult is not None:
            newModelFitter.bootstrapResult = self.bootstrapResult.copy()
        else:
            newModelFitter.bootstrapResult = None
        return newModelFitter

    def initializeRoadRunnerModel(self):
        """
        Sets self.roadrunnerModel.
        """
        if self.roadrunnerModel is None:
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
        endTime = setValue(self.endTime, endTime)
        numPoint = setValue(self.numPoint, numPoint)
        #
        if self.roadrunnerModel is None:
            self.initializeRoadRunnerModel()
        #
        return ModelFitterCore.runSimulation(parameters=params,
              modelSpecification=self.roadrunnerModel,
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
        if self._selectedIdxs is None:
            self._updateSelectedIdxs()
        self.fittedTS = self.fittedTS[self._selectedIdxs]
        residualsArr = self.calcResiduals(self.params)
        numRow = len(self.fittedTS)
        numCol = len(residualsArr)//numRow
        residualsArr = np.reshape(residualsArr, (numRow, numCol))
        cols = self.selectedColumns
        if self.residualsTS is None:
            self.residualsTS = self.observedTS.subsetColumns(cols)
        self.residualsTS[cols] = residualsArr

    @staticmethod
    def selectCompatibleIndices(bigTimes, smallTimes):
        """
        Finds the indices such that smallTimes[n] is close to bigTimes[indices[n]]

        Parameters
        ----------
        bigTimes: np.ndarray
        smalltimes: np.ndarray

        Returns
        np.ndarray
        """
        indices = []
        for idx, _ in enumerate(smallTimes):
            distances = (bigTimes - smallTimes[idx])**2
            def getValue(k):
                return distances[k]
            thisIndices = sorted(range(len(distances)), key=getValue)
            indices.append(thisIndices[0])
        return np.array(indices)

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
        if self._selectedIdxs is None:
            self._updateSelectedIdxs()
        dataTS = ModelFitterCore.runSimulation(parameters=params,
              modelSpecification=self.roadrunnerModel,
              startTime=self.observedTS.start,
              endTime=self.endTime,
              numPoint=self.numPoint,
              selectedColumns=self.selectedColumns,
              _logger=self.logger,
              _loggerPrefix=self._loggerPrefix,
              returnDataFrame=False)
        if dataTS is None:
            residualsArr = np.repeat(LARGE_RESIDUAL, len(self._observedArr))
        else:
            truncatedTS = dataTS[self._selectedIdxs]
            residualsArr = self._observedArr - truncatedTS.flatten()
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
