###############################################################################
#   Copyright 2012, DroneD Project.
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
###############################################################################

from droned.models.event import Event
from twisted.internet import task, defer
from twisted.python.failure import Failure
from droned.logging import logWithContext, err
from droned.entity import Entity
from droned.clients import blaster
from kitt.util import getException, dictwrapper, crashReport
from kitt.daemon import owndir
import threading #decorator dep
import signal #so we can catch the server shutdown
import config
import time
import sys
import os

from kitt.decorators import (
    deferredInThreadPool, 
    synchronizedDeferred,
    synchronizedInThread,
    deferredAsThread,
    debugCall,
)

__author__ = "Justin Venus <justin.venus@gmail.com>"
__doc__ = """
This pseudo service provides legacy droned service api.

This is not the official API of droned, this is legacy API for backwards
compatibility.  This is so really old code has a chance to be migrated to
a cleaner daemon in a piecemeal sort of way.  It's not well documented on
purpose, because you probably shouldn't be using it.

So why does this API exists?  I was asked very nicely by a friend.

"""

class LazinessException(Exception): pass

###############################################################################
# drone module implementation
###############################################################################
class _DroneNameSpaceModule(object):
    def __init__(self):
        self.data = {}

    def items(self):
        return self.data.items()

    def __getitem__(self, param):
        return self.data.get(param)

    def __setitem__(self, param, value):
        self.data[param] = value

    def __delitem__(self, param):
        if param in self.data:
            del self.data[param]

    def __getattr__(self, param): #compatibility hack
        try:
            return self.data[param]
        except KeyError:
            raise AttributeError("%s has no attribute \"%s\"" % (self, param))

    def __iter__(self):
        for key,value in sorted(self.data.items()):
            yield (key,value)
drone = sys.modules['drone'] = _DroneNameSpaceModule()
#this is a decorator helper
drone.twistedThread = deferredInThreadPool(
    pool=config.reactor.getThreadPool(),R=config.reactor)

###############################################################################
# drone.event module implementation
###############################################################################
class LegacyEvent(object):
    """The Event Class used by drone.service.Service to handle events"""
    def __init__(self,name,callback,condition=None,recurring=0.0,silent=False):
        if (recurring and condition):
            raise AssertionError(
                "recurring and condition args are mutually exclusive")
        self.name = name
        self.callback = callback
        self.condition = condition
        if self.condition: #no idea what this condition can do
            self.condition = drone.twistedThread(self.condition)
        self.recurring = recurring
        self.silent = silent
        self.enabled = True
        self.data = None
        self.triggered = False
        if self.recurring:
            self.recurring = task.LoopingCall(self.trigger)
            self.recurring.start(recurring)

    def occurred(self):
        """determine if an event has occured"""
        if not self.enabled:
            return False
        return self.triggered

    def execute(self):
        """execute a callback from an event"""
        self.triggered = False
        d = self.data
        self.data = None
        return self.runCallback(d)

    @drone.twistedThread
    def runCallback(self, data):
        """run the call back function"""
        if data: return self.callback(data)
        else: return self.callback()

    def trigger(self,data=None,delay=0.0):
        """trigger an event"""
        self.data = data
        if delay:
            config.reactor.callLater(delay, self.trigger, data=data)
            return
        self.triggered = True

    def __repr__(self):
        return self.name
    __str__ = __repr__
drone.event = sys.modules['drone.event'] = _DroneNameSpaceModule()
drone.event.Event = LegacyEvent


if 'CancelledError' not in dir(defer):
    class CancelledError(Exception):
        """
        This error is raised by default when a L{Deferred} is cancelled.
        """


debug = logWithContext(type='debug')
# we limit the number of executing threads to droned's max concurrency (5)
semaphore = defer.DeferredSemaphore(config.MAX_CONCURRENT_COMMANDS)

def thread_safety(func):
    """provide some measure of safety"""
    def decorator(self, *args, **kwargs):
        tid = config.reactor.getThreadPool().currentThread().ident
        if thread_safety._thrdid != tid:
            if config.DEBUG_EVENTS:
                debug(
                    "blocking call to %s from thread %s" % \
                        (func.__name__, str(self)))
            newargs = (self,) + args
            thrd = synchronizedInThread(config.reactor)
            sync = thrd(func)
            return sync(*newargs, **kwargs)
        return func(self, *args, **kwargs)
    return decorator
