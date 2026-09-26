"""Streamlit frontend for the same open experiment used by the local web UI."""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from experiments.open_config import load_open_config
from web.open_interface import execute_open_request
from web.presentation import report_html
from core.data import load_count_json


def main():
    import streamlit as st
    import streamlit.components.v1 as components
    st.set_page_config(page_title='ZipfGuard · 策略实验室',layout='wide')
    st.title('ZipfGuard · 真实数据与 Min_auto')
    st.caption('真实频次 → 用户响应 → 开放候选 → 自适应风险与用户成本。全部计算在本地完成。')
    preset=st.sidebar.selectbox('预设',['open_quick','open_full'],format_func=lambda v:'快速验证 · 2,000 次出现' if v=='open_quick' else '研究预设 · 20,000 次出现 + 可选 PCFG')
    source=st.sidebar.selectbox('数据来源',['corpus','upload'],format_func=lambda v:'真实口令及频次' if v=='corpus' else '聚合 JSON 上传（仅分布分析）')
    cfg=load_open_config(preset)
    with st.sidebar.form('open_'+preset):
        cfg['seed']=st.number_input('随机种子',min_value=0,max_value=2**32-1,value=cfg['seed'])
        cfg['data']['path']=st.text_input('本地语料路径',cfg['data']['path'])
        cfg['data']['format']=st.selectbox('输入格式',['password_with_count','raw_occurrences'])
        cfg['data']['encoding']=st.text_input('字符编码',cfg['data']['encoding'])
        cfg['data']['sample_size']=st.number_input('抽样出现次数',min_value=40,max_value=200000,value=cfg['data']['sample_size'])
        budgets=st.text_input('每模型猜测预算',','.join(map(str,cfg['budgets'])))
        cfg['search']['risk_budget']=st.number_input('推荐使用的预算',min_value=1,max_value=1000000,value=cfg['search']['risk_budget'])
        primary=st.selectbox('主要响应情景',['R0','R1','R2'],format_func=lambda v:{'R0':'R0 独立重新选择','R1':'R1 固定修补序列','R2':'R2 修改与重选混合'}[v])
        sensitivity=st.checkbox('同时比较三种响应',value=len(cfg['response']['scenarios'])>1)
        cfg['response']['primary']=primary;cfg['response']['scenarios']=['R0','R1','R2'] if sensitivity else [primary]
        cfg['response']['max_attempts']=st.number_input('R1/R2 最大额外尝试',min_value=1,max_value=30,value=cfg['response']['max_attempts'])
        cfg['response']['r0_max_total_attempts']=st.number_input('R0 总尝试上限（0 表示无限重试）',min_value=0,max_value=100,value=cfg['response']['r0_max_total_attempts'])
        cfg['bootstrap_repetitions']=st.number_input('Bootstrap 次数',min_value=20,max_value=2000,value=cfg['bootstrap_repetitions'])
        selected={}
        for model in ('frequency','dictionary-rules','character-ngram','pcfg'):
            options=['off','required','optional']
            mode=st.selectbox('攻击器 '+model,options,index=options.index(cfg['attackers'].get(model,'off')))
            if mode!='off':selected[model]=mode
        cfg['attackers']=selected
        cfg['generation']['raw_limit']=st.number_input('原始生成上限',min_value=1,max_value=10000000,value=cfg['generation']['raw_limit'])
        cfg['generation']['timeout_seconds']=st.number_input('生成超时秒数',min_value=1,max_value=3600,value=cfg['generation']['timeout_seconds'])
        cfg['pcfg']['raw_limit']=st.number_input('PCFG 原始上限',min_value=1,max_value=1000000,value=cfg['pcfg']['raw_limit'])
        requirements=st.text_area('安全需求与成本约束 JSON',json.dumps(cfg['search']['requirements'],ensure_ascii=False,indent=2))
        advanced=st.text_area('完整配置覆盖（可选，优先于其它控件）','')
        uploaded=st.file_uploader('聚合频次 JSON',type=['json'])
        submitted=st.form_submit_button('运行实验')
    if submitted:
        st.session_state.pop('result',None)
        try:
            cfg['budgets']=[int(v.strip()) for v in budgets.split(',')]
            cfg['search']['requirements']=json.loads(requirements)
            if advanced.strip():cfg=json.loads(advanced)
            if source=='upload' and uploaded is None:raise ValueError('请选择聚合 JSON 文件')
            payload=load_count_json(uploaded) if source=='upload' else None
            progress=st.empty()
            with st.spinner('正在运行本地实验…'):
                st.session_state['result']=execute_open_request({'config':cfg,'source':source,'payload':payload},progress=lambda text:progress.info(text))
            progress.empty()
        except (ValueError,TypeError,KeyError,OSError,RuntimeError) as exc:st.error('实验未完成：'+str(exc))
    result=st.session_state.get('result')
    if result:
        content=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        page=report_html(result)
        components.html(page,height=1500,scrolling=True)
        st.download_button('下载 JSON 报告',content,'zipfguard_open.json','application/json')
        st.download_button('下载 SHA-256',hashlib.sha256(content.encode()).hexdigest()+'  zipfguard_open.json\n','zipfguard_open.json.sha256')
        st.download_button('下载结果页面',page,'zipfguard_open.html','text/html')
        st.download_button('下载本次配置',json.dumps(result['reproducibility']['config'],ensure_ascii=False,indent=2),'experiment.json','application/json')
    else:
        st.info('选择数据、预算和响应条件，再运行实验。候选生成不受共同口令清单限制；Min_auto 的预算按每个模型分别计数。')

if __name__=='__main__':main()
