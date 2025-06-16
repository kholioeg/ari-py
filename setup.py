#!/usr/bin/env python

#
# Copyright (c) 2013, Digium, Inc.
#

import os

from setuptools import setup

setup(
    name="ari",
    version="0.2.0",
    license="BSD 3-Clause License",
    description="Library for accessing the Asterisk REST Interface",
    long_description=open(os.path.join(os.path.dirname(__file__),
                                       "README.rst")).read(),
    author="Digium, Inc.",
    author_email="dlee@digium.com",
    url="https://github.com/asterisk/asterisk_rest_libraries",
    packages=["ari"],
    classifiers=[
        "Development Status :: 1 - Planning",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires='>=3.11',
    tests_require=[
        "coverage==7.9.1",
        "responses==0.25.7",
    ],
    install_requires=[
        "bravado-core==5.17.1",
        "requests==2.31.0",
    ],
)
