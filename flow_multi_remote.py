#!/usr/bin/env python

# Python 2/3 compatibility imports
from __future__ import print_function

# standard library imports
import json
from logging import lastResort
import subprocess
import time
import datetime
from threading import Thread, Lock
import copy
import queue

# request HTTP
import requests

# local module imports
from blinker import signal
import gv  # Get access to SIP's settings, gv = global variables
from sip import template_render
from urls import urls  # Get access to SIP's URLs
import web
from webpages import ProtectedPage

try:
    from db_logger import db_logger_read_definitions
    from db_logger_generic_table import create_generic_table, add_date_generic_table
    from db_logger_flow import get_last_accum_value, init_db_if_needed, add_new_register, check_and_add_flow
    withDBLogger = True
except ImportError:
    withDBLogger = False

from flow_multi_remote_aux import *

# Add a new url to open the data entry page.
# fmt: off
urls.extend(
    [
        u"/flow-home", u"plugins.flow_multi_remote.home",
        u"/flow-get-flow-demand", u"plugins.flow_multi_remote.get_value_on_demand_flow",
        u"/flow-get-acc-demand", u"plugins.flow_multi_remote.get_value_on_demand_acc",
        u"/flow-set", u"plugins.flow_multi_remote.settings",
        u"/flow-set-save", u"plugins.flow_multi_remote.settings_save",
        u"/flow-add-new", u"plugins.flow_multi_remote.setting_add_new",
        u"/flow-arduino", u"plugins.flow_multi_remote.setting_arduino",
        u"/flow-arduino-1-data", u"plugins.flow_multi_remote.setting_arduino_first_data",
        u"/flow-https-inc", u"plugins.flow_multi_remote.flow_http_increment",
        u"/flow-json", u"plugins.flow_multi_remote.settings_json",
        u"/flow-update", u"plugins.flow_multi_remote.update_reading",
    ]
)

gv.plugin_menu.append([u"Flow Muti Remote", u"/flow-home"])

commandsFlowM = {}
commandsFlowMLock = Lock()

commandsFlowQueu = queue.Queue()
threadProcFlow = None
threadProcFlowIsRunning = False
flowDataOnDemand = {}
flowDataOnDemandLock = Lock()

commFlowIncQueu = queue.Queue() # queue of http request of incremental flow
lastDataFlow = {}
threadProcFlowInc = None # Thread thad consupt http produced
threadProcFlowIncStop = None # Thread to check if valve flow stop http pulsers
threadProcFlowIncIsRunning = False # Thread Flow is running
threadProcFlowIncStopIsRunning = False # THread flow increment is running
flowIncData = {} # Data of increment values
flowIncDataLock = Lock()

def threadProcessData():
    global commandsFlowQueu, threadProcFlowIsRunning, commandsFlowM

    dbDefinitions = db_logger_read_definitions()

    while threadProcFlowIsRunning:
        dataRead = commandsFlowQueu.get()

        commandsFlowMLock.acquire()
        localDef = copy.deepcopy(commandsFlowM)
        commandsFlowMLock.release()

        for i in range(len(localDef["FlowRef"])):
            if "FR-" + localDef["FlowRef"][i] in dataRead and "FA-" + localDef["FlowRef"][i] in dataRead and "DateTime" in dataRead:
                validDigit = False
                flowRate = 0
                flowAccum = 0

                try:
                    flowRate = float(dataRead["FR-" + localDef["FlowRef"][i]])
                    flowAccum = float(dataRead["FA-" + localDef["FlowRef"][i]])
                    validDigit = True
                except:
                    pass

                if validDigit:
                    # Check if exists initial value
                    flowDataOnDemandLock.acquire()
                    if "FL-" + localDef["FlowRef"][i] not in flowDataOnDemand:
                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]] = {}

                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["AccumFlow"] = []
                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["RateFlow"] = []
                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["FlowDate"] = []

                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["DBDateSAve"] = None
                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["HasFlow"] = False

                    flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["AccumFlow"].append(flowAccum)
                    flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["RateFlow"].append(flowRate)
                    flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["FlowDate"].append(dataRead["DateTime"])

                    # check if last save DB too long add new value
                    need2Save2DB = False

                    # If first data need to save to DB
                    if flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["DBDateSAve"] == None:
                        need2Save2DB = True
                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["HasFlow"] = flowRate > 0.001
                    elif flowRate > 0.001:
                        # if with flow and delta time = 0 save any data
                        if localDef["RateWithFlow"] == 0 or \
                            (localDef["RateWithFlow"] > 0 and (dataRead["DateTime"] - flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["DBDateSAve"]).total_seconds() / 60.0 > localDef["RateWithFlow"]):
                            need2Save2DB = True
                            flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["HasFlow"] = True
                    elif flowRate <= 0.001 and \
                        (flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["HasFlow"] and localDef["RateWitoutFlow"] == 0) or \
                        (localDef["RateWitoutFlow"] > 0 and (dataRead["DateTime"] - flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["DBDateSAve"]).total_seconds() / 60.0 > localDef["RateWitoutFlow"]):
                        need2Save2DB = True
                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["HasFlow"] = False

                    if need2Save2DB:
                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["DBDateSAve"] = dataRead["DateTime"]

                    # check if any problem with flow
                    # TODO

                    if len(flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["AccumFlow"]) > 100:
                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["AccumFlow"].remove(0)
                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["RateFlow"].remove(0)
                        flowDataOnDemand["FL-" + localDef["FlowRef"][i]]["FlowDate"].remove(0)
                    flowDataOnDemandLock.release()

                    # Outside lock save to DB if case for
                    if need2Save2DB:
                        check_and_add_flow(dbDefinitions, localDef["FlowRef"][i], localDef["SensorPort"][i], localDef["CorrectionFactor"][i], localDef["SlowPulse"][i])
                        add_new_register(dbDefinitions, localDef["FlowRef"][i], flowRate, flowAccum, dataRead["DateTime"])

