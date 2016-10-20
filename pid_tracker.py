#!/usr/bin/python
import os, re, time, sys, glob
import xml.etree.ElementTree as ET
import psutil
import string

PLUGIN='pid-tracker'
# metric names:
UPTIME_METRIC    = 'process-uptime'

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
  def __init__(self, collectd, pidfiles=None, pid_seen_notif=None, verbose=False, interval=None):
    self.collectd = collectd
    self.pidfiles = pidfiles
    self.pid_seen_notif = pid_seen_notif
    self.verbose = verbose
    self.interval = interval
    self.sent_pid_seen_notif = False

  def configure_callback(self, conf):
    """called by collectd to configure the plugin. This is called only once"""
    for node in conf.children:
      if node.key == 'PidFile':
        if len(node.children) != 1:
          self.collectd.warning('pid-tracker plugin: PidFile missing or too many children: "%s' % node)
        else:
          self.add_pidfile(node.values[0], node.children[0].values[0])
      elif node.key == "Interval":
        self.interval = int(node.values[0])
      elif node.key == "IncludePidFilesFromXml":
        source = node.values[0]
        if os.path.isdir(source):
          source = "%s/*" % (source[:-1] if source.endswith("/") else source)

        path_or_paths = glob.glob(source)
        for path in path_or_paths:
          if not os.path.isfile(path):
            self.collectd.warning('pid-tracker plugin: skipping non-file path %s in parsing IncludePidFilesFromXml' % path)
            continue

          try:
            root = ET.parse(path).getroot()
            for tree in root.findall("PidFile"):
              path = tree.find("Path")
              plugin_instance = tree.find("PluginInstance")

              if path is None or plugin_instance is None:
                self.collectd.warning('pid-tracker plugin: included PidFile xml config improperly formed. Must include Path and PluginInstance children of root PidFile. path=%s, plugin_instance=%s' % (path, plugin_instance))
                continue

              self.add_pidfile(path.text, plugin_instance.text)
          except Exception, e:
            self.collectd.error('pid-tracker plugin: error parsing PidFile xml config for path %s, exception=%s' % (path, e))

      elif node.key == 'Verbose':
        self.verbose = bool(node.values[0])
      elif node.key == 'Notification' and node.values[0] == 'pid_seen':
        if len(node.children) != 5:
          self.collectd.warning("pid-tracker plugin: Notification for 'pid_seen' requires all 5 child properties. No notifications will be sent")
        else:
          self.pid_seen_notif = self.create_notification(node.values[0], node.children)
      else:
        self.collectd.warning('pid-tracker plugin: Unknown config key: %s.' % (node.key))

    if not self.pidfiles:
      self.collectd.error('pid-tracker plugin: plugin loaded but no pidfiles found. Use PidFile or IncludePidFilesFromXml to add one or more to track')
    else:
      self.collectd.info('pid-tracker plugin: successfully loaded, tracking %d pid files' % len(self.pidfiles))
      if self.interval:
        self.collectd.register_read(pt.read_callback, interval=self.interval)
      else:
        self.collectd.register_read(pt.read_callback)

  def add_pidfile(self, pid_file, plugin_instance):
    if self.pidfiles is None:
      self.pidfiles = dict()

    self.pidfiles[pid_file] = PidState(pid_file, plugin_instance)

  def create_notification(self, notification_name, node_children):
    for prop in node_children:
      if prop.key == "PluginInstance":
        plugin_instance = prop.values[0]
      elif prop.key == "Type":
        type = prop.values[0]
      elif prop.key == "TypeInstance":
        type_instance = prop.values[0]
      elif prop.key == "Severity":
        if string.lower(prop.values[0]) == "okay":
            severity = 4
        elif string.lower(prop.values[0]) == "warning":
            severity = 2
        elif string.lower(prop.values[0]) == "failure":
            severity = 1
      elif prop.key == "Message":
        message = prop.values[0]
      else:
        self.collectd.error("pid-tracker plugin: Notification for '%s' is improperly formed. See documentation. No notifications will be sent" % notification_name)
        return None

    self.log_verbose('Sending notification [plugin=%s, plugin_instance=%s, type=%s, type_instance=%s, severity=%s, message=%s]' % (PLUGIN, plugin_instance, type, type_instance, severity, message))
    return self.collectd.Notification(
      plugin=PLUGIN,
      plugin_instance=plugin_instance,
      type=type,
      type_instance=type_instance,
      severity=severity,
      message=message)

  def read_callback(self):
    if self.pidfiles:
      # update all states first so that we can report on whether all expected
      # services are running
      any_running = False
      for state in self.pidfiles.values():
        self.update_state(state)
        any_running |= state.running

      if any_running and self.pid_seen_notif and not self.sent_pid_seen_notif:
        self.pid_seen_notif.dispatch()
        self.sent_pid_seen_notif = True

      for state in self.pidfiles.values():
        self.create_metric(state) \
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
    self.log_verbose('Sending value counter.%s[plugin_instance=%s]=%s, extra_dimensions: %s' % (UPTIME_METRIC, pid_state.plugin_instance, pid_state.uptime, extra_dimensions))
    return self.collectd.Values(
      plugin=PLUGIN,
      plugin_instance=pid_state.plugin_instance,
      type="counter", 
      type_instance="%s%s" % (UPTIME_METRIC, extra_dimensions),
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
    self.notification_mock = CollectdNotificationMock
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

  def Notification(self, plugin=None, plugin_instance=None, type=None, type_instance=None, severity=None, message=None):
    return (self.notification_mock)()

class CollectdValuesMock(object):

  def dispatch(self):
        print self

  def __str__(self):
    attrs = []
    for name in dir(self):
      if not name.startswith('_') and name is not 'dispatch':
        attrs.append("%s=%s" % (name, getattr(self, name)))
    return "<CollectdValues %s>" % (' '.join(attrs))

class CollectdNotificationMock(object):

  def dispatch(self):
        print self

  def __str__(self):
    attrs = []
    for name in dir(self):
      if not name.startswith('_') and name is not 'dispatch':
        attrs.append("%s=%s" % (name, getattr(self, name)))
    return "<CollectdNotification %s>" % (' '.join(attrs))

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
