from droned.entity import Entity
from droned.protocols.psproxy.process import Process as original
from droned.models.event import Event
from twisted.internet import defer

class Process(Entity):
    """mimics psutil.Process"""
    reapable = True
    def __init__(self, pid):
        self.pid = pid
        self._process = original(pid)

    def is_running(self):
        """is the process running"""
        return self._process.is_running()

    def get_open_files(self):
        """Returns files opened by the process"""
        return self._process.get_open_files()

    def get_threads(self):
        """return threads open by process"""
        return self._process.get_threads()

    @defer.inlineCallbacks
    def get_children(self):
        """returns the children"""
        data = yield self._process.get_children()
        defer.returnValue([ Process(i.pid) for i in data if i.pid ])

    def get_connections(self, kind='inet'):
        """
        Return connections opened by process as a list of namedtuples.
        The kind parameter filters for connections that fit the following
        criteria:
 
        Kind Value      Connections using
        inet            IPv4 and IPv6
        inet4           IPv4
        inet6           IPv6
        tcp             TCP
        tcp4            TCP over IPv4
        tcp6            TCP over IPv6
        udp             UDP
        udp4            UDP over IPv4
        udp6            UDP over IPv6
        all             the sum of all the possible families and protocols
        """
        return self._process.get_connections(kind=kind)

    def get_cpu_times(self):
        """return the cpu times"""
        return self._process.get_cpu_times()

    def get_memory_info(self):
        """return the memory info"""
        return self._process.get_memory_info()

    def get_cpu_percent(self):
        """returns cpu percent utilization"""
        return self._process.get_cpu_percent()

__all__ = ['Process']
