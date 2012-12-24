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

import platform
_name = platform.system().lower()
if _name == 'linux':
    try: #we should have epollreactor
        from twisted.internet import epollreactor
        epollreactor.install()
    except: pass
#todo finish filling the reactors out

from twisted.internet import reactor

from twisted.python import usage, util
import copyright
import romeo
import copy
import sys
import os
import re

try:
    from kitt import rsa
    from kitt.util import dictwrapper
except ImportError:
    sys.stderr.write("""
You probably tried to import the rsa module before the python path was
properly setup.  Here comes the traceback, in case my assumption is wrong.
""")
    import traceback
    traceback.print_exc()
    sys.exit(1)

__author__ = "Justin Venus <justin.venus@gmail.com>"
__doc__ = """
This module parses the command line and provides runtime configuration.
"""

class Options(usage.Options):
    """Option parser"""
    optParameters = sorted([
        ["config","",None,"Use configuration from file, overrides commandline."],
        ["command","","unix:path=/var/lib/droned/command.sock:mode=666","Command endpoint."],
        ["process","","unix:path=/var/lib/droned/process.sock:mode=666","Process endpoint."],
        ['port',"-p", "tcp:5500", "The server control endpoint"],
        ["uid","","nobody","User ID for unprivledged commands."],
        ["gid","","nobody","Group ID for unprivledged commands."],
        ["journal","",os.path.join(os.path.sep,'var','lib','droned','journal'),
            "Location to write system history"],
        ["homedir","",os.path.join(os.path.sep,'var','lib','droned','home'),
            "Location to use as a home directory"],
        ["debug", "", False, "Don't install signal handlers and turn on debuggging", bool],
        ["hostdb","",os.path.join(os.path.sep,'etc','hostdb'), 
            "The directory providing ROMEO configuration."],
        ["rsadir","",os.path.join(os.path.sep,'etc','pki','droned'), 
            "The directory to providing RSA keys."],
        ["privatekey","","local", "Name of the Server Private RSA key."],
        ["primefile","",os.path.join(os.path.sep,'usr','share','droned','primes'), 
            "Location of prime numbers file."],
        ["concurrency","", 5, 
            "Maximum number of concurrent commands to execute against a remote droned.",
            int],
    ])
    optFlags = []
    def __init__(self):
        usage.Options.__init__(self)

    def postOptions(self):
        """Apply External Configuration."""
        if self['config'] and os.path.exists(self['config']):
            from ConfigParser import SafeConfigParser as _cf
            _config = _cf()
            _config.read(self['config'])
            for key,value in _config.items('droned'):
                if  any([ i for i in self.optFlags if key in i ]):
                    continue #things like stop and nodaemon are not meant for config
                try: #attempt to update the keys
                    if key in self:
                        t = type(self[key])
                        self[key] = t(value)
                        continue
                    self[key] = value
                except: pass

    def opt_version(self):
        """droned version information."""
        util.println("droned (the Drone daemon) %s" % (copyright.version,))
        util.println(copyright.copyright)
        util.println("See LICENSE for details.")
        sys.exit(0)


class SystemAgent(object):
    """abstract system commands."""
    systemd = property(lambda s: s._sysd)
    agent = property(lambda s: s._agent_cmd)
    def __init__(self):
        self._agent_cmd = self._agent('systemctl')
        self._sysd = bool(self._agent_cmd)
        self._defaults = ('--no-pager', '--no-legend')
        if not self.systemd:
            self._agent_cmd = self._agent('service')
            self._chkconfig = self._agent('chkconfig')
            #/sbin/service compatible regex on Centos6.
            self.show_regex = re.compile("(?P<Id>\S+)\s+\(pid\s+(?P<MainPID>[0-9]+)\)\s+is\s+(?P<SubState>[a-zA-Z]+).*|(?P<Id2>\S+)\s+is\s+(?P<SubState2>[a-zA-Z]+).*")

    @staticmethod
    def _agent(x):
        for i in os.environ.get('PATH').split(':'):
            if os.path.exists(os.path.join(i, x)):
                return os.path.join(i,x)

    def list(self):
        if not self.systemd:
            return (self._chkconfig, ('--list',))
        return (self._agent_cmd,
            self._defaults + ('--type=service', 'list-unit-files'))

    def show(self, name):
        if not self.systemd:
            return (self._agent_cmd, (name, 'status'))
        return (self._agent_cmd, self._defaults + ('show', name))

    def disable(self, name):
        if not self.systemd:
            return (self._chkconfig, (name, 'off'))
        return (self._agent_cmd, self._defaults + ('disable', name))

    def enable(self, name):
        if not self.systemd:
            return (self._chkconfig, (name, 'on'))
        return (self._agent_cmd, self._defaults + ('enable', name))

    def start(self, name):
        if not self.systemd:
            return (self._agent_cmd, (name, 'start'))
        return (self._agent_cmd, self._defaults + ('start', name))

    def stop(self, name):
        if not self.systemd:
            return (self._agent_cmd, (name, 'stop'))
        return (self._agent_cmd, self._defaults + ('stop', name))

    def status(self, name):
        if not self.systemd:
            return (self._agent_cmd, (name, 'status'))
        return (self._agent_cmd, self._defaults + ('status', name))

    def restart(self, name):
        if not self.systemd:
            return (self._agent_cmd, (name, 'restart'))
        return (self._agent_cmd, self._defaults + ('restart', name))


