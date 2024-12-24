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
from maglab_crypto import MAGToken
from threading import Event

def conv_value(my_int):
    return Value.INACTIVE if my_int == 0 or my_int == False or (type(my_int) == str and my_int.lower() == "off") else Value.ACTIVE

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
    acObject: Union[List[str], int, List[int]]

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
    tokens: Optional[List[str]] = None
    loglevel: Optional[str] = None

  # overloaded MQTT functions from (mqtt.Client)
  def on_log(self, client, userdata, level, buff):
    if level == mqtt.MQTT_LOG_DEBUG:
      self.log.debug("PAHO MQTT DEBUG: " + buff)
    elif level == mqtt.MQTT_LOG_INFO:
      self.log.info("PAHO MQTT INFO: " + buff)
    elif level == mqtt.MQTT_LOG_NOTICE:
      self.log.info("PAHO MQTT NOTICE: " + buff)
    elif level == mqtt.MQTT_LOG_WARNING:
      self.log.warning("PAHO MQTT WARN: " + buff)
    else:
      self.log.error("PAHO MQTT ERROR: " + buff)

  def on_connect(self, client, userdata, flags, rc):
    self.log.info("Connected: " + str(rc))
    self.subscribe("reporter/checkup_req")
    self.subscribe(self.config.name + "/temp_power")
    self.subscribe(f"{self.config.name}/cmd")

  def on_message(self, client, userdata, message):
    if (message.topic == "reporter/checkup_req"):
      self.log.info("Checkup received.")
      self.checkup()
    elif (message.topic == self.config.name + "/temp_power"):
      decoded = message.payload.decode('utf-8')
      self.log.debug("Temperature sensor power command received: " + decoded)
      if (decoded.lower() == "false" or decoded == "0"):
        self.log.info("Temperature sensor power commanded off")
        self.runtime.temp_power_commanded = False
      else:
        self.log.info("Temperature sensor power commanded on")
        self.runtime.temp_power_commanded = True
    elif message.topic == f"{self.config.name}/cmd":
      if self.mag_token:
        commands = self.mag_token.cmd_msg_auth(message.payload.decode("utf-8"), 7200)
        if commands:
          line_values = {}
          for name, value in commands.items():
            if name in self.runtime.output_channels.keys():
              if conv_value(value) == Value.ACTIVE:
                line_values.update({self.runtime.output_channels[name]:Value.ACTIVE})
                self.runtime.output_channels.update({name:Value.ACTIVE})
              else:
                line_values.update({self.runtime.output_channels[name]:Value.INACTIVE})
                self.runtime.output_channels.update({name:Value.INACTIVE})
          self._gpioreq.set_values(line_values)



  def on_disconnect(self, client, userdata, rc):
    self.log.warning("Disconnected: " + str(rc))
    if rc != 0:
        self.log.error("Unexpected disconnection.  Attempting reconnection.")
        reconnect_count = 0
        while (reconnect_count < 10):
            try:
                reconnect_count += 1
                self.reconnect()
                break
            except OSError:
                self.log.error("Connection error while trying to reconnect.")
                self.log.error(traceback.format_exc())
                self.log.error("Waiting to restart.")
                self.tEvent.wait(30)
        if reconnect_count >= 10:
            self.log.critical("Too many reconnect tries.  Exiting.")
            os._exit(1)

  # HDC functions
  def enable_gpio(self):
    global GPIO, Direction, Bias, Value
    if self.config.gpio_path.startswith("/dev/gpiochip"):
      self.log.debug("Configuring GPIOs at: " + self.config.gpio_path)
      import gpiod as GPIO
      from gpiod.line import Direction, Bias, Value
      self._gpiodict = {}
    else:
      raise KeyError("This HDC implementation does not support the selected GPIO method.")

    # sort GPIOs
    self.runtime = type("Runtime", (object, ), {})
    self.runtime.switch_channels = {}
    self.runtime.flip_channels = {}
    self.runtime.pir_channels = {}
    self.runtime.last_pir_state = {}
    self.runtime.ct_ios = {}
    self.runtime.temp_channels = {}
    self.runtime.temp_power_sm = {}
    self.runtime.output_channels = {}
    self.log.debug("Running through I/O configuration.")
    for acq in self.config.acq_io:
      if acq.acType == "SW":
        self.log.debug("Configuring Switch: " + str(acq.acObject))
        self.runtime.switch_channels.update({acq.name : acq.acObject})
        self._gpiodict.update({acq.acObject : GPIO.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)})
      elif acq.acType == "SW_INV":
        self.log.debug("Configuring invSwitch: " + str(acq.acObject))
        self.runtime.flip_channels.update({acq.name : acq.acObject})
        self._gpiodict.update({acq.acObject : GPIO.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)})
      elif acq.acType == "PIR":
        self.log.debug("Configuring PIR Sensor: " + str(acq.acObject))
        self.runtime.pir_channels.update({acq.name : acq.acObject})
        self.runtime.last_pir_state.update({acq.name : 0})
        self._gpiodict.update({acq.acObject : GPIO.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)})
      elif acq.acType == "TEMP":
        self.log.debug("Configuring Temperature Sensor: " + str(acq.acObject))
        self.runtime.temp_channels.update({acq.name : acq.acObject})
        self.runtime.temp_power_sm.update({acq.name : TempSensorPower(self.config.temp_max_restart)})
      elif acq.acType == "TEMP_FAULT":
        try:
          self.runtime.temp_fault
          raise KeyError("Temperature sensor fault channel already allocated")
        except AttributeError:
          self.log.debug("Configuring Temperature Power Fault: " + str(acq.acObject))
          self.runtime.temp_fault = acq.acObject
          self._gpiodict.update({acq.acObject : GPIO.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)})
      elif acq.acType == "OUT":
        self.log.debug("Configuring Output: " + str(acq.acObject))
        try:
            self.runtime.output_channels.update({acq.name : acq.acObject[0]})
            self.runtime.output_values.update({acq.name : conv_value(acq.acObject[1])})
            self._gpiodict.update({acq.acObject[0] : GPIO.LineSettings(direction=Direction.OUTPUT, output_value=conv_value(acq.acQbject[1]))})
        except TypeError:
            self.runtime.output_channels.update({acq.name : acq.acObject})
            self.runtime.output_values.update({acq.name : Value.INACTIVE})
            self._gpiodict.update({acq.acObject : GPIO.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE)})
      elif acq.acType == "TEMP_EN":
        try:
          self.runtime.temp_en
          raise KeyError("Temperature sensor enable channel already allocated")
        except AttributeError:
          self.log.debug("Configuring Temperature Power Enable: " + str(acq.acObject))
          self.runtime.temp_en = acq.acObject
          self._gpiodict.update({acq.acObject : GPIO.LineSettings(direction=Direction.OUTPUT)})
          self.runtime.temp_power_commanded = True
          self.runtime.temp_power_on = True
          self.runtime.temp_power_last = True
      else:
        raise KeyError('"' + acq.acType + '"' + " is not a valid acquisition type")

    self.log.debug(f"GPIO configuration generated: {self._gpiodict}")
    self.log.debug("Applying configuration.")
    self._gpioreq = GPIO.request_lines(self.config.gpio_path ,consumer=self.config.name ,config=self._gpiodict)

    self.log.debug("Starting debouncing.")
    # Switches
    for name, line in self.runtime.switch_channels.items():
      self.runtime.ct_ios.update({name : confirmation_threshold(1 if self._gpioreq.get_value(line) == Value.ACTIVE else 0, 3)})
    # Inverted Switches
    for name, line in self.runtime.flip_channels.items():
      self.runtime.ct_ios.update({name : confirmation_threshold(0 if self._gpioreq.get_value(line) == Value.ACTIVE else 1, 3)})
    # PIR sensors
    for name, line in self.runtime.pir_channels.items():
      self.runtime.ct_ios.update({name : confirmation_threshold(1 if self._gpioreq.get_value(line) == Value.ACTIVE else 0, 1)})
    # Temperature Fault
    if hasattr(self.runtime, "temp_fault"):
      self.runtime.temp_fault_sm = confirmation_threshold(0 if self._gpioreq.get_value(self.runtime.temp_en) == Value.ACTIVE else 1, 3)

  def notify(self, path, params, retain=False):
    params['time'] = str(time.time())
    self.log.debug(params)

    topic = self.config.name + '/' + path
    self.publish(topic, json.dumps(params), retain=retain)
    self.log.info("Published " + topic)
  
  def notify_bootup(self):
    boot_checks = {}

    self.log.debug("Bootup:")
    
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
    self.log.warning("Caught a deadly signal: " + str(signum) + "!")
    if self.ioPolling:
      self.ioPolling.stop()
    self.running = False

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
            self.log.warn("Temp sensor \"" + ts_name + "down and causing one-wire network restart!")
          else:
            self.log.warn("Temp sensor \"" + ts_name + "down!")
      self.runtime.temp_power_last = self.runtime.temp_power_on
      checks["Temp Power"] = int(self.runtime.temp_power_on)
      self._gpioreq.set_value(self.runtime.temp_en, self.runtime.temp_power_on)
    
    self.notify('checkup', checks)
 
