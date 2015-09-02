
import time, signal, subprocess, http.client, urllib, hmac, hashlib, re, select
from daemon import Daemon

class Haldor(Daemon):
  """Watches the door and monitors various switches and motion via GPIO"""

  # TODO: enable these as configs passed to __init__
  version = "0.0.2a"
  io_channels = [7, 8, 25, 11, 24]
  io_names = {'Front Door': 7, 'Main Door': 8, 'Office Motion': 25, 'Shop Motion': 11, 'Open Switch': 24}
  switch_channels = [7, 8, 24] # light switch and reed switch
  pir_channels = [25, 11] # pir receives and outputs 5v
  gpio_path = "/sys/class/gpio"
  secret_path = "/home/haldor/.open-sesame"
  ds18b20_path = "/sys/devices/w1_bus_master1/28-0000050585f4/w1_slave"
  host = "www.maglaboratory.org"
  use_ssl = True
  checkup_interval = 300
  
  # TODO: Check all the GPIOs
  # 7 -> Front Door
  # 8 - Main Door
  # 25 - Office Motion
  # 11 - Shop Motion
  # 24 - Switch? "plus30Mins" in old app...
  
  def get_secret(self):
    if len(self.secret) <= 0:
      file = open(Haldor.secret_path, 'rb')
      self.secret = file.read()
      file.close
    
    return self.secret
  
  def export_channels(self):
    # export gpio channels so we can do stuff with them
    for chan in Haldor.io_channels:
      # check for the gpio sys directory, we should be the only one managing it
      # so if it's been exported, assume we have authority to control
      print("Checking {}".format(chan))
      chan_path = "{0}/gpio{1}".format(Haldor.gpio_path, chan)
      if 0 != subprocess.call(["ls", chan_path]):
        file = open("{0}/export".format(Haldor.gpio_path), 'a')
        file.write("{0}\n".format(chan))
        file.close()
        time.sleep(1)
        # Have to run this script to chgrp and chown, apparently exported gpio
        # is still limited to root even though gpio group users can export
        subprocess.call('sudo /root/haldor/enable_gpio.sh {0}'.format(chan), shell=True)
  
  def direct_channels(self):
    for chan in Haldor.io_channels:
      print("Marking {} as input".format(chan))
      direction_path = "{0}/gpio{1}/direction".format(Haldor.gpio_path, chan)
      file = open(direction_path, 'w')
      file.write("in\n")
      file.close
  
  def edge_channels(self):
    # We're using epoll, so for switches, we'll watch for both rise and fall
    for chan in Haldor.switch_channels:
      print("Edging {0} as both".format(chan))
      edge_path = "{0}/gpio{1}/edge".format(Haldor.gpio_path, chan)
      file = open(edge_path, 'w')
      file.write("both\n")
      file.close()
    
    # For pir, since it rises and falls pretty quickly (<5s) we'll only watch for rise
    for chan in Haldor.pir_channels:
      print("Edging {0} as rising".format(chan))
      edge_path = "{0}/gpio{1}/edge".format(Haldor.gpio_path, chan)
      file = open(edge_path, 'w')
      file.write("rising\n")
      file.close()
  
  def enable_gpio(self):
    self.export_channels()
    self.direct_channels()
    self.edge_channels()
  
  def notify_hash(self, body):
    hasher = hmac.new(self.get_secret(), body, hashlib.sha256)
    hasher.update(self.session)
    return hasher.hexdigest()
  
  def notify(self, path, params):
    # TODO: Check https certificate
    params['time'] = time.time()
    body = urllib.parse.urlencode(params).encode('utf-8')
    
    headers = {"Content-Type": "application/x-www-form-urlencoded",
      "Accept": "text/plain",
      "X-Haldor": Haldor.version,
      "X-Session": self.session,
      "X-Checksum": self.notify_hash(body)
      }
    conn = None
    if Haldor.use_ssl:
      conn = http.client.HTTPSConnection(Haldor.host)
    else:
      conn = http.client.HTTPConnection(Haldor.host)
    conn.request("POST", "/haldor/{0}".format(path), body, headers)
    print("Notified {0}".format(path))
    return conn.getresponse()
  
  def notify_bootup(self):
    uptime = ""
    uname = ""
    if_eth0 = ""
    therm = ""
  
    print("Bootup:")
    
    try:
      therm = subprocess.check_output(["cat", Haldor.ds18b20_path])
    except:
      print("\tw1 read error")
    
    try:
      uptime = subprocess.check_output("uptime")
      uname = subprocess.check_output(["uname", "-a"])
    except:
      print("\tuptime/uname read error")
    
    try:
      if_eth0 = subprocess.check_output(["/sbin/ifconfig", "eth0"])
    except:
      print("\teth0 read error")
      
  
    try:
      resp = self.notify('bootup', {'uptime': uptime, 'uname': uname, 'ifconfig_eth0': if_eth0, 'thermal': therm})
      self.session = resp.read()
      print("Bootup Complete: {0}".format(self.session))
    except:
      # TODO: Error handling
      print("Bootup ERROR")
      pass
  
  
  def bootup(self):
    print("Bootup.")
    self.secret = ""
    self.session = "".encode('utf-8')
    self.enable_gpio()
    self.notify_bootup()
  
  def read_gpio(self, chan):
    value = '-1'
    try:
      file = open("{0}/gpio{1}/value".format(Haldor.gpio_path, chan), 'r')
      value = file.read().rstrip()
      file.close()
    except:
      value = '-2'

    return value
  
  def check_gpios(self, gpios):
    for name, chan in iter(Haldor.io_names.items()):
      gpios[name] = self.read_gpio(chan)
    
    return gpios
    
  def check_temp(self):
    value = ""
    try:
      temp = subprocess.check_output(["cat", Haldor.ds18b20_path])
      match = re.search('t=(\d+)', temp.decode('utf-8'))
      value = match.group(1)
    except:
      value = "--"
    
    return value
  
  def notify_checkup(self, checks):
    self.notify('checkup', checks)
  
  def checkup(self):
    print("Checkup.")
    checks = {}
    self.check_gpios(checks)
    checks['Temperature'] = self.check_temp()
    print(checks)
    self.notify_checkup(checks)
  
  def register_epoll(self, epoll):
    for chan in Haldor.io_channels:
      fd = open("{0}/gpio{1}/value".format(Haldor.gpio_path, chan), 'r')
      epoll.register(fd.fileno(), select.EPOLLET)
      
  
  def run(self):
    self.bootup()
    
    epoll = select.epoll()
    self.register_epoll(epoll)
    
    while True:      
      epoll.poll(Haldor.checkup_interval)
      # run a checkup whenever epoll returns
      # Happens in two cases: 1) epoll times out 2) epoll received a trigger
      self.checkup()

