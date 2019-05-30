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
    'pyperf',
    'ZODB >= 4.4.2',
    'ZEO >= 4.2.0',
    'statistics; python_version == "2.7"'
]

tests_require = [
    'zope.testrunner',
    'nti.testing',
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
    keywords='ZODB ZEO RelStorage benchmark',
    author='Shane Hathaway',
    author_email='shane@hathawaymix.org',
    maintainer='Jason Madden',
    maintainer_email='jason@nextthought.com',
    license='ZPL',
    packages=find_packages('src'),
    package_dir={'': 'src'},
    namespace_packages=[],
    include_package_data=True,
    platforms='Any',
    zip_safe=False,
    install_requires=install_requires,
    tests_require=tests_require,
    entry_points={
        'console_scripts': [
            'zodbshootout = zodbshootout.main:main',
        ]
    },
    extras_require={
        'mysql': [
            'relstorage[mysql] >= 2.0rc1',
            # Until RelStorage 3.0 is released, we need to pin to an older version
            # of mysqlclient on Python 3.
            # https://github.com/zodb/relstorage/issues/213
            'mysqlclient>=1.3.7, < 1.4;platform_python_implementation=="CPython" and python_version >= "3.3" and sys_platform != "win32"',
        ],
        'postgresql': [
            'relstorage[postgresql] >= 2.0rc1',
        ],
        'oracle': [
            'relstorage[oracle] >= 2.0rc1',
        ],
        "test": tests_require,
    },
    classifiers=[
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: POSIX",
        "Intended Audience :: Developers",
        "Development Status :: 4 - Beta"
    ],

)
