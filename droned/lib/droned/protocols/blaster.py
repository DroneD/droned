from twisted.protocols import amp
from twisted.internet import defer, protocol
from twisted.web import server, static
from droned.errors import DroneCommandFailed
from twisted.python import failure
import getpass
import struct
import time
import os


from kitt.decorators import debugCall
from romeo import MYHOSTNAME

try:
    from Crypto.Cipher import AES
    from Crypto.Util import randpool
#credit for AES implementation goes to Josh VanderLinden in the following
#http://www.codekoala.com/blog/2009/aes-encryption-python-using-pycrypto/
#we need good enough obfuscation and this handles it.
except ImportError:
#NOTE see fixme below
#    import warnings
#    warnings.warn('AES encyption is not available RSA key is vunerable')
    AES = None
    randpool = None

#FIXME
AES_CURRENTLY_BROKEN = True

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
class PrivilegedCommand(Exception): pass
class UnknownEndpoint(Exception): pass

class TrustedCommand(amp.Command):
    """use this class to check the identity of the remote connection."""
    errors = {
        KeyError: 'UNKNOWN_KEY',
        PrivilegedCommand: 'REQUIRES_TRUST'
    }

    @classmethod
    def responder(cls, methodfunc):
        def trusted(func):
            def decorator(self, *args, **kwargs):
                if not self.trusted:
                    raise PrivilegedCommand(
                        '[%s] command requires trust.' % (cls.__name__,))
                return func(self, *args, **kwargs)
            return decorator
        return super(TrustedCommand,cls).responder(trusted(methodfunc))

#
# Send identity
#
class Identity(amp.Command):
    arguments = [('hostname', amp.String())]
    response = []

#
# Inter - process - communication.
#
class QueryProcess(TrustedCommand):
    arguments = [('method', amp.String()),
                 ('pid', amp.Integer()),
                 ('pickledArguments', amp.String())]
    response = [('pickledResponse', amp.String())]

class ProcessStarted(TrustedCommand):
    arguments = [('pid', amp.Integer()),
                 ('create_time', amp.Float()),
                 ('exe', amp.String()),
                 ('cmdline', amp.String()),
                 ('name', amp.String()),
                 ('original', amp.String())]
    response = []

class ProcessLost(TrustedCommand):
    arguments = [('pid', amp.Integer()),]
    response = []

class ProcessStdout(TrustedCommand):
    arguments = [('pid', amp.Integer()),
                 ('data', amp.String())]
    response = []

class ProcessStderr(TrustedCommand):
    arguments = [('pid', amp.Integer()),
                 ('data', amp.String())]
    response = []

class ProcessExited(TrustedCommand):
    arguments = [('pid', amp.Integer()),
                 ('exitCode', amp.Integer())]
    response = []

class SystemCtrl(TrustedCommand):
    arguments = [('service', amp.String()),
                 ('action', amp.String()),
                 ('argstr', amp.String())]
    response = [('code', amp.Integer()),
                ('description', amp.String()),
                ('signal', amp.String()),
                ('status', amp.Integer())]

class Command(TrustedCommand):
    arguments = [
        ('pickledArguments', amp.String())
    ]
    response = [('code', amp.Integer()),
                ('description', amp.String()),
                ('signal', amp.String()),
                ('status', amp.Integer())]

class SystemSettings(TrustedCommand):
    arguments = [('state', amp.String()),]
    response = []

class SendUser(TrustedCommand):
    arguments = [
        ('user', amp.String()),
        ('crypto', amp.Boolean()),
    ]
    response = [('secret', amp.String())]
#
# Connection Authorization
#
class Authorize(amp.Command):
    arguments = [
        ('key', amp.String()),
        ('timestamp', amp.Integer()),
        ('signature', amp.String()),
        ('magic', amp.String()),
    ]
    response = []
    errors = {
        PrivilegedCommand: 'REQUIRES_TRUST',
        MagicException: 'INVALID_MAGIC',
        ZeroAttackException: 'ZERO_ATTACK',
        SignatureException: 'INVALID_SIGNATURE',
        ClockSkewException: 'CLOCK_SKEW',
        UnknownEndpoint: 'UNKNOWN_CLIENT_NAME',
        KeyError: 'UNKNOWN_KEY',
        ValueError: 'RSA_ERROR'
    }

