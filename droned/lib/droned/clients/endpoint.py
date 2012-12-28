from twisted.internet.endpoints import clientFromString
from twisted.internet import defer
import random

class _ReconnectingWrapper(object):
    """loosely based on the reconnecting client factory."""
    maxDelay = 3600
    initialDelay = 1.0
    # Note: These highly sensitive factors have been precisely measured by
    # the National Institute of Science and Technology.  Take extreme care
    # in altering them, or you may damage your Internet!
    # (Seriously: <http://physics.nist.gov/cuu/Constants/index.html>)
    factor = 2.7182818284590451 # (math.e)
    # Phi = 1.6180339887498948 # (Phi is acceptable for use as a
    # factor if e is too large for your application.)
    jitter = 0.11962656472 # molar Planck constant times c, joule meter/mole

    delay = initialDelay
    retries = 0

    def __init__(self, reactor, instance):
        self.reactor = reactor
        self.connector = instance
        self.protocolFactory = None
        self.delay = 0.0
        self._protoInstance = None
        self._try = True

    def _reconnectEvent(self, func):
        def decorator(*args, **kwargs):
            self._protoInstance = None
            try:
                d = defer.maybeDeferred(func, *args, **kwargs)
                d.addErrback(lambda x: None)
            except: pass
            self._scheduleReconnect()
        return decorator

    @defer.inlineCallbacks
    def _scheduleReconnect(self):
        while self._try:
            print 'reconnecting'
            try:
                d = defer.Deferred()
                self.reactor.callLater(self.delay, d.callback, None)
                yield d
                yield self.connector.connect(self.protocolFactory)
            except:
                if not self.delay:
                    self.delay = self.initialDelay
                    continue
                self.delay = min(self.delay * self.factor, self.maxDelay)
                if self.jitter:
                    self.delay = random.normalvariate(self.delay,
                                              self.delay * self.jitter)
                continue
            self.delay = 0.0
            break

    def stopTrying(self):
        """Stop retrying and disconnect"""
        self._try = False
        self.disconnect()

    def disconnect(self):
        """Disconnect, will probably reconnect in a moment."""
        try: self._protoInstance.transport.loseConnection()
        except: pass #could probably make this more complicated, but i don't care enough.

    def retry(self):
        """retry this connection."""
        self.stopTrying()
        self.delay = 0.0
        self._try = True
        return self.connect(self.protocolFactory)
        
    def connect(self, protocolFactory):
        """Connect to the endpoint forever."""
        def wrapper(func):
            def decorator(*args, **kwargs):
                self._protoInstance = func(*args, **kwargs)
                self._protoInstance.connectionLost = self._reconnectEvent(
                    self._protoInstance.connectionLost)
                return self._protoInstance
            return decorator
        if not self.protocolFactory:
            protocolFactory.buildProtocol = wrapper(
                protocolFactory.buildProtocol)
            self.protocolFactory = protocolFactory
        return self._scheduleReconnect()


def reconnectingClientFromString(reactor, description):
    f = clientFromString(reactor, description)
    _wrapper = _ReconnectingWrapper(reactor, f)
    return _wrapper

__all__ = ['reconnectingClientFromString']