thread_safety._thrdid = config.reactor.getThreadPool().currentThread().ident

class LegacyService(object):
    """used to store data from legacy services"""
    serializable = True

    def __init__(self, clsname, name):
        self.name = name
        self.clsname = clsname
        self.context = {}

    @staticmethod
    def construct(state):
        legacy = globals()[state['clsname']](state['clsname'],state['name'])
        legacy.context.update(state['context'])
        return legacy

    def __getstate__(self):
        return {
            'name': self.name,
            'clsname': self.clsname,
            'context': self.context
        }

    #make this show up as expected in ``list``
    __str__ = __repr__ = lambda s: s.name

###############################################################################
# drone.service module implementation
###############################################################################
class Service(object):
    """
       Abstract class for all Legacy DroneD Services.

       Every DroneD module that impliments Service
       method to properly register a service with
       DroneD.
    """
    serviceDebug = property(lambda s: config.DEBUG_EVENTS)
    inThread = property(lambda s: s._inThread)
    running = property(lambda s: s._task.running)
    name = property(lambda s: s._name)
    deferred = property(lambda s: s._deferred)
    deferredLock = property(lambda s: s._dLock)

    def __init__(self, name=None, delay_loop=1.0):
        self.events = {}
        self._task = task.LoopingCall(self.start)
        if isinstance(name, type(None)):
            name = self.__class__.__name__
            logname = name
        else:
            logname = '%s,%s' % (self.__class__.__name__, name)
        #bind the storage Entity that is dynamically created.
        self._service = globals()[self.__class__.__name__](
                self.__class__.__name__, name)
        self._loop = delay_loop
        self._stop = False
        self._deferred = defer.succeed(None)
        self._name = name
        #lock up the main loop so you can pause it as well.
        self._dLock = defer.DeferredLock()
        self._check_events = synchronizedDeferred(
            self._dLock)(self._check_events)
        #provide some safety so we know when we are in a thread
        self._inThread = False
        self._log = logWithContext(type=logname)
        if not os.path.exists(self.picklePath):
            owndir(config.DRONED_USER, self.picklePath)
        #determine whether or not to wrap the threaded method with a semaphore.
        config.reactor.addSystemEventTrigger(
            'after', 'legacy-droned-services', self._wrap)

    def log(self, message):
        self._log(message)
        return message

    def _graceful(self, event):
        if not hasattr(event, 'signum'): return
        if event.signum != signal.SIGTERM: return
        if self.running:
            self._task.stop()
        if not self._deferred.called:
            #handle old twisted on centos6
            if not hasattr(self._deferred, 'cancel'):
                try: self._deferred.errback(Failure(CancelledError()))
                except: pass
            else:
                self._deferred.addErrback(self._graceful)
                self._deferred.cancel() # cancel our thread now
                self.log('gracefully cancelled thread')

    def start(self):
        if self._stop: return
        Event('signal').subscribe(self._graceful)
        self._stop = False
        if not self.running:
            self._task.start(self._loop)
        if self._deferred.called:
            self._deferred = self._check_events()
            self._deferred.addErrback(lambda x: None)

    def stop(self):
        self._stop = True
        Event('signal').unsubscribe(self._graceful)
        if self.running:
            self._task.stop()
        #attempt to cancel the thread
        if not self._deferred.called:
            #handle old twisted on centos6
            if not hasattr(self._deferred, 'cancel'):
                try: self._deferred.errback(Failure(CancelledError()))
                except: pass
            else:
                self._deferred.addErrback(self._graceful)
                config.reactor.callLater(0.1, self._deferred.cancel)
        #work around restarting services!!!
        self._deferred.addBoth(
            lambda x: setattr(self, '_stop', False) and x or x)
        return self._deferred

    def _wrap(self):
        """whether or not to add semaphore overhead."""
        #wrap the threaded method in a concurrency lock
        if len(self.server.services.keys()) > config.MAX_CONCURRENT_COMMANDS:
            sync = synchronizedDeferred(semaphore)
            self._deferred_task = sync(self._deferred_task)
        self._wrap = lambda: None #prevent calling again.

    @defer.inlineCallbacks
    def _check_events(self):
        for name, event in self.events.items():
            if self._stop: break
            try:
                occurred = event.occurred()
                if not occurred:
                    if not event.condition: continue
                    self._inthread = True
                    d = event.condition()
                    d.addBoth(
                        lambda x: setattr(self, '_inthread', False) and x or x)
                    occurred = yield d
                if occurred:
                    func = event.callback.__name__
                    if not event.silent:
                        self.log('%s event occurred: calling %s' % (event,func))
                    elif bool(config.DEBUG_EVENTS):
                        debug('%s event occurred: calling %s' % (event,func))
                    self._inthread = True
                    d = event.execute() #the event is executed in the thread
                    d.addBoth(
                        lambda x: setattr(self, '_inthread', False) and x or x)
                    yield d
            except:
                if bool(config.DEBUG_EVENTS):
                    err('Legacy Event [%s] executing %s' % \
                            (name, event.callback.__name__))
                else:
                    failure = Failure()
                    e = getException(failure)
                    m = failure.getErrorMessage()
                    self.log('[%s,%s] %s: %s' % (
                        name, event.callback.__name__, e, m))

    @thread_safety
    def registerEvent(
            self,name,callback,condition=None,recurring=0.0,silent=False):
        'Interface to Register Service Events'
        self.events[name] = drone.event.Event(
            name, callback, condition, recurring, silent)

    @thread_safety
    def triggerEvent(self,name,data=None,delay=0.0):
        'Interface to trigger an out of band service event'
        if name not in self.events:
            raise AssertionError("No such event '%s'" % (name,))
        return self.events[name].trigger(data,delay)

    @thread_safety
    def disableEvent(self,name):
        'Interface to disable a previously registered service event'
        if name not in self.events:
            raise AssertionError("No such event '%s'" % (name,))
        self.events[name].enabled = False

    @thread_safety
    def enableEvent(self,name):
        'Interface to enable a previously disabled registered service event'
        if name not in self.events:
            raise AssertionError("No such event '%s'" % (name,))
        self.events[name].enabled = True

    def set(self,var,val):
        "persist and maintain type, note actions only get str's"
        atype = type(vars(self)[var])
        if atype is bool:
            if str(val).lower() == 'true': val = True
            if str(val).lower() == 'false': val = False
        #automatically set the write type; if possible, else exception
        self.persist(var, atype(val))

    def getConfig(self):
        "romeo is a better fit, compared to the old service config."
        x = "%s:%s" % (self.__class__.__name__,self.name)
        x = config.SERVICES.get(x, {})
        if x: return x
        x = config.SERVICES.get(self.name, {})
        if x: return x
        x = config.SERVICES.get(self.__class__.__name__, {})
        return x #good luck

    def containerDebug(self, Bool=None):
        return 'not available'

    def debugReport(self):
        """
        Kind of emulated twisted.python.failure.Failure, but not as useful.
        """
        f = None
        try: f = Failure()
        except: return #peace out
        if not config.DEBUG_EVENTS:
            self.log("%s: %s" % (getException(f), f.getErrorMessage()))
            return
        #the only place in the entire code base that uses this method.
        crashReport(
            'Service [%s]: Caught unhandled exception' % (self.name,), self) 

    def sendAlert(self, classException, msg):
        """
        if the experimental snmp support was finished we could support this
        """
        self.log('snmptrap is not supported')
        return 0

    def clearAlert(self, id):
        """
        if the experimental snmp support was finished we could support this
        """
        self.log('snmpclear is not supported')

    @thread_safety
    def load(self,var,default=None,private=False):
