"""
Basic persistent object.
"""
from __future__ import print_function, absolute_import, division

# A separate file to reduce imports before gevent
# No ZODB._compat import or anything that imports transaction as that
# would mess up monkey patching.
from pickle import dumps
from persistent import Persistent

class AbstractPObject(object):
    """
    ABC(-ish) base for the interface to objects we use during the
    reading and writing process.
    """

    # A value that should always be present. It can be set on the instance,
    # but must always have the value 1.
    attr = 1

    def __init__(self, data):
        self._write_data(data)

    def _write_data(self, data):
        raise NotImplementedError()

class PObject(Persistent,
              AbstractPObject):

    def _write_data(self, data):
        self.data = data


# Estimate the size of a minimal PObject stored in ZODB.
pobject_base_size = (
    len(dumps(PObject)) + len(dumps(PObject(b''))))
