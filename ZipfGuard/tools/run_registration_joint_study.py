"""Resumable full study. All parameter freezing precedes final-test execution."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.registration_joint_data import partition, load_private
from experiments.registration_joint_pipeline import (run_job, write_json, ARMS, VERSION)
_RUN_LOCKS = []


def log(message):
    print(time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + message, flush=True)


def task_key(task):
    return '/'.join(str(task.get(k, 'main')) for k in ['variant', 'phase', 'scenario', 'seed'])


def run_stage(tasks, cfg, root, workers, status):
    todo = []
    for task in tasks:
        path = root/'runs'/task.get('variant','main')/task['phase']/task['scenario']/str(task['seed'])/'summary.json'
        if path.exists():
            status['completed'][task_key(task)] = str(path)
        else:
            todo.append(task)
    write_json(root/'status.json', status)
    if not todo:
        return
    status['stage'] = todo[0]['phase']
    status['queued'] = [task_key(t) for t in todo]
    write_json(root/'status.json', status)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_job, {'cfg': cfg, 'task': task, 'root': str(root)}): task for task in todo}
        for future in as_completed(futures):
            task = futures[future]
            result = future.result()
            status['completed'][task_key(task)] = result['summary_path']
            status['queued'].remove(task_key(task))
            status['updated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            write_json(root/'status.json', status)
            log(f'Completed {task_key(task)} ({len(status["completed"])} tasks persisted)')


def collect(root, variant, phase, scenario, seeds):
    return [json.loads((root/'runs'/variant/phase/scenario/str(seed)/'summary.json').read_text('utf-8')) for seed in seeds]


def mean_risk(rows, budget):
    return sum(r['A1_MC']['rates'][str(budget)] for r in rows)/len(rows)


def freeze_comparators(root, cfg):
    for scenario in cfg['scenarios']:
        path = root/'frozen_parameters'/f'{scenario}.json'
        if path.exists():
            continue
        jobs = collect(root, 'main', 'calibration', scenario, cfg['seeds'])
        ranking = []
        for arm in jobs[0]['arms']:
            rows = [job['arms'][arm] for job in jobs]
            eligible = all(not r['eligibility_failures'] and r['A1_MC'] for r in rows)
            ranking.append({'arm': arm, 'eligible_all_seeds': eligible,
                            'mean_A1': mean_risk(rows, cfg['objective_budget']) if all(r['A1_MC'] for r in rows) else None,
                            'mean_extra': sum(r['cost']['mean_extra_submissions'] for r in rows)/len(rows),
                            'failures': [r['eligibility_failures'] for r in rows]})
        fixed = [r for r in ranking if r['arm'].startswith('cal_fixed_') and r['eligible_all_seeds']]
        gate = [r for r in ranking if r['arm'].startswith('cal_gate_') and r['eligible_all_seeds']]
        order = lambda r: (r['mean_A1'], r['mean_extra'], r['arm'])
        best_fixed = min(fixed, key=order) if fixed else None
        best_gate = min(gate, key=order) if gate else None
        pair = list(map(int, best_fixed['arm'].split('_')[-2:])) if best_fixed else [0,0]
        threshold = int(best_gate['arm'].split('_')[-1]) if best_gate else 0
        write_json(path, {'fixed_pair': pair, 'strength_threshold': threshold,
                         'status': 'validation_frozen' if fixed else 'no_cost_eligible_fixed_common_base_fallback',
                         'strength_status': 'validation_frozen' if gate else 'no_cost_eligible_gate_common_base_fallback',
                         'source_phase': 'calibration on validation role only', 'ranking': ranking,
                         'test_data_used': False, 'frozen_at': time.strftime('%Y-%m-%d %H:%M:%S')})


def freeze_recommendation(root, cfg):
    for scenario in cfg['scenarios']:
        path = root/'frozen_recommendations'/f'{scenario}.json'
        if path.exists():
            continue
        jobs = collect(root, 'main', 'validation', scenario, cfg['seeds'])
        eligible = {}
        for arm in cfg.get('arms', ARMS):
            rows = [j['arms'][arm] for j in jobs]
            if all(not r['eligibility_failures'] and r['A1_MC'] for r in rows):
                eligible[arm] = rows
        recommendation, reason = 'base', 'no_cost_eligible_method'
        comparisons = []
        if eligible:
            best_mean = min(mean_risk(rows, cfg['objective_budget']) for rows in eligible.values())
            close = [arm for arm,rows in eligible.items() if mean_risk(rows,cfg['objective_budget']) <= best_mean+cfg['min_gain']]
            recommendation = min(close, key=lambda arm: (
                 sum(r['cost']['mean_extra_submissions'] for r in eligible[arm]), cfg.get('arms', ARMS).index(arm)))
            reason = 'lowest_validation_mean_risk_within_cost_constraints'
            if 'fixed' in eligible and 'joint' in eligible:
                differences = [f['A1_MC']['rates'][str(cfg['objective_budget'])]-d['A1_MC']['rates'][str(cfg['objective_budget'])]
                               for f, d in zip(eligible['fixed'], eligible['joint'])]
                average = sum(differences)/len(differences)
                positive = sum(d > 0 for d in differences)
                comparisons = [{'joint_over_fixed_mean_pp': 100*average, 'same_direction_seeds': positive,
                                'seeds': len(differences), 'practical_gate_pp': 100*cfg['min_gain']}]
                if recommendation == 'joint' and (average < cfg['min_gain'] or positive < math_ceil(.8*len(differences))):
                    recommendation, reason = 'fixed', 'joint_failed_predeclared_practical_gain_or_direction_gate'
        write_json(path, {'recommended_arm': recommendation, 'reason': reason,
                         'eligible_arms': list(eligible), 'comparisons': comparisons,
                         'source_phase': 'validation; final test unopened', 'test_data_used': False,
                         'frozen_at': time.strftime('%Y-%m-%d %H:%M:%S')})


def math_ceil(value):
    import math
    return math.ceil(value)


def export(root, cfg):
    import csv
    rows = []
    for path in sorted((root/'runs').rglob('result.json')):
        result = json.loads(path.read_text('utf-8'))
        variant, phase, scenario, seed, arm = path.relative_to(root/'runs').parts[:5]
        mc = result.get('A1_MC')
        rows.append({'variant': variant, 'phase': phase, 'scenario': scenario, 'seed': seed, 'arm': arm,
                     'status': result['status'], 'objective_budget':cfg['objective_budget'],
                     'A1_estimated_at_objective': mc['rates'].get(str(cfg['objective_budget'])) if mc else None,
                     'cost_is_lower_bound': result.get('cost_is_lower_bound',False),
                     'outside_fraction': mc['outside_model_support_fraction'] if mc else None,
                     'rank_interval_crossing_fraction': mc['rank_interval_crossing_fraction'].get(str(cfg['objective_budget'])) if mc else None,
                     'modification_rate': result['cost']['modification_rate'],
                     'mean_extra_submissions': result['cost']['mean_extra_submissions'],
                     'incremental_modification_rate': result['cost'].get('incremental_modification_rate'),
                     'incremental_mean_extra_submissions': result['cost'].get('incremental_mean_extra_submissions'),
                     'pending_users': result['cost']['pending_users'],
                     'eligibility_failures': '|'.join(result['eligibility_failures']),
                     'attack_failures': json.dumps(result.get('attack_failures',{}), ensure_ascii=False)})
    if rows:
        with (root/'results.csv').open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = ['# 注册阶段联合动态策略：实际运行记录', '',
             '本报告来自独立新实验；蒙特卡洛估计与实际尝试前缀分别记录。未运行的结果不作推断。', '',
             f'已生成 {len(rows)} 条方法轨迹。运行配置、角色划分摘要、逐批候选审计及封存参数保存在同一目录。', '',
             '带有 screened 状态的验证参数只进行了成本下界排除，没有执行该参数的完整轨迹；其成本列是公共基础规则下界，不能当作实测值。', '',
             '| 场景 | 封存推荐 | 验证选择理由 |', '| --- | --- | --- |']
    for scenario in cfg['scenarios']:
        path = root/'frozen_recommendations'/f'{scenario}.json'
        if path.exists():
            rec = json.loads(path.read_text('utf-8'))
            lines.append(f'| {scenario} | {rec["recommended_arm"]} | {rec["reason"]} |')
    lines += ['', '详细数值见 results.csv。成本不合格、攻击失败、模型外状态及未决排名均保留；五种子仅为同源模拟重复。', '']
    lines += ['| 终测场景 | 固定组 A1 (%) | 联合组 A1 (%) | 固定减联合 (百分点) | 联合组修改率 (%) | 联合组额外提交 |',
              '| --- | --- | --- | --- | --- | --- |']
    for scenario in cfg['scenarios']:
        group = [r for r in rows if r['variant']=='main' and r['phase']=='test' and r['scenario']==scenario]
        fixed = [r for r in group if r['arm']=='fixed' and r['A1_estimated_at_objective'] is not None]
        joint = [r for r in group if r['arm']=='joint' and r['A1_estimated_at_objective'] is not None]
        if len(fixed)==len(cfg['seeds']) and len(joint)==len(cfg['seeds']):
            avg = lambda rr,key: sum(r[key] for r in rr)/len(rr)
            f, j = avg(fixed,'A1_estimated_at_objective'), avg(joint,'A1_estimated_at_objective')
            lines.append(f'| {scenario} | {100*f:.3f} | {100*j:.3f} | {100*(f-j):.3f} | {100*avg(joint,"modification_rate"):.2f} | {avg(joint,"mean_extra_submissions"):.3f} |')
    lines += ['', '上述主指标包含模型外用户但不把模型外状态视为已证实安全；应结合覆盖与实际 OMEN/PCFG 前缀审查。', '']
    (root/'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        for scenario in cfg['scenarios']:
            selected = [r for r in rows if r['variant'] == 'main' and r['phase'] == 'test' and r['scenario'] == scenario and r['A1_estimated_at_objective'] is not None]
            if not selected:
                continue
            fig, ax = plt.subplots(figsize=(8,5))
            for arm in cfg.get('arms', ARMS):
                group = [r for r in selected if r['arm'] == arm]
                if group:
                    ax.scatter([r['mean_extra_submissions'] for r in group], [100*r['A1_estimated_at_objective'] for r in group], label=arm)
            ax.set(xlabel='Mean extra submissions', ylabel='Estimated A1 hit rate (%)', title=scenario)
            ax.legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(root/f'cost_risk_{scenario}.png', dpi=180)
            plt.close(fig)
            jobs = []
            for seed in cfg['seeds']:
                p = root/'runs/main/test'/scenario/str(seed)/'summary.json'
                if p.exists():
                    jobs.append(json.loads(p.read_text('utf-8')))
            fig, axes = plt.subplots(1,2,figsize=(12,5))
            for arm in cfg.get('arms',ARMS):
                results = [job['arms'][arm] for job in jobs if job['arms'][arm].get('A1_MC')]
                if not results:
                    continue
                axes[0].plot(cfg['budgets'], [100*sum(r['A1_MC']['rates'][str(b)] for r in results)/len(results) for b in cfg['budgets']], marker='o',label=arm)
                for model in ['pcfg','omen']:
                    actual = [r['actual_attacks'][model] for r in results if model in r.get('actual_attacks',{})]
                    if actual:
                        axes[1].plot(cfg['budgets'],[100*sum(a['points'][i]['rate_per_initial_user'] for a in actual)/len(actual) for i,_ in enumerate(cfg['budgets'])],marker='o',label=f'{arm}/{model}')
            for ax in axes:
                ax.set_xscale('log')
                ax.set_ylabel('Hit rate per initial user (%)')
                ax.legend(fontsize=6)
                ax.grid(alpha=.2)
            axes[0].set_title('Estimated distinct-string PCFG A1 rank')
            axes[0].set_xlabel('Estimated distinct strings')
            axes[1].set_title('Observed prefixes; duplicates charged')
            axes[1].set_xlabel('Actual attempts per model')
            fig.tight_layout()
            fig.savefig(root/f'attack_curves_{scenario}.png',dpi=180)
            fig.savefig(root/f'attack_curves_{scenario}.pdf')
            plt.close(fig)
            fig, axes = plt.subplots(2,1,figsize=(9,6),sharex=True)
            for job in jobs:
                timeline = job['arms'].get('joint',{}).get('timeline',[])
                if timeline:
                    xs = range(1,len(timeline)+1)
                    axes[0].step(xs,[r['k'] for r in timeline],where='post',label=f'seed {job["task"]["seed"]}')
                    axes[1].step(xs,[r['j'] for r in timeline],where='post')
            axes[0].set_ylabel('Complete-password block size k')
            axes[1].set_ylabel('Structure block size j')
            axes[1].set_xlabel('Registration cohort')
            axes[0].legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(root/f'joint_timeline_{scenario}.png',dpi=180)
            plt.close(fig)
    except ImportError:
        log('matplotlib unavailable; CSV and text report retained')


def study(cfg, root, workers):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = (root/'runner.lock').open('a+b')
    lock.seek(0)
    if os.name == 'nt':
        import msvcrt
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            lock.close()
            raise RuntimeError('This study directory is already locked by a running process')
    else:
        import fcntl
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    _RUN_LOCKS.append(lock)
    # Resume fails closed when implementation changes during a recorded study.
    files = [Path(__file__), ROOT/'policy/registration_joint.py', ROOT/'experiments/registration_joint_data.py', ROOT/'experiments/registration_joint_pipeline.py',
             ROOT/'ai/pcfg_monte_carlo.py', ROOT/'ai/pcfg_adapter.py', ROOT/'ai/dual_attack.py']
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    import importlib.metadata
    manifest = {'version': VERSION, 'config': cfg, 'implementation_sha256': hashes, 'python': sys.version,
                'dependencies':{name:importlib.metadata.version(name) for name in ['numpy','matplotlib']}}
    path = root/'manifest.json'
    if path.exists():
        old = json.loads(path.read_text('utf-8'))
        if old != manifest:
            raise ValueError('Manifest changed; preserve this run and start a new output directory')
    else:
        write_json(path, manifest)
    partition_data = partition(ROOT.parent/cfg['source'], cfg, root/'private/partition.pkl.gz', log)
    write_json(root/'data_manifest.json', partition_data['metadata'])
    preflight_path = root/'training_base_feasibility.json'
    if not preflight_path.exists():
        from collections import Counter
        from policy.registration_joint import JointRule, Preferences
        from experiments.registration_joint_pipeline import cost_failures
        words = [w for _,w in partition_data['roles']['train']]
        common = frozenset(w for w,_ in sorted(Counter(words).items(),key=lambda p:(-p[1],p[0]))[:cfg['common_size']])
        base = JointRule(cfg['minimum'],common)
        response = Preferences(words,cfg['minimum'],cfg['weights'],cfg['response_mode'],cfg['candidate_limit'])
        simulated = response.simulate(partition_data['roles']['train'],base,cfg['split_seed'])
        from experiments.registration_joint_pipeline import attach_base_cost
        attach_base_cost(simulated,simulated)
        write_json(preflight_path, {'data_role':'training only; diagnostic, not validation or final outcome',
                   'summary':simulated['summary'],'constraint_failures':cost_failures(simulated['summary'],cfg)})
        log(f'Training-only base cost diagnostic: modification {100*simulated["summary"]["modification_rate"]:.2f}%, mean extra {simulated["summary"]["mean_extra_submissions"]:.3f}')
    del partition_data
    status_path = root/'status.json'
    status = json.loads(status_path.read_text('utf-8')) if status_path.exists() else {'completed': {}}
    status.update(state='running', pid=os.getpid(), started_at=status.get('started_at', time.strftime('%Y-%m-%d %H:%M:%S')))
    tasks = lambda phase, variant='main': [{'phase': phase, 'variant': variant, 'scenario': scenario, 'seed': seed}
                                        for scenario in cfg['scenarios'] for seed in cfg['seeds']]
    try:
        run_stage(tasks('calibration'), cfg, root, workers, status)
        freeze_comparators(root, cfg)
        export(root,cfg)
        run_stage(tasks('validation'), cfg, root, workers, status)
        freeze_recommendation(root, cfg)
        export(root,cfg)
        run_stage(tasks('test'), cfg, root, workers, status)
        export(root,cfg)
        for variant, changes in cfg.get('sensitivities', {}).items():
            varied = copy.deepcopy(cfg)
            varied.update(changes)
            # All frozen comparators and recommendations remain unchanged.
            run_stage(tasks('test', variant), varied, root, workers, status)
        status.update(state='complete', stage='complete', updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
    except BaseException as exc:
        status.update(state='interrupted_or_failed', error=f'{type(exc).__name__}: {exc}', updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
        raise
    finally:
        write_json(status_path, status)
        export(root, cfg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--launch', action='store_true', help='Start a durable hidden local process')
    parser.add_argument('--suite', action='store_true', help='Also run independently validated incremental-cost and length-15 studies')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if args.launch:
        output.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, '-u', str(Path(__file__).resolve()), '--config', str(Path(args.config).resolve()),
                   '--output', str(output), '--workers', str(args.workers)]
        if args.suite:
            command.append('--suite')
        with (output/'runner.log').open('ab') as logfile:
            flags = (getattr(subprocess,'CREATE_NO_WINDOW',0) | getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
            process = subprocess.Popen(command, cwd=ROOT.parent, stdout=logfile, stderr=logfile,
                                       stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
        write_json(output/'launch.json', {'pid': process.pid, 'command': command, 'time': time.strftime('%Y-%m-%d %H:%M:%S')})
        print(json.dumps({'pid': process.pid, 'output': str(output)}, ensure_ascii=False))
        return
    cfg = json.loads(Path(args.config).read_text('utf-8'))
    planned = {'main':cfg}
    if args.suite:
        incremental = copy.deepcopy(cfg)
        incremental.update(cfg['incremental_extension'])
        incremental['sensitivities'] = {}
        length15 = copy.deepcopy(cfg)
        length15.update(minimum=15, sensitivities={})
        planned.update(incremental_cost=incremental,length15=length15)
    output.mkdir(parents=True,exist_ok=True)
    plan_path = output/'suite_plan.json'
    if plan_path.exists() and json.loads(plan_path.read_text('utf-8')) != planned:
        raise ValueError('Predeclared suite plan changed; use a new directory')
    write_json(plan_path,planned)
    suite_status = {'state':'running','pid':os.getpid(),'studies':{},'updated_at':time.strftime('%Y-%m-%d %H:%M:%S')}
    try:
        for name,configuration in planned.items():
            suite_status.update(current_study=name,updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
            suite_status['studies'][name] = 'running'
            write_json(output/'suite_status.json',suite_status)
            study(configuration,output if name=='main' else output/name,args.workers)
            suite_status['studies'][name] = 'complete'
        suite_status.update(state='complete',current_study=None)
    except BaseException as exc:
        suite_status.update(state='interrupted_or_failed',error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        suite_status['updated_at']=time.strftime('%Y-%m-%d %H:%M:%S')
        write_json(output/'suite_status.json',suite_status)


if __name__ == '__main__':
    main()
