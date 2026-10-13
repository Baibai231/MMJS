# TriGuard 双端工具构建包

这些压缩包由仓库提交 `0648085bbdb3e7c9c13e72b6ff4026ed6f529ca1` 的源码构建，并通过 `ops/build_delivery.py` 的私钥与依赖排除检查。

| 文件 | 用途 | SHA-256 |
| --- | --- | --- |
| `user-assistant.zip` | 用户端 Chrome/Chromium 浏览器插件 | `8c097a43d382c401793f3d3f8427a57f41b4314d2cb95255439d3619b431dfc5` |
| `admin-console.zip` | 网站管理员静态前端 | `ca9626220ff6a7faf2c72fa9b729c9c8b96f35c4774161d7b499d7b48026d499` |
| `ZipfGuard-双端完整工具源码.zip` | 两端源码、业务服务、计算方、部署模板和验收脚本 | `83ac8dd960ff47e4e2035b802212e322538fad2f9a69813d8305d7800171579d` |

插件先解压，在 Chrome/Chromium 的 `chrome://extensions` 页面启用开发者模式，选择“加载已解压的扩展程序”，指向解压后含 `manifest.json` 的目录。管理前端解压后，将 `index.html` 和 `assets/` 置于 HTTPS 静态站点。两端都需要本项目的业务服务和三个计算方；单独打开静态页面不能完成注册、登录或策略操作。详细安装步骤见[双端工具安装与使用](../../docs/现实工具_安装与使用.md)。

此构建包内的可信站点配置指向 `localhost` 演示服务。正式部署需要按实际站点地址与公钥配置、重新签名并构建插件，且三个计算方须由独立主体运行。压缩包不包含运行时私钥、数据库或口令语料。
