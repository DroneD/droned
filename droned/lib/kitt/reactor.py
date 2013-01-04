from twisted.internet import reactor

###############################################################################
#   Copyright 2006 to the present, Orbitz Worldwide, LLC.
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

__author__ = "Jacob Richard <jacob.richard@gmail.com>"
__doc__ = """
This reactor module is intended to adapt older twisted versions
which are missing some key attributes to the newer twisted API. 
This is a simple zope adapter installed to provide these.
"""

import platform
_name = platform.system().lower()
if _name == 'linux':
    try: #we should have epollreactor
        from twisted.internet import epollreactor
        epollreactor.install()
    except: pass
#todo finish filling the reactors out

from twisted.internet import reactor

if not hasattr(reactor, 'getThreadPool'):
    ## This is for the adapter
    from zope.interface import implements, classImplements
    ## This is the Interface we are Adapting
    from twisted.internet.interfaces import IReactorThreads

    ## make sure the other interfaces are advertized.
    from twisted.internet.interfaces import IReactorUNIX, IReactorUNIXDatagram
    from twisted.internet.interfaces import (
        IReactorTCP, IReactorUDP, IReactorSSL, IReactorArbitrary)
    from twisted.internet.interfaces import IReactorProcess, IReactorMulticast
    from twisted.internet.interfaces import IReactorFDSet

    ## Need this for registerAdapter
    from twisted.python import components

    class IReactorThreadsV10(IReactorThreads):
        """
        Dispatch methods to be run in threads.

        Internally, this should use a thread pool and dispatch methods to them.
        """

        def getThreadPool():
            """
            Return the threadpool used by L{callInThread}.  Create it first if
            necessary.

            @rtype: L{twisted.python.threadpool.ThreadPool}
            """


    class AdaptToV10API:
        """Make sure we support getThreadPool"""
        implements(
            IReactorThreadsV10,
            IReactorArbitrary,
            IReactorTCP,
            IReactorUDP,
            IReactorMulticast,
            IReactorFDSet
        )

        def __init__(self, original):
            self.__dict__['original'] = original

        def getThreadPool(self):
            """
            Return the threadpool used by L{callInThread}.  Create it first if
            necessary.

            @rtype: L{twisted.python.threadpool.ThreadPool}
            """
            if self.original.threadpool is None:
                self.original._initThreadPool()
            return self.original.threadpool

        def __getattr__(self, attr):
            return getattr(self.original, attr)

        def __setattr__(self, attr, value):
            return setattr(self.original, attr, value)

    from twisted.internet.posixbase import (
        sslEnabled, unixEnabled, processEnabled)

    if sslEnabled:
        classImplements(AdaptToV10API, IReactorSSL)
    if unixEnabled:
        classImplements(AdaptToV10API, IReactorUNIX, IReactorUNIXDatagram)
    if processEnabled:
        classImplements(AdaptToV10API, IReactorProcess)

    ## Doing the needful
    components.registerAdapter(AdaptToV10API, object, IReactorThreadsV10)
    reactor = IReactorThreadsV10(reactor)

__all__ = ['reactor']