#
# Blaster commands
#
class DroneCommand(TrustedCommand):
    """Executes a command"""
    arguments = [
        ('command', amp.String()),
    ]
    response = [
        ('code', amp.Integer()),
        ('sections', amp.Integer())
    ]
    errors = {
        UnactionableException: 'INVALID_ACTION',
        DroneCommandFailed: 'COMMAND_FAILED',
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

import traceback

class DronedProtocol(amp.AMP, object):
    mode = AES and AES.MODE_CBC or None
    pool_size = 512
    block_size = 16
    key_size = 32

    def encodeAES(self, text):
        """if a shared secret exists encrypt AES"""
        assert hasattr(self, 'secret') and self.secret
        pad = self.block_size - len(text) % self.block_size
        iv_bytes = randpool.RandomPool(
            self.pool_size
        ).get_bytes(self.block_size)
        encrypted_bytes = iv_bytes + AES.new(
            self.secret,
            self.mode,
            iv_bytes
        ).encrypt(text + pad * chr(pad))
        return encrypted_bytes

    def decodeAES(self, encrypted_bytes):
        """if a shared secret exists decrypt AES"""
        assert hasattr(self, 'secret') and self.secret
        plain_text = AES.new(
            self.secret,
            self.mode,
            encrypted_bytes[:self.block_size]
        ).decrypt(encrypted_bytes[self.block_size:])
        pad = ord(plain_text[-1])
        return plain_text[:-pad]

    @SendUser.responder
    def receiveUser(self, user, crypto):
        """tell me who you are and i'll tell you a secret"""
        self.user = user
#FIXME
        if AES_CURRENTLY_BROKEN:
            return {'secret': ''} #AES is currently troublesome
        if hasattr(self, 'secret') and AES:
            secret = self.secret
        elif AES and randpool and crypto:
            secret = randpool.RandomPool(
                self.pool_size
            ).get_bytes(self.key_size)
        else:
            secret = ''
        return {'secret': secret}

#FIXME this is turning into a mess
    def sendBox(self, box):
        """overrode to encrypt and encypher messages"""
        try:
            if hasattr(self, 'secret') and self.secret:
                try:
                    result = self.encodeAES(box.serialize())
                    return self.transport.write(result)
                except: pass
            if hasattr(self, 'keyObj') and self.keyObj and self.trusted:
                try:
                    result = self.keyObj.encrypt(box.serialize())
                    return self.transport.write(result)
                except: pass
            try:
                return amp.AMP.sendBox(self, box)
            except:
                if self.transport and hasattr(self.transport, 'loseConnection'):
                    self.transport.loseConnection()
        finally:
            if 'secret' in box:
                self.secret = box.get('secret','')

#FIXME this is turning into a mess
    def dataReceived(self, data):
        """overrode to decrypt and decypher messages"""
        if hasattr(self, 'secret') and self.secret:
            try:
                data = self.decodeAES(data)
                return amp.AMP.dataReceived(self, data)
            except: pass
        if hasattr(self, 'keyObj') and self.keyObj:
            try:
                data = self.keyObj.decrypt(data)
                return amp.AMP.dataReceived(self, data)
            except: pass
        try:
            return amp.AMP.dataReceived(self, data)
        except:
            if self.transport and hasattr(self.transport, 'loseConnection'):
                self.transport.loseConnection()

class DronedServerAMP(DronedProtocol):
    """Implements the server side droned amp protocol"""
    keyObj = property(
        lambda s: s._drone.keyRing.publicKeys[s._key],
        None,
        None,
        "reloadable public key for this connection.")
    def __init__(self, *args, **kwargs):
        self._unionWorker = kwargs.pop('factory', None)
        self._logger = kwargs.pop('logger', lambda x: None)
        DronedProtocol.__init__(self, *args, **kwargs)
        from droned.models.server import drone, Server
        from droned.models.event import Event
        import config
        self.Event = Event
        self.Server = Server
        self._prime = 0
        self.config = config
        self._drone = drone
        self.sections = []
        self.trusted = False
        self.server = None
        self.hostname = None
        self._key = None
        self.user = config.DRONED_USER

    def loseConnection(self):
        """clean up when the client disconnects."""
        self.server.connection = None
        result = DronedProtocol.loseConnection(self)
        if self._prime:
            self._drone.releasePrime(self._prime)
        return result

    @Authorize.responder
    def receiveAuthorization(self, key, timestamp, signature, magic):
        """Sets up connection authorization.

           @ptype key - C{str}
           @param key - RSA Key name used for the signature
           @ptype timestamp - C{int}
           @param timestamp - time of connection from the client
           @ptype signature - C{str}
           @param signature - rsa encrypted payload of the client hostname
           @ptype magic - C{str}
           @param magic - byte packed number that factors with the shared prime
        """
        #do not blindly trust b/c droneblaster cannot be guarenteed to be used
        #by a trusted agent. if authorization is requested, make sure it passes
#        if self.trusted:
#            return {}

        if not self.hostname:
            raise UnknownEndpoint(
                "You forgot to send your hostname on connection.")

        if not self._prime:
            raise ZeroAttackException(
                "Attempted Zero-Attack, dropping request")

        # get the server public key
        self._key = key

        if abs(int(time.time()) - timestamp) > 120:
            raise ClockSkewException("Timestamp is too far out of sync")

        magicNumber = abs(unpackify(magic))
        if magicNumber == 0:
            raise ZeroAttackException(
                "Attempted Zero-Attack, dropping request")
        if (magicNumber % self._prime) != 0:
            raise MagicException("Invalid Magic String")

        digest = DIGEST_INIT()
        digest.update(str(magic) + str(timestamp) + str(self.hostname))

        assumed = digest.hexdigest()
        trusted = self.keyObj.decrypt(signature)

        if trusted != assumed:
            raise SignatureException(
                "Invalid signature: %s != %s" % (assumed,trusted))
        host = self.transport.getHandle().getpeername()[0]
        if self.config.DEBUG_EVENTS:
            self._logger(
                'Authorizing Connection for %s at %s' % (self.hostname, host))
        self.trusted = True #this connection is considered trusted.
        return {}

    @Identity.responder
    def receiveIdentity(self, hostname):
        """receive the identity of the remote connection"""
        self.hostname = hostname
        if not self.Server.exists(hostname):
            return {}
        self.server = self.Server(hostname)
        if MYHOSTNAME == hostname:
            if self.transport.getPeer().host == self.transport.getHost().host:
                self._key = self.config.DRONED_MASTER_KEY.id
#                self.trusted = True #we can trust ourself by default
                return {}
#TODO think about automatic authorization from romeo
        return {}

    @ProcessStarted.responder
    def processStarted(self, **kwargs):
        """fire a notification that we have a process"""
        kwargs['cmdline'] = list(
            filter(None, kwargs.pop('cmdline','').split('\00')))
        kwargs['original'] = kwargs['original'].replace('psutil.Proc','Proc',1)
        kwargs['server'] = self.server
        self.Event('process-started').fire(**kwargs)
        return {}

    @ProcessLost.responder
    def processLost(self, pid):
        """fire a notification that we have lost a process"""
        self.Event('process-lost').fire(pid=pid,server=self.server)
        return {}

    @ProcessStdout.responder
    def processStdout(self, pid, data):
        """fire a notification that we have stdout from a process"""
        self.Event('process-stdout').fire(pid=pid,data=data,server=self.server)
        return {}

    @ProcessStderr.responder
    def processStderr(self, pid, data):
        """fire a notification that we have stderr from a process"""
        self.Event('process-stderr').fire(pid=pid,data=data,server=self.server)
        return {}

    @ProcessExited.responder
    def processExited(self, pid, exitCode):
        """fire a notification that we a process has exited"""
        self.Event('process-exited').fire(
            pid=pid,exitCode=exitCode,server=self.server)
        return {}

    @SystemSettings.responder
    def systemState(self, state):
        """fire a notification that we have received system state info."""
        if self.server is self.Server(MYHOSTNAME):
            self.server.connection = self
            self.Event('system-state').fire(state=state,server=self.server)
        return {}
        
    @DronePrime.responder
    @defer.inlineCallbacks
    def getPrime(self):
        """get the prime initializer

           @rtype C{dict}
           @return a prime number valid for this connection.
        """
        self._prime = yield self._drone.getprime()
        defer.returnValue({'prime': self._prime})

    @DroneCommand.responder
    @defer.inlineCallbacks
    def executeCommand(self, command):
        """execute the command on the server"""
        result = {}
        try:
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
            self._logger('Executing "%s" for %s@%s' % (command,self.user,host))
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
        return DronedProtocol.dataReceived(self, data)

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
        

class DronedClientAMP(DronedProtocol):
    """Implements the client side droned amp protocol"""
    def __init__(self, *args, **kwargs):
        DronedProtocol.__init__(self, *args, **kwargs)
        self._prime = 0
        self.deferred = defer.Deferred()
        self.keyObj = None
        self.trusted = False
        self.user = 'nobody'

    def connectionMade(self):
        result = DronedProtocol.connectionMade(self)
        d = self._on_connection()
        d.addBoth(self.deferred.callback)
        return result

    @defer.inlineCallbacks
    def _on_connection(self):
        # send out the identity from romeo first
        yield self.callRemote(Identity, hostname=MYHOSTNAME)
        # ask for the prime initializer
        data = yield self.callRemote(DronePrime)
        self._prime = int(data['prime'])

    @defer.inlineCallbacks
    def getPrime(self):
        yield self.deferred
        defer.returnValue(self._prime)

    def _requestAuthorization(self, keyobj, magic=None, timestamp=None, signature=None):
        key = keyobj.id
        self.keyObj = keyobj
        if not magic:
            signature = None
            timestamp = None
            magic = packify(self._prime)
        if not timestamp:
            signature = None
            timestamp = int(time.time())
        if not signature:
            digest = DIGEST_INIT()
            payload = str(magic) + str(timestamp) + str(MYHOSTNAME)
            digest.update(payload)
            signature = self.keyObj.encrypt(digest.hexdigest())
        try:
            d = self.callRemote(Authorize, key=key, timestamp=timestamp, 
                signature=signature, magic=magic)
        except:
            return defer.fail()
        #on authorization failure close the connection.
        d.addCallback(lambda s: setattr(self, 'trusted', True) and s or s)
        d.addErrback(lambda x: self.transport.loseConnection() and x or x)
        return d #return the deferred now.

    @defer.inlineCallbacks
    def requestAuthorization(self, *args, **kwargs):
        result = yield self._requestAuthorization(*args, **kwargs)
        self.user = os.environ.get(
                'SUDO_USER', os.environ.get('USER', getpass.getuser()))
        x = yield self.callRemote(SendUser, user=self.user, crypto=bool(AES))
        self.secret = x['secret']
        defer.returnValue(result)

    @defer.inlineCallbacks
    def executeCommand(self, command):
        result = {}
        data = None
        try:
            data = yield self.callRemote(DroneCommand, command=command)
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
    def _collect(self, result, host, port):
        self.collected[(host,port)] = result
        r = yield result.getPrime()
        self.value *= r
        defer.returnValue(result) 

    @defer.inlineCallbacks
    def __call__(self, command, keyObj):
        result = {}
        commands = []
        data = {}

        self.collected = {}

        digest = DIGEST_INIT()
        timestamp = int(time.time())

        #connect to all clients
        for (host, port) in self.servers.keys():
            client = protocol.ClientCreator(self.reactor, DronedClientAMP)
            d = client.connectTCP(host, port)
            #modifies the collected dictionary on callback
            d.addCallback(self._collect, host, port)
            d.addErrback(self._update, host, port)
            commands.append(d)

        #wait for initialization to complete.
        yield defer.DeferredList(commands,consumeErrors=True)

        #build the shared arguments
        magicStr = packify(self.value)
        payload = str(magicStr) + str(timestamp) + str(MYHOSTNAME)
        digest.update(payload)
        signature = keyObj.encrypt(digest.hexdigest())

        #authorize the connection. and immediately send command.
        for ((host, port),connection) in self.collected.items():
            d = connection.requestAuthorization(
                keyObj, magicStr, timestamp, signature)
            d.addCallback(self._command, connection, host, port, command)
            d.addErrback(self._update, host, port)
            commands.append(d)

        #wait for authorization and command to complete.
        yield defer.DeferredList(commands,consumeErrors=True)
        self.collected = {}
        defer.returnValue(self.servers)

    def _command(self, result, conn, host, port, com):
        if isinstance(result, failure.Failure):
            return result
        try:
            d = conn.executeCommand(com)
            d.addBoth(self._update, host, port)
            d.addBoth(lambda s: conn.transport.loseConnection())
            return d #return the current deferred.
        except:
            return defer.fail()