#NOTE private is legacy and unused
        if default is None:
            vars(self)[var] = self._service.context[var]
            return
        try: vars(self)[var] = self._service.context[var]
        except:
            failure = Failure()
            e = getException(failure)
            self.log(
                '%s while loading %s, using default value.' % (e,var))
            if config.DEBUG_EVENTS:
                debug('Error Message: %s' % (failure.getErrorMessage(),))
            vars(self)[var] = default
            try: self.persist(var)
            except: pass

    @thread_safety
    def persist(self,var,val=None,private=False):
#NOTE private is legacy and unused
        if val is None: val = vars(self)[var]
        vars(self)[var] = self._service.context[var] = val

    __str__ = __repr__ = lambda self: self.name


#just barely implement the interface for a droned service,
#we will complete the implementation later.
class BridgeService(object):
    """bridges original drone.service api to services api"""
    service = property(lambda s: s._service)
    def running(self): return self._service.running
    def start(self): return self.service.start()
    def stop(self): return self.service.stop()
    def install(self, _parent):
        self.parentService = _parent


class LegacyAction(object):
    """Wrap Legacy actions into a deferred, and return the new format."""
    def __init__(self, droned_server, func):
        self.__doc__ = func.__doc__
        self._server = droned_server
        self._func = func

    @deferredAsThread
    def __call__(self, *args, **kwargs):
        result = self._func(self._server, *args, **kwargs)
        if isinstance(result, tuple):
            return {'description': result[0], 'code': int(result[1])}
        elif isinstance(result, str):
            return {'description': result, 'code': 0}
        return result #good luck
    __repr__ = __str__ = lambda s: repr(s._func)


