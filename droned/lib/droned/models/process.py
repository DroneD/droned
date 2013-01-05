from droned.entity import Entity
from droned.protocols.blaster import QueryProcess
from twisted.internet import defer
import re

try: import cPickle as pickle
except ImportError:
    import pickle

def _callRemote(method, doc):
    @defer.inlineCallbacks
    def remoteMethod(self, *args, **kwargs):
        d = self.process_broker.callRemote(
            QueryProcess, method=method, pid=self.pid,
            pickledArguments=pickle.dumps({'args': args, 'kwargs': kwargs}))
        d.addErrback(lambda e: Process.delete(self) and e or e)
        x = yield d #wait for success or destruction of this object
        defer.returnValue(pickle.loads(x['pickledResponse']))
    remoteMethod.__doc__ = doc
    return remoteMethod


def _extract_methods():
    import psutil as _psutil
    methods = dict()
    for var, val in vars(_psutil.Process).items():
        if not hasattr(val, '__call__') or var.startswith('_'):
            continue
        methods[var] = _callRemote(var, val.__doc__)
#        print "Loading proc support for ``%s``" % var
    return methods


class Process(type('Process', (Entity,), _extract_methods())):
    """mimics psutil.Process"""
    reapable = True
    pid = property(lambda s: s._pid)
    exe = property(lambda s: s._data.get('exe', ''))
    cmdline = property(lambda s: s._data.get('cmdline', []))
    create_time = property(lambda s: s._data.get('create_time',0.0))
    name = property(lambda s: s._data.get('name', ''))
    def __init__(self, pid):
        self._pid = pid
        self._data = {}

    @property
    def process_broker(self):
        import services
        return services.getService('drone').service.instance

    def update(self, **data):
        self._data.update(data)

    __str__ = __repr__ = lambda s: s._data.get('original', Entity.__repr__(s))


def find_processes(s):
    """finds all processes that match the regex

       searches the command line.

       @rtype generator
    """
    regex = re.compile(s, re.I)
    for pid in Process.objects:
        cmd = ' '.join(pid.cmdline)
        if not cmd: continue
        match = regex.search(cmd)
        if not match: continue
        yield pid

def list_pids():
    """returns the known process list"""
    return [i.pid for i in Process.objects]

__all__ = ['Process', 'find_processes', 'list_pids']
