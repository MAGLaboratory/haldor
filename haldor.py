
import time, signal, subprocess, httplib, urllib, hmac, hashlib
from daemon import Daemon

class Haldor(Daemon):
  """Watches the door and monitors various switches and motion via GPIO"""

  # TODO: enable these as configs passed to __init__
  version = "0.0.1a"
  io_channels = [7, 8, 25, 11, 24]
  io_names = {'Front Door': 7, 'Main Door': 8, 'Office Motion': 25, 'Shop Motion': 11, 'Open Switch': 24}
  gpio_path = "/sys/class/gpio"
  secret_path = "/home/haldor/.open-sesame"
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
      file = open(Haldor.secret_path, 'r')
      self.secret = file.read()
      file.close
    
    return self.secret
  
  def export_channels(self):
    # export gpio channels so we can do stuff with them
    for chan in Haldor.io_channels:
      # check for the gpio sys directory, we should be the only one managing it
      # so if it's been exported, assume we have authority to control
      print "Checking {}".format(chan)
      chan_path = "{0}/gpio{1}".format(Haldor.gpio_path, chan)
      if 0 != subprocess.call(["ls", chan_path]):
        file = open("{0}/export".format(Haldor.gpio_path), 'a')
        file.write("{0}\n".format(chan))
        file.close()
        time.sleep(1)
        # Have to run this script to chgrp and chown, apparently exported gpio
        # is still limited to root even though gpio group users can export
        subprocess.call('sudo /root/haldor/enable_gpio.sh gpio{0}'.format(chan), shell=True)
  
  def mark_input_channels(self):
    for chan in Haldor.io_channels:
      print "Marking {} as input".format(chan)
      direction_path = "{0}/gpio{1}/direction".format(Haldor.gpio_path, chan)
      file = open(direction_path, 'w')
      file.write("in\n")
      file.close
  
  def enable_gpio(self):
    self.export_channels()
    self.mark_input_channels()
  
  def notify_hash(self, body):
    hasher = hmac.new(self.get_secret(), body, hashlib.sha256)
    hasher.update(self.session)
    return hasher.hexdigest()
  
  def notify(self, path, params):
    # TODO: Check https certificate
    params['time'] = time.time()
    body = urllib.urlencode(params)
    
    headers = {"Content-Type": "application/x-www-form-urlencoded",
      "Accept": "text/plain",
      "X-Haldor": Haldor.version,
      "X-Session": self.session,
      "X-Checksum": self.notify_hash(body)
      }
    conn = None
    if Haldor.use_ssl:
      conn = httplib.HTTPSConnection(Haldor.host)
    else:
      conn = httplib.HTTPConnection(Haldor.host)
    conn.request("POST", "/haldor/{0}".format(path), body, headers)
    print "Notified {0}".format(path)
    return conn.getresponse()
  
  def notify_bootup(self):
    try:
      uptime = subprocess.check_output("uptime")
      uname = subprocess.check_output(["uname", "-a"])
      if_eth0 = subprocess.check_output(["/sbin/ifconfig", "eth0"])
      
      resp = self.notify('bootup', {'uptime': uptime, 'uname': uname, 'ifconfig_eth0': if_eth0})
      self.session = resp.read()
      print "Bootup Complete: {0}".format(self.session)
    except:
      # TODO: Error handling
      print "Bootup ERROR"
      pass
  
  
  def bootup(self):
    print "Bootup."
    self.secret = ""
    self.session = ""
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
    for name, chan in Haldor.io_names.iteritems():
      gpios[name] = self.read_gpio(chan)
    
    return gpios
  
  def notify_checkup(self, checks):
    self.notify('checkup', checks)
  
  def checkup(self):
    print "Checkup."
    checks = {}
    self.check_gpios(checks)
    print checks
    self.notify_checkup(checks)
  
  def run(self):
    self.bootup()
    
    while True:
      self.checkup()
      # Execute checkup every 5 minutes
      time.sleep(Haldor.checkup_interval)
