from twisted.internet import defer
from twisted.python import failure
from twisted.spread import pb
from droned.clients import endpoint
import config

_machine_methods = [
    "process_list",
    "pid_exists",
    "phymem_usage",
    "phymem_buffers",
    "disk_io_counters",
    "cpu_percent",
    "network_io_counters",
    "find_processes",
]
_process_methods = {
    "get_connections": "process_get_connections",
    "get_cpu_percent": "process_get_cpu_percent",
    "get_cpu_times": "process_get_cpu_times",
    "get_io_counters": "process_get_io_counters",
    "get_ionice": "process_get_ionice",
    "get_memory_info": "process_get_memory_info",
    "get_memory_percent": "process_get_memory_percent",
    "get_open_files": "process_get_open_files",
    "get_threads": "process_get_threads",
    "getcwd": "process_getcwd",
    "is_running": "process_is_running",
}


class _ReconnectingPBClientFactory(pb.PBClientFactory):
    callback = None
    errback = None
    disconnected = None
    def buildProtocol(self, addr):
        def connected(func):
            def decorator(*args, **kwargs):
                result = func(*args, **kwargs)
                d = self.getRootObject()
                if self.callback:
                    d.addCallback(self.callback)
                if self.errback:
                    d.addErrback(self.errback)
                return result
            return decorator
        def lost(func):
            def decorator(*args, **kwargs):
                result = func(*args, **kwargs)
                self.disconnected()
                return result
            return decorator
        proto = pb.PBClientFactory.buildProtocol(self, addr)
        proto.connectionMade = connected(proto.connectionMade)
        if self.disconnected:
            proto.connectionLost = lost(proto.connectionLost)
        return proto

#hide from list method
class MachineConnector(config.ROMEO_API.entity.Entity):
    connected = property(lambda s: s._deferred.called and not s._error)
    machine = property(lambda s: getattr(s, '_object', s._error))
    error = property(lambda s: s._error)

    def __del__(self):
        self._x.stopTrying()

    def __init__(self, description):
        self._error = None
        self._object = None
        self._deferred = defer.Deferred()
        _ReconnectingPBClientFactory.disconnected = self._onDisconnect
        _ReconnectingPBClientFactory.callback = self._onConnect
        _ReconnectingPBClientFactory.errback = self._onError
        self._x = endpoint.reconnectingClientFromString(config.reactor, description)
        self._x.connect(_ReconnectingPBClientFactory())

    def retry(self):
        return self._x.retry()

    def disconnect(self):
        return self._x.disconnect()

    def _onConnect(self, result):
        setattr(self, '_object', result)
        self._deferred.callback(result)
        return result

    def _onError(self, result):
        setattr(self, '_error', result)
        self._deferred.errback(result)
        return result

    def _onDisconnect(self):
        if not self._deferred.called:
            self._deferred.timeout()
        self._deferred = defer.Deferred()

    def __call__(self, method):
        def remote_call(*args, **kwargs):
            if self.error:
                return defer.fail(self.error)
            elif self._deferred.called and getattr(self, '_object', None):
                return self._object.callRemote(method, *args, **kwargs)
            return self._deferred.addCallback(
                lambda x: x.callRemote(method, *args, **kwargs))
        remote_call.__doc__ = """Remote process proxy call %s""" % (method,)
        return remote_call

    def __getattribute__(self, *args):
        name = args[0]
        if name in _machine_methods:
            return self(name) 
        return object.__getattribute__(self, *args)


class Process(object):
    connected = property(lambda s: s._original.connected)
    machine = property(lambda s: s._original.machine)
    error = property(lambda s: s._original.error)
    pid = property(lambda s: s._pid)

    def __init__(self, pid, machine=None):
        if not machine:
            fixclient = config.DRONED_PROCESS_ENDPOINT.split(':mode=')[0]
            self._original = MachineConnector(fixclient)
        else:
            self._original = machine
        self._pid = int(pid)

    def __call__(self, method):
        def call_wrapper(*args, **kwargs):
            args = (self.pid,) + args
            return self._original(method)(*args, **kwargs)
        return call_wrapper

    def __getattribute__(self, *args):
        name = args[0]
        if name in _process_methods:
            return self(_process_methods[name]) 
        return object.__getattribute__(self, *args)
