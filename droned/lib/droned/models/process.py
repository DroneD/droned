from droned.entity import Entity
from droned.protocols.blaster import QueryProcess
from twisted.internet import defer

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
    return methods


class Process(type('Process', (Entity,), _extract_methods())):
    """mimics psutil.Process"""
    reapable = True
    def __init__(self, pid):
        self.pid = pid

    @property
    def process_broker(self):
        import services
        return services.getService('drone').service.instance

__all__ = ['Process']
