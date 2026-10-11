"""Export a compact paper table from the actual protocol run, and audit evidence."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'privacy_deps'))
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from tools.run_privacy_protocol_study import verify_receipts


def load(path):
    return json.loads(path.read_text('utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = ROOT / 'reports/privacy_protocol_20261011'
    summary, manifest = load(output/'summary.json'), load(output/'manifest.json')
    cases = load(output/'case_results.json')
    assert load(output/'status.json')['state'] == 'complete'
    assert summary['status'] == 'passed' and all(row['passed'] for row in cases)
    assert summary['case_count'] == len(cases)
    for filename, fingerprint in manifest['implementation_sha256'].items():
        assert digest(ROOT/filename) == fingerprint, f'Code changed since experiment: {filename}'
    assert digest(ROOT/'configs/privacy_protocol_minimal.json') == manifest['config_sha256']
    parties = [load(output/f'private/party_{i}/party_result.json') for i in range(3)]
    keylist = [Ed25519PublicKey.from_public_bytes(bytes.fromhex(value)) for value in manifest['receipt_public_keys']]
    transaction_cases = cases[:manifest['synthetic_transactions']]
    expectations = [{'label': row['case'], 'outcome': row['outcome']} for row in transaction_cases]
    verified = verify_receipts(parties, expectations, keylist)
    assert verified == cases
    assert all(row['passed'] for row in summary['secret_profile_diagnostics'])
    assert summary['privacy_budget_spent'] == 1 and summary['profile_releases'] == 1
    assert summary['remediation_tasks_completed'] == summary['remediation_tasks_assigned'] == 1
    assert parties[0]['public_profiles'][0] == parties[0]['public_profiles'][1]
    # No credential state or local binding/signing keys are exported below.
    groups = [
        ('初始化注册', [f'bootstrap_{i}' for i in range(8)], '8个合成账户成功建立秘密凭据'),
        ('输入与同口令绑定', ['alternate_auth_input', 'forged_structure_report', 'inconsistent_client_shares', 'consistent_nonbinary_shares', 'noncontiguous_padding'], '拒绝独立凭据字段、伪造结构和畸形秘密输入'),
        ('隐藏联合规则', ['refresh_hidden_rule', 'hidden_exact_rejection', 'structure_rejection', 'valid_registration'], '不打开秘密字符串即可刷新规则并区分拒绝与接受'),
        ('认证及在线查询限制', ['wrong_login', 'legacy_login_ignores_new_policy', 'old_password_revoked', 'new_password_works', 'login_quota_third', 'login_quota_fourth', 'login_quota_fifth'], '新规则不锁旧登录，改密后旧值失效，第5次查询拒绝'),
        ('提交与状态恢复', ['wrong_old_password', 'abort_without_commit', 'successful_password_change', 'exact_change_replay', 'conflicting_nonce', 'stale_account_version', 'stale_policy', 'checkpoint_reload', 'final_checkpoint_reload'], '认证、统计同次更新；重放与冲突不重复写入；份额从磁盘恢复'),
        ('存量整改闭环', ['private_remediation_eligibility', 'forged_eligibility_without_auth', 'unrelated_account_eligibility', 'unauthenticated_task_denied', 'authenticated_task_assignment', 'task_assignment_idempotent', 'task_count_budget_exhausted', 'task_completed_by_private_change'], '认证后判资格，人数预算1，领取幂等，秘密改密完成任务'),
        ('受保护画像与发布预算', ['profile_before', 'same_snapshot_cached', 'second_snapshot_budget_exhausted'], '三方秘密噪声；发布1次、同图缓存1次、下一快照拒绝'),
        ('三方签名回执', ['tampered_receipt_rejected', 'missing_party_receipt_fail_closed', 'duplicated_signer_fail_closed', 'wrong_receipt_context_fail_closed'], '篡改、缺签、重复签名者和错误事务上下文均拒绝'),
    ]
    indexed = {row['case']: row for row in cases}
    listed = [name for _, names, _ in groups for name in names]
    assert len(listed) == len(set(listed)) and set(listed) == set(indexed)
    rows = [{'环节': title, '通过数': sum(indexed[name]['passed'] for name in names), '检查数': len(names),
             '结果': description, 'case_ids': ';'.join(names)} for title, names, description in groups]
    with (output/'table_protocol_cases.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    phases = load(output/'phase_timings.json')
    op_times = {}
    for phase in phases:
        op_times.setdefault(phase['op'], []).append(phase['seconds'])
    timing_rows = [{'operation': op, 'measured_requests': len(values), 'median_ms': statistics.median(values)*1000,
                    'minimum_ms': min(values)*1000, 'maximum_ms': max(values)*1000}
                   for op, values in op_times.items()]
    with (output/'table_protocol_timings.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(timing_rows[0]))
        writer.writeheader()
        writer.writerows(timing_rows)
    lines = ['# 全流程协议实验的论文表', '', '由真实协议产物自动生成；不是人工填写通过标志。', '',
             '| 工具环节 | 通过／检查 | 实测结果 |', '| --- | --- | --- |']
    lines.extend(f"| {row['环节']} | {row['通过数']}／{row['检查数']} | {row['结果']} |" for row in rows)
    lines += ['', f"共{len(cases)}项，其中{manifest['synthetic_transactions']}次协议事务与4项回执负例；另外{len(summary['secret_profile_diagnostics'])}项内部一致性诊断通过。", '',
              f"墙钟耗时{summary['wall_seconds']:.3f}秒；三方累计发送{sum(row['mpc_bytes_sent'] for row in summary['performance']):,}字节。", '',
              '条件：同机回环TLS、3方阈值1、MPyC被动安全、合成小样本。不是跨组织部署、恶意服务器安全或网站吞吐量测试。', '',
              '输入为固定16字节ASCII；5个结构槽、每槽前3个匿名频次。真实密钥和份额留在private目录。', '',
              '当前测试控制器一次上传整个合成事务序列：验证协议功能与秘密状态更新，尚未实现每次提交返回回执后再交互的HTTP/浏览器服务。', '',
              '份额、凭据及预算从真实磁盘文件恢复，但没有杀进程重启，也未验证跨节点崩溃恢复。', '',
              '论文主要引用本表；逐操作计时CSV只作补充材料。早退的格式/限速/版本错误不在逐操作计时样本中。', '']
    (output/'table_protocol_cases.md').write_text('\n'.join(lines), encoding='utf-8')
    files = ['summary.json', 'case_results.json', 'manifest.json', 'status.json', 'public_profile.json',
             'phase_timings.json', 'table_protocol_cases.csv', 'table_protocol_timings.csv', 'table_protocol_cases.md']
    evidence = {'status': 'passed', 'checks': ['frozen implementation/config hashes', 'all 44 transaction signatures independently reverified',
                'four receipt rejection checks rerun on actual messages', 'all 48 case IDs represented exactly once in table',
                '18 secret-state diagnostics passed', 'cached profile equality', 'release and remediation budget bounds'],
                'algorithm_results_separate': True, 'protocol_scope': summary['scope'],
                'public_artifact_sha256': {name: digest(output/name) for name in files},
                'exporter_sha256': digest(Path(__file__))}
    (output/'evidence_audit.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'audit': 'passed', 'cases': len(cases), 'transactions': manifest['synthetic_transactions'],
                      'diagnostics': len(summary['secret_profile_diagnostics']), 'paper_table': str(output/'table_protocol_cases.md')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