def threadProcessDataInc():
    global lastDataFlow, flowIncData, commFlowIncQueu

    while threadProcFlowIncIsRunning:
        dataRead = commFlowIncQueu.get()

        commandsFlowMLock.acquire()
        localDef = copy.deepcopy(commandsFlowM)
        commandsFlowMLock.release()

        for i in range(len(localDef["FlowRef"])):
            if "FR-" + localDef["FlowRef"][i] in dataRead and "FI-" + localDef["FlowRef"][i] in dataRead and "DateTime" in dataRead:
                validNumber = False
                incrementL = 0
                try:
                    incrementL = float(dataRead["FI-" + localDef["FlowRef"][i]])
                    validNumber = True
                except:
                    pass

                if validNumber:
                    flowIncDataLock.acquire()
                    if "F-" + localDef["FlowRef"][i] not in flowIncData:
                        flowIncData["F-" + localDef["FlowRef"][i]] = {}
                        flowIncData["F-" + localDef["FlowRef"][i]]["DBDateSAve"] = None
                        flowIncData["F-" + localDef["FlowRef"][i]]["HasFlow"] = False

                        if "F-" + localDef["FlowRef"][i] in lastDataFlow:
                            flowIncData["F-" + localDef["FlowRef"][i]]["Bias"] = lastDataFlow["F-" + localDef["FlowRef"][i]] # bias from last reg
                        else:
                            flowIncData["F-" + localDef["FlowRef"][i]]["Bias"] = 0 # if no register, bias is zero

                        flowIncData["F-" + localDef["FlowRef"][i]]["Read"] = []
                    if len(flowIncData["F-" + localDef["FlowRef"][i]]["Read"]) > 0:
                        # increment from last value
                        flowIncData["F-" + localDef["FlowRef"][i]]["Read"].append([dataRead['DateTime'], incrementL + flowIncData["F-" + localDef["FlowRef"][i]]["Read"][-1][1]])
                    else:
                        # start from 0 and check if bias from last turn off
                        flowIncData["F-" + localDef["FlowRef"][i]]["Read"].append([dataRead['DateTime'], incrementL + flowIncData["F-" + localDef["FlowRef"][i]]["Bias"]])

                    if len(flowIncData["F-" + localDef["FlowRef"][i]]["Read"]) > 20:
                        del flowIncData["F-" + localDef["FlowRef"][i]]["Read"][0]
                    flowIncDataLock.release()

