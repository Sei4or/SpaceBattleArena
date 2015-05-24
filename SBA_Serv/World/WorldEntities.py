"""
Space Battle Arena is a Programming Game.

Copyright (C) 2012-2015 Michael A. Hawker and Brett Wortzman

This program is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with this program; if not, write to the Free Software Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA

The full text of the license is available online: http://opensource.org/licenses/GPL-2.0
"""

import random
import pymunk
import math
import logging
import sys

from WorldCommands import RaiseShieldsCommand
from Messaging import MessageQueue
from Commanding import CommandSystem
from WorldMath import PlayerStat
from Entities import PhysicalRound, PhysicalEllipse

class Ship(PhysicalRound):
    """
    A Ship is the main entity a player controls in the world.
    
    Attributes:
        killed: boolean different than destroyed, represents an object forcibly removed from the world (by GUI or end of round), should NOT respawn
    """

    def __init__(self, pos, world):
        super(Ship, self).__init__(28, 500, pos)
        self._world = world # NOTE: The ONLY reason this is here/needed is because the ship is creating a Torpedo from it's command system...
        self.energyRechargeRate = 4

        self.body.velocity_limit = 100

        # Ship Specific
        self.commandQueue = CommandSystem(self, 4)

        self.shield = PlayerStat(100)
        self.shieldConversionRate = 1.0 # Amount of Shield Recovered Per Energy (Higher Better)
        self.shieldDamageReduction = 0.8 # Amount Damage is Multiplied by and taken from Shields before health is touched (Higher Better)
        self.radarRange = 300

        self.energy = PlayerStat(100)
        self.energyRechargeRate = 4

        self.thrusterForce = 3500
        self.rotationAngle = random.randint(0, 359)
        self.rotationSpeed = 120

        #self.resources = PlayerStat(1000)
        #self.resources.empty()
        #self.miningSpeed = 8
        self.tractorbeamForce = 10000
        
        self.lasernodes = []

        # extra state
        self.killed = False # forcibly removed object

    def setCommandBufferSize(self, size):
        old = self.commandQueue[:]
        self.commandQueue = MessageQueue(size)
        self.commandQueue.extend(old)

    def take_damage(self, damage, by=None):
        if self.commandQueue.containstype(RaiseShieldsCommand) and self.shield.value > 0:
            logging.debug("Shields of #%d absorbed %d damage", self.id, damage * self.shieldDamageReduction)
            self.shield -= damage * self.shieldDamageReduction
            super(Ship, self).take_damage(damage * (1.0 - self.shieldDamageReduction), by)
        else:
            super(Ship, self).take_damage(damage, by)
        #eif

        if self.health == 0:
            self.player.sound = "EXPLODE"
        elif by != None and (isinstance(by, Torpedo) or isinstance(by, Asteroid)):
            self.player.sound = "HIT"

    def update(self, t):
        super(Ship, self).update(t)
        if len(self.commandQueue) > 0: 
            self.commandQueue.update(t)
            self.TTL = None
        elif self.TTL == None and hasattr(self, "player") and self.player.netid >= 0: # if we're a human player, make sure we issue another command within 10 seconds, or kill scuttle the ship.
            logging.info("Ship #%d on clock for not issuing a command in given time.", self.id)
            self.TTL = self.timealive + 10
        self.energy += self.energyRechargeRate * t

    def getExtraInfo(self, objData):
        objData["RADARRANGE"] = self.radarRange
        objData["ROTATION"] = self.rotationAngle
        objData["ROTATIONSPEED"] = self.rotationSpeed
        objData["CURSHIELD"] = self.shield.value
        objData["MAXSHIELD"] = self.shield.maximum

class CelestialBody:
    """
    Celestial Bodies are a sub-baseclass to denote Entities which have an effect when the player is within their range of influence.

    They can also be 'mined' for energy with the LowerEnergyScoopCommand, though this is most effective in Nebulas and Suns
    """
    
    def collide_start(self, otherobj):
        self.in_celestialbody.append(otherobj)
        otherobj.in_celestialbody.append(self)

    def collide_end(self, otherobj):
        if otherobj in self.in_celestialbody:
            self.in_celestialbody.remove(otherobj)
        if self in otherobj.in_celestialbody:
            otherobj.in_celestialbody.remove(self)

class Nebula(CelestialBody, PhysicalEllipse):
    """
    Nebulas are an odd shape.

    They impose a 'friction' on objects and TODO: reduce radar range?
    """
    def __init__(self, pos, size=(384, 256), pull=2000, mass=-1):
        if mass == -1: mass = sys.maxint
        super(Nebula, self).__init__(size, mass, pos)

        self.body.angle = math.radians(random.randint(-30, 30))

        # Everything in Group 1 won't hit anything else in Group 1
        self.shape.group = 1
        
        # Nebulas can't be moved by explosions
        self.explodable = False
        
        self.health = PlayerStat(0)
        self.pull = pull
        #self.resources = PlayerStat(random.randint(500, 2000))

    def collide_start(self, otherobj):
        # TODO: Add 'in' function here and drag
        super(Nebula, self).collide_start(otherobj)
        return False

    def update(self, t):
        # Objects in nebulas get slowed down
        if self.pull > 0:
            for obj in self.in_celestialbody:
                if obj.body.velocity.length > 0.1:
                    obj.body.velocity.length -= (self.pull / obj.mass) * t

        super(Nebula, self).update(t)

    def getExtraInfo(self, objData):
        objData["PULL"] = self.pull

        # Overrides
        objData["DIRECTION"] = -math.degrees(self.body.angle)
        objData["ROTATION"] = -math.degrees(self.body.angle)

        objData["MAJOR"] = self.major
        objData["MINOR"] = self.minor

