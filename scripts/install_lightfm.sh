#!/bin/bash
# Install lightfm with patched setup.py to fix setuptools compatibility
# See: https://github.com/lyst/lightfm/issues/707
set -e

pip install wheel cython numpy scipy

# Download tarball directly (pip download triggers the broken build)
curl -sL "https://files.pythonhosted.org/packages/1f/96/5ec230f5c27811534af0faaa8525f11c1000ee1c24c8a82c0546d0724aea/lightfm-1.17.tar.gz" -o /tmp/lightfm-1.17.tar.gz
cd /tmp
tar xzf lightfm-1.17.tar.gz
cd lightfm-1.17

# Patch setup.py: setuptools exec()s setup.py with locals() where
# __builtins__ is a dict, not the module. Replace with __import__.
# Original line: __builtins__.__LIGHTFM_SETUP__ = True
sed -i "s/__builtins__\.__LIGHTFM_SETUP__/__import__('builtins').__LIGHTFM_SETUP__/g" setup.py

echo "Patched setup.py line 11:"
sed -n '11p' setup.py

pip install --no-build-isolation .
