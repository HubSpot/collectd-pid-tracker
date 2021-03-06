#!/usr/bin/python
import os, re, time, sys, glob
import xml.etree.ElementTree as ET
import psutil
import string

PLUGIN='pid-tracker'
# metric names:
UPTIME_METRIC        = 'process-uptime'
RSS_METRIC           = 'process.rss.bytes'
SHARED_MEM_METRIC    = 'process.shared-mem.bytes'

def parse_bool(val):
  return True if val.__str__() in ['True', 'true'] else False

class PidState(object):
  def __init__(self, pid_file=None, plugin_instance=None, collect_mem_stats=False, mem_stats_interval=None):
    self.pid_file = pid_file
    self.plugin_instance = plugin_instance
    self.collect_mem_stats = collect_mem_stats
    self.mem_stats_interval = mem_stats_interval
    self.interval_counter = 0
    self.running = False
    self.uptime = 0
    self.rss = 0
    self.shared_mem = 0

  def set_down(self):
    self.running = False
    self.uptime = 0
    self.rss = 0
    self.shared_mem = 0

  def set_up(self, uptime):
    self.running = True
    self.uptime = uptime

  def __str__(self):
    return "pid_file=%s, plugin_instance=%s, running=%s, uptime=%s, collect_mem_stats=%s, mem_stats_interval=%s, rss=%d, shared_mem=%d" % \
      (self.pid_file, self.plugin_instance, self.running, self.uptime, self.collect_mem_stats, self.mem_stats_interval, self.rss, self.shared_mem)

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
        if len(node.children) == 0:
          self.collectd.warning('pid-tracker plugin: PidFile missing or too many children: "%s' % node)
        elif len(node.children) > 2:
          self.collectd.warning('pid-tracker plugin: PidFile has too many children: "%s' % node)
        else:
          plugin_instance = None
          collect_mem_stats = False
          mem_stats_interval = None
          for child_node in node.children:
            if child_node.key == 'PluginInstance':
              plugin_instance = child_node.values[0]
            elif child_node.key == 'CollectMemStats':
              collect_mem_stats = parse_bool(child_node.values[0])
            elif child_node.key == 'MemStatsInterval':
              mem_stats_interval = int(child_node.values[0])

          self.add_pidfile(node.values[0], plugin_instance, collect_mem_stats, mem_stats_interval)

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

              collect_mem_stats_node = tree.find("CollectMemStats")
              if collect_mem_stats_node is None:
                collect_mem_stats = False
              else:
                collect_mem_stats = parse_bool(collect_mem_stats_node.text)

              mem_stats_interval_node = tree.find("MemStatsInterval")
              if mem_stats_interval_node is None:
                mem_stats_interval = None
              else:
                mem_stats_interval = int(mem_stats_interval_node.text)

              if path is None or plugin_instance is None:
                self.collectd.warning('pid-tracker plugin: included PidFile xml config improperly formed. Must include Path and PluginInstance children of root PidFile. path=%s, plugin_instance=%s' % (path, plugin_instance))
                continue

              self.add_pidfile(path.text, plugin_instance.text, collect_mem_stats, mem_stats_interval)
          except Exception, e:
            self.collectd.error('pid-tracker plugin: error parsing PidFile xml config for path %s, exception=%s' % (path, e))

      elif node.key == 'Verbose':
        self.verbose = parse_bool(node.values[0])
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

  def add_pidfile(self, pid_file, plugin_instance, collect_mem_stats, mem_stats_interval):
    if self.pidfiles is None:
      self.pidfiles = dict()

    self.pidfiles[pid_file] = PidState(pid_file, plugin_instance, collect_mem_stats, mem_stats_interval)
    self.collectd.info("pid-tracker plugin: adding pidfile=%s" % (self.pidfiles[pid_file]))

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
        self.dispatch_metrics(state)

    else:
      self.collectd.warning('pid-tracker plugin: skipping because no pid files ("PidFile" blocks) have been configured')

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

          meminfo = process.get_ext_memory_info()
          state.rss = meminfo.rss
          state.shared_mem = meminfo.shared
        except Exception, e:
          state.set_down()
          self.collectd.debug('pid-tracker plugin: pid for pidfile does not point at running process. PidFile=%s, pid=%s. Exception=%s' % (state.pid_file, pid, e))

  def dispatch_metrics(self, pid_state, extra_dimensions=""):
    self.log_verbose('Sending value counter.%s[plugin_instance=%s]=%s, extra_dimensions: %s' % (UPTIME_METRIC, pid_state.plugin_instance, pid_state.uptime, extra_dimensions))
    self.collectd.Values(
      plugin=PLUGIN,
      plugin_instance=pid_state.plugin_instance,
      type="counter", 
      type_instance="%s%s" % (UPTIME_METRIC, extra_dimensions),
      values=[pid_state.uptime]).dispatch()

    print "dispatching metrics %s\n" % (pid_state.collect_mem_stats)
    if self.should_collect_mem_stats(pid_state):
      self.log_verbose("dispatching rss=%d\n" % (pid_state.rss))
      self.collectd.Values(
        plugin=PLUGIN,
        plugin_instance=pid_state.plugin_instance,
        type="gauge",
        type_instance="%s%s" % (RSS_METRIC, extra_dimensions),
        values=[pid_state.rss]).dispatch()

      self.log_verbose("dispatching shared_mem=%d\n" % (pid_state.shared_mem))
      self.collectd.Values(
        plugin=PLUGIN,
        plugin_instance=pid_state.plugin_instance,
        type="gauge",
        type_instance="%s%s" % (SHARED_MEM_METRIC, extra_dimensions),
        values=[pid_state.shared_mem]).dispatch()

  def should_collect_mem_stats(self, pid_state):
    return pid_state.running and pid_state.collect_mem_stats and self.is_mem_collection_interval(pid_state)

  def is_mem_collection_interval(self, pid_state):
    # Assume true if no intervals are specified
    if self.interval is None or pid_state.mem_stats_interval is None:
      return True

    flag = False

    # be sure to send metrics immediately at startup
    if pid_state.interval_counter == 0:
      flag = True

    pid_state.interval_counter += 1

    if pid_state.interval_counter >= (pid_state.mem_stats_interval / self.interval):
      pid_state.interval_counter = 0

    return flag

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
  if len(sys.argv) < 4 or (len(sys.argv) - 1) % 3 != 0 :
    print "Must pass one or more pidfile + process_name + collect_mem_stat_bool tuples"
    print "Usage: python pid_tracker.py /path/to/pidfile.pid process_name true|false [ /path/to/another/pidfile.pid another_process_name true|false [ etc...]]"
    sys.exit(1)

  args = sys.argv[1:]
  pidfiles = dict()
  for i in range(len(args) / 3):
    curr = i * 3
    collect_mem_stats = parse_bool(args[curr+2])
    pidfiles[args[curr]] = PidState(args[curr], args[curr+1], collect_mem_stats)
    print "pidstate=%s\n" % (pidfiles[args[curr]])


  collectd = CollectdMock('pid_tracker')
  pt = PidTracker(collectd, pidfiles=pidfiles, verbose=True)
  pt.read_callback()
else:
  import collectd
  pt = PidTracker(collectd)
  collectd.register_config(pt.configure_callback)
