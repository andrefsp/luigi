# Copyright (c) 2012 Spotify AB
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

from luigi.parameter import DateIntervalParameter as DI
import unittest
import datetime


class DateIntervalTest(unittest.TestCase):
    def test_date(self):
        di = DI().parse('2012-01-01')
        self.assertEqual(di.dates(), [datetime.date(2012, 1, 1)])
        self.assertEqual(di.next().dates(), [datetime.date(2012, 1, 2)])
        self.assertEqual(di.prev().dates(), [datetime.date(2011, 12, 31)])
        self.assertEqual(str(di), '2012-01-01')

    def test_month(self):
        di = DI().parse('2012-01')
        self.assertEqual(di.dates(), [datetime.date(2012, 1, 1) + datetime.timedelta(i) for i in xrange(31)])
        self.assertEqual(di.next().dates(), [datetime.date(2012, 2, 1) + datetime.timedelta(i) for i in xrange(29)])
        self.assertEqual(di.prev().dates(), [datetime.date(2011, 12, 1) + datetime.timedelta(i) for i in xrange(31)])
        self.assertEqual(str(di), '2012-01')

    def test_year(self):
        di = DI().parse('2012')
        self.assertEqual(di.dates(), [datetime.date(2012, 1, 1) + datetime.timedelta(i) for i in xrange(366)])
        self.assertEqual(di.next().dates(), [datetime.date(2013, 1, 1) + datetime.timedelta(i) for i in xrange(365)])
        self.assertEqual(di.prev().dates(), [datetime.date(2011, 1, 1) + datetime.timedelta(i) for i in xrange(365)])
        self.assertEqual(str(di), '2012')

    def test_week(self):
        # >>> datetime.date(2012, 1, 1).isocalendar()
        # (2011, 52, 7)
        # >>> datetime.date(2012, 12, 31).isocalendar()
        # (2013, 1, 1)

        di = DI().parse('2011-W52')
        self.assertEqual(di.dates(), [datetime.date(2011, 12, 26) + datetime.timedelta(i) for i in xrange(7)])
        self.assertEqual(di.next().dates(), [datetime.date(2012, 1, 2) + datetime.timedelta(i) for i in xrange(7)])
        self.assertEqual(str(di), '2011-W52')

        di = DI().parse('2013-W01')
        self.assertEqual(di.dates(), [datetime.date(2012, 12, 31) + datetime.timedelta(i) for i in xrange(7)])
        self.assertEqual(di.prev().dates(), [datetime.date(2012, 12, 24) + datetime.timedelta(i) for i in xrange(7)])
        self.assertEqual(str(di), '2013-W01')

    def test_interval(self):
        di = DI().parse('2012-01-01-2012-02-01')
        self.assertEqual(di.dates(), [datetime.date(2012, 1, 1) + datetime.timedelta(i) for i in xrange(31)])
        self.assertRaises(NotImplementedError, di.next)
        self.assertRaises(NotImplementedError, di.prev)

    def test_exception(self):
        self.assertRaises(ValueError, DI().parse, 'xyz')

    def test_comparison(self):
        a = DI().parse('2011')
        b = DI().parse('2013')
        c = DI().parse('2012')
        self.assertTrue(a < b)
        self.assertTrue(a < c)
        self.assertTrue(b > c)
        d = DI().parse('2012')
        self.assertTrue(d == c)
        self.assertEquals(d, min(c, b))
        self.assertEquals(3, len(set([a, b, c, d])))

    def test_comparison_different_types(self):
        x = DI().parse('2012')
        y = DI().parse('2012-01-01-2013-01-01')
        self.assertRaises(TypeError, lambda: x == y)
