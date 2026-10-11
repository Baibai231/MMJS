# 独立部署

本机启动器用于复现流程。实际部署须由三个独立主体分别掌握节点私钥、数据库、日志和备份，不将三方秘密挂载到同一服务或交给站点管理员。

## 计算方

每方在自己的主机安装 `requirements-tool.txt`，复制程序与公开规则资产，使用 `node.template.json` 设置本方数据库、三方公开地址和签名公钥。本方创建 `receipt.key`（Ed25519）和 `binding.key`（随机32字节），仅交换签名公钥。可信部署协调方核对这些身份并将公开配置编入独立发布的用户扩展。

本方 `.config/` 下放置自己的 `party_<编号>.crt`、`party_<编号>.key` 和可信 `mpyc_ca.crt`。证书须覆盖 `MPyC party <编号>` 身份；浏览器HTTPS证书另外覆盖本方服务域名。`mpc.ini` 为三方私网地址，**本方段落的 host 留空**。MPyC据此识别本方。启动示例（工作目录为本方私有目录）：

```text
python /opt/zipfguard/privacy/service/node.py -C mpc.ini -I 0 -T 1 -K 40 --ssl --node-config /var/lib/zipfguard-own-party/config.json
```

其他两方将编号改为1、2。可以使用所附systemd模板。MPC端口仅允许计算方之间访问；公开HTTPS入口允许可信扩展和管理来源，仍逐项验证签名、受众、站点与额度。反向代理不可把三个份额接口汇聚为同一个可见明文的代理。

## 两个业务服务

用 `business.template.json` 分别生成用户服务和管理员服务配置。两者拥有不同签名私钥、来源及端口；管理员来源为管理网站，用户来源为独立扩展。仅业务白名单元数据共享业务数据库，计算方份额数据库不与之连接。

```text
python services/run.py --kind user --config /service-user/config.json
python services/run.py --kind admin --config /service-admin/config.json
python services/jobs/runner.py --config /service-admin/config.json
```

管理员的 `rp_id`、`web_origin` 必须与实际管理站点相符。正式HTTPS使用受信任证书；不能使用浏览器测试的证书忽略开关。管理员安装凭证在服务本地生成，首次负责人登记后作废，后续通过负责人邀请登记通行密钥。

## 备份和恢复

只备份本方。SQLite本地演示可用以下命令，账本位置要与备份分离。正式环境将账本置于独立留存或WORM存储，并使用本方PostgreSQL原生备份。

```text
python ops/backup_tool.py backup --config /own/config.json --path /own-backups/date --ledger /independent-ledger/highwater.json
python ops/backup_tool.py restore --config /own/config.json --path /own-backups/date --ledger /independent-ledger/highwater.json
```

恢复前停止本方服务。恢复工具保留查询记录、已消耗隐私预算和人数预算。旧备份缺少已经提交的新凭据份额时会拒绝恢复；必须找到自己的最新份额备份，其他方不能把秘密还原给此方。重启时三方核对共同签名决定，各方仅应用自己已准备的份额。

软件支持分地址配置与PostgreSQL存储；当前验收在单机上完成，**未进行跨机构、跨主机部署实测或生产安全审计**。
