##############################################################################
#
# Copyright (c) 2009 Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""A ZODB performance test"""

version = "0.5dev"

from setuptools import setup, find_packages
import os
import sys

install_requires=[
    'setuptools',
    'ZODB3',
]

if sys.version_info < (2, 6):
    install_requires.append('multiprocessing')

def read_file(*path):
    base_dir = os.path.dirname(__file__)
    return open(os.path.join(base_dir, *tuple(path))).read()

setup(
    name='zodbshootout',
    version = version,
    description = __doc__,
    long_description = read_file("README.txt"),
    keywords = 'ZODB ZEO RelStorage',
    author = 'Shane Hathaway',
    license = 'ZPL',
    packages = find_packages('src'),
    package_dir = {'': 'src'},
    namespace_packages = [],
    include_package_data = True,
    platforms = 'Any',
    zip_safe = False,
    install_requires=install_requires,
    entry_points = {'console_scripts': [
        'zodbshootout = zodbshootout.main:main',
        ]},
)
