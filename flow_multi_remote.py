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
    from db_logger_flow import get_last_accum_value, init_db_if_needed, add_new_register, check_and_add_flow, add_valve_flow, get_last_valve_accum_val
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

threadDevicesFlowAfected = None
threadDevicesFlowAfectedIsRunning = False

# variables related to each valves flow
valveFlowCurrentVal = []
valveFlowLastFlowReading = []
valveFlowLock = Lock()


def threadProcessData():
    global commandsFlowQueu, threadProcFlowIsRunning, commandsFlowM

    dbDefinitions = db_logger_read_definitions()

    while threadProcFlowIsRunning:
        dataRead = commandsFlowQueu.get()        

        listValuesValves2Add = {}
        listFlow2Save = []

        # lok global variables relative to flow meter and valves
        commandsFlowMLock.acquire()
        flowDataOnDemandLock.acquire()
        valveFlowLock.acquire()

        for i in range(len(commandsFlowM["FlowRef"])):
            if "FR-" + commandsFlowM["FlowRef"][i] in dataRead and "FA-" + commandsFlowM["FlowRef"][i] in dataRead and "DateTime" in dataRead:
                validDigit = False
                flowRate = 0
                flowAccum = 0

                try:
                    flowRate = float(dataRead["FR-" + commandsFlowM["FlowRef"][i]])
                    flowAccum = float(dataRead["FA-" + commandsFlowM["FlowRef"][i]])
                    validDigit = True
                except:
                    pass

                if validDigit:
                    # Check if exists initial value
                    if "FL-" + commandsFlowM["FlowRef"][i] not in flowDataOnDemand:
                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]] = {}

                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["AccumFlow"] = []
                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["RateFlow"] = []
                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["FlowDate"] = []

                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["DBDateSAve"] = None
                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["HasFlow"] = False

                    flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["AccumFlow"].append(flowAccum)
                    flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["RateFlow"].append(flowRate)
                    flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["FlowDate"].append(dataRead["DateTime"])

                    # check if last save DB too long add new value
                    need2Save2DB = False

                    # If first data need to save to DB
                    if flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["DBDateSAve"] == None:
                        need2Save2DB = True
                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["HasFlow"] = flowRate > 0.001
                    elif flowRate > 0.001:
                        # if with flow and delta time = 0 save any data
                        if commandsFlowM["RateWithFlow"] == 0 or \
                            (commandsFlowM["RateWithFlow"] > 0 and (dataRead["DateTime"] - flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["DBDateSAve"]).total_seconds() / 60.0 > commandsFlowM["RateWithFlow"]):
                            need2Save2DB = True
                            flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["HasFlow"] = True
                    elif flowRate <= 0.001 and \
                        (flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["HasFlow"] and commandsFlowM["RateWitoutFlow"] == 0) or \
                        (commandsFlowM["RateWitoutFlow"] > 0 and (dataRead["DateTime"] - flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["DBDateSAve"]).total_seconds() / 60.0 > commandsFlowM["RateWitoutFlow"]):
                        need2Save2DB = True
                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["HasFlow"] = False

                    if need2Save2DB:
                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["DBDateSAve"] = dataRead["DateTime"]

                    # check if any problem with flow
                    # TODO

                    if len(flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["AccumFlow"]) > 100:
                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["AccumFlow"].remove(0)
                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["RateFlow"].remove(0)
                        flowDataOnDemand["FL-" + commandsFlowM["FlowRef"][i]]["FlowDate"].remove(0)

                    # Outside lock save to DB if case for
                    if need2Save2DB:
                        # save raw data
                        listFlow2Save.append([commandsFlowM["FlowRef"][i], commandsFlowM["SensorPort"][i], commandsFlowM["CorrectionFactor"][i], commandsFlowM["SlowPulse"][i], flowRate, flowAccum, dataRead["DateTime"]])

        commandsFlowMLock.release()
        valveFlowLock.release()
        flowDataOnDemandLock.release()

        # save data from flow meters
        for dataValFlow2Save in listFlow2Save:
            saveFlowRef = dataValFlow2Save[0]
            saveSensorPort = dataValFlow2Save[1]
            saveCorrectionFactor = dataValFlow2Save[2]
            saveSlowPulse = dataValFlow2Save[3]
            saveFlowRate = dataValFlow2Save[4]
            saveAccumRate = dataValFlow2Save[5]
            saveDateTime = dataValFlow2Save[6]

            check_and_add_flow(dbDefinitions, saveFlowRef, saveSensorPort, saveCorrectionFactor, saveSlowPulse)
            add_new_register(dbDefinitions, saveFlowRef, saveFlowRate, saveAccumRate, saveDateTime)

        # save data from valves
        for dataVal2SaveKey in listValuesValves2Add:
            dataVal2Save = listValuesValves2Add[dataVal2SaveKey]

            currValveId = dataVal2Save[0]
            currValveAccum = dataVal2Save[1]
            currValveFlow = dataVal2Save[2]
            currValveDateTime = dataVal2Save[3]
            add_valve_flow(dbDefinitions, currValveId, currValveFlow, currValveAccum, currValveDateTime)

