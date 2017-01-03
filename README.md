# Monitors given pid files, reporting whether it is up or not and for how long

A [CollectD](http://collectd.org) plugin to collect uptime value for pid files. Uses CollectD's [Python plugin](http://collectd.org/documentation/manpages/collectd-python.5.shtml).

####Configuration

##### Adding pids to track

There are two ways to add pids to track. One is by directly declaring them in the configuration file using `PidFile`. The syntax for this looks like:  

```
<PidFile "/path/to/pidfile.pid">
  PluginInstance "foo"
</PidFile>
```

Both the PidFile value string, and the PluginInstance are required using this configuration.  

An alternative configuration method is provided, for the case where you do not know all of the pid files at conf file configuration time. Using `IncludePidFilesFromXml`, at collectd start time the plugin will glob (using python's glob module) the path, and parse any result as xml. You can point it to a path with wildcards, or directly at a file. Two examples:  

```
IncludePidFilesFromXml "/path/to/pidfiles/*.xml"
IncludePidFilesFromXml "/path/to/pidfile-conf.xml"
```

The expected format of the xml file is as follows:  

```xml
<PidFiles>
  <PidFile>
    <Path>/path/to/one.pid</Path>
    <PluginInstance>one</PluginInstance>
  </PidFile>
  <PidFile>
    <Path>/path/to/two.pid</Path>
    <PluginInstance>two</PluginInstance>
    <CollectMemStats>true</CollectMemStats>
    <MemStatsInterval>60</MemStatsInterval>
  </PidFile>
</PidFiles>
```

You can have one or more PidFile blocks per xml file.  

Note: It's unfortunate to need to use XML here instead of collectd's configuration format for consistency. However, I couldn't find any docs on a way to parse a collectd configuration object, so decided on XML.

##### Other configuration

- **`Interval`**: Specify an interval in seconds, if you want to run this at a different interval than globally
- **`Verbose`**: if `true`, print verbose logging (`false`).
- **`CollectMemStats`**: Add as a pid file parameter with `true` to send the RSS and Shared Memory byte sizes
- **`MemStatsInterval`**: Add as a pid file parameter in seconds, sets the requested time interval to collect memory stats. Since the Interval parameter specifies when metrics can be collected, this value should be an even multiple of the Interval parameter.

##### Resulting metrics

- `counter.process-uptime`
- `gauge.process.rss.bytes` if being collected
- `gauge.process.shared-memory.bytes` if being collected
