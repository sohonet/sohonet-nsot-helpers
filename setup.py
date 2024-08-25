"""setup.py file."""

import uuid

from setuptools import setup, find_packages

__author__ = "Johan van den Dorpe <johan.vandendorpe@sohonet.com>"

with open("requirements.txt") as f:
    install_requires = f.read().strip().splitlines()

setup(
    name="sohonet-nsot-helpers",
    version="0.1.17",
    packages=find_packages(),
    author="Johan van den Dorpe",
    author_email="johan.vandendorpe@sohonet.com",
    description="Napalm and other shared helpers for Netbox importers and Nautobot SSoT jobs",
    classifiers=[
        "Topic :: Utilities",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS",
    ],
    url="https://github.com/sohonet/sohonet-nsot-helpers",
    include_package_data=True,
    install_requires=install_requires,
)
