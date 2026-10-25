# H96 Max Plus (RK3328/RK3318) TV 化固件工坊

把你的原厂 Android 10 刷机包(`RK3318_DC_R1_AP6330_10_*.img`)改造成
国内可用的电视固件的自动化流水线。**不编译任何源码**——直接对原厂镜像做
"手术":删掉约 757MB 国内不可用的死重(无 GMS 的 Google 系、YouTube 系、
盗版私货、厂商 OTA、手机风桌面),预装当贝市场 + 当贝桌面,注入 TV 属性、
关闭 AVB 校验,然后在 GitHub Actions 上免费重打包成 update.img。

## 目录结构

```
tools/            纯 Python 工具链(本地 Windows 也能跑)
  rkfw.py         RKFW/RKAF update.img 解包/重组
  sparseimg.py    Android sparse 镜像 <-> 裸镜像互转
  lptools.py      super 动态分区里的 ext4 分区定位器
  vbmeta_patch.py AVB flags 补丁(关校验)
scripts/
  surgery.sh      CI 上的手术主流程(挂载 ext4,应用 overlay)
overlay/          你的定制层 —— 改这里,push 即重新构建
work/             本地实验/CI 中间产物(git 忽略)
```

## 首次部署(一次性)

1. 建一个 **公开** GitHub 仓库(私有仓库每月只有 2000 分钟免费额度),
   把本目录内容推上去。
2. 拆分原厂镜像(单文件 2.4GB 超过 Release 的 2GB 上限):
   ```bash
   split -b 1900M RK3318_DC_R1_AP6330_10_20200707.0137.img base.img.part
   ```
   Windows 下用 Git Bash 同样可用。
3. 创建名为 `base-image` 的 Release,把所有 `base.img.part*` 传上去。
4. 在国内浏览器下载两个 APK 放进 `overlay/apks/`(约 26MB,当贝 CDN
   拦截海外 CI,只能本地下载后提交;下载页见 overlay/apks/README.md),
   一起 push。
5. Actions 页手动触发 `build TV rom`,等约 20~40 分钟,
   Artifacts 里下载 `update-tv-*.img`。

## 日常迭代

只改 `overlay/` 里的内容(见 overlay/README.md),push 后自动构建。

## 刷机(RKDevTool)

1. 装 Rockchip DriverAssistant 装驱动;打开 RKDevTool(应显示"发现一个LOADER设备"或进 Maskrom)。
2. 盒子断电,USB 公对公线连电脑,用牙签按住复位孔通电 → 进 Loader/Maskrom。
3. RKDevTool → 升级固件 → 固件 → 选 `update-tv.img` → 升级。
4. 失败/变砖:原厂 img 同样方式刷回;再不行用 Maskrom(短接 eMMC CLK,
   H96 Max+ 的短接点在 Armbian RK3318 帖子里有图)。

## 已验证的正确性(本地)

- `rkfw.py` round-trip:解包→原样重组,**与原厂镜像逐字节一致**(含 bootloader 布局、尾部 trailer、MD5)。
- `sparseimg.py`:sparse→raw→sparse→raw 两轮,**内容 MD5 一致**。
- `lptools.py`:在真实 super 上定位出 system@0x100000(1837MB)/vendor/product/odm,与 ext4 卷标互证。
- `vbmeta_patch.py`:flags 2→3(关 verification+hashtree),原厂 vbmeta 无签名无 descriptor,安全。

## 已知风险与边界

- 尾部 4 字节 trailer(`0x573bfd17`)算法未破解,重组时原样保留;
  由于 round-trip 字节一致,此字段被 RKDevTool 校验导致失败的风险极低,
  但首次刷机请在能进 Maskrom 的环境下进行(本盒子支持,基本刷不死)。
- system 分区仅剩 ~5.5MB 空闲:默认方案删约 757MB、装约 26MB,余量巨大;
  继续追加预装保持"删 > 装"即可,空间不够时 CI 明确失败(刻意设计)。
- 删除原厂桌面(MsLauncher)而未提供任何替代桌面 APK 时,构建直接中止
  (防开机无桌面黑屏)。
- 手术不改变分区表/LP 元数据/ext4 尺寸,只改文件内容,风险面最小。
- 没有 GMS:Play 商店/Netflix HD 与本方案无关(硬件无认证,任何 ROM 都无解),
  App 靠侧载或预装。
- 看 ROM 里装了什么(Windows 可用,纯 Python ext4 只读解析):
  ```
  python tools/ext4ls.py work/super_raw.img --offset 0x100000 system/app
  ```

## 本地实验(可选)

```bash
python tools/rkfw.py info  <原厂img>
python tools/rkfw.py unpack <原厂img> work/unpacked
python tools/sparseimg.py unsparse work/unpacked/super.img work/super_raw.img
python tools/lptools.py work/super_raw.img
```
