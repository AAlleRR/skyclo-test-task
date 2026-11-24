import unittest
from datetime import date
from pathlib import Path
from report import build, load, money, fmt


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.data = load(Path(__file__).resolve().parents[1] / 'data')

    def report(self, when):
        w, c = build(*self.data, date.fromisoformat(when))
        return {r['work_id']: r for r in w}, {r['contract_id']: r for r in c}

    def test_reference(self):
        w, c = self.report('2026-11-10')
        self.assertEqual(len(w), 9)
        self.assertEqual(len(c), 2)
        for cid, net, pool, remaining, balance in [('D-1',315000,15000,200000,185000), ('D-2',75000,25000,40000,15000)]:
            r = c[cid]
            self.assertEqual([r[k] for k in ('net_paid','unallocated_advance','remaining','net_balance','due_now')], [net*100,pool*100,remaining*100,balance*100,0])
        self.assertEqual(w['W-14']['advance_allocated'], 8500000)
        self.assertEqual(w['W-13']['status'], 'cancelled')
        self.assertEqual(w['W-23']['status'], 'planned')
        self.assertEqual(c['D-1']['queued'], 15000000)

    def test_refund_boundary(self):
        self.assertEqual(self.report('2026-09-09')[1]['D-1']['net_paid'],35000000)
        self.assertEqual(self.report('2026-09-10')[1]['D-1']['net_paid'],30000000)

    def test_future_acceptance_and_payment(self):
        self.assertEqual(self.report('2026-11-11')[0]['W-23']['due_now'],1500000)
        w,c = self.report('2026-11-12')
        self.assertEqual(w['W-23']['remaining'],0)
        self.assertEqual(c['D-2']['unallocated_advance'],500000)
        self.assertEqual(c['D-2']['net_balance'],-500000)

    def test_cancel_boundary_and_signing(self):
        self.assertEqual(self.report('2026-08-24'), ({}, {}))
        self.assertEqual(self.report('2026-10-11')[0]['W-13']['status'],'planned')
        self.assertEqual(self.report('2026-10-12')[0]['W-13']['remaining'],0)

    def test_direct_excess_not_reassigned(self):
        self.data[2]['P-3']['amount'] = 12000000
        w,c = self.report('2026-11-10')
        self.assertEqual(w['W-14']['excess'],2000000)
        self.assertEqual(w['W-14']['advance_allocated'],0)
        self.assertEqual(c['D-1']['unallocated_advance'],10000000)

    def test_direct_cancelled_retained(self):
        self.data[2]['P-3']['work_id'] = 'W-13'
        w,c = self.report('2026-11-10')
        self.assertEqual(w['W-13']['excess'],1500000)
        self.assertEqual(w['W-13']['remaining'],0)
        self.assertEqual(c['D-1']['allocated_paid'],31500000)

    def test_money(self):
        self.assertEqual(money('0.01')+money('0.10'),11)
        self.assertEqual(fmt(-11),'-0.11')
        for value in ('1.001','NaN','-1','1e2',''):
            with self.assertRaises(ValueError):
                money(value)


if __name__ == '__main__':
    unittest.main()
