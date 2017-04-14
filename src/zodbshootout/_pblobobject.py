"""
ZODB.blob.Blob based PObject.
"""
from __future__ import print_function, absolute_import, division

# This can only be imported when it's safe to import potentially gevent monkey-patched
# things.

from ._pobject import PObject

from ZODB.blob import Blob

class BlobObject(PObject):

    blob = None

    _v_seen_data = b''

    def _write_data(self, data):
        self.blob = Blob(data)
        self._v_seen_data = data
