from twisted.protocols import amp
from twisted.internet import defer, protocol
from twisted.web import server, static
from droned.errors import DroneCommandFailed
from twisted.python import failure
import struct
import time


DIGEST_INIT = None
try: #newer versions of python
    import hashlib
    DIGEST_INIT = hashlib.sha1
except ImportError: #python2.4?
    import sha
    DIGEST_INIT = sha.new

def unpackify(s):
    """Unpacks the magic number from a DroneD message"""
    n = 0
    while True:
        n += struct.unpack("!B",s[-1])[0]
        s = s[:-1]
        if not s: return n
        n <<= 8

def packify(n):
    """Packs the magic number for a DroneD message"""
    s = ''
    while True:
        i = n & 255
        n >>= 8
        s += struct.pack("!B",i)
        if not n: return s


class MagicException(Exception): pass
class ZeroAttackException(Exception): pass
class SignatureException(Exception): pass
class UnactionableException(Exception): pass
class ClockSkewException(Exception): pass


class DroneCommand(amp.Command):
    """Executes a command"""
    arguments = [
        ('command', amp.String()),
        ('magic', amp.String()),
        ('timestamp', amp.Integer()),
        ('key', amp.String()),
        ('signature', amp.String())
    ]
    response = [
        ('code', amp.Integer()),
        ('sections', amp.Integer())
    ]
    errors = {
        MagicException: 'INVALID_MAGIC',
        ZeroAttackException: 'ZERO_ATTACK',
        SignatureException: 'INVALID_SIGNATURE',
        UnactionableException: 'INVALID_ACTION',
        DroneCommandFailed: 'COMMAND_FAILED',
        ClockSkewException: 'CLOCK_SKEW',
    }


class DroneResult(amp.Command):
    """Retrieve long messages from the server"""
    arguments = []
    response = [('description', amp.String())]
    errors = {
        IndexError: 'EndOfDescription'
    }


class DronePrime(amp.Command):
    """Ask droned for a prime initializer"""
    arguments = []
    response = [('prime', amp.Integer())]


class DronedServerAMP(amp.AMP):
    """Implements the server side droned amp protocol"""
    def __init__(self, *args, **kwargs):
        self._unionWorker = kwargs.pop('factory', None)
        self._logger = kwargs.pop('logger', lambda x: None)
        amp.AMP.__init__(self, *args, **kwargs)
        from droned.models.server import drone
        import config
        self._prime = 0
        self.config = config
        self._drone = drone
        self.sections = []

    def loseConnection(self):
        result = amp.AMP.loseConnection(self)
        if self._prime:
            self._drone.releasePrime(self._prime)
        return result

    @DronePrime.responder
    @defer.inlineCallbacks
    def getPrime(self):
        """get the prime initializer"""
        self._prime = yield self._drone.getprime()
        defer.returnValue({'prime': self._prime})

    @DroneCommand.responder
    @defer.inlineCallbacks
    def executeCommand(self, command, magic, timestamp, key, signature):
        """execute the command on the server"""
        result = {}
        try:
            if abs(int(time.time()) - timestamp) > 120:
                raise ClockSkewException("Timestamp is too far out of sync")

            magicNumber = abs(unpackify(magic))

            if magicNumber == 0:
                raise ZeroAttackException(
                    "Attempted Zero-Attack, dropping request")

            if (magicNumber % self._prime) != 0:
                raise MagicException("Invalid Magic String")

            digest = DIGEST_INIT()
            digest.update(str(magic) + str(timestamp) + str(command))

            assumed = digest.hexdigest()
            trusted = self._drone.keyRing.publicDecrypt(key, signature)

            if trusted != assumed:
                raise SignatureException(
                    "Invalid signature: %s != %s" % (assumed,trusted))

            action = command.split(' ')[0]
            args = command.replace(action,'').lstrip()
            try:
                func = self._drone.get_action(action)
                assert func
            except:
                raise UnactionableException(
                    "Action %s, Not actionable" % (action,))

            #get the remote host for this connection
            host = self.transport.getHandle().getpeername()[0]
            self._logger('Executing "%s" for %s@%s' % (command, key, host))
            #get the result of the request as a deferred
            data = yield defer.maybeDeferred(func, args).addBoth(
                self._drone.formatResults)
            self.sections = self.split_(data.pop('description',''))
            result['sections'] = len(self.sections)
            result['code'] = data.pop('code',0)
        except:
            data = failure.Failure()
            result['code'] = -1
            self.sections = self.split_(data.getTraceback())
            if hasattr(data.value, 'code'):
                result['code'] = data.value.code
            elif hasattr(data.value, 'errno') and data.value.errno:
                result['code'] = data.value.errno
            if hasattr(data.value, 'message'):
                self.sections = self.split_(data.value.message)
            result['sections'] = len(self.sections)
        defer.returnValue(result.copy())

    @DroneResult.responder
    def getDescription(self):
        """returns the description string."""
        return {'description': self.sections.pop(0)}

    def dataReceived(self, data):
        """Overrode to switch to a text protocol such as http."""
        if not hasattr(self, '_initialDataStream') and self._unionWorker:
            setattr(self, '_initialDataStream', None)
            if not data.startswith('\0'):
                #factories should have union workers
                self.innerProtocol = self._unionWorker.buildProtocol(
                    self.transport.getPeer())
                self.innerProtocol.transport = self.transport
                return self.dataReceived(data)
        return amp.AMP.dataReceived(self, data)

    @staticmethod
    def split_(seq):
        """Values are limited to the maximum encodable size in a 
           16-bit length, 65535 bytes.

           @return C{list}
        """
        return [seq[i:i+65535] for i in range(0, len(seq), 65535)]


