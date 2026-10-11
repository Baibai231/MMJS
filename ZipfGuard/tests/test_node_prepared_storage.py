"""Exercise actual node commit/recovery storage using an isolated SQLite database."""
import asyncio
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from privacy.service.node import Node
from services.common.store import Store
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

class StopAtCheckpoint(Exception): pass

class StorageTests(unittest.IsolatedAsyncioTestCase):
    def make_node(self, directory):
        node=Node.__new__(Node)
        node.pid=0
        node.cfg={'nodes':[], 'ca':True, 'node_keys':{}, 'site':'test'}
        node.directory=Path(directory)
        node.store=Store(node.directory/'state.sqlite')
        node.ledger_path=node.directory/'highwater.json'
        node.key=Ed25519PrivateKey.generate()
        node.state={'epoch':0,'policy_version':1,'epsilon_spent':4,'task_used':82,'accounts':{'a':{'shares':[123]}},'snapshots':{}}
        node.store.put('state','current',node.state)
        node.store.put('budget','epsilon',4)
        node.active=False
        return node

    async def worker_until(self, node, phase, changed):
        context={'nonce':'test','site':'test','account':'a','op':'change','policy_version':1}
        node.store.put('pending','test',{'context':context,'fields':{},'binding':'private','created':0})
        state=copy.deepcopy(node.state)
        if changed:
            state['epoch']=1;state['accounts']['a']['shares']=[456]
        node.execute=AsyncMock(return_value=(state,'done',{},changed))
        async def transfer(value): return [value,value,value]
        def fault(at, nonce):
            if at==phase: raise StopAtCheckpoint()
        node.local_fault=fault
        with patch.object(node,'recover',AsyncMock()), patch('privacy.service.node.mpc.start',AsyncMock()), patch('privacy.service.node.mpc.transfer',transfer), patch('privacy.service.node.receipt_quorum'):
            with self.assertRaises(StopAtCheckpoint): await node.worker()

    async def test_precommit_mutating_shares_remain_recoverable(self):
        for phase in ('after_prepare','after_decision'):
            with tempfile.TemporaryDirectory() as directory:
                node=self.make_node(directory)
                await self.worker_until(node,phase,True)
                self.assertEqual(node.store.get('prepared','test')['state']['accounts']['a']['shares'],[456])
                self.assertEqual(node.store.get('state','current')['epoch'],0)
                if phase=='after_decision':
                    with patch('privacy.service.node.receipt_quorum'): await node.recover()
                    self.assertEqual(node.state['epoch'],1)
                    self.assertEqual(node.state['accounts']['a']['shares'],[456])
                    self.assertIn('test',node.store.list('completed'))
                    self.assertNotIn('state',node.store.get('prepared','test'))

    async def test_committed_state_and_receipts_survive_pruning(self):
        with tempfile.TemporaryDirectory() as directory:
            node=self.make_node(directory)
            await self.worker_until(node,'after_commit',True)
            self.assertEqual(node.store.get('state','current')['accounts']['a']['shares'],[456])
            self.assertNotIn('state',node.store.get('prepared','test'))
            before={n:node.store.list(n) for n in ('completed','decisions','budget')}
            with patch('privacy.service.node.receipt_quorum'): await node.recover()
            self.assertEqual(before,{n:node.store.list(n) for n in before})
            self.assertEqual(node.state['epoch'],1)
            self.assertEqual(node.state['epsilon_spent'],4)
            self.assertEqual(node.state['task_used'],82)

    async def test_nonmutating_prepare_stores_no_full_state(self):
        with tempfile.TemporaryDirectory() as directory:
            node=self.make_node(directory)
            await self.worker_until(node,'after_decision',False)
            self.assertNotIn('state',node.store.get('prepared','test'))
            with patch('privacy.service.node.receipt_quorum'): await node.recover()
            self.assertEqual(node.state['epoch'],0)
            self.assertIn('test',node.store.list('completed'))

    async def test_stale_backup_missing_shares_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            node=self.make_node(directory)
            node.store.put('prepared','test',{'receipt':{},'committed_epoch':1})
            node.store.put('decisions','test',{'epoch':1,'events':[]})
            with patch('privacy.service.node.receipt_quorum'):
                with self.assertRaisesRegex(ValueError,'missing local prepared shares'): await node.recover()
            self.assertEqual(node.store.get('state','current')['epoch'],0)

if __name__=='__main__': unittest.main()
