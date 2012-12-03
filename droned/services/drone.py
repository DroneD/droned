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

from kitt.interfaces import moduleProvides, IDroneDService
moduleProvides(IDroneDService) #requirement
from kitt.util import dictwrapper

import os
SERVICENAME = 'drone'
#override in romeo
SERVICECONFIG = dictwrapper({
    'DRONED_SERVER_TIMEOUT': int(60 * 60 * 12),
    'DRONED_WEBROOT': os.path.join(os.path.sep, 'var','lib','droned','WEB_ROOT'),
    'DRONED_PORT': 5500,
    'DRONED_PRIME_TTL': 120,
})

from twisted.python.failure import Failure
from twisted.application import internet
from twisted.web import server, static, resource
from twisted.internet import defer, task
import config

from kitt.decorators import deferredAsThread
from droned.logging import logWithContext
from droned.entity import Entity
import time
import gc

from droned.protocols.blaster import DroneServerFactory

#setup some logging contexts
http_log = logWithContext(type='http', route=SERVICENAME)
server_log = logWithContext(type=SERVICENAME)
gremlin_log = logWithContext(type='gremlin', route=SERVICENAME)

DIGEST_INIT = None
try: #newer versions of python
    import hashlib
    DIGEST_INIT = hashlib.sha1
except ImportError: #python2.4?
    import sha
    DIGEST_INIT = sha.new

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

# module state globals
parentService = None
service = None

server.version = 'DroneD/twisted' 


class Gremlin(resource.Resource):
    """stream serialized data out of the server"""
    @deferredAsThread
    def _serialize_objects(self, request):
        buf = StringIO()
        for obj in gc.get_objects():
            if isinstance(obj, Entity) and obj.serializable and \
                    obj.__class__.isValid(obj):
                try:
                    buf.write(obj.serialize())
                except:
                    gremlin_log(Failure().getTraceback())
        result = buf.getvalue()
        buf.close()
        return result

    def render_GET(self, request):
        Type = "application/x-pickle.python"
        def _render(result, r):
            if r.finished: return
            r.setHeader("Content-Type", Type)
            r.setHeader("Content-Length", str(len(result)))
            r.setHeader("Pragma", "no-cache")
            r.setHeader("Cache-Control", "no-cache")
            r.write(result)
            r.finish()

        d = self._serialize_objects(request)
        d.addCallback(_render, request)
        d.addErrback(lambda x: gremlin_log(x.getTraceback()) and x or x)
        return server.NOT_DONE_YET
        

class DroneSite(server.Site):
    """Implements DroneD logging"""
    def log(self, request):
       """Overrode to use DroneD logging and filter some requests"""
       line = '%s - -"%s" %d %s "%s" "%s"' % (
                request.getClientIP(),
                '%s %s %s' % (self._escape(request.method),
                              self._escape(request.uri),
                              self._escape(request.clientproto)),
                request.code,
                request.sentLength or "-",
                self._escape(request.getHeader("referer") or "-"),
                self._escape(request.getHeader("user-agent") or "-"))

       #filter this junk out otherwise the logs are very chatty
       if ('favicon.ico' in line) or ('gremlin' in line):
            return

       http_log(line)


###############################################################################
# Service API Requirements
###############################################################################
def install(_parentService):
    global parentService
    global SERVICECONFIG
    for var, val in SERVICECONFIG.wrapped.items():
        setattr(config, var, val)
    parentService = _parentService

def start():
    global service
    if not running():
        kwargs = {'timeout': 60 * 60 * 12, 'logPath': None}
        try: kwargs.update({'timeout': config.DRONED_SERVER_TIMEOUT})
        except: pass
        dr = static.File(config.DRONED_WEBROOT)

        dr.putChild('gremlin', Gremlin())

        site = DroneSite(dr, **kwargs)
        factory = DroneServerFactory(server_log, text_factory=site)
        #service = internet.TCPServer(config.DRONED_PORT, site) 
        service = internet.TCPServer(config.DRONED_PORT, factory) 
        service.setName(SERVICENAME)
        service.setServiceParent(parentService)

def stop():
    global service
    if running():
        service.disownServiceParent()
        service.stopService()
        service = None

def running():
    global service
    return bool(service) and service.running



__all__ = ['start','stop','install','running']