class DroneServerFactory(protocol.ServerFactory, object):
    """Implements the Core DroneD Command and Control Server"""
    protocol = DronedServerAMP
    def __init__(self, logger, text_factory=None):
        self.logger = logger
        self.innerfactory = text_factory

    def buildProtocol(self, addr):
        """overrode to setup protocol logging."""
        p = self.protocol(factory=self.innerfactory, logger=self.logger)
        p.factory = self
        return p

    def __getattribute__(self, *args):
        f = None #capture the inner resource
        try: return object.__getattribute__(self, *args)
        except: f = failure.Failure()
        try: return getattr(self.innerfactory, *args)
        except: f.raiseException()
        raise AttributeError("%s" % ' '.join(list(args)))
        

class DronedClientAMP(amp.AMP):
    """Implements the client side droned amp protocol"""
    def __init__(self, *args, **kwargs):
        amp.AMP.__init__(self, *args, **kwargs)
        self._prime = 0
        self._deferred = defer.Deferred()

    def connectionMade(self):
        result = amp.AMP.connectionMade(self)
        d = self.callRemote(DronePrime)
        d.addCallback(
            lambda d: setattr(self, '_prime', int(d['prime'])))
        d.addBoth(self._deferred.callback)
        return result

    @defer.inlineCallbacks
    def getPrime(self):
        yield self._deferred
        defer.returnValue(self._prime)

    @defer.inlineCallbacks
    def executeCommand(self, **kwargs):
        result = {}
        data = None
        try:
            data = yield self.callRemote(DroneCommand, **kwargs)
            result['code'] = int(data['code'])
            sections = int(data['sections'])
            description = ""
            while sections:
                data = yield self.callRemote(DroneResult)
                description += data['description']
                sections -= 1
            result['description'] = description
        except:
            data = failure.Failure()
            result['code'] = -1
            result['description'] = data.getTraceback()
            if hasattr(data.value, 'code'):
                result['code'] = data.value.code
            elif hasattr(data.value, 'errno') and data.value.errno:
                result['code'] = data.value.errno
            if hasattr(data.value, 'message'):
                result['description'] = data.value.message
        defer.returnValue(result)


class MultiClient(object):
    """Allows multiple clients to be contacted concurrently."""
    def __init__(self, reactor, serverlist, default_port=5500, timeout=120.0):
        """a list of host:port endpoints"""
        self.servers = {}
        self.reactor = reactor
        self.value = 1
        for server in serverlist:
            if isinstance(server, (tuple,list)):
                hostport = (list(server) + [default_port])[0:2]
            else:
                hostport = (server.split(':') + [default_port])[0:2]
            host, port = hostport[0], int(hostport[1])
            self.servers[(host, port)] = {}

    def _update(self, result, host, port):
        if isinstance(result, failure.Failure):
            data = result
            result = {}
            result['code'] = -1
            result['description'] = data.getTraceback()
            if hasattr(data.value, 'code'):
                result['code'] = data.value.code
            elif hasattr(data.value, 'errno') and data.value.errno:
                result['code'] = data.value.errno
            if hasattr(data.value, 'message'):
                result['description'] = data.value.message
        self.servers[(host, port)] = result.copy()
        return result

    @defer.inlineCallbacks
    def __call__(self, command, keyObj):
        result = {}
        commands = []
        data = {}
        collected = {}

        digest = DIGEST_INIT()
        timestamp = int(time.time())

        @defer.inlineCallbacks
        def collect(result, host, port):
            collected[(host,port)] = result
            r = yield result.getPrime()
            self.value *= r
            defer.returnValue(result) 

        #connect to all clients
        for (host, port) in self.servers.keys():
            client = protocol.ClientCreator(self.reactor, DronedClientAMP)
            d = client.connectTCP(host, port)
            d.addCallback(collect, host, port)
            d.addErrback(self._update, host, port)
            commands.append(d)

        #wait for initialization to complete.
        yield defer.DeferredList(commands,consumeErrors=True)

        #build the shared arguments
        magicStr = packify(self.value)
        payload = str(magicStr) + str(timestamp) + str(command)
        digest.update(payload)
        signature = keyObj.encrypt(digest.hexdigest())

        kwargs = {
            'command': command,
            'magic': magicStr,
            'timestamp': timestamp,
            'key': keyObj.id,
            'signature': signature,
        }

        for ((host, port),connection) in collected.items():
            d = connection.executeCommand(**kwargs)
            d.addBoth(self._update, host, port)
            d.addBoth(lambda s: connection.loseConnection())
            commands.append(d)

        #wait for all commands to be returned. 
        yield defer.DeferredList(commands, consumeErrors=True)
        defer.returnValue(self.servers)
