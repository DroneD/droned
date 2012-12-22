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
import os
import pwd

__author__ = "Justin Venus <justin.venus@orbitz.com>"
__doc__ = """Provide utilities for DroneD"""

def owndir(USER, DIR):
    """behaves like chown -R $PATHSOMEWHERE and mkdir -p """
    uid,gid = pwd.getpwnam(USER)[2:4]
    try:
        if not os.path.exists(DIR):
            os.makedirs(DIR, mode=0755)
        os.chown(DIR, uid, gid)
        for r, d, f in os.walk(DIR):
            for dir in d:
                try: os.chown(os.path.join(r, dir), uid, gid)
                except: pass
            for file in f:
                try: os.chown(os.path.join(r, file), uid, gid)
                except: pass
    except: pass

###############################################################################
# Watchdog Services
###############################################################################

from ctypes import *
from ctypes.util import find_library
import platform

_platform = platform.system().lower()


# default implementation
timer = lambda: 0
keep_alive = lambda: None

if _platform == 'linux':
    try: # systemd version 183+ watchdog support
        systemd_daemon = CDLL( find_library("systemd-daemon") )

        if not systemd_daemon._name:
            raise ImportError("could not find systemd support")

        # <systemd/sd-daemon>
        systemd_daemon.sd_notify.argtypes = [c_int, c_char_p]
        systemd_daemon.sd_notify.restypes = c_int

        def timer():
            """Provides a mechanism to setup the timer"""
            #microseconds take the half live of the environment value
            return float(os.environ.get('WATCHDOG_USEC',0))/2000000.0

        def keep_alive():
            """Notify SystemD that we are alive"""
            return systemd_daemon.sd_notify(0, "WATCHDOG=1")
    except ImportError: pass
# For Solaris we can base the code off of the following example
# http://docs.oracle.com/cd/E19874-01/820-4443-11/appendix1.html


from twisted.internet.task import LoopingCall

class WatchDog(LoopingCall):
    def __init__(self):
        LoopingCall.__init__(self, keep_alive) # this platform methods will keep us alive
        self.__run = timer() # value > 0 allows this task to run

    def start(self):
        if self.__run:
            LoopingCall.start(self, self.__run)

    def stop(self):
        if self.running:
            return LoopingCall.stop(self)

__all__ = ['owndir','WatchDog']