###############################################################################
# legacy core droned server side api implementation.
###############################################################################
class EmulateClassicDroned(object):
    """This class pretends to be the original droned server interfaces.
       It is missing a lot of unused API and that is fine, you don't need it.
    """
    def __init__(self):
        self.services = {}
        self.actions = {}
        self.log = logWithContext(type='droned-legacy')

    def getService(self, name):
        """legacy support method"""
        try:
            return self.services[name]
        except: pass
        raise AssertionError("No Such Service")

    def unregisterService(self, name):
        self.log('unsupported service de-registration for ' + str(name))

    def registerAction(self, name, function):
        """legacy action support"""
        from droned.models.server import drone as _drone
        if not function.__doc__:
            raise LazinessException("The %s action has no docstring!!!" % name)
        if name in _drone.builtins:
            raise AttributeError(
                "The %s action is already registered!!!" % name)
        _drone.builtins[name] = LegacyAction(self, function)
        self.actions[name] = function #legacy blocking support

    def registerService(self, _service):
        """legacy service support"""
        self.services[_service.name] = _service


class DelayedInstance(type):
    """
    We need to delay the construction of the Instance until droned is ready.
    """
    def __init__(cls, name, bases, members):
        super(DelayedInstance, cls).__init__(name, bases, members)

    def __call__(cls, *args, **kwargs):
        #allocate memory for the class object.
        instance = cls.__new__(cls, *args, **kwargs)
        #delay calling init until after the service is loaded and configured
        #also avoids a race condition with the journal service for reloading
        #saved state. We do this b/c we don't have the data store setup yet.
        config.reactor.addSystemEventTrigger(
            'before', 'droned-configured', instance.__init__, *args, **kwargs)
        return instance #return partially initialized instance

drone.service = sys.modules['drone.service'] = _DroneNameSpaceModule()
# control the construction of the legacy service class
drone.service.Service = DelayedInstance(
    'Service', (Service,),
    {
        'server': EmulateClassicDroned(), #historic
        'WEB_ROOT': property(lambda s: config.DRONED_WEBROOT), #historic
        'picklePath': property(
            lambda s: os.path.join(config.DRONED_HOMEDIR,s.name)) #historic
    }
)
drone.service.thread_safety = thread_safety

###############################################################################
# drone.decorators module implementation.
###############################################################################
drone.decorators = sys.modules['drone.decorators'] = _DroneNameSpaceModule()
def synchronized(lock):
    "The function will run with the given lock acquired"
    def decorator(func):
        def newfunc(*args,**kwargs):
            lock.acquire()
            try: return func(*args,**kwargs)
            finally: lock.release()
        return newfunc
    return decorator
drone.decorators.synchronized = synchronized

def threaded(func):
    "Make a function run in its own thread (returns Thread object)"
    def decorator(*args,**kwargs):
        t = threading.Thread(target=func,args=args,kwargs=kwargs)
        t.start()
        return t
    return decorator
drone.decorators.threaded = threaded

def delayedLoop(delay):
    "Run a function in a repeating loop with a delay between iterations"
    def decorator(func):
        def newfunc(*args,**kwargs):
            thrid = config.reactor.getThreadPool().currentThread().ident
            if thrid == delayedLoop._thrdid:
                raise LazinessException("""
                    Blocking call to ``reactor`` detected.

                    use L{twisted.internet.task.LoopingCall} instead.
                    """)
            while True:
                func(*args,**kwargs)
                time.sleep(float(delay))
        return newfunc
    return decorator
