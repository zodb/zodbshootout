"""
Basic persistent object.
"""

# A separate file to reduce imports before gevent
# No ZODB._compat import or anything that imports transaction as that
# would mess up monkey patching.
from pickle import dumps
from persistent import Persistent

class PObject(Persistent):
    """A trivial persistent object"""
    attr = 1

    def __init__(self, data):
        self.data = data


# Estimate the size of a minimal PObject stored in ZODB.
pobject_base_size = (
    len(dumps(PObject)) + len(dumps(PObject(b''))))