def threadProcessDataIncCheckStop():
    global commandsFlowMLock, commandsFlowM

    dbDefinitions = db_logger_read_definitions()

    while threadProcFlowIncStopIsRunning:
        time.sleep(1)

        commandsFlowMLock.acquire()
        localDef = copy.deepcopy(commandsFlowM)
        commandsFlowMLock.release()

        # from all flow increment check if singal stop
        data2SaveDBList = [] # [FlowRate, DateTime, AccumInLiters]

        flowIncDataLock.acquire()
        for i in range(len(localDef["FlowRef"])):
            if "F-" + localDef["FlowRef"][i] in flowIncData and len(flowIncData["F-" + localDef["FlowRef"][i]]["Read"]) > 4:
                diffTimesList = []
                for j in range(1, len(flowIncData["F-" + localDef["FlowRef"][i]]["Read"])):
                    diffTimesList.append((flowIncData["F-" + localDef["FlowRef"][i]]["Read"][j][0] - flowIncData["F-" + localDef["FlowRef"][i]]["Read"][j - 1][0]).total_seconds())

                # estimate median
                diffTimesList.sort()
                mid = len(diffTimesList) // 2
                resMed = (diffTimesList[mid] + diffTimesList[~mid]) / 2.0

                # if median > 2X last reading, considering STOP
                isStop = (datetime.datetime.now() - flowIncData["F-" + localDef["FlowRef"][i]]["Read"][-1][0]).total_seconds() > 2*resMed
                if isStop:
                    flowIncData["F-" + localDef["FlowRef"][i]]["FlowRate"] = 0
                else:
                    last2RegDif = (flowIncData["F-" + localDef["FlowRef"][i]]["Read"][-1][0] - flowIncData["F-" + localDef["FlowRef"][i]]["Read"][-2][0]).total_seconds()
                    if last2RegDif <= 2*resMed:
                        litersBetweenReading = flowIncData["F-" + localDef["FlowRef"][i]]["Read"][-1][1] - flowIncData["F-" + localDef["FlowRef"][i]]["Read"][-2][1]
                        flowIncData["F-" + localDef["FlowRef"][i]]["FlowRate"] = litersBetweenReading / (last2RegDif / 60.0)
                    else:
                        flowIncData["F-" + localDef["FlowRef"][i]]["FlowRate"] = 0

                need2AddDB = False

                if flowIncData["F-" + localDef["FlowRef"][i]]["DBDateSAve"] == None:
                    need2AddDB = True
                elif isStop:
                    if flowIncData["F-" + localDef["FlowRef"][i]]["HasFlow"] or localDef["RateWitoutFlow"] == 0 or \
                        (localDef["RateWitoutFlow"] > 0 and (datetime.datetime.now() - flowIncData["F-" + localDef["FlowRef"][i]]["DBDateSAve"]).total_seconds()  / 60.0 > localDef["RateWitoutFlow"]):
                        need2AddDB = True
                    flowIncData["F-" + localDef["FlowRef"][i]]["HasFlow"] = False # no flow detected in valve
                elif not isStop:
                    # wather is passing
                    if localDef["RateWithFlow"] == 0 or not flowIncData["F-" + localDef["FlowRef"][i]]["HasFlow"] or (localDef["RateWithFlow"] > 0 and (flowIncData["F-" + localDef["FlowRef"][i]]["Read"][-1][0] - flowIncData["F-" + localDef["FlowRef"][i]]["DBDateSAve"]).total_seconds() / 60.0 > localDef["RateWithFlow"]):
                        need2AddDB = True

                if need2AddDB:
                    data2SaveDBList.append([flowIncData["F-" + localDef["FlowRef"][i]]["FlowRate"], flowIncData["F-" + localDef["FlowRef"][i]]["Read"][-1][0], flowIncData["F-" + localDef["FlowRef"][i]]["Read"][-1][1], localDef["FlowRef"][i]])
                    flowIncData["F-" + localDef["FlowRef"][i]]["DBDateSAve"] = flowIncData["F-" + localDef["FlowRef"][i]]["Read"][-1][0]
                    flowIncData["F-" + localDef["FlowRef"][i]]["HasFlow"] = not isStop
        flowIncDataLock.release()

        for data2SaveDB in data2SaveDBList:
            add_new_register(dbDefinitions, data2SaveDB[3], data2SaveDB[0], data2SaveDB[2], data2SaveDB[1])

