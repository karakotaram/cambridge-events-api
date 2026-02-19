#!/bin/bash
# Install lightfm with patched setup.py to fix setuptools compatibility
# See: https://github.com/lyst/lightfm/issues/707
set -e

pip install cython numpy scipy

pip download lightfm==1.17 --no-binary :all: --no-deps -d /tmp/lfm
cd /tmp/lfm
tar xzf lightfm-1.17.tar.gz
cd lightfm-1.17

# Patch setup.py: replace 'import builtins' + 'builtins.__LIGHTFM_SETUP__'
# with __import__('builtins') which works correctly inside setuptools exec()
sed -i 's/^import builtins$//' setup.py
sed -i "s/builtins\.__LIGHTFM_SETUP__/__import__('builtins').__LIGHTFM_SETUP__/g" setup.py

pip install --no-build-isolation .
