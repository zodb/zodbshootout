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

version = "0.6.dev0"

from setuptools import setup, find_packages
import os

install_requires = [
    'setuptools',
    'ZODB',
    'ZEO'
]

def read_file(*path):
    base_dir = os.path.dirname(__file__)
    with open(os.path.join(base_dir, *tuple(path))) as f:
        return f.read()

setup(
    name='zodbshootout',
    version=version,
    description=__doc__,
    long_description=read_file("README.rst"),
    url='https://github.com/zodb/zodbshootout',
    keywords='ZODB ZEO RelStorage',
    author='Shane Hathaway',
    author_email='shane@hathawaymix.org',
    license='ZPL',
    packages=find_packages('src'),
    package_dir={'': 'src'},
    namespace_packages=[],
    include_package_data=True,
    platforms='Any',
    zip_safe=False,
    install_requires=install_requires,
    entry_points={
        'console_scripts': [
            'zodbshootout = zodbshootout.main:main',
        ]
    },
    extras_require={
        'mysql': ['relstorage[mysql]'],
        'postgresql': ['relstorage[postgresql]'],
        'oracle': ['relstorage[oracle]'],
    },
    classifiers=[
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: POSIX",
        "Intended Audience :: Developers",
        "Development Status :: 4 - Beta"
    ],

)
