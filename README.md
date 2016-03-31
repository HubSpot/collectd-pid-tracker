# Monitors given pid files, reporting whether it is up or not and for how long

A [CollectD](http://collectd.org) plugin to collect uptime value for pid files. Uses CollectD's [Python plugin](http://collectd.org/documentation/manpages/collectd-python.5.shtml).

####Configuration parameters
- **`<PidFile "/path/to/pidfile">`**: PidFile to track, value is the path to the pid. (REQUIRED: no default).
..- **`PluginInstance`**: determines the plugin_instance as reported by collectd. (REQUIRED: no default)
- **`Verbose`**: if `true`, print verbose logging (`false`).
