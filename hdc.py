#!/usr/bin/env python3

import paho.mqtt.client as mqtt
import time, signal, subprocess, http.client, urllib, re, json, atexit, logging, socket
import traceback, os
#from functools import partial
from daemon import Daemon
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from enum import Enum
from typing import *
from multitimer import MultiTimer
from confirmation_threshold import confirmation_threshold
from threading import Event
from threading import Thread

class HDCDaemon(Daemon):
  def run(self):
    h_datacollector = HDC()
    my_path = os.path.dirname(os.path.abspath(__file__))
    with open(my_path + "/hdc_config.json", "r") as config:
        h_datacollector.config = HDC.config.from_json(config.read())

    h_datacollector.run()

# Class for acquisition object.
# The name gets published to the MQTT JSON string
# The type determines how it is handled
# The object is handled depending on the type
@dataclass_json
@dataclass
class Acquisition:
    name: str
    acType: str
    acObject: Union[List[str], int]

# state machine for temperature sensor power network restart
# should probably add the state machine diagram in ascii art here
class TempSensorPower:
  class PowerState(Enum):
    INIT = 0
    RESTART = 1
    CHECK = 2

  def __init__(self, allowedRestarts=0):
    self.state = self.PowerState.INIT
    self.allowedRestarts = allowedRestarts
    self.restarts = 0
    self.broke = False

  # state machine run function
  # lastPower is the last commanded one-wire network power status
  # power is the commanded power state this cycle
  #     note: power is set to true outside of this function!
  # reception is whether the sensor is returning valid data
  # fault is whether the power network is faulted
  def run(self, lastPower, power, reception, fault):
    # if the power network is working (lastPower and not fault)
    # and the sensor is still not receiving
    # then the sensor must be broken!
    self.broke = lastPower and not fault and not reception
    # transitions
    if self.state == self.PowerState.INIT:
      if self.broke:
        self.state = self.PowerState.RESTART
    elif self.state == self.PowerState.RESTART:
      self.state = self.PowerState.CHECK
    elif self.state == self.PowerState.CHECK:
      if self.allowedRestarts == 0 or self.restarts < self.allowedRestarts:
        if self.broke:
          self.state = self.PowerState.RESTART
      if not self.broke:
          self.state = self.PowerState.INIT
    else:
      self.state = self.PowerState.INIT

    # output
    if self.state == self.PowerState.RESTART:
      power = False
      self.restarts += 1
    return power

