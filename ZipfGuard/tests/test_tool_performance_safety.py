"""Lossless storage compatibility and adversarial authentication regression checks."""
import json, sqlite3, tempfile, unittest
from pathlib import Path
from services.common.store import Store, encode_value, decode_value, COMPRESSED_PREFIX
from privacy.service.compute import Computer
class PerformanceSafetyTests(unittest.TestCase):
    def test_large_values_preserve_shares_receipts_unicode_and_budgets(self):
        value={'shares':[str((-1)**i*(18889465931478580854700-i)) for i in range(512)],'epsilon':.5,'receipt':{'签名':'ab'*64},'history':[{'version':i,'enabled':True} for i in range(100)]}
        encoded=encode_value(value)
        self.assertTrue(encoded.startswith(COMPRESSED_PREFIX))
        self.assertLess(len(encoded),len(json.dumps(value,ensure_ascii=False)))
        self.assertEqual(decode_value(encoded),value)
    def test_legacy_json_and_marker_looking_strings(self):
        for value in [None,17,'~zgz1:not-compressed',{'legacy':[1,2,3]}]:
            self.assertEqual(decode_value(json.dumps(value)),value)
            self.assertEqual(decode_value(encode_value(value)),value)
    def test_mixed_database_round_trip_and_transaction_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'state.sqlite');large={'shares':['12345678901234567890']*512}
            store.put('state','new',large)
            with store.transaction() as conn:
                store.execute(conn,'INSERT INTO objects VALUES(?,?,?,?)',('state','legacy',json.dumps({'v':1}),0))
            self.assertEqual(store.list('state'),{'new':large,'legacy':{'v':1}})
            with self.assertRaises(RuntimeError):
                with store.transaction() as conn:
                    store.put('state','new',{'bad':True},conn)
                    store.append('audit',large,conn)
                    raise RuntimeError('simulated interrupted transaction')
            self.assertEqual(store.get('state','new'),large);self.assertEqual(store.events(),[])
            store.append('audit',large);self.assertEqual(store.events()[0]['data'],large)
    def test_damaged_compressed_value_is_not_accepted_as_missing(self):
        with self.assertRaises(Exception):decode_value(COMPRESSED_PREFIX+'invalid')
    def test_authentication_byte_packing_does_not_alias_non_ascii_inputs(self):
        c=Computer.__new__(Computer);c.equal=lambda a,b:a==b
        self.assertFalse(c.equal_bytes([128,0,0],[0,1,0]))
        for first in range(256):
            self.assertTrue(c.equal_bytes([first,255,0],[first,255,0]))
            self.assertFalse(c.equal_bytes([first,255,0],[first,254,0]))
if __name__=='__main__':unittest.main()
