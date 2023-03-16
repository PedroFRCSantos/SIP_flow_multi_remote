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
    withDBLogger = True
except ImportError:
    withDBLogger = False

from flow_multi_remote_aux import *

# Add a new url to open the data entry page.
# fmt: off
urls.extend(
    [
        u"/flow-home", u"plugins.flow_multi_remote.home",
        u"/flow-set", u"plugins.flow_multi_remote.settings",
        u"/flow-set-save", u"plugins.flow_multi_remote.settings_save",
        u"/flow-add-new", u"plugins.flow_multi_remote.setting_add_new",
        u"/flow-arduino", u"plugins.flow_multi_remote.setting_arduino",
        u"/flow-json", u"plugins.flow_multi_remote.settings_json",
        u"/flow-update", u"plugins.flow_multi_remote.update_reading",
    ]
)

gv.plugin_menu.append([u"Flow Muti Remote", u"/flow-home"])

commandsFlowM = {}
commandsFlowMLock = Lock()

def load_flows():
    global commandsFlowM

    try:
        with open(u"./data/flow_multi_remote.json", u"r") as f:
            commandsFlowM = json.load(f)  # Read the commands from file
    except IOError:  #  If file does not exist create file with defaults.
        commandsFlowM = {"FlowRef": [], "ConvertionFactor": [], "CorrectionFactor": [], "SlowPulse": [], "SensorPort": [], "Save2DB": True, "RateWithFlow": 5, "RateWitoutFlow": 15, "FlowRateUnits": "L/min", "FlowAccUnits": "L"}

load_flows()

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
                commandsFlowM["RateWithFlow"] = qdict["FlowRate2Save"]
            except:
                pass

        if "NoFlowRate2Save" in qdict:
            try:
                commandsFlowM["RateWitoutFlow"] = qdict["NoFlowRate2Save"]
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

class setting_arduino(ProtectedPage):
    """Return status of valve"""

    def GET(self):
        global commandsFlowM
