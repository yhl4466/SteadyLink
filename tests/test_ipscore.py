"""本地 IP 纯净度估算的评分规则。"""
import unittest

import ipscore


def base_ev(**kw):
    ev = {
        'ip': '1.2.3.4',
        'ipapi': {'status': 'success', 'hosting': False, 'proxy': False, 'mobile': False,
                  'country': '美国', 'countryCode': 'US', 'regionName': 'California',
                  'city': 'Los Angeles', 'isp': 'Comcast', 'org': 'Comcast',
                  'as': 'AS7922 Comcast', 'timezone': 'America/Los_Angeles'},
        'ipapi_is': {},
        'ptr': 'c-1-2-3-4.hsd1.ca.comcast.net',
        'dnsbl': [],
        'sources': ['ip-api', 'ipapi.is', 'DNSBL'],
        'errors': [],
    }
    ev.update(kw)
    return ev


class GradeTest(unittest.TestCase):
    def test_grade_boundaries(self):
        self.assertEqual(ipscore.grade_of(95), 'A+')
        self.assertEqual(ipscore.grade_of(91), 'A+')
        self.assertEqual(ipscore.grade_of(90), 'A')
        self.assertEqual(ipscore.grade_of(81), 'A')
        self.assertEqual(ipscore.grade_of(80), 'B')
        self.assertEqual(ipscore.grade_of(66), 'B')
        self.assertEqual(ipscore.grade_of(65), 'C')
        self.assertEqual(ipscore.grade_of(46), 'C')
        self.assertEqual(ipscore.grade_of(45), 'D')
        self.assertEqual(ipscore.grade_of(21), 'D')
        self.assertEqual(ipscore.grade_of(20), 'F')


class ScoreTest(unittest.TestCase):
    def test_clean_residential(self):
        r = ipscore.score(base_ev())
        self.assertEqual(r['score'], 100)
        self.assertEqual(r['grade'], 'A+')
        self.assertFalse(r['flags']['hosting'])

    def test_datacenter_capped_at_75(self):
        ev = base_ev(ipapi={'status': 'success', 'hosting': True, 'proxy': False,
                            'isp': 'DigitalOcean', 'org': 'DigitalOcean',
                            'as': 'AS14061 DigitalOcean'})
        r = ipscore.score(ev)
        self.assertLessEqual(r['score'], 75)
        self.assertEqual(r['ip_type'], '机房/IDC')
        self.assertTrue(any(f.get('code') == 'idc' for f in r['findings']))

    def test_proxy_penalty(self):
        ev = base_ev(ipapi={'status': 'success', 'proxy': True, 'hosting': False,
                            'isp': 'X', 'as': 'AS1 X'})
        r = ipscore.score(ev)
        self.assertEqual(r['score'], 80)
        self.assertEqual(r['grade'], 'B')

    def test_tor_heavy_penalty(self):
        ev = base_ev(ipapi_is={'is_tor': True})
        r = ipscore.score(ev)
        self.assertEqual(r['score'], 55)
        self.assertEqual(r['grade'], 'C')

    def test_spam_blacklists_stack(self):
        ev = base_ev(dnsbl=[{'zone': 'zen.spamhaus.org', 'name': 'Spamhaus ZEN'},
                            {'zone': 'bl.spamcop.net', 'name': 'SpamCop'}])
        r = ipscore.score(ev)
        self.assertEqual(r['score'], 76)

    def test_email_blacklist_only_penalized_once(self):
        ev = base_ev(dnsbl=[{'zone': 'spam.dnsbl.sorbs.net', 'name': 'SORBS-SMTP'},
                            {'zone': 'spam.dnsbl.sorbs.net', 'name': 'SORBS-SMTP'}])
        r = ipscore.score(ev)
        self.assertEqual(r['score'], 95)

    def test_abuse_flag(self):
        ev = base_ev(ipapi_is={'is_abuser': True})
        r = ipscore.score(ev)
        self.assertEqual(r['score'], 85)

    def test_asn_keywords_detect_hosting(self):
        ev = base_ev(ipapi={'status': 'success', 'hosting': False,
                            'isp': 'Hetzner Online GmbH', 'org': 'Hetzner',
                            'as': 'AS24940 Hetzner'})
        r = ipscore.score(ev)
        self.assertTrue(r['flags']['hosting'])

    def test_score_never_negative(self):
        ev = base_ev(ipapi_is={'is_tor': True, 'is_abuser': True, 'is_proxy': True},
                     dnsbl=[{'zone': z, 'name': n} for z, n in ipscore.DNSBL])
        r = ipscore.score(ev)
        self.assertGreaterEqual(r['score'], 0)
        self.assertLessEqual(r['score'], 100)
