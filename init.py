#!/usr/bin/env python
 
import sys
from haldor import Haldor



if __name__ != "__main__":
  print "This must be executed directly."
  sys.exit(3)


daemon = Haldor('/tmp/haldor.pid')
if len(sys.argv) == 2:
  if 'start' == sys.argv[1]:
    print "Starting..."
    daemon.start()
  elif 'stop' == sys.argv[1]:
    print "Stopping..."
    daemon.stop()
  elif 'restart' == sys.argv[1]:
    print "Restarting..."
    daemon.restart()
  elif 'test' == sys.argv[1]:
    daemon.run()
  else:
    print "Unknown command"
    sys.exit(2)
  sys.exit(0)
else:
  print "usage: %s start|stop|restart" % sys.argv[0]
  sys.exit(2)
