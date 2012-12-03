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

from droned.protocols.blaster import MultiClient
from twisted.internet import defer
import sys

DEFAULT_TIMEOUT = 120.0

__author__ = 'Justin Venus <justin.venus@gmail.com>'

def blast(command, clientList, keyObj, **kwargs):
    """Public Method to send messages to a DroneD Client.

       command:    String
       clientList: List
       keyName:    String
       timeout:    number
       callback:   function(dict)

       returns deferred

         This function will send a blaster protocol command to all servers
         and execute all callback function that accepts one parameter. The
         one callback parameter is a dictionary response to the supplied
         command action.
    """
    R = None
    try:
        R = sys.modules['config'].reactor
    except KeyError:
        from twisted.internet import reactor
        R = reactor

    timeout = kwargs.pop('timeout', DEFAULT_TIMEOUT)
    callback = kwargs.pop('callback',None)
    if 'timeout' not in kwargs:
        kwargs['timeout'] = DEFAULT_TIMEOUT

    Debug = kwargs.pop('debug', False)
    blaster = MultiClient(R, clientList, timeout=timeout)

    d = blaster(command, keyObj)
    if callback and hasattr(callback, '__call__'):
        d.addCallback(callback)
    return d

class DroneBlaster(MultiClient):
    def __init__(self, serverList, debug=False):
        try:
            R = sys.modules['config'].reactor
        except KeyError:
            from twisted.internet import reactor
            R = reactor
        MultiClient.__init__(self, R, serverList)

