###############################################################################
#   Copyright 2006 - 2012, Orbitz Worldwide, LLC.
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
from drone.service import Service

ROOM_TEMP = 76

class ToasterService(Service):
    def __init__(self):
        Service.__init__(self)
        self.load('cooking',False)
        self.load('temperature',ROOM_TEMP)
        self.registerEvent('bread-inserted', self.startCooking)
        self.registerEvent('adjust-temp', self.adjustTemperature, recurring=5, silent=True)
        self.registerEvent('toasting-complete', self.stopCooking, condition=lambda: self.temperature > 200)

    def startCooking(self):
        self.persist('cooking',True)
        self.triggerEvent('toasting-complete',delay=60) #Never cook for more than a minute

    def adjustTemperature(self):
        if self.cooking:
            self.temperature += 10
            self.log("Increased temperature to %d degrees" % self.temperature)
        elif self.temperature > ROOM_TEMP:
            self.temperature -= 10
            self.log("Decreased temperature to %d degrees" % self.temperature)
        self.persist('temperature')

    def stopCooking(self):
        self.persist('cooking',False)
