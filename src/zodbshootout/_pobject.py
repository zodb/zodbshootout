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
    """
    Persistent object for tests.

    This object can resolve all conflicts by simply throwing away
    whatever was found in the database and persisting its current
    state.
    """
    def _write_data(self, data):
        self.data = data

    def __eq__(self, other):
        if isinstance(other, PObject):
            return other.data == self.data and other.attr == self.attr
        return NotImplemented

    def __hash__(self):
        return hash((self.data, self.attr))

    def _p_resolveConflict(self, old_state, committed_state, new_state):
        # pylint:disable=unused-argument
        return new_state


# Estimate the size of a minimal PObject stored in ZODB.
pobject_base_size = (
    len(dumps(PObject)) + len(dumps(PObject(b''))))
