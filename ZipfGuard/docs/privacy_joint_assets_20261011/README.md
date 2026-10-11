# 第三板块图表与复现说明

本目录对应[第三板块报告](../第三板块_隐私保护口令统计与联合策略报告.md)。实验在一个固定研究窗口上完成，不包含安全两方协议的运行性能测试。

## 交付文件

- `figure1_profile.png`、`.pdf`、`.svg`：联合人数预测误差与重复名额比例。
- `figure2_registration.png`、`.pdf`、`.svg`：固定攻击下的注册结果与修改负担。
- `table1_ablation.csv`：中间隐私档、指定联合参数的消融结果。
- `figure_source_data.csv`：各图的均值、经验10%和90%分位范围及重复数量。
- `figure_manifest.json`：生成图表使用的结果文件指纹。

图1左图平均六组参数，右图平均其中四组联合参数；表1和图2采用 $\varepsilon=1$、$(k,j)=(10,1)$，口径不能混用。误差线只表示固定数据下20次噪声重复的变化，不是跨用户总体置信区间。

## 复现

在 `ZipfGuard` 目录下使用项目研究环境运行：

```powershell
& '.\.venv\Scripts\python.exe' '.\tools\run_privacy_joint_study.py' '--config' 'configs/privacy_joint_minimal.json'
& '.\.venv\Scripts\python.exe' '.\tools\report_privacy_joint_study.py'
& '.\.venv\Scripts\python.exe' '-m' 'unittest' 'discover' '-s' 'tests' '-p' 'test_privacy_joint_profile.py' '-v'
& '.\.venv\Scripts\python.exe' '.\tools\audit_privacy_joint_study.py'
```

需要本地已有的 `registration_joint_minimal_20261011` 私有数据划分和固定攻击缓存。输入缺失时程序报错，不会重新训练攻击者，也不会将小样本扩充冒充真实记录。

实验结果与配置保存在[结果目录](../../reports/privacy_joint_minimal_20261011)。原始口令仅由离线评价器使用，没有导出到本图表目录。理想隐藏状态在研究进程中访问明文，不能将该进程直接作为网站隐私后端部署。

图表展示的研究评价值包含真值和攻击结果，不是网站可以直接对外发布的差分隐私统计接口。生产环境的画像发布需要保密随机数和多次发布的预算记账。
