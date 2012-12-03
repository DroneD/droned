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


# The psutil project has granted use of their api in this project.
# http://code.google.com/p/psutil/issues/detail?id=320#c3

from zope.interface import Interface, implements, implementer
from twisted.internet import threads, defer
from twisted.python import components, util
from romeo.entity import ParameterizedSingleton
import psutil

__author__ = "Justin Venus <justin.venus@gmail.com>"
__doc__ = getattr(psutil,'__doc__', "") + """
This is a twisted wrapper library for the excellent psutil library. Many of the
psutil implementations make a lot of blocking IO calls, which don't fit well in
twisted's asynchronous model.  This wrapper makes use of some of twisted's 
ability to hide blocking calls in deferrable threads.  The api should feel very
familiar to anyone who has used psutil or twisted.  This wrapper is about 4-5x
slower than the native psutil implementation, but most of the methods do not
block the reactor now.  There are some places where we may be able to do some
caching and pick up some of the missing speed.
"""

#NOTE ... all the translation of the psutil library may account for the loss
# of speed compared to the native implementation. Just a thought.
def threadQueue(semaphore):
    """We must limit the amount of threaded concurrency we are willing to
       allow.  Threads are dangerous ... so we pick an arbitrarily small
       number of threads.

       @return L{twisted.internet.defer.Deferred}
    """
    def newfunc(func):
        def deferredThreadQueue(*args, **kwargs):
            return semaphore.run(func,*args,**kwargs)
        return deferredThreadQueue
    return newfunc
#Naive attempt to limit the number of concurrent threads
_queue = psutil.NUM_CPUS * 4
_semaphore = defer.DeferredSemaphore(_queue < 32 and _queue or 32)

#we use threads b/c the Linux psutil API makes heavy use of open and has been
#observed to block the reactor under extreme use cases (full system scans).
def threaded(function):
    """This decorator wraps functions and returns deferred

       See twisted.internet.threads.deferToThread

       you should only use this decorator if there are no
       side effects .. ie you are only emitting data, not
       modifying class or server state inside of the thread.

       @return L{twisted.internet.defer.Deferred}
    """
    @threadQueue(_semaphore)
    def deferredInThread(*args, **kwargs):
        return threads.deferToThread(function, *args, **kwargs)
    return deferredInThread

#vars that will be excluded
_exclude_vars = [
    'process_iter', 'get_process_list', 'Popen', 'Process', 'test'
]
#we will expose these but perform no method wrapping
_only_expose = [
    'NoSuchProcess', 'TimeoutExpired', 'AccessDenied', 'Error'
]

def makeDeferredDoc(func, original='psutil'):
    """I extract function docstrings and update it to reflect
       that the original has been wrapped in a deferred call.
    """
    doc = None
    if hasattr(func, '__doc__') and func.__doc__:
        doc = func.__doc__
        doc += "\nrefer to %s.%s for api usage.\n" % (original,func.__name__)
        doc += "\n@return L{twisted.internet.defer.Deferred}"
    return doc

###############################################################################
#this section wraps psutil public api methods into deferred threads
#this feels dirty, but it works remarkably well.
_all = set()
for var, val in vars(psutil).items():
    if var.startswith('_') or var in _exclude_vars:
        continue
    if hasattr(psutil, '__all__') and var not in psutil.__all__:
        continue
    if not hasattr(val, '__call__') or var in _only_expose:
        globals()[var] = val
        _all.add(var)
        continue
    _doc = makeDeferredDoc(val)
#we could probably perform some inteligent platform detection and avoid threads
    globals()[var] = threaded(val)
    if _doc:
        globals()[var].__doc__ = _doc
    _all.add(var)
###############################################################################


def extractVars(obj, exclude=[], original='psutil', extra=None):
    """I extract methods from an object and wrap the methods
       in a deferred thread.  I attempt to preserve documentation.
    """
    result = {}
    for var, val in vars(obj).items():
        if var in exclude: continue
        if not hasattr(val, '__call__') or var.startswith('_'):
            result[var] = val
            continue
        _doc = makeDeferredDoc(val, original=original)
        result[var] = threaded(val)
        if _doc: result[var].__doc__ = _doc
    if extra and isinstance(extra, dict):
        result.update(extra)
    return result

###############################################################################
# Interfaces only used for Adaptation of Instances.
###############################################################################
class IPsutilProcess(Interface):
    """
    A psutil Process Interface.
    """

class IPsutilPopen(Interface):
    """
    A psutil Popen Interface.
    """
###############################################################################
# Special Wrapped Classes
###############################################################################
@threaded
def initialScan(instance):
    try:
        instance._cache['create_time'] = instance.create_time
    except: pass
    try: 
        instance._cache['cmdline'] = instance.cmdline
    except: pass
    try:
        instance._cache['exe'] = instance.exe
    except: pass
    try:
        instance._cache['name'] = instance.name
    except: pass
    
class ProcessCache(ParameterizedSingleton):
    def __call__(klass, *args, **kwargs):
        instance = ParameterizedSingleton.__call__(klass, *args, **kwargs)
        #we know this is new if the cache attribute is missing
        if not hasattr(instance, '_cache'):
            instance._cache = {'original': psutil.Process(*args,**kwargs)}
            initialScan(instance)
            return instance
        if klass.isValid(instance):
            if psutil.Process(instance.pid).create_time == instance.create_time:
                return instance
        klass.delete(instance)
        raise psutil.NoSuchProcess(
            instance.pid, None, "no process found with pid %s" % instance.pid)