def threadProcessDataInc():
    global lastDataFlow, flowIncData, commFlowIncQueu

    while threadProcFlowIncIsRunning:
        dataRead = commFlowIncQueu.get()

        commandsFlowMLock.acquire()
        flowIncDataLock.acquire()

        for i in range(len(commandsFlowM["FlowRef"])):
            if "FR-" + commandsFlowM["FlowRef"][i] in dataRead and "FI-" + commandsFlowM["FlowRef"][i] in dataRead and "DateTime" in dataRead:
                validNumber = False
                incrementL = 0
                try:
                    incrementL = float(dataRead["FI-" + commandsFlowM["FlowRef"][i]])
                    validNumber = True
                except:
                    pass

                if validNumber:
                    if "F-" + commandsFlowM["FlowRef"][i] not in flowIncData:
                        flowIncData["F-" + commandsFlowM["FlowRef"][i]] = {}
                        flowIncData["F-" + commandsFlowM["FlowRef"][i]]["DBDateSAve"] = None
                        flowIncData["F-" + commandsFlowM["FlowRef"][i]]["HasFlow"] = False

                        if "F-" + commandsFlowM["FlowRef"][i] in lastDataFlow:
                            flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Bias"] = lastDataFlow["F-" + commandsFlowM["FlowRef"][i]] # bias from last reg
                        else:
                            flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Bias"] = 0 # if no register, bias is zero

                        flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"] = []
                    if len(flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"]) > 0:
                        # increment from last value
                        flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"].append([dataRead['DateTime'], incrementL + flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][-1][1]])
                    else:
                        # start from 0 and check if bias from last turn off
                        flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"].append([dataRead['DateTime'], incrementL + flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Bias"]])

                    if len(flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"]) > 20:
                        del flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][0]
                    
        commandsFlowMLock.release()
        flowIncDataLock.release()

