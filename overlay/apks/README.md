# 此目录存放要预装进 /system/app 的 APK(直接提交到 git,构建时打包进 ROM)

# 默认国内方案需要你手动下载放入以下两个文件(在国内浏览器里下载很顺畅,
# CI 服务器在海外,被当贝 CDN 的反爬拦截,所以必须本地下载后提交):
#
#   1. 当贝市场(约 10MB)
#      下载页: https://www.dangbei.com/app/tv/2014/0419/463.html
#      放为:   dangbeimarket.apk
#
#   2. 当贝桌面(约 16MB,作为 TV 桌面)
#      下载页: https://www.dangbei.com/zhuomian
#            (或 https://www.dangbei.com/app/tv/2015/0303/1724.html)
#      放为:   dangbeizhuomian.apk
#
# 放好后 git add + push 即可。之后想升级版本,重新下载同名覆盖再 push。
#
# 其他想预装的 APK(如当贝播放器、Kodi 等)也可放进来,建议克制:
# 大多数应用通过当贝市场安装/更新更方便。
