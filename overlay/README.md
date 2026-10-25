# overlay/ — 每次构建时对 system 动的"手术单"(国内 TV 方案)

改完 push 即自动重新构建。

## 文件说明

| 文件/目录 | 作用 |
|---|---|
| `remove.list` | 删除清单(相对 ext4 根,`system/...` 开头)。已按"国内不可用"标准填好:删约 757MB(Google 死重、YouTube 系、盗版私货、厂商 OTA、手机风桌面等) |
| `apks/` | **随仓库提交的预装 APK**。当贝的 CDN 拦截海外 CI,所以当贝市场/当贝桌面在这里手动放入(见 apks/README.md,唯一的必做手动步骤) |
| `apps.txt` | CI 在线下载的预装 APK(可选;支持 `@latest:owner/repo` 与 `priv:` 前缀) |
| `system/` | 整目录拷进挂载根的文件(保持 `system/...` 相对路径,如 `system/media/bootanimation.zip` 指文件放在 `overlay/system/system/media/` 下) |
| `props/tv.prop` | 合并进 /system/build.prop 的属性,已存在的键会覆盖 |

## 默认构建产物(国内 TV 就绪)

- 已删除:所有无 GMS 跑不起来的 Google 系、YouTube/SmartYouTube(国内不可达)、
  CyberFlixTV/Mobdro 等盗版私货、厂商 OTA(防刷回原厂)、手机风桌面
- 已预装:当贝桌面(TV 桌面,唯一桌面,开机即它)+ 当贝市场(装一切的入口)
- 属性:TV 形态(ro.build.characteristics=tv 等)

## 刷好后建议从当贝市场装

爱奇艺TV版(银河奇异果)、腾讯视频TV版(云视听极光)、芒果TV电视版、
当贝播放器(本地/网盘播放,替代已删的 RKMC)、Kodi(可选)。
这些都是大厂 TV 客户端,市场里一键装、自动更,比烤进 ROM 合适。

## 安全机制

- 删 MsLauncher 前必须有替代桌面 APK,否则构建中止(防黑屏)
- 空间不足时构建明确失败(system 原厂仅剩 5.5MB;本方案删 757MB 装 26MB)

## 回退

清空 remove.list / apps.txt、移除 apks 里的 APK,push 重新构建即回原厂内容。