class ConfigManager(romeo.entity.Entity):
    """ConfigManager Provides an Interface to get to any
       configuration item.
       
       After the ConfigManager has been instantiated you can
       access config from any python package in the DroneD
       framework simply by placing ```import config``` in
       your code.
    """
    serializable = False #would not be discovered in this file anyway
    def __init__(self):
        self.data = {}
        options = Options()
        options.parseOptions()
        #if we made it here we are running an application.
        self.drone = dictwrapper(dict(options))
        util.println('Initializing Configuration...')
        self.configure()
        util.println('Configuration is loaded.')
        sys.modules['config'] = self
        #automatically subscribe to the watchdog
        from kitt.daemon import WatchDog
        watchdog = WatchDog()
        self.reactor.callWhenRunning(watchdog.start)
        self.reactor.addSystemEventTrigger('during', 'shutdown', watchdog.stop)

    def configure(self):
        """load configuration for the rest of us."""
        try:
            util.println('Processing ROMEO Configuration')
            romeo.reload(datadir=self.drone.hostdb)
            me = romeo.whoami()
        except romeo.IdentityCrisis:
            util.println('ROMEO Configuration is missing SERVER entry for %s' % \
                    (romeo.MYHOSTNAME,))
            util.println('DroneD is Exiting')
            exit(1) #Config FAIL!!!!

        ENV_NAME = [ i for i in romeo.listEnvironments() \
                if i.isChild(me) ][0].get('NAME').VALUE

        ENV_OBJECT = romeo.getEnvironment(ENV_NAME)

        #figure out if we are supposed to run new services
        SERVICES = {}
        for service in me.VALUE.get('SERVICES', []):
            if 'SERVICENAME' not in service: continue
            SERVICES[service['SERVICENAME']] = copy.deepcopy(service)

        #figure out if we are supposed to manage application artifacts
        APPLICATIONS = {}
        for i in romeo.grammars.search('my SHORTNAME'):
            search = str('select ARTIFACT')
            for x in romeo.grammars.search(search):
                if x.get('SHORTNAME') != i: continue
                if isinstance(x.VALUE.get('CLASS', False), type(None)): continue
                if not ENV_OBJECT.isChild(x):
                    continue #right artifact/wrong env check
                APPLICATIONS[i.VALUE] = copy.deepcopy(x.VALUE)

        #journal and drone are builtins and should always run
        AUTOSTART_SERVICES = ('journal','drone')
        for service_object in SERVICES.values():
            if 'AUTOSTART' in service_object and service_object['AUTOSTART']:
                if service_object['SERVICENAME'] in AUTOSTART_SERVICES: continue
                AUTOSTART_SERVICES += (service_object['SERVICENAME'],)

        #make sure the application service is available
#        if APPLICATIONS and 'application' not in AUTOSTART_SERVICES:
#            AUTOSTART_SERVICES += ('application',)
        #primary data storage
        RSA_MASTER_KEY_FILE = os.path.join(
            self.drone.rsadir, self.drone.privatekey + '.private')

        self.data = {
            'system': SystemAgent(),
            'reactor': reactor,
            'AUTOSTART_SERVICES': AUTOSTART_SERVICES,
            'EXCESSIVE_LOGGING': self.drone.debug,
            'ROMEO_API': romeo,
            'ROMEO_HOST_OBJECT': me,
            'ROMEO_ENV_NAME': ENV_NAME,
            'ROMEO_ENV_OBJECT': ENV_OBJECT,
            'HOSTNAME': me.get('HOSTNAME').VALUE,
            'SERVICES': SERVICES,
            'APPLICATIONS': APPLICATIONS,
            'DRONED_PORT': self.drone.port,
            'DRONED_PROCESS_ENDPOINT': self.drone.process,
            'DRONED_COMMAND_ENDPOINT': self.drone.command,
            'DRONED_USER': self.drone.uid,
            'DRONED_GROUP': self.drone.gid,
            'DRONED_HOMEDIR': self.drone.homedir,
            'JOURNAL_DIR': self.drone.journal,
            'DEBUG_EVENTS': self.drone.debug,
            'DRONED_PRIMES': self.drone.primefile,
            'DRONED_KEY_DIR': self.drone.rsadir,
            'DRONED_MASTER_KEY_FILE': RSA_MASTER_KEY_FILE,
            'DRONED_MASTER_KEY': rsa.PrivateKey(RSA_MASTER_KEY_FILE),
            'DRONED_POLL_INTERVAL': 30,
            'SERVER_POLL_OFFSET': 0.333,
            'INSTANCE_POLL_INTERVAL': 1.0,
            'ACTION_EXPIRATION_TIME': 600,
            'DO_NOTHING_MODE': False,
            'MAX_CONCURRENT_COMMANDS': 5,
            'SERVER_MANAGEMENT_INTERVAL': 10,
        }

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
ConfigManager = ConfigManager()