class Planet(CelestialBody, PhysicalRound):
    """
    Planets (and similar celestial bodies) have gravity which will pull a player towards their center

    TODO:
    Planets have stockpiles of energy which slowly recharge overtime
    Player's can leach energy off of a planet at twice their Energy Recharge Rate (Recover Energy Twice as Fast)
    Planet's also have resources which can be mined, as the planet's resources are mined, the planet recovers energy less
    """
    EnergyRechargeRateFactor = 2

    def __init__(self, pos, size=128, pull=15, radius=60, mass=-1):
        if mass == -1: mass = sys.maxint
        super(Planet, self).__init__(radius, mass, pos)

        #self.energyRechargeRate = 1
        #self.energy = PlayerStat(random.randint(50, 200))

        # Everything in Group 1 won't hit anything else in Group 1
        self.shape.group = 1
        
        # Planets can't be moved by explosions
        self.explodable = False
        
        # Planet specific
        self.health = PlayerStat(0)
        self.pull = pull
        self.gravityFieldLength = size
        #self.resources = PlayerStat(random.randint(500, 2000))

    def getExtraInfo(self, objData):
        objData["PULL"] = self.pull

        objData["MAJOR"] = self.gravityFieldLength
        objData["MINOR"] = self.gravityFieldLength

class BlackHole(Planet):
    """
    BlackHoles are similar to planets however have no resources and have stronger gravity fields
    """
    def __init__(self, pos, size=96, pull=64):
        super(BlackHole, self).__init__(pos, size, pull, 16)

    def collide_start(self, otherobj):
        otherobj.bh_timer = 0
        super(BlackHole, self).collide_start(otherobj)
        return False

    def update(self, t):
        if self.pull > 0:
            # Ships in center of BH for too long get crushed
            for obj in self.in_celestialbody:
                if isinstance(obj, Ship):
                    obj.bh_timer += t
                    if obj.bh_timer >= 5:
                        obj.health.empty()
                        obj.player.sound = "EXPLODE"
                        obj.killedby = self
                        obj.destroyed = True
                        obj._world.remove(obj)

class Star(Planet):
    """
    Stars are similar to planets/blackholes however cause damage in relation to how close you are to their center
    """
    def __init__(self, pos, size=192, pull=32):
        super(Star, self).__init__(pos, size, pull, 90)

    def collide_start(self, otherobj):
        super(Star, self).collide_start(otherobj)
        return False

    def update(self, t):
        # Objects in stars take damage
        if self.pull > 0:
            for obj in self.in_celestialbody:
                if isinstance(obj, Ship):
                    obj.take_damage(max(0, (self.radius + obj.radius - self.body.position.get_distance(obj.body.position)) * t / 16), self)
                    if obj.health == 0: #HACK: Need a better way to detect 'damaged' ships as currently only done on collision
                        obj.destroyed = True
                        obj._world.remove(obj)

        super(Star, self).update(t)

class Asteroid(PhysicalRound):
    """
    Asteroids are given an initial random direction and speed and will travel in that direction forever until disrupted...
    """

    def __init__(self, pos, mass = None):
        #TODO: Make Asteroids of different sizes
        if mass == None:
            mass = random.randint(1500, 3500)
        super(Asteroid, self).__init__(16, mass, pos)
        self.shape.elasticity = 0.8
        self.health = PlayerStat(self.mass / 15.0)

        self.shape.group = 1

        # initial movement
        v = random.randint(20000, 40000)
        self.body.apply_impulse((random.randint(-1, 1) * v, random.randint(-1, 1) * v), (0,0))

class Torpedo(PhysicalRound):
    """
    Torpedos are weapons which are launched from ships at a given position and direction
    """
    def __init__(self, pos, direction, owner=None):
        pos = (pos[0] + math.cos(math.radians(-direction)) * 32,
               pos[1] + math.sin(math.radians(-direction)) * 32)
        super(Torpedo, self).__init__(6, 60, pos)
        self.TTL = 4 # Torpedos last 4 seconds
        self.shape.elasticity = 0.8
        self.health = PlayerStat(1)
        self.owner = owner
        self.explodable = False

        #self.shape.group = 1
        v = 15000
        self.body.apply_impulse((math.cos(math.radians(-direction)) * v,
                                 math.sin(math.radians(-direction)) * v), (0,0))
                                 
    def getExtraInfo(self, objData):
        objData["OWNERID"] = self.owner.id