def load_flows():
    global commandsFlowM, threadProcFlowIsRunning, threadProcFlow, threadProcFlowIncIsRunning, threadProcFlowInc, threadProcFlowIncStopIsRunning, threadProcFlowIncStop, lastDataFlow

    try:
        with open(u"./data/flow_multi_remote.json", u"r") as f:
            commandsFlowM = json.load(f)  # Read the commands from file
    except IOError:  #  If file does not exist create file with defaults.
        commandsFlowM = {"FlowRef": [], "ConvertionFactor": [], "CorrectionFactor": [], "SlowPulse": [], "PulseFromHTTP": [], "SensorPort": [], "Save2DB": True, "RateWithFlow": 5, "RateWitoutFlow": 15, "FlowRateUnits": "L/min", "FlowAccUnits": "L"}

    dbDefinitions = db_logger_read_definitions()

    init_db_if_needed(dbDefinitions, commandsFlowM)

    # get values from HTTPS increment, source don´t have memory, only send increments
    dataValues = get_last_accum_value(dbDefinitions, commandsFlowM)
    for keyValve in dataValues:
        valveRef = keyValve[2:]
        indexRef = commandsFlowM["FlowRef"].index(valveRef)
        if commandsFlowM["PulseFromHTTP"][indexRef]:
            valueAccum = dataValues[keyValve]["AccumFlow"]
            lastDataFlow["F-" + valveRef] = valueAccum

    threadProcFlowIsRunning = True
    threadProcFlow = Thread(target = threadProcessData)
    threadProcFlow.start()

    threadProcFlowIncIsRunning = True
    threadProcFlowInc = Thread(target = threadProcessDataInc)
    threadProcFlowInc.start()

    threadProcFlowIncStopIsRunning = True
    threadProcFlowIncStop = Thread(target = threadProcessDataIncCheckStop)
    threadProcFlowIncStop.start()

load_flows()

def getFlowReading(flowRef):
    global commandsFlowMLock, commandsFlowM, flowIncDataLock, flowIncData, flowDataOnDemandLock, flowDataOnDemand

    dataOut = {}

    dataOut["IsValid"] = False

    commandsFlowMLock.acquire()
    flowIncDataLock.acquire()
    flowDataOnDemandLock.acquire()
            
    # check in arduino sensors
    if "FL-" + flowRef in flowDataOnDemand:
        dataOut["RateFlow"] = flowDataOnDemand["FL-" + flowRef]["RateFlow"][-1]
        dataOut["AccumFlow"] = flowDataOnDemand["FL-" + flowRef]["AccumFlow"][-1]
        dataOut["LastDateTime"] = ""
        dataOut["IsValid"] = True

    # check in pulse https request
    if "F-" + flowRef in flowIncData:
        dataOut["RateFlow"] = flowIncData["F-" + flowRef]["FlowRate"]
        dataOut["AccumFlow"] = flowIncData["F-" + flowRef]["Read"][-1][1]
        dataOut["IsValid"] = True

    flowDataOnDemandLock.release()
    flowIncDataLock.release()
    commandsFlowMLock.release()

    return dataOut

class settings(ProtectedPage):
    """Returns plugin settings in JSON format"""

    def GET(self):
        global commandsFlowM

        commandsFlowMLock.acquire()
        commandsFlowMLocal = copy.deepcopy(commandsFlowM)
        commandsFlowMLock.release()

        return template_render.flow_multi_settings(commandsFlowMLocal)

class settings_json(ProtectedPage):
    """Returns plugin settings in JSON format"""

    def GET(self):
        global commandsFlowM

        web.header(u"Access-Control-Allow-Origin", u"*")
        web.header(u"Content-Type", u"application/json")
        return json.dumps(commandsFlowM)

class home(ProtectedPage):
    """Return status of valve"""

    def GET(self):
        global commandsFlowM, commandsFlowMLock

        commandsFlowMLock.acquire()
        commandsFlowMLocal = copy.deepcopy(commandsFlowM)
        commandsFlowMLock.release()

        return template_render.flow_multi_home(commandsFlowMLocal)

class update_reading(ProtectedPage):
    """Return status of valve"""

    def GET(self):
        global commandsFlowM

        qdict = web.input()

        return "|correctionfactor"