delayedLoop._thrdid = config.reactor.getThreadPool().currentThread().ident
drone.decorators.delayedLoop = delayedLoop

def safe(func):
    """
    Stop all exception propagation 
    (except KeyboardInterrupt & SystemExit) 
    but print tracebacks
    """
    def newfunc(*args,**kwargs):
        try: return func(*args,**kwargs)
        except KeyboardInterrupt: raise
        except SystemExit: raise
        except: Failure().printTraceback()
    return newfunc
drone.decorators.safe = safe


###############################################################################
# drone.blaster module support
###############################################################################
drone.blaster = sys.modules['drone.blaster'] = _DroneNameSpaceModule()

def _blast(message, hosts, sigkey, timeout=120, ContentEncoding=None):
    thrid = config.reactor.getThreadPool().currentThread().ident
    if thrid == _blast._thrdid:
        #thanks for using the legacy api
        raise LazinessException("""
        You have called a ``reactor`` blocking legacy method for blaster
        protocol from the same thread as the ``reactor``.  I just saved
        you from deadlock.  You are welcome. So here is what you have to 
        do to avoid this exception.

        1) Use droned.client.blaster.blast from the ``reactor`` thread.
           Note this method will return a L{Deferred} and integrate with
           twisted.

        2) Make sure the caller of this method is running in a different
           thread than the ``reactor``.
        """)
    #note ContentEncoding is not supported
    thrd = synchronizedInThread(config.reactor)
    sync = thrd(blaster.blast)
    result = {} #we need to reformat the response
    for var, val in sync(message, hosts, sigkey, timeout=timeout).items():
        result[var] = tuple([val['code'], val['description']])
    return result
_blast._thrdid = config.reactor.getThreadPool().currentThread().ident
drone.blaster.blast = _blast

# dyamically create new-style droned services from old style services.
# without the aid of a zope interface adapter.
def create_service(obj):
    """Bridge the classic droned service to the modern service api"""
    name = "%s_%s" % (obj.__class__.__name__, obj.name)
    if obj.__class__.__name__ == obj.name:
        name = obj.name
    return (name, type(name, (BridgeService,), 
        {
            '_service': obj, 
            'SERVICENAME': name, 
            'parentService': None,
            'SERVICECONFIG': dictwrapper({})
        }
    ))

# make sure there is something to load.
mod_dir = os.path.join(
    os.path.sep.join(__file__.split(os.path.sep)[:-2]), 'modules')

# i hate module globals.
LEGACY_SERVICES = {}

def initialize():
    """initialization happens after services are configured."""
    import services
    global LEGACY_SERVICES
    for var, val in LEGACY_SERVICES.items():
        if val.name in drone.service.Service.server.services:
            continue
        drone.service.Service.server.registerService(val)
        name, srvc = create_service(val)
        services.EXPORTED_SERVICES[name] = srvc()
        #add the legacy service to the autostart services
        if name not in config.AUTOSTART_SERVICES:
            config.AUTOSTART_SERVICES += (name,)
    config.reactor.fireSystemEvent('legacy-droned-services')

# important modules must come after the metaclass
try: #attempt to load legacy services.
    assert os.path.exists(mod_dir)
    assert os.path.exists(os.path.join(mod_dir, '__init__.py'))
    import modules #import the modules
    for var, val in vars(modules).items():
        if isinstance(val, drone.service.Service):
            LEGACY_SERVICES[var] = val
            #dynamically create the data storage entity
            globals()[val.__class__.__name__] = type(
                val.__class__.__name__, (LegacyService,Entity), {})
        elif var.endswith('_action') and len(var) > 7:
            if not hasattr(val, '__call__'): continue
            actName = var[:-7]
            try:
                drone.service.Service.server.registerAction(actName,val)
            except:
                err('In action setup')
except ImportError: err('Loading DroneD Service API v1.0')
except AssertionError: pass
except: err('Something bad happened')

# to avoid circular dependencies we will update the service api out of band.
#config.reactor.addSystemEventTrigger('after', 'droned-configured', loadAll)
config.reactor.addSystemEventTrigger('after', 'droned-configured', initialize)
if config.DEBUG_EVENTS: debug("Enabled Legacy DroneD Service API Hooks")
__all__ = [] #don't expose anything, b/c we are just a loader.
