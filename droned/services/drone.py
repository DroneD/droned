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
from twisted.web.error import NoResource
from twisted.internet import defer, task
import config
import romeo

from kitt.util import getException
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


#output formatters
try: import simplejson as json
except ImportError:
    try: import json
    except: json = None
try: import cPickle as pickle
except ImportError:
    import pickle
from yaml import dump as yaml_dumper
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper
   

def resource_error(func):
    """decorator the get child messages to indicate errors"""
    def decorator(*args, **kwargs):
        try: return func(*args, **kwargs)
        except:
            failure = Failure()
            msg = getException(failure)
            msg += ': ' + failure.getErrorMessage()
            return NoResource(msg)
    return decorator

def make_dict(romeo_key_value):
    """Converts a RomeoKeyValue object to a dictionary.

       @param romeo_key_value L{romeo.foundation.RomeoKeyValue}
       @raises L{exceptions.TypeError} on invalid input
       @return C{dict}
    """
    if isinstance(romeo_key_value, dict): return romeo_key_value
    if not isinstance(romeo_key_value, romeo.foundation.RomeoKeyValue):
        raise TypeError('%s is not a RomeoKeyValue' % str(romeo_key_value))
    return {romeo_key_value.KEY: romeo_key_value.VALUE}

###############################################################################
# Web Resource Bridge to Romeo Configuration
###############################################################################
class _ConfigResource(resource.Resource):
    """Resource to provide serializers and convenient child resource lookup"""
    def __init__(self):
        resource.Resource.__init__(self)
        if not hasattr(self, 'OUTPUT_DATA'):
            OUTPUT_DATA = None

    def json_serialize(self, data):
        """Take a python object and return the json serialized representation.

           @param data C{object}
           @return C{str}
        """
        return json.dumps(data)

    def pickle_serialize(self, data):
        """Take a python object and return the pickle serialized representation.

           @param data C{object}
           @return C{str}
        """
        return pickle.dumps(data)

    def yaml_serialize(self, data):
        """Take a python object and return the yaml serialized representation.

           @param data C{object}
           @return C{str}
        """
        return yaml_dumper(data, Dumper=Dumper, default_flow_style=False)

    def value_serialize(self, data, request):
        """Take a python object and extract the values and apply a delimitter.

           @param data C{object}
           @return C{str}
        """
        def default_pack(d):
            if isinstance(d, (str,int,float,bool)):
                return str(d)
        output = []
        delimiter = request.args.get('delimiter', ['\n'])[0]
        if 'pickle' in request.args.get('format', []):
            request.setHeader("Content-Type", "text/x-pickle.python")
            func = self.pickle_serialize
        elif 'yaml' in request.args.get('format', []):
            request.setHeader("Content-Type", "text/yaml")
            func = self.yaml_serialize
        elif 'json' in request.args.get('format', []):
            request.setHeader("Content-Type", "application/json")
            func = self.json_serialize
        else:
            request.setHeader("Content-Type", "text/plain")
            func = default_pack
        #if this is a dictionary it is an early item in the tree
        if isinstance(data, dict):
            for o in data.values():
                value = func(o)
                if not value:
                    output += o
                    continue
                output.append(value)
        else: #this is the normal use case
            for item in data:
                for o in item.values():
                    value = func(o)
                    if not value: continue
                    output.append(value)
        return delimiter.join(output)

    def getChild(self, name, request):
        """overrode to get child resource if applicable"""
        r = self.children.get(name, self)
        if r is self: return self
        return r.getChild(name, request)

    @resource_error
    def render_GET(self, request):
        if 'values' in request.args.get('format', []):
            return self.value_serialize(self.OUTPUT_DATA, request)
        if 'pickle' in request.args.get('format', []):
            request.setHeader("Content-Type", "text/x-pickle.python")
            return self.pickle_serialize(self.OUTPUT_DATA)
        if 'yaml' in request.args.get('format', []):
            request.setHeader("Content-Type", "text/yaml")
            return self.yaml_serialize(self.OUTPUT_DATA)
        request.setHeader("Content-Type", "application/json")
        return self.json_serialize(self.OUTPUT_DATA)