class Process(
    ProcessCache(
        'Process', 
        (object,),
        extractVars(
            psutil.Process,
            exclude=['get_children','is_running'],
            original='psutil.Process',
        )
    )):
    """Process was dynamically built from a runtime metaclass.
       see psutil.Process for original api
    """
    implements(IPsutilProcess)
    reapable = True
    @threaded
    def get_children(self):
        children = []
        #intercept the child objects and rewrap them in a nicer object.
        for child in psutil.Process.get_children(self):
            try: children.append(IPsutilProcess(child))
            except: pass
        return children
    #don't forget to add docstrings back to the method.
    get_children.__doc__ = makeDeferredDoc(
        psutil.Process.get_children, original='psutil.Process')

    @threaded
    def is_running(self):
        result = psutil.Process(self.pid).is_running()
        if not result:
            self.__class__.delete(self)
        return result
    is_running.__doc__ = psutil.Process.is_running.__doc__

    @property
    def cmdline(self):
        if 'cmdline' in self._cache:
            return self._cache['cmdline']
        return self._cache['original'].cmdline

    @property
    def name(self):
        if 'name' in self._cache:
            return self._cache['name']
        return self._cache['original'].name

    @property
    def exe(self):
        if 'exe' in self._cache:
            return self._cache['exe']
        return self._cache['original'].exe

    @property
    def create_time(self):
        if 'create_time' in self._cache:
            return self._cache['create_time']
        return self._cache['original'].create_time

@implementer(IPsutilProcess)
def adaptPsutilProcessToProcessProcess(original):
    return Process(original.pid)
components.registerAdapter(
    adaptPsutilProcessToProcessProcess, psutil.Process, IPsutilProcess)


class Popen(
    type(
        'Popen',
        (object,),
        extractVars(psutil.Popen, original='psutil.Popen')
    )):
    """Popen was dynamically built from a runtime metaclass.
       see psutil.Popen for original api.
    """
    implements(IPsutilPopen)


@implementer(IPsutilPopen)
def adaptPsutilPopenToProcessPopen(original):
    return Popen(original.pid)
components.registerAdapter(
    adaptPsutilPopenToProcessPopen, psutil.Popen, IPsutilPopen)


###############################################################################
# Special Wrapped Methods
###############################################################################
def process_iter(*args, **kwargs):
    for process in psutil.process_iter(*args, **kwargs):
        try:
            #intercept the object and rewrap it in a nicer implementation.
            yield IPsutilProcess(process)
        except: pass
process_iter.__doc__ = psutil.process_iter.__doc__


@threaded
def get_process_list(*args, **kwargs):
    processList = []
    for process in psutil.get_process_list(*args, **kwargs):
        #intercept the object and rewrap it in a nicer implementation.
        try: processList.append(IPsutilProcess(process))
        except: continue
    return processList
get_process_list.__doc__ = makeDeferredDoc(psutil.get_process_list)


#publicly exposed api
__all__ = list(_all) + _exclude_vars + ['IPsutilProcess', 'IPsutilPopen']


###############################################################################
# Test method ported to twisted from psutil. Shamelessy borrowed from psutil.
###############################################################################
@defer.inlineCallbacks
def test():
    """List info of all currently running processes emulating a
    ps -aux output.

    @return L{twisted.internet.defer.Deferred}
    """
    import datetime
    import time
    import os
    today_day = datetime.date.today()

    @defer.inlineCallbacks
    def get_process_info(pid):
        proc = Process(pid)
        user = proc.username
        if os.name == 'nt' and '\\' in user:
            user = user.split('\\')[1]
        pid = proc.pid
        data = yield proc.get_cpu_percent(interval=None)
        cpu = round(data, 1)
        data = yield proc.get_memory_percent()
        mem = round(data, 1)
        data = yield proc.get_memory_info()
        rss, vsz = [x / 1024 for x in data]

        # If process has been created today print H:M, else MonthDay
        start = datetime.datetime.fromtimestamp(proc.create_time)
        if start.date() == today_day:
            start = start.strftime("%H:%M")
        else:
            start = start.strftime("%b%d")

        data = yield proc.get_cpu_times()
        cputime = time.strftime("%M:%S", time.localtime(sum(data)))
        cmd = ' '.join(proc.cmdline)
        # where cmdline is not available UNIX shows process name between
        # [] parentheses
        if not cmd:
            cmd = "[%s]" % proc.name
        defer.returnValue("%-9s %-5s %-4s %4s %7s %7s %5s %8s %s" \
                % (user, pid, cpu, mem, vsz, rss, start, cputime, cmd))

    util.println("%-9s %-5s %-4s %4s %7s %7s %5s %7s  %s" \
      % ("USER", "PID", "%CPU", "%MEM", "VSZ", "RSS", "START", "TIME", "COMMAND"))
    pids = yield get_pid_list()
    pids.sort()
    for pid in pids:
        result = yield get_process_info(pid)
        util.println(result)

if __name__ == "__main__":
    from twisted.internet import reactor
    def setupHook():
        d = test()
        d.addCallback(lambda x: reactor.stop())
    reactor.callWhenRunning(setupHook)
    reactor.run()
