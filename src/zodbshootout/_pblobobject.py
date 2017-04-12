"""
ZODB.blob.Blob based PObject.
"""
from __future__ import print_function, absolute_import, division

# This can only be imported when it's safe to import potentially gevent monkey-patched
# things.

from ._pobject import PObject

from ZODB.blob import Blob

class BlobObject(PObject):

    _blob = None

    _v_seen_data = b''

    def _write_data(self, data):
        self._blob = Blob(data)
        self._v_seen_data = data

    def zs_read(self):
        with self._blob.open('r') as f:
            self._v_seen_data = f.read()
        return self.attr

    def zs_update(self):
        with self._blob.open('w') as f:
            f.write(self._v_seen_data)
        self.attr = 1