def threadProcessDataIncCheckStop():
    global commandsFlowMLock, commandsFlowM

    dbDefinitions = db_logger_read_definitions()

    while threadProcFlowIncStopIsRunning:
        time.sleep(1)

        # from all flow increment check if singal stop
        data2SaveDBList = [] # [FlowRate, DateTime, AccumInLiters]

        commandsFlowMLock.acquire()
        flowIncDataLock.acquire()

        for i in range(len(commandsFlowM["FlowRef"])):
            if "F-" + commandsFlowM["FlowRef"][i] in flowIncData and len(flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"]) > 4:
                diffTimesList = []
                for j in range(1, len(flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"])):
                    diffTimesList.append((flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][j][0] - flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][j - 1][0]).total_seconds())

                # estimate median
                diffTimesList.sort()
                mid = len(diffTimesList) // 2
                resMed = (diffTimesList[mid] + diffTimesList[~mid]) / 2.0

                # if median > 2X last reading, considering STOP
                isStop = (datetime.datetime.now() - flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][-1][0]).total_seconds() > 2*resMed
                if isStop:
                    flowIncData["F-" + commandsFlowM["FlowRef"][i]]["FlowRate"] = 0
                else:
                    last2RegDif = (flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][-1][0] - flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][-2][0]).total_seconds()
                    if last2RegDif <= 2*resMed:
                        litersBetweenReading = flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][-1][1] - flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][-2][1]
                        flowIncData["F-" + commandsFlowM["FlowRef"][i]]["FlowRate"] = litersBetweenReading / (last2RegDif / 60.0)
                    else:
                        flowIncData["F-" + commandsFlowM["FlowRef"][i]]["FlowRate"] = 0

                need2AddDB = False

                if flowIncData["F-" + commandsFlowM["FlowRef"][i]]["DBDateSAve"] == None:
                    need2AddDB = True
                elif isStop:
                    if flowIncData["F-" + commandsFlowM["FlowRef"][i]]["HasFlow"] or commandsFlowM["RateWitoutFlow"] == 0 or \
                        (commandsFlowM["RateWitoutFlow"] > 0 and (datetime.datetime.now() - flowIncData["F-" + commandsFlowM["FlowRef"][i]]["DBDateSAve"]).total_seconds()  / 60.0 > commandsFlowM["RateWitoutFlow"]):
                        need2AddDB = True
                    flowIncData["F-" + commandsFlowM["FlowRef"][i]]["HasFlow"] = False # no flow detected in valve
                elif not isStop:
                    # wather is passing
                    if commandsFlowM["RateWithFlow"] == 0 or not flowIncData["F-" + commandsFlowM["FlowRef"][i]]["HasFlow"] or (commandsFlowM["RateWithFlow"] > 0 and (flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][-1][0] - flowIncData["F-" + commandsFlowM["FlowRef"][i]]["DBDateSAve"]).total_seconds() / 60.0 > commandsFlowM["RateWithFlow"]):
                        need2AddDB = True

                if need2AddDB:
                    data2SaveDBList.append([flowIncData["F-" + commandsFlowM["FlowRef"][i]]["FlowRate"], flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][-1][0], flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][-1][1], commandsFlowM["FlowRef"][i]])
                    flowIncData["F-" + commandsFlowM["FlowRef"][i]]["DBDateSAve"] = flowIncData["F-" + commandsFlowM["FlowRef"][i]]["Read"][-1][0]
                    flowIncData["F-" + commandsFlowM["FlowRef"][i]]["HasFlow"] = not isStop

        commandsFlowMLock.release()
        flowIncDataLock.release()

        for data2SaveDB in data2SaveDBList:
            flowRateSave = data2SaveDB[0]
            flowDateTime = data2SaveDB[1]
            flowAccumValue = data2SaveDB[2]
            flowRefSave = data2SaveDB[3]

            add_new_register(dbDefinitions, flowRefSave, flowRateSave, flowAccumValue, flowDateTime)

