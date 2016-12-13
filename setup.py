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


from setuptools import setup, find_packages
import os

install_requires = [
    'objgraph',
    'setuptools',
    # ZODB and ZEO are handled as extras with environment markers
    #'ZODB',
    #'ZEO'
]

def read_file(*path):
    base_dir = os.path.dirname(__file__)
    with open(os.path.join(base_dir, *tuple(path))) as f:
        return f.read()

version = read_file('version.txt').strip()

setup(
    name='zodbshootout',
    version=version,
    description=__doc__,
    long_description=read_file("README.rst"),
    url='http://zodbshootout.readthedocs.io',
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
        ':python_version == "2.7"': [
            'statistics'
        ],
        ":python_full_version > '2.7.8'": [
            'ZODB',
            'ZEO',
        ],
        ":python_full_version < '2.7.9'": [
            # We must pin old versions prior to 2.7.9 because ZEO
            # 5 only runs on versions with good SSL support.
            'ZODB >= 4.4.2, <5.0',
            'ZEO >= 4.2.0, <5.0'
        ],
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