# this function is called by the polling timer.
  def io_check(self):
    checks = {}
    if (self.io_check_count >= 65535):
      self.io_check_count = 0
    else:
      self.io_check_count += 1
    self.log.debug("IO check " + str(self.io_check_count))
    for name, chan in self.runtime.switch_channels.items():
      result = self.runtime.ct_ios[name].update(1 if self._gpioreq.get_value(chan) == Value.ACTIVE else 0)
      # value confirmed
      if result[0]:
        checks[name] = result[1]
    for name, chan in self.runtime.flip_channels.items():
      result = self.runtime.ct_ios[name].update(0 if self._gpioreq.get_value(chan) == Value.ACTIVE else 1)
      if result[0]:
        checks[name] = result[1]
    for name, chan in self.runtime.pir_channels.items():
      result = self.runtime.ct_ios[name].update(1 if self._gpioreq.get_value(chan) == Value.ACTIVE else 0)
      # PIR's are special because they like to be on and are only turned off during
      # timed checkups
      if result[0] and result[1] != self.runtime.last_pir_state[name]:
        checks[name] = result[1]
        self.runtime.last_pir_state[name] = result[1]
    for name, chan in self.runtime.output_channels.items():
      self.log.debug(f"Channel {chan} outputting value {self.runtime.output_values[chan]}")
      self._gpioreq.set_value(chan, self.runtime.output_values[name])
    # don't run the temperature power control if there is no such thing.
    try:
      result = self.runtime.temp_fault_sm.update(0 if self._gpioreq.get_value(self.runtime.temp_fault) == Value.ACTIVE else 1)
      if result[0]:
        checks["Temp Power Fault"] = result[1]
    except AttributeError:
      pass
    # notify if any values were changed
    if checks:
      self.notify('event', checks)
    else:
      self.log.debug("Noting changed between timed io checks")
  
  def run(self):
    self.log = logging.getLogger(__name__)
    self.tEvent = Event()
    self.running = True
    startup_count = 0
    self.io_check_count = 0
    self.loop_count = 0
    self.mag_token = None
    try:
      if type(logging.getLevelName(self.config.loglevel.upper())) is int:
        logging.basicConfig(level=self.config.loglevel.upper())
      else:
        self.log.warning("Log level not configured.  Defaulting to WARNING.")
    except (KeyError, AttributeError) as e:
      self.log.warning("Log level not configured.  Defaulting to WARNING.  Caught: " + str(e))

    if self.config.tokens:
        self.mag_token = MAGToken(self.config.tokens)

    self.bootup()
    while startup_count < 10:
      try:
        startup_count += 1
        # check loglevel
        self.connect(self.config.mqtt_broker, self.config.mqtt_port, self.config.mqtt_timeout)
        atexit.register(self.disconnect)
        self.notify_bootup()
        self.ioPolling = MultiTimer(interval=1, function=self.io_check)
        self.ioPolling.start()
        atexit.register(self.ioPolling.stop)
        break
      except OSError:
        self.log.error("Error connecting on bootup.")
        self.log.error(traceback.format_exc())
        self.log.error("Waiting to reconnect...")
        self.tEvent.wait(30)
        
    if startup_count >= 10:
      self.log.critical("Too many startup tries.  Exiting.")
      os._exit(1)

    self.log.info("Startup success.")
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
        self.log.error("MQTT loop error.  Attempting to reconnect: " + inner_reconnect_try + "/10")
      except:
        self.log.critical("Exception in MQTT loop.")
        self.log.critical(traceback.format_exc())
        self.log.critical("Exiting.")
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
