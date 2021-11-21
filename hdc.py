import paho.mqtt.client as mqtt
import time, signal, subprocess, http.client, urllib, re, json
import traceback, os
#from functools import partial
from daemon import Daemon
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from enum import Enum
from typing import *
from multitimer import MultiTimer
from confirmation_threshold import confirmation_threshold

class HDCDaemon(Daemon):
  def run(self):
    h_datacollector = HDC()
    my_path = os.path.dirname(os.path.abspath(__file__))
    config = open(my_path + "/hdc_config.json", "r")
    h_datacollector.config = HDC.config.from_json(config.read())
    config.close()

    h_datacollector.run()

@dataclass_json
@dataclass
class Acquisition:
    name: str
    acType: str
    acObject: Union[List[str], int]

class TempSensorPower:
  class PowerState(Enum):
    INIT = 0
    RESTART = 1
    CHECK = 2

  state = PowerState.INIT
  allowedRestarts = 1
  restarts = 0
  broke = False

  def run(self, lastPower, power, reception, fault):
    self.broke = lastPower and not reception and not fault
    # transitions
    if self.state == self.PowerState.INIT:
      if self.broke:
        self.state = self.PowerState.RESTART
    elif self.state == self.PowerState.RESTART:
      self.state = self.PowerState.CHECK
    elif self.state == self.PowerState.CHECK:
      if self.restarts < self.allowedRestarts:
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
    temp_max_restart: int = 1

  # overloaded MQTT functions from (mqtt.Client)
  def on_log(self, client, userdata, level, buff):
    if level != mqtt.MQTT_LOG_DEBUG:
      print (level)
      print(buff)
    if level == mqtt.MQTT_LOG_ERR:
      print ("error handler")
      traceback.print_exc()
      self.running = False;

  def on_connect(self, client, userdata, flags, rc):
    print("Connected: " + str(rc))
    self.subscribe("reporter/checkup_req")

  def on_message(self, client, userdata, message):
    print("Checkup received.")
    self.checkup()

  def on_disconnect(self, client, userdata, rc):
    print("Disconnected: " + str(rc))
    self.running = False;

  # HDC functions
  def enable_gpio(self):
    global GPIO
    print(self.config.gpio_path)
    if not self.config.gpio_path:
      import orangepi.one
      import OPi.GPIO as GPIO
      GPIO.setmode(orangepi.one.BOARD)
      GPIO.setwarnings(False)
    else:
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
        GPIO.setup(acq.acObject, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.runtime.switch_channels.update({acq.name : acq.acObject})
        self.runtime.ct_ios.update({acq.name : confirmation_threshold(GPIO.input(acq.acObject),3)})
      elif acq.acType == "SW_INV":
        GPIO.setup(acq.acObject, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.runtime.flip_channels.update({acq.name : acq.acObject})
        self.runtime.ct_ios.update({acq.name : confirmation_threshold(GPIO.input(acq.acObject),3)})
      elif acq.acType == "PIR":
        GPIO.setup(acq.acObject, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.runtime.pir_channels.update({acq.name : acq.acObject})
        self.runtime.ct_ios.update({acq.name : confirmation_threshold(GPIO.input(acq.acObject),3)})
        self.runtime.last_pir_state.update({acq.name : 0})
      elif acq.acType == "TEMP":
        self.runtime.temp_channels.update({acq.name : acq.acObject})
        self.runtime.temp_power_sm.update({acq.name : TempSensorPower()})
      elif acq.acType == "TEMP_FAULT":
        try:
          self.runtime.temp_fault
          raise KeyError("Temperature sensor fault channel already allocated")
        except AttributeError:
          print ("Creating Temperature Fault Runtime Objects")
          self.runtime.temp_fault = acq.acObject
          GPIO.setup(acq.acObject, GPIO.IN, pull_up_down=GPIO.PUD_UP)
          self.runtime.temp_fault_sm = confirmation_threshold(not GPIO.input(acq.acObject),3)
      elif acq.acType == "TEMP_EN":
        try:
          self.runtime.temp_en
          raise KeyError("Temperature sensor enable channel already allocated")
        except AttributeError:
          print ("Creating Temperature Enable Runtime Objects")
          self.runtime.temp_en = acq.acObject
          GPIO.setup(acq.acObject, GPIO.OUT)
          self.runtime.temp_power_on = True
          self.runtime.temp_power_last = True
      else:
        raise KeyError('"' + acq.acType + '"' + " is not a valid acquisition type")

  def notify(self, path, params, retain=False):
    params['time'] = str(time.time())
    print (params)

    topic = self.config.name + '/' + path
    self.publish(topic, json.dumps(params), retain=retain)
    print("Published " + topic)
  
  def notify_bootup(self):
    boot_checks = {}

    print("Bootup:")
    
    for bc_name, bc_cmd in self.config.boot_check_list.items():
        boot_checks[bc_name] = subprocess.check_output(
                bc_cmd, 
                shell=True
        ).decode('utf-8')

    self.notify('bootup', boot_checks, retain=True)
  
  def bootup(self):
    print("Bootup sequence called.")

    # invert dictionary for reporting
    self.enable_gpio()
    self.notify_bootup()
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
    print("Caught a deadly signal!")
    self.running = False
    self.exiting = True

  def checkup(self):
    print("Checkup.")
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
      self.runtime.temp_power_on = True
      for ts_name, ts_path in self.runtime.temp_channels.items():
        checks[ts_name] = self.check_temp(ts_path[0])
        received = checks[ts_name] != "XX"
        self.runtime.temp_power_on = self.runtime.temp_power_sm[ts_name].run(self.runtime.temp_power_last, self.runtime.temp_power_on, received, self.runtime.temp_power_fault)
        if self.runtime.temp_power_sm[ts_name].broke:
          print("Temp sensor \"" + ts_name + "\" down", end = '')
          if self.runtime.temp_power_sm[ts_name].state == TempSensorPower.PowerState.RESTART:
            print(" and causing one-wire network restart!")
          else:
            print("!")
      self.runtime.temp_power_last = self.runtime.temp_power_on
      checks["Temp Power"] = int(self.runtime.temp_power_on)
      GPIO.output(self.runtime.temp_en, self.runtime.temp_power_on)

    self.notify('checkup', checks)
  
  def timed_checkup(self):
    checks = {}
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

    try:
      result = self.runtime.temp_fault_sm.update(not GPIO.input(self.runtime.temp_fault))
      if result[0]:
        checks["Temp Power Fault"] = result[1]
    except AttributeError:
      pass
    # notify if any values were changed
    if checks:
      self.notify('event', checks)
  
  def run(self):
    while True:
      self.running = True
      while self.running:
        try:
          self.connect(self.config.mqtt_broker, self.config.mqtt_port, self.config.mqtt_timeout)
          self.bootup()
          timer = MultiTimer(interval=5, function=self.timed_checkup)
          timer.start()
    
          while self.running:
            self.loop()

          timer.stop()
          GPIO.cleanup()
        except:
          timer.stop()
          GPIO.cleanup()
          traceback.print_exc()
          pass

        if self.exiting:
          self.disconnect()
          exit(0) 