def threadProcessDevicesAfectedByFlow():
    global threadDevicesFlowAfectedIsRunning, commandsFlowMLock, flowIncDataLock, flowDataOnDemandLock, valveFlowLock, valveFlowLastFlowReading, valveFlowCurrentVal

    dbDefinitions = db_logger_read_definitions()

    priorFlow = gv.srvals[:]
    curretntValveState = gv.srvals[:]
    numberOfValves  = len(gv.srvals)

    lastTimeSaveValve2DB = []
    for i in range(len(gv.srvals)):
        lastTimeSaveValve2DB.append(None)

    while threadDevicesFlowAfectedIsRunning:
        time.sleep(1)

        if numberOfValves > len(gv.srvals):
            priorFlow = priorFlow[:len(gv.srvals)]
            lastTimeSaveValve2DB = lastTimeSaveValve2DB[:len(gv.srvals)]
        elif numberOfValves < len(gv.srvals):
            priorFlow = gv.srvals[:]
            for i in range(numberOfValves, gv.srvals):
                lastTimeSaveValve2DB.append(None)

        numberOfValves  = len(gv.srvals)
        curretntValveState = gv.srvals[:]

        list2SaveDB = []

        commandsFlowMLock.acquire()
        flowIncDataLock.acquire()
        flowDataOnDemandLock.acquire()
        valveFlowLock.acquire()

        if len(valveFlowLastFlowReading) > numberOfValves:
            valveFlowLastFlowReading = valveFlowLastFlowReading[:numberOfValves]
            valveFlowCurrentVal = valveFlowCurrentVal[:numberOfValves]
        elif len(valveFlowLastFlowReading) < numberOfValves:
            difOfLen = numberOfValves - len(valveFlowLastFlowReading)
            for i in range(difOfLen):
                valveFlowLastFlowReading.append({})
                valveFlowCurrentVal.append(0)

        for i in range(numberOfValves):
            # check if valve affected by any valve
            afectedByFlow = False
            for j in range(len(commandsFlowM["ValvesAffected"])):
                if i in commandsFlowM["ValvesAffected"][j]:
                    afectedByFlow = True

            if afectedByFlow:
                # if start on and before of save to DB flow or on by last reading to long or on and turn to off
                if (curretntValveState[i] and not priorFlow[i]) or (curretntValveState[i] and lastTimeSaveValve2DB[i] != None and (datetime.datetime.now() - lastTimeSaveValve2DB[i]).total_seconds()  / 60.0 > 1.0) or (not curretntValveState[i] and priorFlow[i]):
                    currentDate2Save = datetime.datetime.now()
                    lastTimeSaveValve2DB[i] = currentDate2Save

                    # count for each valve number active valves
                    numberActiveValves = []
                    for k in range(len(commandsFlowM["ValvesAffected"])):
                        numberActiveValves.append(0)
                        for l in range(numberOfValves):
                            if (curretntValveState[l] or (not curretntValveState[l] and priorFlow[l])) and l in commandsFlowM["ValvesAffected"][k]:
                                numberActiveValves[k] = numberActiveValves[k] + 1.0

                        # check number of tanks
                        # TODO

                    currentFlow = 0

                    for k in range(len(commandsFlowM["ValvesAffected"])):
                        currentData = getFlowReading(commandsFlowM["FlowRef"][k], False)

                        if i in commandsFlowM["ValvesAffected"][k]:
                            if commandsFlowM["FlowRef"][k] in valveFlowLastFlowReading[i]:
                                lastValue = valveFlowLastFlowReading[i][commandsFlowM["FlowRef"][k]]
                                valveFlowCurrentVal[i] = valveFlowCurrentVal[i] + (currentData["AccumFlow"] - lastValue) / numberActiveValves[k]

                            # else:  sum 0 to total accum, need at least a previous reading, by can increment flow

                            valveFlowLastFlowReading[i][commandsFlowM["FlowRef"][k]] = currentData["AccumFlow"] # save current as last
                            currentFlow = currentFlow + currentData["RateFlow"] / numberActiveValves[k]

                    list2SaveDB.append([valveFlowCurrentVal[i], currentFlow, i, currentDate2Save])

            # for all off valve crean last flow reading
            if not curretntValveState[i]:
                valveFlowLastFlowReading[i] = {}

        commandsFlowMLock.release()
        flowIncDataLock.release()
        flowDataOnDemandLock.release()
        valveFlowLock.release()

        priorFlow = curretntValveState

        # Save to DB
        for curr2Save in list2SaveDB:
            currentAccum2Save = curr2Save[0]
            currentFlow2Save = curr2Save[1]
            valve2Save = curr2Save[2]
            dateTime = curr2Save[3]

            add_valve_flow(dbDefinitions, valve2Save, currentFlow2Save, currentAccum2Save, dateTime)


def load_flows():
    global commandsFlowM, threadProcFlowIsRunning, threadProcFlow, threadProcFlowIncIsRunning, threadProcFlowInc, threadProcFlowIncStopIsRunning, threadProcFlowIncStop, lastDataFlow, threadDevicesFlowAfectedIsRunning, threadDevicesFlowAfected

    try:
        with open(u"./data/flow_multi_remote.json", u"r") as f:
            commandsFlowM = json.load(f)  # Read the commands from file
    except IOError:  #  If file does not exist create file with defaults.
        commandsFlowM = {"FlowRef": [], "ConvertionFactor": [], "CorrectionFactor": [], "SlowPulse": [], "PulseFromHTTP": [], "SensorPort": [], "ValvesAffected": [], "Save2DB": True, "RateWithFlow": 5, "RateWitoutFlow": 15, "FlowRateUnits": "L/min", "FlowAccUnits": "L"}

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

    for j in range(len(gv.srvals)):
        valveFlowLastFlowReading.append({})

        # Get value from DB
        lastAccumValue = get_last_valve_accum_val(dbDefinitions, j)
        valveFlowCurrentVal.append(lastAccumValue)

    threadProcFlowIsRunning = True
    threadProcFlow = Thread(target = threadProcessData)
    threadProcFlow.start()

    threadProcFlowIncIsRunning = True
    threadProcFlowInc = Thread(target = threadProcessDataInc)
    threadProcFlowInc.start()

    threadProcFlowIncStopIsRunning = True
    threadProcFlowIncStop = Thread(target = threadProcessDataIncCheckStop)
    threadProcFlowIncStop.start()

    threadDevicesFlowAfectedIsRunning = True
    threadDevicesFlowAfected = Thread(target = threadProcessDevicesAfectedByFlow)
    threadDevicesFlowAfected.start()

