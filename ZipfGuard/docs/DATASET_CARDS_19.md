# 19 站数据卡

出现次数不是已核验的账户人数。上游格式化和去重记为 unknown。

| 数据集 | 出现次数 | 独特字符串 | 拟合 | 特征 | 频次用途 |
| --- | ---: | ---: | --- | --- | --- |
| rockyou | 32602874 | 14314551 | 主协议 | 规模上限跳过 | occurrence_not_verified_accounts |
| myspace | 41545 | 37124 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| phpbb | 255420 | 184358 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| linkedin | 60650662 | 60591405 | 主协议 | 规模上限跳过 | semantics_pending |
| hotmail | 9813 | 8924 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| mailru | 3723513 | 2260489 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| yandex | 1261809 | 717202 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| yahoo | 442838 | 342479 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| faithwriters | 9709 | 8347 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| hak5 | 2984 | 2351 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| 000webhost | 15270702 | 10588510 | 主协议 | 规模上限跳过 | occurrence_not_verified_accounts |
| singles | 16248 | 12233 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| gmail | 4926671 | 3135384 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| zomato | 5870749 | 4989070 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| taobao | 7492029 | 6165938 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| mate1 | 27402201 | 11957093 | 主协议 | 规模上限跳过 | occurrence_not_verified_accounts |
| twitter | 39518 | 35137 | 主协议 | 已计算 | occurrence_not_verified_accounts |
| ashleymadison | 375853 | 375745 | 仅敏感性 | 已计算 | unique_string_only |
| libero | 667635 | 418360 | 主协议 | 已计算 | occurrence_not_verified_accounts |

## 异常

- LinkedIn：重复很少，α 约 0.108，切分在第 1 名。频次语义待确认，账户风险结论不适用。
- Ashley Madison：主协议 `frequency > 3` 失败。可做独特字符串分析，不能把敏感性拟合当成主协议。

载荷哈希和来源标识在 `reports/research19/data_audit.json`。该 JSON 不包含口令。
