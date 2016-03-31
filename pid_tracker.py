#!/usr/bin/python
import os, re, time, sys
import psutil

# metric names:
uptime_metric    = 'process-uptime'

class PidState(object):
  def __init__(self, pid_file=None, plugin_instance=None):
    self.pid_file = pid_file
    self.plugin_instance = plugin_instance
    self.running = False
    self.uptime = 0

  def set_down(self):
    self.running = False
    self.uptime = 0

  def set_up(self, uptime):
    self.running = True
    self.uptime = uptime

  def __str__(self):
    return "pid_file=%s, plugin_instance=%s, running=%s, uptime=%s" % \
      (self.pid_file, self.plugin_instance, self.running, self.uptime)

  def __repr__(self):
    return "PidState[%s]" % self.__str__()


class PidTracker(object):
  def __init__(self, collectd, pidfiles=None, verbose=False, interval=None):
    self.collectd = collectd
    self.pidfiles = pidfiles
    self.verbose = verbose
    self.interval = interval

  def configure_callback(self, conf):
    """called by collectd to configure the plugin. This is called only once"""
    for node in conf.children:
      if node.key == 'PidFile':
        if len(node.children) != 1:
          self.collectd.warning('pid-tracker plugin: PidFile missing or too many children: "%s' % node)
        else:
          if self.pidfiles is None:
            self.pidfiles = dict()
          self.pidfiles[node.values[0]] = PidState(node.values[0], node.children[0].values[0])
      elif node.key == "Interval":
        self.interval = int(node.values[0])
      elif node.key == 'Verbose':
        self.verbose = bool(node.values[0])
      else:
        self.collectd.warning('pid-tracker plugin: Unknown config key: %s.' % (node.key))

    if self.interval:
      self.collectd.register_read(pt.read_callback, interval=self.interval)
    else:
      self.collectd.register_read(pt.read_callback)

  def read_callback(self):
    if self.pidfiles:
      # update all states first so that we can report on whether all expected
      # services are running
      all_running = True
      for state in self.pidfiles.values():
        self.update_state(state)
        all_running &= state.running

      for state in self.pidfiles.values():
        extra_dimensions = "[all-services-running=%s,%s-running=%s]" % (all_running, state.plugin_instance, state.running)
        self.create_metric(state, extra_dimensions) \
          .dispatch()
    else:
      self.collectd.warning('pid-tracker plugin: skipping because no pid files ("PidFile" blocks) has been configured')

  def update_state(self, state):
    if os.path.exists(state.pid_file):
      with open(state.pid_file, "r") as f:
        pid = f.read().strip()

      if not pid or not pid.isdigit():
        state.set_down()
        self.collectd.warning('pid-tracker plugin: pidfile contains no pid or bad pid. PidFile=%s, value=%s' % (state.pid_file, pid))
      else:
        try:
          process = psutil.Process(int(pid))
          state.set_up((time.time() - process.create_time) * 1000)
        except Exception, e:
          state.set_down()
          self.collectd.debug('pid-tracker plugin: pid for pidfile does not point at running process. PidFile=%s, pid=%s. Exception=%s' % (state.pid_file, pid, e))

  def create_metric(self, pid_state, extra_dimensions=""):
    self.log_verbose('Sending value counter.%s[plugin_instance=%s]=%s, extra_dimensions: %s' % (uptime_metric, pid_state.plugin_instance, pid_state.uptime, extra_dimensions))
    return self.collectd.Values(
      plugin='pid-tracker', 
      plugin_instance=pid_state.plugin_instance,
      type="counter", 
      type_instance="%s%s" % (uptime_metric, extra_dimensions),
      values=[pid_state.uptime])

  def log_verbose(self, msg):
    if self.verbose:
      self.collectd.info('pid-tracker plugin [verbose]: '+msg)

# The following classes are copied from collectd-mapreduce/mapreduce_utils.py
# to launch the plugin manually (./pid_tracker.py) for development
# purposes. They basically mock the calls on the "collectd" symbol
# so everything prints to stdout.
class CollectdMock(object):
  def __init__(self, plugin):
    self.value_mock = CollectdValuesMock
    self.plugin = plugin

  def info(self, msg):
    print 'INFO: %s' % (msg)

  def warning(self, msg):
    print 'WARN: %s' % (msg)

  def error(self, msg):
    print 'ERROR: %s' % (msg)
    sys.exit(1)

  def debug(self, msg):
    print 'DEBUG: %s' % (msg)

  def Values(self, plugin=None, plugin_instance=None, type=None, type_instance=None, values=None):
    return (self.value_mock)()

class CollectdValuesMock(object):

  def dispatch(self):
        print self

  def __str__(self):
    attrs = []
    for name in dir(self):
      if not name.startswith('_') and name is not 'dispatch':
        attrs.append("%s=%s" % (name, getattr(self, name)))
    return "<CollectdValues %s>" % (' '.join(attrs))

if __name__ == '__main__':
  if len(sys.argv) < 3 or (len(sys.argv) - 1) % 2 != 0 :
    print "Must pass one or more pidfile + process_name pair"
    print "Usage: python pid_tracker.py /path/to/pidfile.pid process_name[ /path/to/another/pidfile.pid another_process_name[ etc...]]"
    sys.exit(1)

  args = sys.argv[1:]
  pidfiles = dict()
  for i in range(len(args) - 2):
    curr = i * 2
    pidfiles[args[curr]] = PidState(args[curr], args[curr+1])


  collectd = CollectdMock('pid_tracker')
  pt = PidTracker(collectd, pidfiles=pidfiles, verbose=True)
  pt.read_callback()
else:
  import collectd
  pt = PidTracker(collectd)
  collectd.register_config(pt.configure_callback)