load_flows()

def getFlowReading(flowRef, useLocks = True):
    global commandsFlowMLock, commandsFlowM, flowIncDataLock, flowIncData, flowDataOnDemandLock, flowDataOnDemand

    dataOut = {}

    dataOut["IsValid"] = False

    if useLocks:
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
        if flowIncData["F-" + flowRef]["HasFlow"]:
            dataOut["RateFlow"] = flowIncData["F-" + flowRef]["FlowRate"]
        else:
            dataOut["RateFlow"] = 0

        dataOut["AccumFlow"] = flowIncData["F-" + flowRef]["Read"][-1][1]
        dataOut["IsValid"] = True
    elif "F-" + flowRef in lastDataFlow:
        dataOut["RateFlow"] = 0
        dataOut["AccumFlow"] = lastDataFlow["F-" + flowRef]
        dataOut["IsValid"] = True

    if useLocks:
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

            commandsFlowM["ValvesAffected"][i] = []

            for bid in range(0,gv.sd['nbrd']):
                for s in range(0,8):
                    sid = bid*8 + s;
                    if "Flow"+ str(i) +"Valve"+ str(sid) in qdict:
                        commandsFlowM["ValvesAffected"][i].append(sid)

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
        commandsFlowM["ValvesAffected"].append([])
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
                commandsFlowMLock.acquire()
                # by default use L/min, if in another units, need to be converted
                if commandsFlowM["FlowRateUnits"] == 'Lmin':
                    str2Return = str(round(sensorData["RateFlow"], 2)) +" L/min"
                elif commandsFlowM["FlowRateUnits"] == 'Lhour':
                    str2Return = str(round(convertLitersByMinute2LitersByHour(sensorData["RateFlow"]), 2)) +" L/hour"
                elif commandsFlowM["FlowRateUnits"] == 'm3hour':
                    str2Return = str(round(convertLitersByMinute2m3ByHour(sensorData["RateFlow"]), 5)) +" m^3/h"
                elif commandsFlowM["FlowRateUnits"] == 'galmin':
                    str2Return = str(round(convertLitersByMinute2GallonsByMinute(sensorData["RateFlow"]), 5)) +" gal/min"
                elif commandsFlowM["FlowRateUnits"] == 'galh':
                    str2Return = str(round(convertLitersByMinute2GallonsByHour(sensorData["RateFlow"]), 5)) +" gal/hour"
                commandsFlowMLock.release()

        return str2Return

class get_value_on_demand_acc(ProtectedPage):
    def GET(self):

        qdict = web.input()

        str2Return = "none"

        if "FlowRef" in qdict:
            sensorData = getFlowReading(qdict["FlowRef"])
            if sensorData["IsValid"]:
                commandsFlowMLock.acquire()
                if commandsFlowM["FlowAccUnits"] == 'liters':
                    str2Return = str(round(sensorData["AccumFlow"], 2)) +" L"
                elif commandsFlowM["FlowAccUnits"] == 'm3':
                    str2Return = str(round(convertLiters2m3(sensorData["AccumFlow"]), 5)) +" m^3"
                elif commandsFlowM["FlowAccUnits"] == 'gallonUS':
                    str2Return = str(round(convertLiters2Gal(sensorData["AccumFlow"]), 5)) +" gal"
                commandsFlowMLock.release()

        return str2Return