class HDC(mqtt.Client):
  """Watches the door and monitors various switches and motion via GPIO"""

  version = '2020'
  # dataclass variable declaration
  @dataclass_json
  @dataclass
  class config:
    name: str
    description: str
    boot_check_list: Dict[str, List[str]]
    acq_io: List[Acquisition]
    long_checkup_freq: int
    long_checkup_leng: int
    gpio_path: str
    mqtt_broker: str
    mqtt_port: int
    mqtt_timeout: int
    temp_max_restart: int = 3
    loglevel: Optional[str] = None

  # overloaded MQTT functions from (mqtt.Client)
  def on_log(self, client, userdata, level, buff):
    if level == mqtt.MQTT_LOG_DEBUG:
      logging.debug("PAHO MQTT DEBUG: " + buff)
    elif level == mqtt.MQTT_LOG_INFO:
      logging.info("PAHO MQTT INFO: " + buff)
    elif level == mqtt.MQTT_LOG_NOTICE:
      logging.info("PAHO MQTT NOTICE: " + buff)
    elif level == mqtt.MQTT_LOG_WARNING:
      logging.warning("PAHO MQTT WARN: " + buff)
    else:
      logging.error("PAHO MQTT ERROR: " + buff)

  def on_connect(self, client, userdata, flags, rc):
    logging.info("Connected: " + str(rc))
    self.subscribe("reporter/checkup_req")
    self.subscribe(self.config.name + "/temp_power")
    self.dmthread = Thread(target = self.deadman_checkup)
    self.dmthread.start()

  def on_message(self, client, userdata, message):
    if (message.topic == "reporter/checkup_req"):
      logging.info("Checkup received.")
      self.check_now.set()
      self.checkup()
      self.dmthread.join()
      self.dmthread = Thread(target = self.deadman_checkup)
      self.dmthread.start()
    elif (message.topic == self.config.name + "/temp_power"):
      decoded = message.payload.decode('utf-8')
      logging.debug("Temperature sensor power command received: " + decoded)
      if (decoded.lower() == "false" or decoded == "0"):
        logging.info("Temperature sensor power commanded off")
        self.runtime.temp_power_commanded = False
      else:
        logging.info("Temperature sensor power commanded on")
        self.runtime.temp_power_commanded = True

  def on_disconnect(self, client, userdata, rc):
    logging.warning("Disconnected: " + str(rc))
    self.check_now.set()
    self.dmthread.join()
    if rc != 0:
        logging.error("Unexpected disconnection.  Attempting reconnection.")
        reconnect_count = 0
        while (reconnect_count < 10):
            try:
                reconnect_count += 1
                self.reconnect()
                break
            except OSError:
                logging.error("Connection error while trying to reconnect.")
                logging.error(traceback.format_exc())
                logging.error("Waiting to restart.")
                self.tEvent.wait(30)
        if reconnect_count >= 10:
            logging.critical("Too many reconnect tries.  Exiting.")
            os._exit(1)

  # HDC functions
  def enable_gpio(self):
    global GPIO
    if not self.config.gpio_path:
      logging.debug("Configuring GPIOs")
      import orangepi.one
      import OPi.GPIO as GPIO
      GPIO.setmode(orangepi.one.BOARD)
      GPIO.setwarnings(False)
    else:
      logging.debug("Configuring GPIOs at: " + self.config.gpio_path)
      import RPi.GPIO as GPIO
      GPIO.setmode(GPIO.BCM)

    # sort GPIOs
    self.runtime = type("Runtime", (object, ), {})
    self.runtime.switch_channels = {}
    self.runtime.flip_channels = {}
    self.runtime.pir_channels = {}
    self.runtime.last_pir_state = {}
    self.runtime.ct_ios = {}
    self.runtime.temp_channels = {}
    self.runtime.temp_power_sm = {}
    for acq in self.config.acq_io:
      if acq.acType == "SW":
        logging.debug("Configuring Switch: " + str(acq.acObject))
        GPIO.setup(acq.acObject, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.runtime.switch_channels.update({acq.name : acq.acObject})
        self.runtime.ct_ios.update({acq.name : confirmation_threshold(GPIO.input(acq.acObject),3)})
      elif acq.acType == "SW_INV":
        logging.debug("Configuring invSwitch: " + str(acq.acObject))
        GPIO.setup(acq.acObject, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.runtime.flip_channels.update({acq.name : acq.acObject})
        self.runtime.ct_ios.update({acq.name : confirmation_threshold(not GPIO.input(acq.acObject),3)})
      elif acq.acType == "PIR":
        logging.debug("Configuring PIR Sensor: " + str(acq.acObject))
        GPIO.setup(acq.acObject, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.runtime.pir_channels.update({acq.name : acq.acObject})
        self.runtime.ct_ios.update({acq.name : confirmation_threshold(GPIO.input(acq.acObject),3)})
        self.runtime.last_pir_state.update({acq.name : 0})
      elif acq.acType == "TEMP":
        logging.debug("Configuring Temperature Sensor: " + str(acq.acObject))
        self.runtime.temp_channels.update({acq.name : acq.acObject})
        self.runtime.temp_power_sm.update({acq.name : TempSensorPower(self.config.temp_max_restart)})
      elif acq.acType == "TEMP_FAULT":
        try:
          self.runtime.temp_fault
          raise KeyError("Temperature sensor fault channel already allocated")
        except AttributeError:
          logging.debug("Configuring Temperature Power Fault: " + str(acq.acObject))
          self.runtime.temp_fault = acq.acObject
          GPIO.setup(acq.acObject, GPIO.IN, pull_up_down=GPIO.PUD_UP)
          self.runtime.temp_fault_sm = confirmation_threshold(not GPIO.input(acq.acObject),3)
      elif acq.acType == "TEMP_EN":
        try:
          self.runtime.temp_en
          raise KeyError("Temperature sensor enable channel already allocated")
        except AttributeError:
          logging.debug("Configuring Temperature Power Enable: " + str(acq.acObject))
          self.runtime.temp_en = acq.acObject
          GPIO.setup(acq.acObject, GPIO.OUT)
          self.runtime.temp_power_commanded = True
          self.runtime.temp_power_on = True
          self.runtime.temp_power_last = True
      else:
        raise KeyError('"' + acq.acType + '"' + " is not a valid acquisition type")

  def notify(self, path, params, retain=False):
    params['time'] = str(time.time())
    logging.debug(params)

    topic = self.config.name + '/' + path
    self.publish(topic, json.dumps(params), retain=retain)
    logging.info("Published " + topic)
  
  def notify_bootup(self):
    boot_checks = {}

    logging.debug("Bootup:")
    
    for bc_name, bc_cmd in self.config.boot_check_list.items():
        boot_checks[bc_name] = subprocess.check_output(
                bc_cmd, 
                shell=True
        ).decode('utf-8')

    self.notify('bootup', boot_checks, retain=True)
  
  def bootup(self):

    # invert dictionary for reporting
    self.enable_gpio()
    self.pings = 0

    signal.signal(signal.SIGINT, self.signal_handler)
    signal.signal(signal.SIGTERM, self.signal_handler)
    self.running = True
    self.exiting = False
    
  def check_temp(self, temp_path):
    value = ""
    try:
      temp = subprocess.check_output(["cat", temp_path])
      match = re.search('t=(\d+)', temp.decode('utf-8'))
      if match:
        value = match.group(1)
      else:
        value = "XX"
    except:
      value = "XX"
    
    return value
 
  def signal_handler(self, signum, frame):
    # so far, we only need to handle signals that make the program exit.
    logging.warning("Caught a deadly signal: " + str(signum) + "!")
    if self.ioPolling:
      self.ioPolling.stop()
    self.running = False
    self.check_now.set()
    self.dmthread.join()

  def checkup(self):
    checks = {}
    
    self.pings+=1
    if(self.pings % self.config.long_checkup_freq == 0):
      self.pings = 0
      long_checks = 0
      for check_name, check_command in self.config.boot_check_list.items():
        long_checks += 1
        if long_checks > self.config.long_checkup_leng:
          break
        checks[check_name] = subprocess.check_output(
                check_command, 
                shell=True
        ).decode('utf-8')
    
    for name in self.runtime.switch_channels:
      checks[name] = self.runtime.ct_ios[name].confirmed
    for name in self.runtime.flip_channels:
      checks[name] = self.runtime.ct_ios[name].confirmed
    for name in self.runtime.pir_channels:
      checks[name] = self.runtime.ct_ios[name].confirmed
      self.runtime.last_pir_state[name] = checks[name]
    
    # bad coding for testing if the temperature fault restart can happen
    try: 
      self.runtime.temp_power_fault = self.runtime.temp_fault_sm.confirmed
    except AttributeError:
      for ts_name, ts_path in self.runtime.temp_channels.items():
        checks[ts_name] = self.check_temp(ts_path[0])
    else:
      checks["Temp Power Fault"] = int(self.runtime.temp_power_fault)
      self.runtime.temp_power_on = self.runtime.temp_power_commanded
      for ts_name, ts_path in self.runtime.temp_channels.items():
        checks[ts_name] = self.check_temp(ts_path[0])
        received = checks[ts_name] != "XX"
        self.runtime.temp_power_on = self.runtime.temp_power_sm[ts_name].run(self.runtime.temp_power_last, self.runtime.temp_power_on, received, self.runtime.temp_power_fault)
        if self.runtime.temp_power_sm[ts_name].broke:
          if self.runtime.temp_power_sm[ts_name].state == TempSensorPower.PowerState.RESTART:
            logging.warn("Temp sensor \"" + ts_name + "down and causing one-wire network restart!")
          else:
            logging.warn("Temp sensor \"" + ts_name + "down!")
      self.runtime.temp_power_last = self.runtime.temp_power_on
      checks["Temp Power"] = int(self.runtime.temp_power_on)
      GPIO.output(self.runtime.temp_en, self.runtime.temp_power_on)
    
    self.notify('checkup', checks)
 
# this function is called by the polling timer.
  def io_check(self):
    checks = {}
    if (self.io_check_count >= 65535):
      self.io_check_count = 0
    else:
      self.io_check_count += 1
    logging.debug("IO check " + str(self.io_check_count))
    for name, chan in self.runtime.switch_channels.items():
      result = self.runtime.ct_ios[name].update(GPIO.input(chan))
      # value confirmed
      if result[0]:
        checks[name] = result[1]
    for name, chan in self.runtime.flip_channels.items():
      result = self.runtime.ct_ios[name].update(int (not GPIO.input(chan)))
      if result[0]:
        checks[name] = result[1]
    for name, chan in self.runtime.pir_channels.items():
      result = self.runtime.ct_ios[name].update(GPIO.input(chan))
      # PIR's are special because they like to be on and are only turned off during
      # timed checkups
      if result[0] and result[1] and not self.runtime.last_pir_state[name]:
        checks[name] = result[1]
        self.runtime.last_pir_state[name] = result[1]

    # don't run the temperature power control if there is no such thing.
    try:
      result = self.runtime.temp_fault_sm.update(not GPIO.input(self.runtime.temp_fault))
      if result[0]:
        checks["Temp Power Fault"] = result[1]
    except AttributeError:
      pass
    # notify if any values were changed
    if checks:
      self.notify('event', checks)
    else:
      logging.debug("Noting changed between timed io checks")
  
  def deadman_checkup(self):
    while self.check_now.is_set() == False:
      logging.info("Deadman thread waiting.")
      self.check_now.wait(60*7)
      logging.info("Checkup thread execution")
      if self.running == True and self.is_connected() == True:
        if self.check_now.is_set() == False:
          self.checkup()
      else:
        break
    self.check_now.clear()

  def run(self):
    self.tEvent = Event()
    self.running = True
    startup_count = 0
    self.io_check_count = 0
    self.loop_count = 0
    self.check_now = Event()
    try:
      if type(logging.getLevelName(self.config.loglevel.upper())) is int:
        logging.basicConfig(level=self.config.loglevel.upper())
      else:
        logging.warning("Log level not configured.  Defaulting to WARNING.")
    except (KeyError, AttributeError) as e:
      logging.warning("Log level not configured.  Defaulting to WARNING.  Caught: " + str(e))

    self.bootup()
    while startup_count < 10:
      try:
        startup_count += 1
        # check loglevel
        self.connect(self.config.mqtt_broker, self.config.mqtt_port, self.config.mqtt_timeout)
        atexit.register(self.disconnect)
        self.notify_bootup()
        self.ioPolling = MultiTimer(interval=5, function=self.io_check)
        self.ioPolling.start()
        atexit.register(self.ioPolling.stop)
        break
      except OSError:
        logging.error("Error connecting on bootup.")
        logging.error(traceback.format_exc())
        logging.error("Waiting to reconnect...")
        self.tEvent.wait(30)
        
    if startup_count >= 10:
      logging.critical("Too many startup tries.  Exiting.")
      os._exit(1)

    logging.info("Startup success.")
    self.reconnect_me = False
    self.inner_reconnect_try = 0
    while self.running and (self.inner_reconnect_try < 10):
      if self.loop_count >= 65535:
        self.loop_count = 0
      else:
        self.loop_count += 1
      try:
        if self.reconnect_me == True:
          self.reconnect()
          self.reconnect_me = False
        
        self.loop()
        self.inner_reconnect_try = 0
      except SystemExit:
        break
      except (socket.timeout, TimeoutError, ConnectionError):
        self.inner_reconnect_try += 1
        self.reconnect_me = True
        logging.error("MQTT loop error.  Attempting to reconnect: " + inner_reconnect_try + "/10")
      except:
        logging.critical("Exception in MQTT loop.")
        logging.critical(traceback.format_exc())
        logging.critical("Exiting.")
        exit(2)
    if self.inner_reconnect_try >= 10:
      exit(1)
    exit(0)

# the code that is run only on direct invocation of this file
if __name__ == "__main__":
    hdc = HDC()
    my_path  = os.path.dirname(os.path.abspath(__file__))
    with open(my_path + "/hdc_config.json", "r") as configFile:
        hdc.config = HDC.config.from_json(configFile.read())
    hdc.run()
