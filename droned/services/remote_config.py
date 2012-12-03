###############################################################################
#   Copyright 2012 to the present, Orbitz Worldwide, LLC.
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


__author__ = "Justin Venus <justin.venus@orbitz.com>"
__doc__ = """A Service to provide romeo config over droned's command port"""
from kitt.interfaces import moduleProvides, IDroneDService
from kitt.util import dictwrapper, getException
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET
from twisted.web.error import NoResource
from twisted.python.failure import Failure
from droned.models.event import Event
import romeo

moduleProvides(IDroneDService)
parentService = None
service = None
SERVICENAME = 'remote_config'
SERVICECONFIG = dictwrapper({})
dependant_service = 'drone'

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

class _service(object):
    """helper to find our module so we can fire our own service events"""
    @property
    def module(self):
        import sys
        return sys.modules[self.__class__.__module__]
__module__ = _service().module #feels kinda hacky

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
class _ConfigResource(Resource):
    """Resource to provide serializers and convenient child resource lookup"""
    def __init__(self):
        Resource.__init__(self)
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


class ConfigResouce(_ConfigResource):
    """HTTP Resource /remote_config"""
    isLeaf = False
    OUTPUT_DATA = property(lambda s: {'RESOURCES': s.children.keys()})

    def __init__(self):
        _ConfigResource.__init__(self)
        self.putChild('environment', EnvironmentResource())
        self.putChild('server', ServerResource())


class EnvironmentResource(_ConfigResource):
    """HTTP Resource /remote_config/environment"""
    isLeaf = False
    environments = [ i.get('NAME').VALUE for i in romeo.listEnvironments() ]
    OUTPUT_DATA = property(lambda s: {'ENVIRONMENTS': s.environments})

    def getChild(self, name, request):
        if name in self.environments:
            return RomeoResource(name, romeo.getEnvironment(name))
        return _ConfigResource.getChild(self, name, request)


class ServerResource(_ConfigResource):
    """HTTP Resource /remote_config/server"""
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


###############################################################################
# Glue code to get our resource hooked into the server
###############################################################################
def get_resource():
    """get the webserver resource from the droned service"""
    #thanks to twisted interface definitions we know whic argument is
    #the site object in which the original resource is attached.
    return getService(dependant_service).service.args[1].innerfactory.resource

def add_hooks(occurrence):
    """hook into droned's default server resource"""
    if occurrence.service.SERVICENAME not in (dependant_service, SERVICENAME):
        return
    global service
    service = True
    if not running(): return
    resource = get_resource()
    resource.putChild(SERVICENAME, ConfigResouce())

def remove_hooks(occurrence):
    """remove hook from droned's default server resource"""
    if occurrence.service.SERVICENAME not in (dependant_service, SERVICENAME):
        return
    global service
    service = False
    try:
        resource = get_resource()
        if SERVICENAME in resource.children:
            resource.children.pop(SERVICENAME)
    except: pass
    Event('service-stopped').unsubscribe(remove_hooks)
    if occurrence.service.SERVICENAME == 'drone':
        Event('service-stopped').fire(service=__module__)
    else:
        Event('service-started').unsubscribe(add_hooks)

###############################################################################
# API Requirements
###############################################################################
def install(_parentService):
    global parentService
    parentService = _parentService

def start():
    global service
    service = True
    Event('service-started').subscribe(add_hooks)
    Event('service-stopped').subscribe(remove_hooks)

def stop():
    global service
    service = False

def running():
    return bool(service) and getService(dependant_service).running

from services import getService
__all__ = ['install', 'start', 'stop', 'running']