class ConfigResource(_ConfigResource):
    """HTTP Resource /config"""
    isLeaf = False
    OUTPUT_DATA = property(lambda s: {'RESOURCES': s.children.keys()})

    def __init__(self):
        _ConfigResource.__init__(self)
        self.putChild('environment', EnvironmentResource())
        self.putChild('server', ServerResource())


class EnvironmentResource(_ConfigResource):
    """HTTP Resource /config/environment"""
    isLeaf = False
    environments = [ i.get('NAME').VALUE for i in romeo.listEnvironments() ]
    OUTPUT_DATA = property(lambda s: {'ENVIRONMENTS': s.environments})

    def getChild(self, name, request):
        if name in self.environments:
            return RomeoResource(name, romeo.getEnvironment(name))
        return _ConfigResource.getChild(self, name, request)


class ServerResource(_ConfigResource):
    """HTTP Resource /config/server"""
    isLeaf = False
    servers = [ i.VALUE for i in romeo.grammars.search('select HOSTNAME') ]
    OUTPUT_DATA = property(lambda s: {'SERVERS': s.servers})

    def getChild(self, name, request):
        if name in self.servers:
            return RomeoResource(name, romeo.whoami(name))
        return _ConfigResource.getChild(self, name, request)


nodata = object()
class RomeoResource(_ConfigResource):
    """dynamically recurse romeo objects to the appropriate spot"""
    def __init__(self, name, entity):
        self.entity = entity
        #dynamically determine if this node is an endpoint
        self.isLeaf = not entity.COMPLEX_CONSTRUCTOR
        self.name = name
        #helps handle leaf nodes
        if self.isLeaf:
            self.data = entity
        else:
            self.data = nodata
        _ConfigResource.__init__(self)

    @resource_error
    def getChild(self, name, request):
        """overrode to route us on our way"""
        if not name: return self
        #allow servers to get back to their environment for global information
        if name.upper() == 'ENVIRONMENT' and self.entity.KEY == 'SERVER':
            env = None
            for i in self.entity.search('ENVIRONMENT'):
                if i.isChild(self.entity):
                    env = i
                    break
            if not env:
                raise romeo.EnvironmentalError('Serious issue for %s' % name.lower())
            return RomeoResource(name, env)
        key = [ i for i in self.entity.keys() if i.upper() == name.upper() ]
        if not key:
            raise romeo.EnvironmentalError('no such romeo key %s' % name.lower())
        key = key.pop()
        data = self.entity.get(key)
        if not self.isLeaf:
            self.data = data
        return self

    @property
    def OUTPUT_DATA(self):
        """format the entity data for display"""
        if self.data is nodata:
            self.data = self.entity
        if hasattr(self.data, '__iter__') and not isinstance(self.data, dict):
            self.data = [ make_dict(d) for d in self.data ]
        else:
            self.data = [ make_dict(self.data) ]
        return self.data


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
       for x in (i for i in ['favicon.ico','gremlin','config']):
           if x in line: break
       else: http_log(line)


###############################################################################
# Service API Requirements
###############################################################################
def install(_parentService):
    global parentService
    global SERVICECONFIG
    for var, val in SERVICECONFIG.wrapped.items():
        setattr(config, var, val)
    parentService = _parentService
    if SERVICENAME in config.AUTOSTART_SERVICES:
        from kitt.daemon import owndir
        owndir(config.DRONED_USER, config.DRONED_WEBROOT)

def start():
    global service
    if not running():
        kwargs = {'timeout': 60 * 60 * 12, 'logPath': None}
        try: kwargs.update({'timeout': config.DRONED_SERVER_TIMEOUT})
        except: pass
        dr = static.File(config.DRONED_WEBROOT)

        dr.putChild('gremlin', Gremlin())
        dr.putChild('config', ConfigResource())

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
