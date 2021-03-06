import paho.mqtt.client as mqtt
import time, signal, subprocess, http.client, urllib, re, json
import traceback, os
#from functools import partial
from daemon import Daemon
from dataclasses import dataclass
from dataclasses_json import dataclass_json
from typing import *
from multitimer import MultiTimer
from confirmation_threshold import confirmation_threshold

class HDCDaemon(Daemon):
  def run(self):
    h_datacollector = HDC()
    my_path = os.path.dirname(os.path.abspath(__file__))
    config = open(my_path + "/hdc_config.json", "r")
    h_datacollector.data = HDC.data.from_json(config.read())
    config.close()

    h_datacollector.run()

@dataclass_json
@dataclass
class Acquisition:
    name: str
    acType: str
    acObject: Union[List[str], int]

class HDC(mqtt.Client):
  """Watches the door and monitors various switches and motion via GPIO"""

  version = '2020'
  # dataclass variable declaration
  @dataclass_json
  @dataclass
  class data:
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
    print(self.data.gpio_path)
    if not self.data.gpio_path:
      import orangepi.one
      import OPi.GPIO as GPIO
      GPIO.setmode(orangepi.one.BOARD)
      GPIO.setwarnings(False)
    else:
      import RPi.GPIO as GPIO
      GPIO.setmode(GPIO.BCM)

    # sort GPIOs
    self.data.switch_channels = {}
    self.data.flip_channels = {}
    self.data.pir_channels = {}
    self.data.last_pir_state = {}
    self.data.ct_ios = {}
    self.data.temp_channels = {}
    for acq in self.data.acq_io:
      if acq.acType == "SW":
        GPIO.setup(acq.acObject, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.data.switch_channels.update({acq.name : acq.acObject})
        self.data.ct_ios.update({acq.name : confirmation_threshold(GPIO.input(acq.acObject),3)})
      if acq.acType == "SW_INV":
        GPIO.setup(acq.acObject, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.data.flip_channels.update({acq.name : acq.acObject})
        self.data.ct_ios.update({acq.name : confirmation_threshold(GPIO.input(acq.acObject),3)})
      if acq.acType == "PIR":
        GPIO.setup(acq.acObject, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self.data.pir_channels.update({acq.name : acq.acObject})
        self.data.ct_ios.update({acq.name : confirmation_threshold(GPIO.input(acq.acObject),3)})
        self.data.last_pir_state.update({acq.name : 0})
      if acq.acType == "TEMP":
        self.data.temp_channels.update({acq.name : acq.acObject})

  def notify(self, path, params, retain=False):
    params['time'] = str(time.time())
    print (params)

    topic = self.data.name + '/' + path
    self.publish(topic, json.dumps(params), retain=retain)
    print("Published " + topic)
  
  def notify_bootup(self):
    boot_checks = {}

    print("Bootup:")
    
    for bc_name, bc_cmd in self.data.boot_check_list.items():
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
    if(self.pings % self.data.long_checkup_freq == 0):
      self.pings = 0
      long_checkup_count = 0
      for check_name, check_command in self.data.boot_check_list:
        long_checkup_count += 1
        if long_checkup_count > self.data.long_checkup_leng:
          break
        checks[check_name] = subprocess.check_output(
                check_command, 
                shell=True
        ).decode('utf-8')
    
    for name in self.data.switch_channels:
      checks[name] = self.data.ct_ios[name].confirmed
    for name in self.data.flip_channels:
      checks[name] = self.data.ct_ios[name].confirmed
    for name in self.data.pir_channels:
      checks[name] = self.data.ct_ios[name].confirmed
      self.data.last_pir_state[name] = checks[name]
    for ts_name, ts_path in self.data.temp_channels.items():
      checks[ts_name] = self.check_temp(ts_path[0])
    self.notify('checkup', checks)
  
  def timed_checkup(self):
    checks = {}
    for name, chan in self.data.switch_channels.items():
      result = self.data.ct_ios[name].update(GPIO.input(chan))
      # value confirmed
      if result[0]:
        checks[name] = result[1]
    for name, chan in self.data.flip_channels.items():
      result = self.data.ct_ios[name].update(int (not GPIO.input(chan)))
      if result[0]:
        checks[name] = result[1]
    for name, chan in self.data.pir_channels.items():
      result = self.data.ct_ios[name].update(GPIO.input(chan))
      # PIR's are special because they like to be on and are only turned off during
      # timed checkups
      if result[0] and result[1] and not self.data.last_pir_state[name]:
        checks[name] = result[1]
        self.data.last_pir_state[name] = result[1]
    # notify if any values were changed
    if checks:
      self.notify('event', checks)
  
  def run(self):
    while True:
      self.running = True
      while self.running:
        try:
          self.connect(self.data.mqtt_broker, self.data.mqtt_port, self.data.mqtt_timeout)
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
          exit(0) 
