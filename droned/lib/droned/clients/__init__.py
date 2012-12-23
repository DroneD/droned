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

from twisted.python.failure import Failure
from twisted.internet import defer
from twisted.internet.protocol import ClientCreator
from droned.errors import DroneCommandFailed


def connect(host, port, protocol, *proto_args, **proto_kwargs):
    """connect(host, port, protocol, *proto_args, **proto_kwargs)
       Initiate a TCP connection to the given host and port using the given 
       protocol class. *proto_args and **proto_kwargs are passed to the protocol
       constructor, and the last positional argument will be a Deferred for the
       result of the task. The protocol constructor must take at least this one
       argument.
    """
    deferredResult = defer.Deferred()

    proto_args += (deferredResult,)

    if 'timeout' in proto_kwargs:
        timeout = proto_kwargs.pop('timeout')
    else:
        timeout = None

    try: #dmx work around
        import config
        reactor = config.reactor
    except:
        from twisted.internet import reactor
    connector = ClientCreator(reactor, protocol, *proto_args, **proto_kwargs)
    deferredConnect = connector.connectTCP(host, port)

    from kitt.decorators import debugCall
    if 'debug' in proto_kwargs and proto_kwargs['debug'] == True:
        deferredResult.errback = debugCall( deferredResult.errback )
        deferredResult.callback = debugCall( deferredResult.callback )

    #If the connection fails the protocol task fails
    deferredConnect.addErrback(lambda failure: deferredResult.called or 
                               deferredResult.errback(failure))

    if timeout:
        reactor.callLater(timeout, cancelTask, deferredResult)

    #Inject the server name and port into the results in the callback chain
    def injectServer(outcome):
        if isinstance(outcome, dict):
            outcome['server'] = proto_kwargs.get('hostname',host)
            outcome['port'] = port
        elif isinstance(outcome, Failure) and outcome.check(DroneCommandFailed):
            outcome.value.resultContext['server'] = proto_kwargs.get('hostname',
                                                                     host)
            outcome.value.resultContext['port'] = port
        return outcome

    if 'debug' in proto_kwargs and proto_kwargs['debug'] == True:
        injectServer = debugCall( injectServer )

    deferredResult.addBoth(injectServer)

    return deferredResult


def cancelTask(deferredResult):
    """As suggested this cancels our task"""
    if not deferredResult.called:
        defer.timeout(deferredResult)
