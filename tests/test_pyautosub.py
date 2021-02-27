#!/usr/bin/env python
"""Tests for `pyautosub` package."""

import unittest

from pyautosub import AutoSub
from os import environ


class TestPyAutoSub(unittest.TestCase):
    """Tests for `pyautosub` package."""
    def setUp(self):
        assert bool(AutoSub)
        assert environ.get("OS_U") and environ.get("OS_P")

    def tearDown(self):
        """Tear down test fixtures, if any."""

    def test_1(self):
        assert True