class settings_save(ProtectedPage):
    """Return status of valve"""

    def GET(self):
        global commandsFlowM, commandsFlowMLock

        qdict = web.input()

        commandsFlowMLock.acquire()
        if "FlowRate2Save" in qdict:
            try:
                commandsFlowM["RateWithFlow"] = float(qdict["FlowRate2Save"])
            except:
                pass

        if "NoFlowRate2Save" in qdict:
            try:
                commandsFlowM["RateWitoutFlow"] = float(qdict["NoFlowRate2Save"])
            except:
                pass

        commandsFlowM["Save2DB"] = "FlowSave2DB" in qdict

        if "FlowRateUnits" in qdict:
            commandsFlowM["FlowRateUnits"] = qdict["FlowRateUnits"]

        if "WatherSumUnits" in qdict:
            commandsFlowM["FlowAccUnits"] = qdict["WatherSumUnits"]

        for i in range(len(commandsFlowM["FlowRef"])):
            if "FlowRef" + str(i) in qdict:
                commandsFlowM["FlowRef"][i] = qdict["FlowRef" + str(i)]
            if "ConvertionFactor" + str(i) in qdict:
                try:
                    commandsFlowM["ConvertionFactor"][i] = float(qdict["ConvertionFactor" + str(i)].replace(",", "."))
                except:
                    pass
            if "CorrectionFactor" + str(i) in qdict:
                try:
                    commandsFlowM["CorrectionFactor"][i] = float(qdict["CorrectionFactor" + str(i)].replace(",", "."))
                except:
                    pass
            commandsFlowM["SlowPulse"][i] = "SlowPulse" + str(i) in qdict
            commandsFlowM["PulseFromHTTP"][i] = "FlowHTTPPulse" + str(i) in qdict

        # save 2 file definitions
        with open(u"./data/flow_multi_remote.json", u"w") as f:
            json.dump(commandsFlowM, f, indent=4)
        commandsFlowMLock.release()

        raise web.seeother(u"/flow-set")

class setting_add_new(ProtectedPage):
    """Return status of valve"""

    def GET(self):
        global commandsFlowM, commandsFlowMLock

        commandsFlowMLock.acquire()
        commandsFlowM["FlowRef"].append(getRandomString(5))
        commandsFlowM["ConvertionFactor"].append(0.5)
        commandsFlowM["CorrectionFactor"].append(1)
        commandsFlowM["SlowPulse"].append(False)
        commandsFlowM["SensorPort"].append(0)
        commandsFlowMLock.release()

        raise web.seeother(u"/flow-set")

class setting_arduino_first_data(ProtectedPage):
    """Get last values save"""

    def GET(self):
        commandsFlowMLock.acquire()
        flowDefinitionLocal = copy.deepcopy(commandsFlowM)
        commandsFlowMLock.release()

        dbDefinitions = db_logger_read_definitions()
        dataValues = get_last_accum_value(dbDefinitions, flowDefinitionLocal)

        if len(dataValues) > 0:
            dataOut = "|"
        else:
            dataOut = ""

        for keyValve in dataValues:
            valveRef = keyValve[2:]
            valueAccum = dataValues[keyValve]["AccumFlow"]
            dataOut = dataOut + str(valveRef) + "|" + str(valueAccum) + "|"

        return dataOut

class setting_arduino(ProtectedPage):
    """Return status of valve"""

    def GET(self):
        global commandsFlowQueu

        qdict = web.input()

        # add date time as referece
        readingDatime = datetime.datetime.now()
        qdict['DateTime'] = readingDatime

        commandsFlowQueu.put(qdict)

class flow_http_increment(ProtectedPage):
    """receive increment"""

    def GET(self):
        global commFlowIncQueu

        qdict = web.input()

        # add date time as referece
        readingDatime = datetime.datetime.now()
        qdict['DateTime'] = readingDatime

        commFlowIncQueu.put(qdict)

class get_value_on_demand_flow(ProtectedPage):
    def GET(self):
        global commandsFlowMLock, commandsFlowM, flowIncDataLock, flowIncData, flowDataOnDemandLock, flowDataOnDemand

        qdict = web.input()

        str2Return = "none"

        if "FlowRef" in qdict:
            sensorData = getFlowReading(qdict["FlowRef"])
            if sensorData["IsValid"]:
                str2Return = str(sensorData["RateFlow"])

        return str2Return

class get_value_on_demand_acc(ProtectedPage):
    def GET(self):

        qdict = web.input()

        str2Return = "none"

        if "FlowRef" in qdict:
            sensorData = getFlowReading(qdict["FlowRef"])
            if sensorData["IsValid"]:
                str2Return = str(sensorData["AccumFlow"])

        return str2Return
