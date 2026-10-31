# 启元 T1 Pro 双板实机分析（orin 大脑板 / rk 主控板）

> **采集方式**：全程通过 `ssh t1pro_orin` / `ssh t1pro_rk` 只读探测（`ip`/`ps`/`lscpu`/`taskset`/`systemctl cat`/`cat`/`ros2 node|topic|service list`），未对板卡做任何写入、重启或服务操作。
> **采集时间**：2026-10-29（机器人完整开机运行态，双板 uptime ≈ 46 min，业务栈全部在线）。
> **结论口径**：接口字段全部来自板载 `/robot/software/interface/aimdk_msgs` 与 `/robot/software/algorithm/aimdk_msgs` 的 `.msg`/`.srv` 原始定义；官方语义以 SDK《接口说明.md》交叉核对；无法实证的归属均标注「推断」或「未确证」。


## 目录
- [1. 网络拓扑与连接实测：PC / rk 运控板 / orin 大脑板三机组网](#sec1)
- [2. 硬件规格与布局实测：CPU / GPU / NPU / 内存 / 存储 / 隔离核](#sec2)
- [3. 双板进程全景：清单、职责与 em 部署机制](#sec3)
- [4. 进程 → ROS 2 节点 → 接口挂载总表](#sec4)
- [5. ROS 2 接口全貌：198 话题 / 480 服务实测清单与明细](#sec5)
- [6. 启元 T1 Pro 软件栈分析（包括：三方工具、软件包、框架，3 个 usecase 场景的启动、运行时状态、部署、静态资源占用）](#sec6)
- [7. 整体架构与软件模块接口关系图（第 1–6 章实测的综合视图）](#sec7)
- [附录 A. 采集方法与只读合规说明](#sec8)
- [附录 B. 与既有调研文档的关系](#sec9)

---

<a id="sec1"></a>

## 1. 网络拓扑与连接实测：PC / rk 运控板 / orin 大脑板三机组网

### 1.1 网络拓扑（实测）

三台设备同处 `10.1.1.0/24` 一个二层网段，**PC 是直连机器人内部以太网，不经过 rk 路由转发**：

```
                        ┌──────────── 互联网 / 云端 (aiem-gw.primebot.cn)
                        │ wifi 上联
┌──────────────────┐    │    ┌──────────────────────────────────┐
│ 开发者 PC         │    │    │  rk3576 主控板（soc0）            │
│ enp0s31f6:       │    │    │  hostname: localhost              │
│  10.1.1.99/24 ───┼────┴────┤ eth0:    10.1.1.100/24           │
│ (有线直插机器人   │  有线    │ wlan0:   192.168.10.113 (DHCP)   │
│  内部网口)        │  同网段  │ p2p0:    192.168.88.88 (自建热点) │
└──────────────────┘  直连   │ USB网卡: 192.168.43.100 (RNDIS)   │
                              └───────────────┬──────────────────┘
                                              │ eth0 ↔ 内部以太网 10.1.1.0/24
                              ┌───────────────┴──────────────────┐
                              │ Jetson Orin 大脑板（soc1）         │
                              │ hostname: jetson                  │
                              │ unused0: 10.1.1.101/24（唯一网口） │
                              └──────────────────────────────────┘
```

要点：

- **rk（10.1.1.100）是对外网关**：WiFi、p2p0 热点（hostapd + dnsmasq，给手机 App 连的 192.168.88.x）、USB RNDIS 网卡都在 rk 上；orin 只有一张内网口 `unused0`。
- **orin 出网必须经 rk**（orin 无 WiFi/上联网口），双板间业务流量走 10.1.1.0/24。
- 两块板都运行 `tetherd --bind <本机IP>`（tetherd.service），负责维护这张内部以太网。
- 两板时钟由 `ptp4l`（PTP，`/etc/linuxptp/ptp4l.conf`）+ rk 侧 `chrony` 同步。

### 1.2 SSH 访问配置（本机 `~/.ssh/config`）

| 别名 | HostName | 用户 | 端口 | 密钥 | sudo |
|---|---|---|---|---|---|
| `t1pro_rk` | 10.1.1.100 | `run` | 22 | `~/.ssh/t1pro_rk` | **免密 sudo** |
| `t1pro_orin` | 10.1.1.101 | `run` | 22 | `~/.ssh/t1pro_orin` | **免密 sudo** |

### 1.3 连接测试结果

| 测试项 | `ssh t1pro_orin` | `ssh t1pro_rk` |
|---|---|---|
| 连通性 | ✅ 通 | ✅ 通 |
| hostname | `jetson` | `localhost` |
| 内核 | `5.15.148-tegra #1 SMP PREEMPT` | `6.1.141-rk3576 #1 SMP` |
| 架构 | aarch64 | aarch64 |
| 系统 | Ubuntu 22.04.5 LTS (jammy) | Ubuntu 22.04.5 LTS (jammy) |
| 开机时长（采集时） | 9→46 min | 10→48 min |
| 负载（采集时） | 8.43 / 7.47 / 4.11（完整任务运行中） | 15.96 / 16.35 / 9.69（完整任务运行中） |
| 登录用户 | `run`（uid 1000） | `run`（uid 1000） |
| 设备树型号 | `NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super` | `primebot rockchip rk3576 t2-3` |

> 板上**没有 docker**：`/opt/docker/` 只是环境脚本目录（`env.sh`/`entrypoint.sh`，source ROS 2 Humble + `/opt/env.d/`），整机为裸机部署。

---

<a id="sec2"></a>

## 2. 硬件规格与布局实测：CPU / GPU / NPU / 内存 / 存储 / 隔离核

### 2.1 硬件查询命令对照表（整表刷新）

| 查询项 | 命令 | rk（soc0）实测 | orin（soc1）实测 |
|---|---|---|---|
| **CPU 核数 / 微架构** | `nproc` / `lscpu` / `cat /proc/cpuinfo` | `nproc`=`6`⚠️（实际 8 核，见 §2.4）；lscpu：CPU0-3 = **Cortex-A53**、CPU4-7 = **Cortex-A72** | `nproc`=`6`；`Model name: ARMv8 Processor rev 1`；lscpu：**Cortex-A78AE**（每簇 3 核 ×2） |
| **CPU 最高频率（实测）** | `cat /sys/devices/system/cpu/cpufreq/policy*/cpuinfo_max_freq`（orin 单核路径 `cpu0/cpufreq/…`） | A53 簇（policy0，核 0-3）**2016 MHz**；A72 簇（policy4，核 4-7）**2208 MHz** | `cpuinfo_max_freq` = **1728 MHz**；`scaling_max_freq` = 1344 MHz（当前调度上限；tegrastats 实测运行 @1344 MHz） |
| **Ubuntu / 固件基线** | `cat /etc/os-release`；rk：`cat /proc/device-tree/model`；orin：`head -1 /etc/nv_tegra_release` | Ubuntu 22.04.5；设备树 `primebot rockchip rk3576 t2-3` | Ubuntu 22.04.5；L4T **R36.4.4**（JetPack 6.2） |
| **GPU 型号** | orin：`nvidia-smi --query-gpu=name,driver_version --format=csv`；rk：无 nvidia-smi → `ls /dev/dri/` + 内核线程佐证 | SoC 集成 **ARM Mali-G52 MC3**（图形 GPU）；证据：`/dev/dri/card0、card1、renderD128、renderD129` + 内核 `kbase_*` 线程（Mali 驱动）；型号字符串无法从 sysfs 直读 | `Orin (nvgpu)`，driver **540.4.0**（Orin Nano 开发套件 Super，集成 Ampere GPU） |
| **GPU 频率 / 利用率** | `cat /sys/class/devfreq/*gpu*/{cur,max}_freq` 与 `load`；orin 另用 `tegrastats` | devfreq `27800000.gpu`：300–**900 MHz**（实测运行 800–900 MHz；利用率见§6.3） | devfreq `17000000.gpu`：306–**918 MHz**（动态调频；另有 NVDEC/NVJPG/VIC/OFA 独立时钟 435–538 MHz） |
| **NPU（仅 rk 有）** | rk：`sudo cat /sys/kernel/debug/rknpu/load`、`…/version`、`…/freq`；orin 无 NPU | **RKNPU driver v0.9.8**；双核 `Core0/Core1 xx%`；频率 **950 MHz** | —（AI 由 GPU 承担，`tegrastats` 的 `GR3D_FREQ` 看利用率） |
| **内存** | `free -h` | 总 **7.7 GiB**（8GB 物理；颗粒类型未从板上确证，RK3576 支持 LPDDR4X/LPDDR5） | 总 **7.4 GiB**（8GB **LPDDR5 统一内存**，官方带宽 102.4 GB/s） |
| **存储** | `lsblk -d -o NAME,SIZE,TYPE` | **eMMC 116.5 GB**（A/B 槽位分区即在其上） | **NVMe SSD 238.5 GB** + zram 交换 6×634 MB |
| **功耗模式** | orin：`nvpmodel -q` | —（无该机制） | **NV Power Mode: 25W**（即 Orin Nano「Super」模式；实测静止整板 7.9 W，见 6.3.4） |
| **网口速率** | `cat /sys/class/net/<if>/speed`；拓扑看 `sudo dmesg \| grep yt9215` 与 `ip neigh` | eth0 = **1000 Mb/s**（GMAC 经 RGMII 接 YT9215RB 交换芯片） | unused0 = **1000 Mb/s**（接同一交换芯片） |
| **CPU 同频 IPC** | 无板载测量命令 | 公开微架构资料近似值，见 §2.3 注 | 同左 |

### 2.2 CPU / NPU / GPU 硬件布局与分工（澄清：**NPU ≠ GPU**）

**先回答一个关键问题：rk 板的「NPU」就是 GPU 吗？——不是，它们是 SoC 内两个互不相干的独立硬件。**

- **NPU（Network Processing Unit，神经网络加速器）**：rk3576 内置的**INT8 矩阵乘专用加速器**（6 TOPS、双核、950 MHz，内核驱动 RKNPU v0.9.8）。它只为神经网络推理而生，rk 上**只被 Agent 使用**——6 个视觉模型（人脸/手势/姿态/ReID）经 RKNN Runtime 2.3.2 部署其上（§6.1.3）。
- **GPU（Mali-G52 MC3，图形处理器）**：只负责**图形渲染**——胸屏 flutter UI/表情动画（flutter-pi 持有 GPU 设备）。它**不做任何 AI 推理**，也没有 nvidia-smi（那是 NVIDIA 专属工具，rk 上「没有 GPU」的说法实际指的是没有 NVIDIA 计算卡/nvidia-smi）。
- **rk 的 AI 推理 = CPU + NPU 分工**：RL 运动策略走 **CPU**（onnxruntime 1.19.2，跑在隔离核 CPU3 的实时环里）；视觉模型走 **NPU**（RKNN）；语音前端（VAD/AEC/KWS）走 CPU。GPU 完全不参与 AI。
- **orin 正好相反：AI = GPU**。Orin Nano 无独立 NPU、无 DLA，全部 24 个 `.engine` 模型由 **Ampere GPU**（1024 CUDA 核心 + 32 Tensor Core，TensorRT 10.7 / CUDA 12.6）推理；GPU 在这块板上既是「显卡」（可接显示）也是「AI 计算卡」。
- 两板同为**统一内存（UMA）架构**：CPU/GPU/NPU 共享同一池 DDR，**没有独立显存**——这就是§6.3 中「GPU 显存无独立数值」的硬件根源。

```mermaid
flowchart TB
    subgraph RK3576["🖥 rk3576 SoC（瑞芯微 · 统一内存）——AI = CPU + NPU，GPU 只做图形"]
        RK_CPU0["CPU 簇 0：4× Cortex-A53 ≤2016MHz<br/>（CPU2=CAN 硬中断+device_manager 实时线程<br/>CPU3=MC 独占，见 §2.4）"]
        RK_CPU1["CPU 簇 1：4× Cortex-A72 ≤2208MHz<br/>（全部业务栈跑 CPU0/1/4-7）"]
        RK_GPU["Mali-G52 MC3 图形 GPU<br/>300–900MHz（仅胸屏/UI 渲染，非 AI）"]
        RK_NPU["NPU：6 TOPS INT8 · 双核 @950MHz<br/>（RKNN · Agent 视觉 6 模型专用）"]
        RK_MM["多媒体块：MPP 4K 编解码 · RGA 2.2 · ISP"]
        RK_DDR["统一 DDR 8GB<br/>（CPU/GPU/NPU 共享，无独立显存）"]
    end
    subgraph ORIN_SOC["🧠 Jetson Orin Nano Super SoC（NVIDIA · 统一内存）——AI = GPU"]
        O_CPU["6× Cortex-A78AE ≤1728MHz<br/>（@25W 模式调度上限 1344MHz）"]
        O_GPU["Ampere GPU：1024 CUDA + 32 Tensor Core<br/>306–918MHz（TensorRT 10.7 / CUDA 12.6，全部 .engine 推理）"]
        O_MM["多媒体引擎：NVDEC/NVENC/NVJPG/VIC/OFA<br/>（独立时钟 435–538MHz）"]
        O_DDR["统一 LPDDR5 8GB · 102.4GB/s<br/>（CPU/GPU 共享，无独立显存）"]
    end
    RK_CPU0 & RK_CPU1 & RK_GPU & RK_NPU & RK_MM --- RK_DDR
    O_CPU & O_GPU & O_MM --- O_DDR
```

> 分工速记：**rk = CPU（RL 策略/语音）+ NPU（视觉）+ GPU（胸屏渲染）三路各司其职；orin = CPU（调度/融合/导航）+ GPU（感知推理）两路**。推理框架与模型的对应关系见§6.1.3。

### 2.3 两板硬件规格总表

> 「实测」= 板上命令直接读出；「公开」= 厂商Datasheet/官方口径；IPC 为公开微架构资料近似值（无板载测量手段）。

| 规格项 | rk3576（运控板 soc0） | Jetson Orin Nano Super（大脑板 soc1） | 来源 |
|---|---|---|---|
| CPU 核心 IP 设计 | ARM **Cortex-A53**（小核）+ ARM **Cortex-A72**（大核） | ARM **Cortex-A78AE**（A78 嵌入式/自驾变体） | ARM IP |
| SoC 集成设计厂家 | **瑞芯微 Rockchip** | **NVIDIA** | — |
| 工艺 | 8 nm（RK3576 公开口径） | 8 nm（Samsung 8LPP，Orin 系公开口径） | 公开 |
| CPU 核心数 | **8**（4×A53 + 4×A72，big.LITTLE；CPU2/3 隔离，见 §2.4） | **6**（同构） | 实测 |
| CPU 微架构 | A53：顺序、双发射；A72：乱序、3 宽 | A78AE：乱序、宽发射（≈A78） | 公开 |
| CPU 频率（实测） | A53 ≤**2016 MHz**、A72 ≤**2208 MHz** | ≤**1728 MHz**（25W 模式调度上限实测 1344 MHz） | 实测 cpufreq |
| CPU 同频 IPC（整数） | A53 ≈ **2.1**、A72 ≈ **3.2**（同频整数吞吐 A72 ≈ 1.5×A53） | A78AE ≈ **4.6**（≈ 2.2×A53） | 公开微架构资料近似（无法板载实测） |
| GPU | ARM **Mali-G52 MC3**（Bifrost 三核，图形定位） | NVIDIA **Ampere**：1024 CUDA 核心 + 32 Tensor Core | 实测+公开 |
| GPU 频率（实测） | 300–900 MHz（devfreq） | 306–918 MHz（devfreq，动态） | 实测 |
| GPU 算力 | FP32 ≈ 0.05–0.1 TFLOPS 级（按 G52 MC3 3 核 × 900 MHz 估算，ARM 未公布官方 FLOPS；**不用于 AI**） | FP32 ≈ **2.1 TFLOPS**；INT8 稀疏 **67 TOPS**（25W Super 模式官方口径，含 Tensor Core） | orin 官方；rk 为注明口径的估算 |
| GPU 显存 | **无独立显存**——统一内存（与 CPU/NPU 共享 8GB） | **无独立显存**——统一 LPDDR5 8GB（102.4 GB/s） | 架构事实 |
| NPU 算力 | **6 TOPS INT8**（双核 @950 MHz，RKNPU v0.9.8；实测仅 Agent 视觉使用） | **无独立 NPU/DLA**（AI 全在 GPU Tensor Core） | 实测+公开 |
| 内存 | **8 GB**（free 7.7 GiB；颗粒类型 LPDDR4X/LPDDR5 待确证） | **8 GB LPDDR5** 统一内存（102.4 GB/s） | 实测+公开 |
| 存储（实测） | **eMMC 116.5 GB**（A/B 双槽分区，robot-mount 按 slot 挂载） | **NVMe SSD 238.5 GB** + zram 交换 3.7 GB | 实测 lsblk |
| AI 引擎实际使用（实测） | onnxruntime 1.19.2 **@CPU**（643 个 RL 策略，隔离核实时环）+ RKNN 2.3.2 **@NPU**（6 视觉模型） | TensorRT 10.7 **@GPU**（24 个 .engine） | 实测（6.1.3） |
| 多媒体引擎 | MPP（4K 编解码）+ RGA 2.2 + ISP | NVDEC / NVENC / NVJPG / VIC / OFA（实测独立时钟 435–538 MHz） | 实测 |
| 功耗模式 | —（无 nvpmodel） | `nvpmodel`：**25W Super 模式**（实测静止整板 7.9 W，6.3.4） | 实测 |
| 以太网口 | 千兆 GMAC ×1（eth0）——经 RGMII 接**裕太微 YT9215RB（5+2 口千兆交换芯片）** | 千兆 ×1（unused0，接到同一颗交换芯片） | 实测 dmesg/ARP + 芯片官方规格 |

> **网口拓扑说明（2026-10-30 实测补充）**：整机**对外物理网口只有 1 个**（开发用 RJ45，见 §1.1），不是两块板各一个对外口。实测拓扑：rk 的千兆 GMAC（`rk_gmac-dwmac`）经 RGMII 接到 **Motorcomm/裕太微 YT9215RB——5+2 端口 10/100/1000M L2 交换芯片**（rk dmesg：`yt9215rb stmmac-1:00`；[裕太微官网产品页](https://www.motor-comm.com/product/switching-chip)），orin 的 `unused0` 与对外 RJ45 同接这颗交换芯片——因此 rk(10.1.1.100)/orin(10.1.1.101)/PC(10.1.1.99) 三者同处一个二层（两板 `ip neigh` 实测互见），与 §1.1「PC 直连内部以太网、不经 rk 路由」互证。交换芯片其余端口的用途（如坞座/扩展）远程无法确证。

### 2.4 ⚠️ rk 的 `nproc` 陷阱与隔离核（重点）

rk 内核启动参数做了 CPU 隔离（`/sys/devices/system/cpu/isolated` = **`2-3`**）：

- **被隔离的是 CPU2、CPU3，属 Cortex-A53 小核簇**（CPU0-3 = A53@2.0GHz，CPU4-7 = A72@2.2GHz）。
- 普通用户进程（含 sshd 登录 shell）的默认亲和性被设为 **`0,1,4-7`**（`taskset -pc 1` 实测 = `0,1,4-7`），所以 `nproc` 只看到 **6** 个 CPU——`lscpu` 才显示真实的 8。
- 整机几乎所有用户态进程都带 `0,1,4-7` 亲和性（systemd 全局默认），即**调度器层面与隔离核绝缘**。

**隔离核上到底跑了什么（2026-10-30 线程级实测，`ps -eLo`/`ps -L`/`/proc/irq`）：**

**CPU2 —— CAN 中断 + device_manager 实时设备线程专用核**

| 占用者（线程/中断） | 调度方式 | 优先级 | 功能 / 用途 |
|---|---|---|---|
| `irq83 can0`、`irq84 can1` | 硬中断（未线程化） | —（中断上下文） | **CAN0/CAN1 总线收发**：14 关节执行器/主控 MCU 挂此两路总线（累计 ~600/~450 万次，`/proc/irq/*/smp_affinity_list` 实测钉 2） |
| `irq14 rk_timer`、`irq38/39 arm-pmu` | 硬中断 | — | 内核定时器、PMU 性能计数采样 |
| device_manager `rt_topic_sub`、`rt_iceoryx_sub` | **SCHED_RR** | **rtprio 85** | 设备数据实时订阅（DDS 话题 + iceoryx 零拷贝通道） |
| device_manager `rt_dev_ioctl.0/.1`、`rt_dev_poll`、`rt_topic_pub` | **SCHED_RR** | **rtprio 80** | **本体设备实时 ioctl/轮询**（CAN/电源等设备读写）与实时发布——device_manager 常驻 ~50% CPU 的主因；注意其**进程本体亲和性为 `0,1,4-7`，仅这 6 个 rt 线程被单独钉在 CPU2**（线程级 affinity） |
| `cpuhp/2`、`migration/2`、`ksoftirqd/2`、`kworker/2:*` | 内核线程（migration 为 SCHED_FIFO 99） | FF 99/其他 | 每核必备内核管家线程（isolcpus 不排除它们） |

**CPU3 —— mc 运动控制独占核（进程亲和性=3，36 线程全覆盖）**

| 占用者（线程） | 调度方式 | 优先级 | 功能 / 用途 |
|---|---|---|---|
| mc `rt_iceoryx_sub` | **SCHED_RR** | **rtprio 85** | 传感器/指令 **iceoryx 零拷贝实时订阅**（运控输入关键路径，全板最高用户态优先级） |
| mc `rt_joint_pub`、`passive_default_t0` | **SCHED_RR** | **rtprio 80** | 关节状态实时发布；控制环线程——**其名字随运控模式切换**（2026-10-30 实测）：静止时 `passive_default_t0`（被动/默认控制环），**四足站立/运动时更名为 `rl_quad_locomotion_t0`**（RL 四足运动策略推理环，同 tid 同 RR 80；站立时瞬时 ~17.6% CPU、策略持续推理维持站姿，`rt_iceoryx_sub` 同步拉满 ~30%）。线程名系运行时拼接（二进制无字面名），`rl/` 共 48 份策略 yaml（如 `quad_walk_dance.yaml` → `t2_quad_twist_xy_v1.0_0708.onnx`，配置中留有 `.rknn` NPU 试验注释，现役 onnx/CPU）；**ORT 无独立线程池（36 线程中无一 onnx/omp 线程），推理即在调用线程内执行**——其余模式（双足 WBC/舞蹈/模仿）是否同样复用此线程槽未逐一实测 |
| mc `mc_t1`、`mc_sm_thread`、`MotionControl executor ×2`、`com_data_pub`、`hds_pub`、`event_trace_pub`、`security_guard`、`main_executor` | SCHED_OTHER | nice 0 | **运控内核主体**：T1 机型控制逻辑、状态机、运动控制执行器、`mc/common/state` 状态与诊断发布、安全看护 |
| mc `ros2_executor ×2`、`RMW_node`、`RTPS{log,event,flow_control}`、`UDP* ×6`、`aimrt_guard`、`nsm/default executor ×4`、`KeepAlive`、`default_time_wheel` | SCHED_OTHER | nice 0 | AimRT / ROS 2 / FastDDS 框架线程（通信与执行器底座，全部随进程钉在 CPU3） |

> 采集口径：2026-10-30 实测（重启后 mc pid=2672、device_manager pid=2698，pid 随启动变化）；线程名截断自 `ps -L -o comm`（15 字符限长）。**机器人四足站立态复测**：线程总数与调度结构不变（36=3RR+33TS、dm 6RR 钉 CPU2），但 CPU2 利用率升至 ~73%（CAN 中断+设备轮询满负荷）、CPU3 ~42%（RL 站姿策略环+实时订阅/发布），且控制环线程切换为 RL 运动策略身份（见上表注）。

> 结论：rk 用「1 核收 CAN 中断+device_manager 实时设备线程 + 1 核跑运动控制」的隔离方案替代了 PREEMPT_RT，其余 6 核（2×A53 + 4×A72）跑全部业务栈。
> 全板 RT 线程普查（2026-10-30 实测）：用户态实时线程共 **9 个 SCHED_RR**（mc ×3 @CPU3、device_manager ×6 @CPU2，另有 rtkit-daemon@99）+ 32 个 SCHED_FIFO **内核线程**（migration/irq/watchdog 等）；其余 ~1460 线程均为 SCHED_OTHER。

---

<a id="sec3"></a>

## 3. 双板进程全景：清单、职责与 em 部署机制

### 3.1 部署机制（两板一致，实测自 systemd 单元与进程树）

```
systemd
 ├─ em.service（EM App，Requires=system_entry.service，After=iox-roudi/camera-start/sync_time）
 │    └─ /robot/software/process_manager/bin/start_process_manager.sh
 │         └─ ./process_manager --cfg_file_path=./cfg/process_manager_config.yaml
 │              └─ 按 yaml 逐个拉起每个应用的 em_run.sh：
 │                   sudo -u #1000|0 -g … -E --preserve-env=PYTHONPATH EM_APP_NAME
 │                     FASTRTPS_DEFAULT_PROFILES_FILE LOG_PATH ROBOT_FEATURE_ROS2_CHANNEL_QOS
 │                     ROS_LOG_DIR  /robot/software/<app>/bin/em_run.sh
 │                        └─ 各应用二进制 ./xxx --cfg_file_path=./cfg/xxx.yaml
 ├─ iox-roudi.service → /robot/software/entry/run_roudi.sh
 │    └─ /opt/iceoryx/bin/iox-roudi -c /robot/software/entry/cfg/roudi_config.toml -m on   （板内零拷贝 IPC 总线）
 ├─ tetherd.service → /usr/bin/tetherd --bind 10.1.1.100|101
 └─（rk 独有）hostapd/dnsmasq（p2p0 热点）、mosquitto（conduit_router TLS MQTT）、fce863r-bt.service（蓝牙）
```

- 应用根目录 `/robot/software/<app>/`，统一 `bin/em_run.sh + cfg/*.yaml` 布局；**多数应用以用户 `run`(uid 1000) 运行，少数以 root（`sudo -u #0`：data_exporter、ota_master/ota_slave、sys_guard、hds）**。
- 环境注入：`EM_APP_NAME`（应用名）、`FASTRTPS_DEFAULT_PROFILES_FILE=/robot/software/entry/cfg/standard/ros_dds_configuration.xml`（FastDDS 配置）、`ROS_LOG_DIR/LOG_PATH=/robot/persist/log/<app>`、`ROBOT_FEATURE_ROS2_CHANNEL_QOS`。
- ROS 2 Humble 裸机安装于 `/opt/ros/humble`；aimdk 消息定义包安装于 `/robot/software/interface/aimdk_msgs`。
- 进程守护：em.service `Restart=always` + process_manager 按配置重启子应用；健康诊断由 hds + sys_guard 负责。

### 3.2 rk（主控板 soc0，10.1.1.100）进程表

| 进程（二进制） | 运行用户 | 功能 | 备注 |
|---|---|---|---|
| `process_manager` | root | 进程管理器：按 `process_manager_config.yaml` 拉起/守护全部 em 应用 | 对应 ROS 节点 `/process_manager0` |
| `sys_guard` | root | 系统守护/场景看护（温控、资源、活动场景） | `/sys_guard_node_soc0` |
| **`mc_app_main`** | run | **运动控制主程序（实时运控内核）** | **钉在隔离核 CPU3**，36 线程，`/mc_ros2_node` |
| `aim_sm_app`（qy3576_t1_v2d1_2X.yaml） | run | 状态机 SM：整机有限状态机与场景调度 | `/sm` |
| `aima-agent-app`（agent.yaml） | run | Agent 对话智能体（LLM/语音交互/任务编排入口），RSS ~1.1GB | `/aimrt_agent_node`；子进程 ping `aiem-gw.primebot.cn` 探测云端 |
| `task_engine` | run | 任务引擎：长任务编排/仲裁执行（跟随、建图、运镜…） | `/task_engine` |
| `aimrt_main`（exposure_config.yaml） | run | 相机曝光控制 | 图上未见独立 ROS 节点（iceoryx 通道为主） |
| `aimrt_main`（t1_v2d0_config.yaml） | run | 设备管理器 device_manager | `/device_manager_node`（cwd=/robot/software/device_manager/bin 实证） |
| `aimrt_main`（audio_manager.yaml） | run | 音频管理（麦克风阵列/播放/音乐） | `/audiomanager_ros2_node` |
| `aimrt_main`（hal_sensor_t1_v2d0_cfg.yaml） | run | 传感器 HAL（BMS/触摸/灯/舵机/颈部灯，soc0 侧） | `/hal_sensor_soc0` |
| `aima-hal_camera-app-main`（rk 配置） | run | **相机 HAL：头部相机直连采集**（ox05bis 5MP 经 max96724 GMSL→rkcif，2592×1944@10fps，fuser 实证持有 /dev/video0-1）+ **BMI08 头部 IMU**（/dev/bmi08_imu）；帧经 iceoryx/dmabuf 板内分发（Agent 视觉/rtsp） | 图上未见 ROS 节点（图像走 iceoryx，2026-10-31 板上实证） |
| `aima-hds-app`（hds_master.yaml） | root | 健康诊断服务 HDS（故障码汇总/告警） | `/hds` |
| `flutter-pi --release -r 180 display_ui` | root | 胸屏 Flutter UI 渲染 | 非 ROS 进程 |
| `aima-legged_odom-app` | run | 足式里程计（odom/tf/躯干位姿） | `/legged_odom_node` |
| `aima-rc-app`（rc_T1.yaml） | run | 遥控器 rc 接入（体感遥控/摇杆） | `/rc` |
| `aima-dp-app`（drp） | run | 数据记录（rosbag 录包，bag 存 /robot/persist/bag/） | `/record_ros2_node_3765`（节点名含 pid） |
| `aima-cloud_proxy-app` | root | 云端代理（上行状态/资源/日志） | `/cloud_proxy` |
| `aima_ota_master_app` | root | OTA 主控（升级编排/心跳） | `/ota_master` |
| `aimrt_main`（insta360.yaml） | run | Insta360 云台相机接入 | `/insta360_ros2_node` + `/insta360_gimbal_tts` |
| `aimrt_main`（quanji.yaml，connx） | run | 「圈圈」UWB 随行设备接入（UWB/IMU/雷达/音频） | `/quanji_uwb_ros2_node` |
| `picture_service`（resource_manager 目录） | run | 拍照与资源管理（图片/自定义动作资源） | `/picture_service`（SM 注册名 `resource_manager`） |
| `scene_engine` | run | 场景引擎（场景 tick/切换） | 图上未见独立 ROS 节点 |
| `python3 housekeeper` | run | 管家服务：WiFi/蓝牙/4G/设备信息/权限管理 | `/housekeeper0`（另 `/HK_ros2_rpc_node` 推断亦属管家/云属性桥） |
| `python3 local_app_proxy.app_proxy` | run | 手机 App 代理（App 连接/鉴权/指令转发） | `/app_proxy` + `/app_proxy_client` |
| `ros2 run rtsp_server` ×2 | run | RTSP 视频流服务（App 看相机画面） | `/rtsp_video_server` |
| `lumina_server` | run | 灯效服务器（氛围灯/表情灯语联动） | `/lumina_server` |
| `aima-data_exporter-app` | root | 数据导出（云端异步任务/文件分片上传） | `/data_exporter`（soc0 实例） |
| `abox server / abox log`（--project t1） | run | 日志服务（采集/上传） | 非 ROS 进程 |
| `mosquitto`（conduit_router_tls.conf） | root | MQTT over TLS（设备侧消息管道） | 非 ROS 进程 |
| `hostapd` + `dnsmasq` | root/nobody | p2p0 热点（192.168.88.0/24）及 DHCP | 非 ROS 进程 |
| `iox-roudi` | run | iceoryx 零拷贝 IPC 守护 | 两板各一 |
| `secret_store` | root | 密钥/凭据存储 | 非 ROS 进程 |

### 3.3 orin（大脑板 soc1，10.1.1.101）进程表

| 进程（二进制） | 运行用户 | 功能 | 备注 |
|---|---|---|---|
| `process_manager` | root | 同 rk（soc1 实例） | `/process_manager1` |
| `sys_guard` | root | 系统守护（soc1 侧） | `/sys_guard_node_soc1` |
| `aimrt_main`（hal_sensor_orin_t1_cfg.yaml） | run | 传感器 HAL（ADU 电源板/温度，soc1 侧） | `/hal_sensor_soc1` |
| `aimrt_main`（t1_v2d1_config.yaml，hal_rgbd） | run | RGBD/ToF 相机 HAL——**5 目 ToF 数据面**（ofilm_tof/tofb GMSL 驱动，fuser 实证持有 /dev/video2-7；main/front/rear/left/right） | `/hal_rgbd_node` + `/hal_rgbd_node_0`（cwd=hal_rgbd/bin 实证） |
| `aima-hal_camera-app-main`（orin 配置） | run | 相机 HAL——2× ofilm_x5b 5MP RGB（2592×1944 UYVY，GMSL 直连，fuser 实证持有 /dev/video0-1；ToF 曝光控制面在 rk 侧 exposure）；imu_head 由 rk 侧发布（本板配置中已注释） | `/hal_camera` + `/hal_camera_1`（图像走 iceoryx，ROS 侧 0 发布者） |
| `aimrt_main`（aimrt_person_track_config_T.yaml） | run | 人体跟踪感知（跟随候选目标检测） | `/aimrt_person_track_node` + `/aimrt_person_track_srv` |
| `aimrt_main`（aimrt_brightness_status_config_T.yaml） | run | 亮度检测（跟随过暗门控） | `/aimrt_brightness_status_node` + `/aimrt_brightness_status_srv` |
| `coreon_fusion` | run | 多传感器融合（5×ToF+相机+IMU+UWB → 融合点云/目标/避障状态） | `/coreon_fusion_ros2_node` |
| `percep`（algorithm/perception） | run | 感知算法主程序（语义分割/环境识别/楼梯检测/局部代价图） | `/perception` |
| `ros2 launch nav2_bringup start_real_navigation.launch.py` | run | **Nav2 导航栈**：bt_navigator、controller_server、planner_server、behavior_server、smoother_server、map_server、robot_navigation、双层 costmap、lifecycle_manager_navigation、nav2_container、4×static_transform_publisher | 导航全链条 |
| `ros2 launch follow follow.launch.py` | run | 跟随控制（语义跟随规划） | `/follow_node` |
| `ros2 launch path_follow_node trajectory_tracker.launch.py` | run | 轨迹跟踪（环绕运镜等智能运动） | `/trajectory_tracker` |
| `aimrt_main`（nav2_perception：pointcloud_combiner + perception_node） | run | 点云合并（前/后 ToF）+ 感知生命周期节点 | `/pointcloud_combiner_node`、`/perception_node`、`/lifecycle_manager_t1_perception` |
| `aima-data_exporter-app` | root | 数据导出（soc1 实例） | `/data_exporter`（soc1 实例） |
| `aima_ota_slave_app`（ota_slave_soc1.yaml） | root | OTA 从控（soc1 升级执行） | `/ota_slave` |
| `iox-roudi` | run | iceoryx Roudi | |
| `tetherd` | root | 内部网维护 | |

### 3.4 隔离核专享进程（rk）小结

见 §2.4 表：**CPU2 = CAN0/CAN1 硬中断 + rk_timer/arm-pmu；CPU3 = `mc_app_main` 运动控制独占（36 线程全钉 CPU3）**。除隔离核外，rk 其余进程亲和性一律 `0,1,4-7`；orin 无隔离核。


---

<a id="sec4"></a>

## 4. 进程 → ROS 2 节点 → 接口挂载总表

### 4.0 图与采集说明

- 两板**同属一个 ROS 2 域**：`ROS_DOMAIN_ID=0`（默认未覆盖）、`RMW=rmw_fastrtps_cpp`、`ROS_LOCALHOST_ONLY=0`，FastDDS 统一加载 `/robot/software/entry/cfg/standard/ros_dds_configuration.xml`，经 10.1.1.0/24 组播互通——**从任一板能看到双板全部 69 个节点**（含隐藏辅助节点）。
- 板上 ros2 CLI 用法：`source /opt/docker/env.sh` 后直接 `ros2 node list`（orin 侧已存在用户级 ros2-daemon，pid 3066，`--ros-domain-id 0 --rmw-implementation rmw_fastrtps_cpp`）。
- ⚠️ 实测现象：在 rk 上执行 `ros2 node list`（无 daemon/`--no-daemon`）只能看到约 16 个节点（绝大多数是 orin 侧节点），rk 本机节点反而不可见——推断 rk 业务进程的 FastDDS 配置限制了本机发现或绑定特定网卡；**全量图以 orin 侧采集为准**。
- 节点归属判定依据：板上 `ps` 进程参数链（em_run.sh 目录 ↔ 二进制 ↔ 配置）+ 命名约定 `soc0=rk、soc1=orin`（如 `hal_sensor_soc0/soc1`、`sys_guard_node_soc0/soc1`、`process_manager0/1`、`ota_slave_soc1.yaml`）+ 双实例同名节点（`/data_exporter` 出现两次 = 双板各一）。个别无法实证的标注「推断/未确证」。
- 下表 `P/S/V/C` = 发布话题数 / 订阅话题数 / 业务服务端数 / 业务客户端数（已剔除 `describe_parameters` 等 6 项参数标准服务与 `/rosout`、`/parameter_events`）。

### 4.1 rk（主控板 soc0）进程与节点

| 进程 | ROS 2 节点 | P | S | V | C |
|---|---|---|---|---|---|
| process_manager | `/process_manager0` | 0 | 0 | 0 | 0 |
| sys_guard | `/sys_guard_node_soc0` | 3 | 1 | 2 | 0 |
| **mc_app_main（CPU3 独占）** | `/mc_ros2_node` | 4 | 2 | 13 | 0 |
| aim_sm_app | `/sm` | 3 | 2 | 4 | 28 |
| aima-agent-app | `/aimrt_agent_node` | 15 | 8 | 18 | 24 |
| （管家/云属性桥，实证） | `/HK_ros2_rpc_node` | 0 | 0 | 1 | 1 |
| task_engine | `/task_engine` | 12 | 39 | 30 | 51 |
| （任务触发链路，实证=Agent 交互触发） | `/task_trigger_ros2_node` | 0 | 3 | 0 | 1 |
| aimrt_main（t1_v2d0，device_manager） | `/device_manager_node` | 1 | 0 | 0 | 1 |
| aimrt_main（audio_manager） | `/audiomanager_ros2_node` | 9 | 1 | 15 | 1 |
| aimrt_main（hal_sensor soc0） | `/hal_sensor_soc0` | 6 | 2 | 9 | 0 |
| aima-hds-app | `/hds` | 2 | 3 | 3 | 1 |
| housekeeper | `/housekeeper0` | 6 | 0 | 23 | 2 |
| local_app_proxy | `/app_proxy`、`/app_proxy_client` | 3 / 0 | 29 / 0 | 3 / 0 | 0 / 2 |
| aima-rc-app | `/rc` | 3 | 0 | 2 | 6 |
| aima-dp-app（drp 录包） | `/record_ros2_node_3765` | 1 | 4 | 2 | 0 |
| cloud_proxy | `/cloud_proxy` | 3 | 7 | 0 | 1 |
| ota_master | `/ota_master` | 3 | 5 | 2 | 10 |
| aimrt_main（insta360） | `/insta360_ros2_node`、`/insta360_gimbal_tts` | 6 / 0 | 2 / 0 | 3 / 0 | 1 / 2 |
| aimrt_main（quanji） | `/quanji_uwb_ros2_node` | 5 | 0 | 1 | 0 |
| picture_service | `/picture_service` | 3 | 1 | 10 | 3 |
| rtsp_server（×2 进程） | `/rtsp_video_server` | 0 | 0 | 0 | 1 |
| lumina_server | `/lumina_server` | 1 | 9 | 0 | 13 |
| legged_odom | `/legged_odom_node` | 8 | 1 | 2 | 0 |
| data_exporter（soc0 实例） | `/data_exporter` ① | 1 | 0 | 12 | 10 |
| 无 ROS 节点：aimrt_main(exposure)、hal_camera(rk)、scene_engine、flutter-pi、abox、mosquitto 等 | — | | | | |

**① `/data_exporter` 在图中出现两次（重名双实例），soc0/soc1 各一个。**

### 4.2 orin（大脑板 soc1）进程与节点

| 进程 | ROS 2 节点 | P | S | V | C |
|---|---|---|---|---|---|
| process_manager | `/process_manager1` | 0 | 0 | 0 | 0 |
| sys_guard | `/sys_guard_node_soc1` | 3 | 1 | 2 | 0 |
| aimrt_main（hal_sensor orin） | `/hal_sensor_soc1` | 3 | 0 | 1 | 0 |
| aimrt_main（hal_rgbd） | `/hal_rgbd_node`、`/hal_rgbd_node_0` | 8 / 1 | 11 / 6 | 3 / 0 | 1 / 0 |
| aima-hal_camera-app-main（orin） | `/hal_camera`、`/hal_camera_1` | 5 / 3 | 2 / 2 | 4 / 3 | 1 / 1 |
| aimrt_main（person_track） | `/aimrt_person_track_node`、`/aimrt_person_track_srv` | 6 / 0 | 4 / 0 | 0 / 1 | 0 / 1 |
| aimrt_main（brightness） | `/aimrt_brightness_status_node`、`/aimrt_brightness_status_srv` | 1 / 0 | 1 / 0 | 0 / 1 | 0 / 0 |
| coreon_fusion | `/coreon_fusion_ros2_node` | 7 | 27 | 7 | 1 |
| percep | `/perception` | 10 | 5 | 1 | 0 |
| nav2_bringup launch | `/bt_navigator`、`/bt_navigator_navigate_to_pose_rclcpp_node`、`/controller_server`、`/planner_server`、`/behavior_server`、`/smoother_server`、`/map_server`、`/robot_navigation`、`/global_costmap/global_costmap`、`/local_costmap/local_costmap`、`/lifecycle_manager_navigation`、`/nav2_container`、`/map_to_odom_tf_publisher`、`/camera_to_base_tf_publisher`、`/front_tof_to_combined_tf_publisher`、`/rear_tof_to_combined_tf_publisher` | 见 4.4 | | | |
| follow launch | `/follow_node` | 9 | 1 | 1 | 0 |
| trajectory_tracker launch | `/trajectory_tracker` | 5 | 2 | 1 | 0 |
| nav2_perception | `/pointcloud_combiner_node`、`/perception_node`、`/lifecycle_manager_t1_perception` | 见 4.4 | | | |
| data_exporter（soc1 实例） | `/data_exporter` ① | 1 | 0 | 12 | 10 |
| aima_ota_slave_app | `/ota_slave` | 2 | 0 | 2 | 0 |
| （App 通道接收器，随 app_proxy 域，实证） | `/trro_recv_dedicated` | 0 | 1 | 0 | 0 |

### 4.3 各节点完整接口挂载（rk 侧业务节点）

> 格式：`发布` / `订阅` / `服务端` / `客户端`。参数类标准服务与 `/rosout`、`/parameter_events` 省略。带 ★ 的为 SDK 官方对外接口。

**`/mc_ros2_node`（mc_app_main，rk·CPU3）**
- 发布：`/aima/mc/common/state`（运控全量状态）★、`/aima/hds/diagnostic_list`、`/aimrte/sm/node_state/pb_…SmNodeStateChannel`、`/robot/trace/events`
- 订阅：`/aima/mc/locomotion/velocity`（速度指令）★、`/aima/mc/custom/joint/command`（上肢自定义关节指令）★
- 服务端：`SetMcAction`★、`GetMcAction`★、`SetMcMotion`★、`GetMcMotions`、`SetMcPresetMotion`★、`GetMcPresetMotionState`、`SetMcInputSource`★、`GetCurrentInputSource`★、`SetRobotRuntimeModel`、`GetRobotRuntimeModel`、`McMotionRecord`、`SmNodeService/mc/ExecuteAction|Fallback`
- 客户端：—

**`/sm`（aim_sm_app，rk）**
- 发布：`/aima/sm/system_state`（整机状态机当前状态）、`/aimrte/sm/node_state/…`、`/robot/trace/events`
- 订阅：`/aima/hal/bms/state`、`/aimrte/sm/node_state/…`
- 服务端：`GetSystemState`、`MigrateSystemState`、`SmNodeService/sm/ExecuteAction|Fallback`
- 客户端：`EmAppService/GetAllAppInfos|GetAppInfo|GetDevSettings|SetDevSettings|StartApp|StartApps|StopApp|StopApps`（process_manager 的 proto RPC 桥）、各应用 `SmNodeService/<app>/ExecuteAction|Fallback`（audio_manager、coreon_fusion、hal_camera、hal_rgbd、hds、mc、task_engine）、`SetJointPower`、`GetJointPowerState`、`SetPowerDown`、`SetPowerDownOrin`、`SetHalTofStream`、`SetHalCameraStream`

**`/aimrt_agent_node`（aima-agent-app，rk）**
- 发布：`/aima/agent/asr_result`（ASR 结果）★、`/aima/agent/face_info`（人脸）、`/aima/agent/greeting_state`、`/aima/agent/interaction_state`、`/aima/agent/biologic_state`、`/aima/agent/business_agent_control_state`、`/aima/agent/semantic_intent`、`/aima/agent_show_text`、`/agent/process_audio_output`、`/agent/task_operation`、`/interaction/tts_status`、`/aima/app_proxy/trro_pull_message`、`/aima/hal/audio/play_state`、`/aimrte/sm/node_state/…`、`/robot/trace/events`
- 订阅：`/aima/app_proxy/trro_push_message`、`/aima/hal/audio/music_state`、`/aima/hal/bms/state`、`/aima/hal/network/state`、`/aima/interaction/state`、`/aima/robot/permission_state`、`/aima/sm/system_state`、`/interaction/tts_status`
- 服务端：`PlayTts`★、`GetTtsDuration`、`GetAudioStatus`、`PlayMediaFile`、`GetAndPlayNlgText`、`SetTtsParameters`、`StopTtsTraceId`、`StartVoiceInteraction`、`StartBiologicalEntry`、`BusinessAgentControl`、`GetAgentPropertiesRequest`、`SetAgentPropertiesRequest`、`SetAgentSuppression`、`GetMicSourceRequest`、`SetMicSourceRequest`、`TaskStatus`、`SmNodeService/agent/ExecuteAction|Fallback`
- 客户端：`AmRequest`、`AmCancel`、`ControlTask`、`ControlMusic`、`PlayAnimation`、`PlayEmotion`、`PlayEmotionByStr`、`PlayCustomMotion`、`PlayMove`、`PlayMoveWithWalkMode`、`PlayPhoto`、`FollowTaskCtrl`、`NaviTaskCtrl`、`SearchMusic`、`GetMusicList`、`GetAndPlayNlgText`、`PlayTts`、`PlayMediaFile`、`SetMode`、`SetNeckLight`、`CtrlPermission`、`ShutdownTask`、`TouchEnable`

**`/task_engine`（task_engine，rk）— 全图最重的业务枢纽（P12/S39/V30/C51）**
- 发布：`/aima/interaction/state`（交互长任务状态）★、`/aima/task_engine/task_status`、`/aima/task_engine/agent_flow/task_info`、`/aima/extreme_action/status`、`/aima/map/localization/state`、`/aima/map/charge_point/set_state`、`/aima/map/charge_point/navi_sit_state`、`/aima/mc/locomotion/velocity`、`/aima/core/locomotion/velocity`、`/aima/resource/update`、`/aimrte/sm/node_state/…`、`/robot/trace/events`
- 订阅（39 路）：agent 侧（biologic/interaction_state/semantic_intent/agent_show_text）、算法状态（navi/state、navi/relocate/state、follow/state、charge_point/recognition_state、charge_point/sit_state）、HAL（bms/touch/servo/rgb/network/audio×3）、系统（sm/system_state、permission_state、hds/alert_code_list、net_port_state）、交互（display_state、tts_status、interaction task_status）、感知（follow_candidates、follow_brightness_dark、percep/detect/env）、里程计（odom、odom/status）、圈圈（quanji/state、quanji/radar）、insta360/task_state、app/info、map/build_state、map/state、slam_state、fall_guard/danger_regions、intelligent_task_state
- 服务端：`PlayMove`、`PlayMoveWithWalkMode`、`PlayAnimation`、`PlayEmotion`、`PlayCustomMotion`、`PlayDefaultEmoji`、`SetFaceMode`、`RecordMotion`、`FollowTaskCtrl`、`NaviTaskCtrl`、`MapCommandTask`、`MapLocalizationTask`、`SetChargePointTask`、`ControlTask`、`AmRequest`、`AmCancel`、`AgentFlowTaskCtrl`、`GetMotionList`、`GetTaskInfo`、`InteractWithId`、`FaceGreet`、`SetMode`、`SetAppConfig`、`ShutdownTask`、`TaskInjection`、`TouchEnable`、`TransformTask`、`ControlPowerSave`、`SmNodeService/task_engine/ExecuteAction|Fallback`
- 客户端（51 路）：mc 侧（SetMcAction/Motion/PresetMotion、GetMc*、McMotionRecord）、导航（/navi/command|loadmap|relocate、NaviTaskCtrl）、建图（MapCommand、MMStartLocalization、MMStopLocalization、GetMapList、GetMapData、GetCurrentMap、GetNaviPoints、SetNaviPoint）、跟随（SetFollowPerceptionTask、SetFollowTaskBiped）、专座（setchargepoint、setchargepointsit、chargepointsit/enable）、感知（/perception/enable、set_brightness_enable）、资源（GetResourcePath、GetCustomMotionList、DeleteResource、RenameResource）、交互（PlayTts、PlayAudioFile、StopAudioPlay、GetAndPlayNlgText、StopTtsTraceId、SetVolume、GetVolume、SetNeckLight、LedStripCommand）、Insta360（AssignInsta360Task、ControlInsta360、PlayPhoto、CaptureJpegImage）、系统（SetPowerDown、SetPowerDownOrin、SetServo、ObstacleCheck、SetAvoidance、GetWifiState、MigrateSystemState、StartBiologicalEntry、StartVoiceInteraction、SpawnIntelligentMotionTask）

**`/audiomanager_ros2_node`（audio_manager，rk）**
- 发布：`/aima/hal/audio/audio_device_list`（音频设备列表）★、`play_state`、`volume`、`mute`、`mic_source`、`music_state`、`event_probe`、`diagnostic_list`、`sm/node_state`
- 订阅：`/aima/sm/system_state`
- 服务端：`GetVolume`★、`SetVolume`★、`GetMute`★、`SetMute`★、`PlayAudioFile`★、`StopAudioPlay`、`ControlMusic`、`SearchMusic`、`GetMusicList`、`GetMicSourceRequest`、`SetMicSourceRequest`、`GetSpkSinkRequest`、`SetSpkSinkRequest`、`SmNodeService/audio_manager/…`
- 客户端：`PlayEmotion`

**`/hal_sensor_soc0`（hal_sensor，rk）**
- 发布：`/aima/hal/bms/state`（电源管理数据）★、`touch/state`（头部触摸）★、`neck_light/state`、`rgb/state`、`servo/state`、`diagnostic_list`
- 订阅：`/aima/hal/thermal/soc1`、`/aima/sm/system_state`
- 服务端：`SetServo`★、`SetNeckLight`★、`GetNeckLightState`、`LedStripCommand`★、`GetBatteryInfo`、`SetJointPower`、`GetJointPowerState`、`SetExt12vPower`、`SetPowerDown`
- 客户端：—

**`/housekeeper0`（housekeeper，rk）— 网络/设备管理全家桶（V23）**
- 发布：`/aima/hal/network/state`、`/aima/net/net_port_state`、`/aima/robot/basic_info`、`/aima/robot/permission_state`、`/robot/scan/bluetooth`、`diagnostic_list`
- 服务端：`GetWifiState`、`CtrlWifi`、`GetWifiHotspotState`、`CtrlWifiHotspot`、`SetHotspotInfo`、`CtrlScanBluetooth`、`ConnectBluetooth`、`DisConnectBluetooth`、`ReBroadcastBluetoothService`、`SetBleAlias`、`SetBluetoothName`、`CtrlModem`、`GetModemInfo`、`GetLbsLocation`、`GetNetworkState`、`CtrlPermission`、`GetRobotInfo`、`GetRobotState`、`GetWholeRobotInfo`、`SetRobotName`、`SetSN`、`SetBoardSN`、`SetDevSecret`
- 客户端：`GetRobotInfo`、`JsonWebCheck`

**`/app_proxy` + `/app_proxy_client`（local_app_proxy，rk）**
- `/app_proxy` 发布：`/aima/app_proxy/trro_push_message`、`/aima/mc/locomotion/velocity`、`/aima/mc/body_pose`；订阅 29 路（App 所需的全部状态镜像）；服务端：`ConnectRobot`、`DisconnectRobot`、`JsonWebCheck`（App 接入鉴权三件套）
- `/app_proxy_client` 客户端：`GetCurrentMap`、`SetAppConfig`

**`/rc`（aima-rc-app，rk）**：发布 `mc/locomotion/velocity`、`mc/body_pose`、sm/node_state；服务端 `SmNodeService/rc/…`；客户端 `SetMcAction`、`GetMcAction`、`SetMcMotion`、`SetMcPresetMotion`、`GetMcPresetMotionState`、`ControlTask`

**`/ota_master`（rk）**：发布 OTA 心跳 proto 通道、sm/node_state、trace；订阅 bms(×2)、network、mc/common/state、sm/system_state；服务端 `SmNodeService/ota_master/…`；客户端 `AmRequest`、`ControlPowerSave`、`GetBatteryInfo`、`GetSystemState`、`LedStripCommand`、`MigrateSystemState`、`PlayAnimation`、`PlayTts`、`SetMcAction`、`SetPowerDown`

**`/lumina_server`（rk）**：订阅 asr_result、face_info、play_state、bms、network、touch、interaction/state、tts_status、bluetooth 共 9 路做灯语联动；发布 trace/events；客户端 13 路（PlayTts、PlayEmotion、PlayAnimation、PlayMove、PlayLight、PlayAudioFile、PlayCustomMotion、SetMode、Get/SetVolume、StopAudioPlay、AmCancel、TouchEnable）

**`/picture_service`（rk，resource_manager）**：服务端 `GetPictureList`、`GetPicture`、`DeletePicture`、`GetResourcePath`、`GetCustomMotionList`、`DeleteResource`、`RenameResource`、`PlayPhoto`、`SmNodeService/resource_manager/…`；客户端 `CaptureJpegImage`、`ControlInsta360`、`PlayTts`；发布 `resource/update`、sm/node_state、trace

**`/insta360_ros2_node` + `/insta360_gimbal_tts`（rk）**：发布 `insta360/state`、`task_state`、`track_frame`、`cmd`、`robot_control`、`mc/locomotion/velocity`；服务端 `AssignInsta360Task`、`ControlInsta360`、`GetInsta360WiFiInfo`；gimbal_tts 客户端 `PlayTts`、`SetMcAction`

**`/quanji_uwb_ros2_node`（rk）**：发布 `quanji/uwb`、`imu`、`radar`、`audio_frame`、`state`；服务端 `ControlQuanji`

**`/legged_odom_node`（rk）**：发布 `/odom`、`/odom_torso`、`/odom/status`、`/odom/trajectory`、`/tf`、`torso_imu_orientation`、`upper_body_joint_states`、sm/node_state；订阅 `mc/common/state`；服务端 `SmNodeService/aima-legged_odom-app/…`

**`/cloud_proxy`（rk）**：发布 `resource/update`、diagnostic、sm/node_state；订阅 7 路（network、alert、interaction、mc 状态、task_status、trace）；客户端 `ControlTask`

**`/record_ros2_node_3765`（drp 录包，rk）**：订阅 `mc/common/state`、`mc/locomotion/velocity`、`mc/body_pose`、`bms/state` 落 rosbag；服务端 `SmNodeService/drp/…`

**`/data_exporter`（soc0 实例，root）**：服务端 `soc0/async/Create|CancelAsyncTask`、`soc0/sync/CreateSyncTask|GetTaskStatus`、`soc0/cloud/platform/*`（GetJWT、GetUploadPath、CombineFileSlices、UnbindProject）、`soc0/cloud/result/Upload*`；客户端跨板调用 soc1 同名服务

**`/device_manager_node`（rk）**：发布 diagnostic；客户端 `GetJointPowerState`

**`/sys_guard_node_soc0`（rk）**：发布 `sys_guard/active_scene`、diagnostic、sm/node_state；订阅 sm/system_state；服务端 `SmNodeService/sys_guard/…`

### 4.4 各节点完整接口挂载（orin 侧节点）

**`/coreon_fusion_ros2_node`（coreon_fusion）— 感知融合枢纽（S27）**
- 发布：`/aima/coreon_fusion/state`（融合/避障状态）★、`/perception/percep_fusion/point_cloud`、`/perception/target_info`、`mc/locomotion/velocity`、diagnostic、sm/node_state、trace
- 订阅（27 路）：5 路 ToF sensor_info、5 路 pmd_royale 点云、metoak 相机 3 路、orin 相机图像 5 路（bgr_centra、fisheye×2、stereo×2）、torso IMU/odom×2、filtered_point_cloud、occlusion_status、fall_guard/edge_xs、interaction/state、mc/locomotion/velocity
- 服务端：`ObstacleCheck`、`SetAvoidance`、`GetSceneList`、`GetSceneState`、`SceneCommand`、`SmNodeService/coreon_fusion/…`
- 客户端：`GetAndPlayNlgText`

**`/perception`（percep）**：发布 `/percep/local_costmap`（语义局部代价图）、`percep/cloud`、`fused_obstacle_cloud`、`tof/{main,front,rear}/point_cloud`、`detect/env`（室内外）、`detect/grip`、`stair_detection`、`stereo_right/seg_mask`；订阅 `mc/common/state`、`odom`、`follow_brightness_dark`、`follow_camera_lift_angle`、`target_info`；服务端 `/perception/enable`（SetBool）

**`/hal_rgbd_node` + `/hal_rgbd_node_0`（hal_rgbd）**：发布 5 路 `ofilm/tof/*/sensor_info`（RgbdInfo：曝光/温度）、`ofilm/tof/inter_board`、diagnostic、sm/node_state；订阅 10 路 `ofilm/tof/*/raw_record|rrf_record`（录制开关）；服务端 `SetHalTofStream`、`SmNodeService/hal_rgbd/…`

**`/hal_camera` + `/hal_camera_1`（hal_camera orin）**：`/hal_camera` 发布 `hal/camera/module_info`、`soc_sync/ready`；服务端 `CaptureJpegImage`★、`HALCameraCommand`；`/hal_camera_1` 服务端 `SetHalCameraStream`；两者共同订阅 `permission_state`（相机权限门控）、`mc/common/state`

**`/aimrt_person_track_node` + `_srv`（person_track）**：发布 `perception/follow_candidates`（跟随候选）★、`perception/target_info`（Detection2DArray）、`follow_camera_lift_angle`、3 路调试（debug_cloud/image/markers）；订阅 `mc/common/state`、`odom`、`enable_follow`、`follow_target_selection`；服务端 `/perception/set_follow_perception_task`；客户端 `SetServo`（相机抬升）

**`/aimrt_brightness_status_node` + `_srv`**：发布 `perception/follow_brightness_dark`（亮度门控）；订阅 `hal/camera/head_stereo_left`；服务端 `/perception/set_brightness_enable`

**Nav2 栈（`ros2 launch nav2_bringup start_real_navigation.launch.py`）**：
- `/robot_navigation`（对外导航封装）：发布 `aima/algo/navi/state`★、`navi/relocate/state`★、`mc/locomotion/velocity`；服务端 `/navi/command`（SetNaviTask）★、`/navi/relocate`（SetNaviRelocate）★；客户端 lifecycle_manager/manage_nodes
- `/map_server`：发布 `grid_map_origin`、`keepout_zones`；服务端 `/navi/loadmap`（LoadNaviMap）★、`load_map`、`map`、生命周期组；客户端 `/navi/command`
- `/bt_navigator`（订阅 goal_pose/odom/tf）、`/controller_server`（发布 cmd_vel 汇聚点 `mc/locomotion/velocity`、local_plan、cost_cloud 等）、`/planner_server`（发布 plan、goal_blocked_by_obstacle）、`/behavior_server`（恢复行为：look_ahead、ray_scan）、`/smoother_server`（plan_smoothed）、双层 costmap（`global_costmap/global_costmap`、`local_costmap/local_costmap`，各带 clear_around/except/entirely + get_costmap 服务）、`/lifecycle_manager_navigation`（bond 看护 + manage_nodes/is_active）、`/nav2_container`（组件容器，当前未加载组件）
- 4×static_transform_publisher：`map_to_odom_tf_publisher`、`camera_to_base_tf_publisher`、`front_tof_to_combined_tf_publisher`、`rear_tof_to_combined_tf_publisher`（发布 `/tf_static`）

**`/follow_node`（follow）**：发布 `aima/algo/follow/state`★、`mc/locomotion/velocity`、`enable_follow`、`follow_target_selection`、语义调试 4 路；订阅 `follow_node/command`；服务端 `SetFollowTaskBiped`

**`/trajectory_tracker`（path_follow_node）**：发布 `aima/motion/intelligent_task_state`（环绕运镜状态）★、`global_trajectory_path`、`trajectory_tracker/status`、`enable_follow`、`mc/locomotion/velocity`；订阅 `perception/target_info`、`pointcloud_filter`；服务端 `SpawnIntelligentMotionTask`★

**`/pointcloud_combiner_node` + `/perception_node` + `/lifecycle_manager_t1_perception`（nav2_perception）**：合并前/后 ToF 点云 → `percep/tof/combined/point_cloud`；`perception_node` 发布 `pointcloud_filter`、订阅 `hal/joint/state`（关节状态，二开低层反馈）与合并点云，受 lifecycle 管理

**`/hal_sensor_soc1`（orin）**：发布 `hal/adu/state`（ADU 电源板状态）、`hal/thermal/soc1`（orin CPU 温度）、diagnostic；服务端 `SetPowerDownOrin`（orin 侧关机）

**`/ota_slave`（orin）**：发布 sm/node_state、trace；服务端 `SmNodeService/ota_slave/…`

**`/sys_guard_node_soc1`（orin）**：同 soc0 结构（active_scene、diagnostic、sm/node_state）

**`/trro_recv_dedicated`（实证：App 通道接收器，无独立进程＝app_proxy 域进程内节点）**：订阅 `aima/app_proxy/trro_pull_message`（App 通道专用接收器）

**`/HK_ros2_rpc_node`（实证 rk 管家/云属性桥，2026-10-31 node info 复核）**：服务端 `aimomp.protocol.DevicePropertyService/SetProperty`；客户端 `aimomp.protocol.CloudPropertyService/ReportProperty`（proto RPC 包装：设备属性设置→上报云端）

**辅助节点**：`/launch_ros_1692`（launch 进程自身）、8×`/transform_listener_impl_*`（各进程内 tf2 监听辅助节点）、`/bt_navigator_navigate_to_pose_rclcpp_node`（bt_navigator 内部桥）——非业务节点。


---

<a id="sec5"></a>

## 5. ROS 2 接口全貌：198 话题 / 480 服务实测清单与明细

### 5.0 图概况与统计

| 统计项 | 数值 | 说明 |
|---|---|---|
| `ros2 node list` | **69** | 双板合计（含 8 个 transform_listener 辅助、1 个 launch_ros、1 个空容器等非业务节点） |
| `ros2 topic list` | **198** | 双向（有发有订）86；仅发布 65；**仅订阅 47（= 二次开发入口或发布者离线）** |
| `ros2 service list` | **480** | aimdk 业务服务 **147** + proto 包装 RPC **58** + 参数标准服务 210（35 节点×6）+ 生命周期服务 ~44 + nav2 地图/代价图/管理 ~16 + 其他 |
| 域/中间件 | domain 0 / `rmw_fastrtps_cpp` | FastDDS 配置：`/robot/software/entry/cfg/standard/ros_dds_configuration.xml` |

**接口表约定**：
- 话题（Topic）无入参出参之分，表中「入参」= 发布方需填写的**负载字段**（.msg 定义）、「出参」= 订阅方读到的同构负载 + 主要消费方；
- 服务（Service）「入参」= Request 字段、「出参」= Response 字段；
- ★ = SDK 官方对外接口（《接口说明.md》30 项面）；未标★为内部接口，**非 SDK 承诺面，跨版本可能变动，慎依赖**；
- 所有 aimdk 服务共用统一响应模型（`CommonResponse`/`CommonTaskResponse` + `CommonState` 状态码与 reason 枚举），详见 §5.4，表中不逐条重复。

### 5.1 Topic 接口全表（198 项，按模块分组）

#### 5.1.1 运动控制与底盘

| 接口 | 类型 | 中文名 · 简介 | 入参（发布方填写） | 出参（订阅方读取） | 注意事项 |
|---|---|---|---|---|---|
| `/aima/mc/locomotion/velocity` ★ | McLocomotionVelocity | **走跑控制指令**（全图中枢话题：11 发布者/6 订阅者） | `source` 输入源名(rc/vr/app_proxy/interaction/pnc)；`forward_velocity` 前进 m/s（+前−后）；`lateral_velocity` 侧移 m/s（+左−右）；`angular_velocity` 角速度（+左旋）；`pitch_velocity` 弯腰角速度；`level` 档位 | 同负载；mc 网关按输入源优先级仲裁后送运控 | ★官方；**需先经 `SetMcInputSource` 抢占输入源**；多源并发时高优先级者生效（timeout 默认 1000ms，超时自动回落）；建议 10Hz+ 持续发布 |
| `/aima/core/locomotion/velocity` | McLocomotionVelocity | task_engine→运控的内部速度通道 | 同上 | 同负载（mc 侧订阅） | 内部通道，与上条并行，二开勿用 |
| `/aima/mc/common/state` ★ | McCommonState | **运控全量状态**（输入源/Action/形态/FSM/姿态/提起/牵手） | `header`；`input_source`（name/priority/timeout）；`action_info`（current_action/source/status/switch_ready）；`runtime_model`（腰/左右手状态）；`motion_status`（player_state/control_area/motion/进度）；`fsm_state`；`form_state`（四足/双足）；`posture`（roll/pitch/yaw）；`is_moving`；`is_fallen`；`lift_state`（提起检测概率/阈值）；`task_info`（task_id）；`held_state`（牵手状态） | 同负载；全图 10 个订阅者（task_engine/follow/rc/感知/ota…） | 权威状态源：判断可下发动作前先看 `action_info.switch_ready`；`McActionStatus` 的 100=RUNNING（≠CommonState 语义） |
| `/aima/mc/body_pose` | McBodyPose | 躯干姿态控制（站立高度/弯腰/侧弯/转头） | `header`；`source`；`stand_height` 站高；`bend_forward` 前弯；`bend_sideways` 侧弯；`twist_waist` 转腰；`raise_head` 抬头；`turn_head` 转头 | 同负载；rc 与 app_proxy 发布、drp 记录 | 姿态叠加量，量纲见官方运控文档；二开走 rc 同款通道需输入源授权 |
| `/aima/mc/custom/joint/command` ★ | McCustomJointCommand | **自定义上肢关节控制指令**（二开低层入口，当前仅订阅） | `header`；`joints[]`：JointCommand（关节名/位置/速度/力矩等，详见 SDK 低层接口章） | mc 网关消费 | ★官方；仅 `BIPED_CUSTOM_UPPER`(1014) 等动作模式下生效；频率与安全限位见 SDK §2.1.2 |
| `/aima/hal/joint/state` ★ | JointStateArray | **关节状态反馈数组**（当前仅订阅、发布者离线） | `header`；`joints[]`：JointState（`name`；`position`；`velocity`；`effort`；`coil_temp` 线圈温度；`motor_temp`；`motor_vol`） | perception_node、nav2 链路订阅 | ★官方；当前由运控侧按需发布（图上暂无发布者，订阅端就绪）；高频大数组，注意 QoS |
| `/cmd_vel` | geometry_msgs/Twist | Nav2 标准速度话题（仅订阅，当前 nav2 未使用该通路） | `linear.x/y/z`；`angular.x/y/z` | 双层 costmap 订阅 | T1 实际速度走 `mc/locomotion/velocity`；`/cmd_vel` 为 nav2 兼容保留 |
| `/upper_body_joint_states` | sensor_msgs/JointState | 上肢关节状态（legged_odom 发布，rviz/调试用） | 标准 JointState（name/position/velocity/effort） | 调试/RViz | 调试用途 |
| `/odom/status` | std_msgs/UInt8 | 里程计状态码 | `data` 状态码 | task_engine 订阅 | 内部 |
| `/odom/trajectory` | nav_msgs/Path | 里程计轨迹（建图回显） | `header`；`poses[]` PoseStamped | 调试/RViz | 调试用途 |
| `/aima/motion/intelligent_task_state` ★ | IntelligentMotionTaskState | **环绕运镜任务状态** | `task_id`；`action_type`（1 后退2.5m/2 半径2m环绕/3 直线后退6m）；`task_running`；`task_finished`；`task_failed` | 同负载；task_engine 订阅 | ★官方（SpawnIntelligentMotionTask 的状态回传） |
| `/aima/extreme_action/status` | ExtremeActionStatus | 极限动作（空翻等）授权与限位状态 | `schema_version`…`permission_level`；`speed_limit_mps`；`recent_30d_count/limit`；`lifetime_count/limit`；`cloud_sync_status` | app_proxy 订阅（App 展示） | 内部；含云端配额同步语义 |

#### 5.1.2 交互与 Agent（语音/音频/表情/人脸/任务）

| 接口 | 类型 | 中文名 · 简介 | 入参（发布方填写） | 出参（订阅方读取） | 注意事项 |
|---|---|---|---|---|---|
| `/aima/agent/asr_result` ★ | AsrResult | **语音识别结果** | `header`；`text` 识别文本；`event_id`；`is_final` 是否终稿；`confidence` 置信度；`language` | 同负载；task_engine/lumina 订阅 | ★官方；流式语义：`is_final=false` 为中间结果 |
| `/interaction/tts_status` ★ | TtsStatus | **TTS 播报状态** | `text`；`priority` 优先级；`trace_id`；`tts_status` 状态机（播放中/完成/失败）；`domain`；`error_message` | 同负载；Agent/task_engine/lumina 等 4 订阅 | ★官方；打断/完成判定以 `trace_id` 对齐 |
| `/aima/agent/face_info` | FaceInfoArray | 人脸信息数组（识别到的人脸+face_id） | `header`；`list[]` FaceInfo | 同负载；Agent/agent 侧 3 订阅 | 内部（Agent 由相机/识别链路生成） |
| `/aima/agent/greeting_state` | GreetingState | 迎宾问候触发状态 | `header`；`state`（1=触发）；`face_id`；`name` | app_proxy 订阅 | 内部 |
| `/aima/agent/interaction_state` | AgentConversationState | Agent 会话主/子状态 | `main_state`；`sub_state`；`event_id`；`trace_id` | task_engine 订阅 | 内部 |
| `/aima/agent/biologic_state` | BiologicalEntryState | 生物录入（声纹/人脸注册）进度 | `header`；`stage`（0-6：空闲→人脸→姓名→声纹→上传）；`state`（0-7 含异常）；`reason` | task_engine/app_proxy 订阅 | 内部 |
| `/aima/agent/business_agent_control_state` | BusinessAgentControlState | 业务 Agent 控制状态透传 | `header`；`agent_name`；`payload_json` | app_proxy 订阅 | 与 `BusinessAgentControl` 服务配对 |
| `/aima/agent/semantic_intent` | SemanticIntent | 语义意图（LLM 结构化输出） | `header`；`type` 意图类型；`value` | task_engine 订阅 | 内部 |
| `/aima/agent_show_text` | AgentShowText | Agent 上屏文本（流式） | `msg_type`（1开始/2结束/3内容/4单条）；`session_id`；`language`；`text` | task_engine/app_proxy 订阅 | 内部；App 气泡渲染数据源 |
| `/agent/process_audio_output` | ProcessedAudioOutput | 处理后音频流（回声消除后） | `header`；`stream_id`；`audio_vad_state`；`audio_data[]` PCM | 外部音频消费（当前仅发布） | 高带宽；音频调试用 |
| `/agent/task_operation` | TaskOperation | Agent 任务操作事件（CRUD） | `header`；`msg_type`；`operator_type`（1建/2读/3改/4删/5完成）；`agentflow_id`；`msg_content` | task_trigger 订阅 | 内部 |
| `/aima/interaction/state` ★ | InteractionState | **交互长任务状态**（当前 interaction/task_state + 形态/模式） | `header`；`interaction_id`；`mp_state` 上肢动作状态；`form_state`；`action_info`；`mode`（0室内/1户外/2超能）；`mode_speed`；`task_state`（长任务类型枚举 1-12：跟随/建图/定位/运镜…） | 同负载；全图 6 订阅 | ★官方；`mode` 与 `SetMode` 服务联动 |
| `/aima/interaction/task_status` | AmStatus | 任务模块函数级状态 | `function_name`；`function_status`（0未知/1运行/2停止）；`json_data` | app_proxy 订阅 | 内部 |
| `/aima/display_state` | DisplaySate | 胸屏当前表情 ID | `header`；`emoticon_id` | task_engine 订阅 | 内部（屏侧回显） |
| `/aima/hal/audio/audio_device_list` ★ | AudioDeviceInfoList | **音频设备列表** | `input_list[]`/`output_list[]`：AudioDeviceInfo | task_engine 订阅 | ★官方（查询输入/输出设备） |
| `/aima/hal/audio/play_state` | PlayStateChange | 播放状态变化 | `pkg_name` 来源包名；`state` PlayStateType | Agent/task_engine 等 2 订阅 | 与 `PlayAudioFile`/音乐播放联动 |
| `/aima/hal/audio/music_state` | MusicState | 音乐播放全量状态（歌曲/进度/音质/VIP） | `control_command_id`；`playback_state`(0-5)；`song_name`；`artist[]`；`total_duration/current_time`；`vip_song/trail`；`qualities[]`；`playback_index`… | Agent/task_engine 订阅 | 内部；与 `ControlMusic`/`SearchMusic` 配对 |
| `/aima/hal/audio/volume` | VolumeChange | 音量变化通知 | `type` VolumeType；`audio_volume` | 2 订阅 | 与 `SetVolume` 联动 |
| `/aima/hal/audio/mute` | MuteChange | 静音变化通知 | `mute` bool | task_engine 订阅 | 与 `SetMute` 联动 |
| `/aima/hal/audio/mic_source` | MicSourceChange | 麦克风源切换通知 | `mic_source`；`pid`；`vid` | 外部（仅发布） | 与 `SetMicSourceRequest` 联动 |
| `/aima/hal/audio/event_probe` | EventProbeInfoArray | 音频事件探针（埋点） | `list[]` EventProbeInfo | 外部（仅发布） | 诊断用途 |
| `/aima/robot/permission_state` | PermissionState | 隐私权限状态（麦克风/相机/定位） | `microphone`；`camera`；`location`；`updated_timestamp` | 全图 5 订阅（hal_camera/rgbd 等门控） | 内部；权限关闭时相机节点停流 |
| `/robot/scan/bluetooth` | BluetoothInfoArray | 蓝牙扫描结果 | `header`；`list[]` BluetoothScannedInfo | lumina/app_proxy 订阅 | 与 `CtrlScanBluetooth` 配对 |

#### 5.1.3 跟随链路

| 接口 | 类型 | 中文名 · 简介 | 入参（发布方填写） | 出参（订阅方读取） | 注意事项 |
|---|---|---|---|---|---|
| `/aima/algo/follow/state` ★ | FollowState | **跟随任务状态** | `state`（0空闲/1未发现目标/2跟随中/3丢失/4跟错/5太远/6太近/7受阻） | 同负载；task_engine 订阅 | ★官方 |
| `/perception/follow_candidates` ★ | PerceptionFollow | **跟随候选目标列表**（归一化框） | `header`；`perception_list[]`（`id`；`x,y` 左上角归一化坐标；`w,h` 归一化宽高） | task_engine/app_proxy 订阅（follow_node 经 `follow_target_selection` 接收选定目标） | ★官方；坐标是**图像归一化比例**非米制 |
| `/perception/follow_brightness_dark` | BrightnessStatus | 跟随亮度门控（过暗禁跟随） | `allow_follow`；`brightness` 灰度均值 0-255 | perception/task_engine 订阅 | 由 `set_brightness_enable` 服务开关 |
| `/perception/follow_camera_lift_angle` | std_msgs/UInt8 | 相机抬升角度档位 | `data` 角度档 | perception/person_track 链路 | 内部 |
| `/follow_node/enable_follow` | std_msgs/Int8 | 跟随使能（person_track↔follow 联动） | `data` | person_track 订阅 | 内部 |
| `/follow_node/follow_target_selection` | std_msgs/String | 跟随目标选择 | `data` 目标 id | person_track 订阅 | 内部 |
| `/follow_node/command` | std_msgs/Int8 | 跟随控制命令（仅订阅，外部可发） | `data` 命令码 | follow_node 订阅 | 简易控制入口（非 SDK 承诺） |
| `/follow/viz/*`、`/follow/diag/semantic_costmap`、`/follow/transform_event` | Marker/OccupancyGrid/String 等 | 跟随可视化/诊断 4 路 | 标准可视化消息 | RViz（仅发布） | 调试用 |

#### 5.1.4 导航 · 建图 · 定位

| 接口 | 类型 | 中文名 · 简介 | 入参（发布方填写） | 出参（订阅方读取） | 注意事项 |
|---|---|---|---|---|---|
| `/aima/algo/navi/state` ★ | NaviState | **导航任务状态** | `state`（0空闲/1初始化/2导航中/3暂停/4到达/5失败/6取消/7低功耗/8初始化失败）；`goal` 当前目标 PoseStamped；`description` | 同负载；map_server/task_engine 订阅 | ★官方 |
| `/aima/algo/navi/relocate/state` ★ | NaviRelocateState | **导航重定位状态** | `state`（0空闲/1初始化/2移动/3暂停/4取消/10失败/11初始化失败）；`description` | task_engine 订阅 | ★官方 |
| `/navi/command`（服务 `/navi/command` ★ 同名） | — | 见 §5.2 服务表 | — | — | — |
| `/odom` ★相关 | nav_msgs/Odometry | **轮式/足式里程计**（legged_odom 发布） | `header`；`pose`（含 covariance）；`twist` | 全图 7 订阅（bt_navigator/controller/costmap/follow/感知） | 标准消息；坐标系 odom |
| `/odom_torso` | nav_msgs/Odometry | 躯干（上半身）里程计 | 同上 | coreon_fusion 订阅 | 内部 |
| `/torso_imu_orientation` | nav_msgs/Odometry | 躯干 IMU 航向（以里程计形式表达） | 同上 | coreon_fusion 订阅 | 内部 |
| `/aima/hal/imu/torso/state` | sensor_msgs/Imu | 躯干 IMU 原始数据（仅订阅、发布者离线） | 标准 Imu（角速度/线加速度/姿态） | coreon_fusion 订阅 | 内部 HAL 通道 |
| `/aima/hal/imu/imu_head` | sensor_msgs/Imu | 头部 IMU（**rk** hal_camera/BMI08 发布，跨板可见；orin 配置中已注释） | 标准 Imu | 外部（仅发布） | 内部 |
| `/tf`、`/tf_static` | tf2_msgs/TFMessage | 坐标变换树（map→odom 静态、base_link→相机/ToF 静态、odom→base_link 动态） | TFMessage（transforms[]） | 全图 9 订阅 | 4 个静态发布器 + legged_odom 动态发布 |
| `/goal_pose` | geometry_msgs/PoseStamped | 导航目标点（仅订阅；nav2 标准入口） | `header`；`pose` | bt_navigator 订阅 | T1 业务侧走 `/navi/command` 服务 |
| `/grid_map_origin` | nav_msgs/OccupancyGrid | 当前地图原点/元信息（map_server 发布） | 标准 OccupancyGrid | robot_navigation、costmap 订阅 | 内部 |
| `/plan`、`/plan_smoothed`、`/local_plan`、`/global_trajectory_path`、`/received_global_plan`、`/transformed_global_plan` | nav_msgs/Path | 全局/局部/平滑/跟踪路径 6 路 | `header`；`poses[]` | RViz/下游控制 | nav2 标准可视化链路 |
| `/global_costmap/costmap[_raw|_updates]`、`/local_costmap/costmap[_raw|_updates]` | OccupancyGrid / nav2_msgs/Costmap / OccupancyGridUpdate | 全局/局部代价图（原始+增量） | 标准代价图消息 | behavior_server、coreon_fusion、RViz | nav2 标准 |
| `/percep/local_costmap` | PercepCostmap | **感知语义代价图**（occupancy+语义类别+可通行代价） | `header`；`width/height/resolution/origin`；`robot_form`；`occupancy[]`(-1未知/0-59自由/60-100占)；`semantic_class[]`；`semantic_source[]`(1图像语义/2几何/3融合)；`traversability_cost[]` | nav2 规划链路 | 内部；以 odom 锚定的滚动图 |
| `/goal_blocked_by_obstacle` | std_msgs/Bool | 目标点被障碍封堵 | `data` | behavior_server 订阅 | 内部 |
| `/speed_limit` | nav2_msgs/SpeedLimit | 速度限制（仅订阅） | `percentage`/`max_speed` | controller_server 订阅 | nav2 标准 |
| `/keepout_zones` | nav_msgs/Path | 禁行区（map_server） | `poses[]` | RViz | 内部 |
| `/aima/map/state` | MapState | 当前地图切换 | `header`；`current_map` | task_engine 等订阅 | 内部 |
| `/aima/map/build_state` | MapBuildState | 建图任务状态（阶段/进度/轨迹/PNG） | `stage`(0未知/1建图/2保存/3取消)；`state`(0-3)；`mapping_type`(1手动/2自动探索)；`task_id`；`map_info`；`png_data[]`；`trajectory[]` | task_engine/app_proxy 订阅 | 内部；与 `MapCommand` 服务配对 |
| `/aima/map/localization/state` | MapLocalizationTaskState | 定位任务状态 | `task_id`；`map_id`；`state`(0-4)；`message` | app_proxy 订阅 | 内部 |
| `/aima/map/charge_point/set_state` | ChargePointTaskState | 专座（充电点）搜索任务状态 | `task_id`；`map_id`；`state`(0-4)；`message` | app_proxy 订阅 | 与 `SetChargePointTask` 配对 |
| `/aima/map/charge_point/navi_sit_state` | ChargePointSitTaskState | 对座/入座任务状态 | 同上结构 | app_proxy 订阅 | 与 `SetChargePointSit` 配对 |
| `/aima/algo/charge_point/recognition_state` | ChargePointRecognitionState | 专座识别算法状态（仅订阅） | `state`(0-5)；`has_result`；`charge_pose`；`charge_front_pose` | task_engine 订阅 | 算法→引擎回报通道 |
| `/aima/algo/charge_point/sit_state` | ChargePointSitState | 对座算法状态（仅订阅） | `state`(0-12 含占用/路径阻塞/对座失败)；`ready_to_sit` | task_engine 订阅 | 算法→引擎回报通道 |
| `/slam_state` | SlamState | SLAM 状态（仅订阅） | `header`；`state_msg` | task_engine 订阅 | 内部 |
| `/aima/sm/system_state` | SmSystemState | **整机状态机当前状态**（如 startup_check/idle/…） | `header`；`cur_state`；`cur_status`；`next_state` | 全图 8 订阅 | 系统级门控：多数服务在非就绪态返回 NOT_READY |
| `/aima/hds/alert_code_list` | AlertCodeArray | 告警码列表（HDS 汇总） | `header`；`list[]` AlertCode | task_engine/hds/cloud/app 订阅 | 与 `GetAlertCodeJsonFile` 配对 |
| `/aima/hds/diagnostic_list` | DiagnosticInfoArray | 诊断信息（14 个模块上报） | `header`；`diagnostics[]` Diagnostic | hds 汇总 | 内部健康总线 |
| `/diagnostics` | diagnostic_msgs/DiagnosticArray | ROS 标准诊断（lifecycle_manager×2） | 标准 | ROS 生态工具 | nav2 侧 |

#### 5.1.5 感知算法输出（orin）

| 接口 | 类型 | 中文名 · 简介 | 入参（发布方填写） | 出参（订阅方读取） | 注意事项 |
|---|---|---|---|---|---|
| `/aima/coreon_fusion/state` | CoreonFusionState | 融合感知/避障状态 | `header`；`is_avoidance` 是否避障中；`source` 触发源；`obstacle_info` 障碍信息 | task_engine/app_proxy 订阅 | 内部；跟随/导航避障联动信号 |
| `/perception/target_info` | vision_msgs/Detection2DArray | **检测目标数组**（person_track 与 coreon_fusion 双发布） | 标准 2D 检测（id/bbox/score/hypothesis） | trajectory_tracker、coreon_fusion 订阅 | 同名双发布者，消费方需按时间戳甄别 |
| `/percep/local_costmap` | PercepCostmap | 语义代价图（见 5.1.4） | 见 5.1.4 | nav2 链路 | — |
| `/percep/cloud` | sensor_msgs/PointCloud2 | 主感知点云 | 标准点云 | RViz（仅发布） | 调试/录包 |
| `/percep/fused_obstacle_cloud` | PointCloud2 | 融合障碍点云 | 标准点云 | 仅发布 | — |
| `/percep/tof/main\|front\|rear/point_cloud` | PointCloud2 | ToF 点云（percep 转发 main/front/rear） | 标准点云 | pointcloud_combiner 订阅 front/rear | — |
| `/percep/tof/combined/point_cloud` | PointCloud2 | 前+后 ToF 合并点云 | 标准点云 | perception_node 订阅 | — |
| `/percep/detect/env` | std_msgs/Int32 | 环境判定（0 室内 / 1 室外 / 2 判定中） | `data` | task_engine 订阅 | 与室内/超能模式联动 |
| `/percep/detect/grip` | std_msgs/UInt8 | 抓取检测 | `data` | 仅发布 | — |
| `/percep/stair_detection` | std_msgs/Int32 | 楼梯检测 | `data` | 仅发布 | — |
| `/percep/stereo_right/seg_mask` | sensor_msgs/Image | 右目语义分割掩码 | 标准 Image | 仅发布 | 高带宽调试流 |
| `/perception/percep_fusion/point_cloud` | PointCloud2 | 融合点云（coreon_fusion 输出） | 标准点云 | RViz（仅发布） | — |
| `/pointcloud_filter` | PointCloud2 | 过滤后点云（perception_node 输出） | 标准点云 | trajectory_tracker 订阅 | 运镜避障输入 |
| `/filtered_point_cloud` | PointCloud2 | 过滤点云（仅订阅） | 标准点云 | coreon_fusion 订阅 | 内部 |

#### 5.1.6 传感器原始数据流（HAL → 算法）

| 接口 | 类型 | 中文名 · 简介 | 入参（发布方填写） | 出参（订阅方读取） | 注意事项 |
|---|---|---|---|---|---|
| `/aima/hal/camera/bgr_centra` | sensor_msgs/Image | 中央彩色相机图像（BGR） | 标准 Image | coreon_fusion 订阅 | 大带宽；iceoryx 板内优先，跨板走 DDS |
| `/aima/hal/camera/head_stereo_left` | Image | 头部左目立体相机图像 | 标准 Image | 亮度检测订阅 | — |
| `/aima/hal/camera/head_stereo_left_orin` / `_right_orin` | Image | 头部双目（orin 处理流）左右目 | 标准 Image | coreon_fusion 订阅 | — |
| `/aima/hal/camera/head_fisheye_left_orin` / `_right_orin` | Image | 头部左右鱼眼（orin 流） | 标准 Image | coreon_fusion 订阅 | — |
| `/ofilm/tof/{main,front,rear,left,right}/sensor_info` | RgbdInfo | 5 路 ToF 模组信息 | `header`；`exposure` 曝光；`temperature` 温度 | coreon_fusion/融合链路订阅 | 与 `SetHalTofStream` 供电/出流控制配对 |
| `/ofilm/tof/inter_board` | std_msgs/Bool | ToF 板间同步标志 | `data` | hal_rgbd_node_0 与 rk exposure 订阅 | rk↔orin ToF 采集同步 |
| `/ofilm/tof/*/raw_record`、`/ofilm/tof/*/rrf_record`（×10） | Bool | 各 ToF 原始/RRF 录制开关（**仅订阅 = 二开可发**） | `data` | hal_rgbd 订阅 | 数据采集入口（录制约 /robot/persist） |
| `/pmd_royale_ros_camera_node[_0..4]/point_cloud_0`（×5） | PointCloud2 | PMD Royale ToF 相机点云（仅订阅） | 标准点云 | coreon_fusion 订阅 | 驱动节点未上图（iceoryx 桥） |
| `/metoak/camera/images/left`、`depth/left`、`point_cloud`（×3） | Image/PointCloud2 | Metoak 相机彩色/深度/点云（仅订阅） | 标准 | coreon_fusion 订阅 | 同上 |
| `/aima/hal/camera/module_info` | ModuleInfo | 相机模组信息（hal_camera×2 发布） | `header`；`module_id`；`info[]` KeyValue | 同步链路订阅 | — |
| `/aima/hal_camera/soc_sync/ready` | ModuleInfo | 双板相机同步就绪 | 同上 | hal_camera_1 订阅 | — |
| `/quanji/uwb` | QuanjiUwbData | 圈圈 UWB 测距/测角 | `distance`；`angle`；`pitch`；`rssi_uwb/rssi_ble`；`pos_confidence` | 仅发布（外部可订） | 「圈圈」随行配件定位 |
| `/quanji/imu` | QuanjiImuData | 圈圈 IMU（int16 原始+四元数） | `timestamp_ms`；`acc_x/y/z`；`gyro_*`；`qw..qz` | 仅发布 | — |
| `/quanji/radar` | QuanjiRadarData | 圈圈雷达（活体/触摸检测） | `living_body_detected`；`touch_detected` | task_engine/coreon_fusion 订阅 | — |
| `/quanji/audio_frame` | QuanjiAudioFrame | 圈圈音频帧（PCM 流） | `sequence`；`boundary`(0起/1中/2止)；`pcm[]`；`total/lost_packets`；`duration_ms` | 仅发布 | 高带宽 |
| `/quanji/state` | QuanjiState | 圈圈设备状态（UWB/IMU/OTA） | `timestamp`；`uwb_connected/started`；`imu_started`；`follow_started`；`ota_state/progress` | task_engine 订阅 | 与 `ControlQuanji` 服务配对 |
| `/insta360/state` | Insta360State | Insta360 连接状态 | `connected` | 仅发布 | — |
| `/insta360/task_state` | Insta360TaskState | 云台合影任务状态 | `task_mode`(1=合影)；`task_state`(0-2/100-104 初始化/搜索/调整/移动/拍摄) | task_engine/app_proxy 订阅 | 与 `AssignInsta360Task` 配对 |
| `/insta360/track_frame` | TrackFrame | 云台跟踪框（跟踪/候选 rect 集） | `timestamp_ms`；`track_state`(0-5)；`track_result`；`track_rects_{x,y,width,height}[]`；`candidate_rects_*` | app_proxy 订阅（App 框选） | 归一化坐标 |
| `/insta360/cmd` | ControlCmd | 云台控制命令 | `control_type`(10变焦/15设角/17移动)；`zoom_scale`；`gimbal_roll/pitch/yaw`；`gimbal_up_down/left_right` | insta360 节点自订阅 | 与 `ControlInsta360` 服务同构的话题版 |
| `/insta360/robot_control` | RobotControl | 云台相机按键事件 | `key_code`(0-24 电源/摇杆/变焦/模式…)；`key_value`(0-34 单击/多击/长按) | 仅发布 | — |

#### 5.1.7 HAL 状态与系统管理

| 接口 | 类型 | 中文名 · 简介 | 入参（发布方填写） | 出参（订阅方读取） | 注意事项 |
|---|---|---|---|---|---|
| `/aima/hal/bms/state` ★ | Bms | **电源管理数据**（电池） | `header`；`manufacturer/serial_number`；`protocol/hardware/software_version`；`power_request`；`status`；`voltage` mV 级；`current`；`power`；`temperature`；`remaining_capacity`；`remaining_capacity_percentage`；`cycle_count`；`cycle_total_capacity` | 同负载；全图 7 订阅（Agent/task_engine/ota…） | ★官方；低电量会触发功能门控（REASON_LOW_BATTERY） |
| `/aima/hal/adu/state` | Adu | ADU 电源分配板状态（多路电压/电流/风扇） | `header`；版本×3；`status_word`；`error_code`；`board_{12v,5v,3v3,1v2,1v8,3v8}_voltage`；`system_12v_current`；`ntc_temperature`；`fan1/2_{target,feedback}_speed` | 仅发布 | 内部（orin hal_sensor_soc1） |
| `/aima/hal/touch/state` ★ | TouchState | **头部触摸事件** | `header`；`sensor_id`；`event_type`（1-6 单击~六击 / 8 左滑 / 9 右滑） | 同负载；3 订阅 | ★官方；`TouchEnable` 服务可开关 |
| `/aima/hal/servo/state` ★ | ServoStatus | 舵机状态（相机抬升舵机） | `header`；`position` | task_engine 订阅 | ★官方（与 `SetServo` 配对） |
| `/aima/hal/neck_light/state` ★ | NeckLightState | 颈部灯光状态 | `header`；`enable`；`brightness` | app_proxy 订阅 | ★官方（`SetNeckLight`/`GetNeckLightState` 配对） |
| `/aima/hal/rgb/state` | RGBState | RGB 灯状态 | `header`；`display_mode`；`r/g/b`；`period` | task_engine 订阅 | 内部 |
| `/aima/hal/thermal/soc1` | SysInfo | 大脑板（soc1）CPU 温度 | `header`；`soc1_cpu_temperature` | rk hal_sensor 订阅 | 内部温控链路 |
| `/aima/hal/network/state` | NetworkState | 网络状态（WiFi/热点/蜂窝） | `header`；`connect_wifi_info`；`hotspot_device_list[]`；`cellular_state/operator/rsrp/network_type` | 全图 6 订阅 | housekeeper 发布 |
| `/aima/net/net_port_state` | NetPortState | 有线网口状态 | `state`（0 未知/1 无线/2 有线） | task_engine 订阅 | 内部 |
| `/aima/robot/basic_info` | RobotBasicInfo | 机器人基本信息（名称/蓝牙名/热点名） | `header`；`robot_name`；`bluetooth_name`；`hotspot_name` | app_proxy 订阅 | 与 `SetRobotName` 等配对 |
| `/aima/resource/update` | ResourceUpdate | 资源（图片/自定义动作/灵创）增删通知 | `update_type`(1建/2删/3容量满)；`resource_type`(1图片/2灵创/3自定义动作)；`update_item` | 3 订阅（picture_service/cloud_proxy/task_engine） | 内部 |
| `/aima/app/info` | AppHeartInfo | App 心跳（仅订阅） | `control_mode`；`app_connecting`；`is_joystick_ctrl` | task_engine 订阅 | App 在线/摇杆接管判定 |
| `/aima/app_proxy/trro_push_message` / `trro_pull_message` | TrroPushMessage | App↔机器人 TRRO 透传通道 | `data[]`（加密字节流） | 对向订阅 | 二进制透传，勿直写 |
| `/aima/battery_state/pb_…BmsState` | RosMsgWrapper | 电池状态 proto 包装通道（仅订阅） | protobuf 序列化负载 | ota_master 订阅 | proto 桥 |
| `/aimrte/sm/node_state/pb_…SmNodeStateChannel` | RosMsgWrapper | **SM↔各应用节点状态广播**（21 发布/2 订阅） | protobuf 包装 | sm/hds 汇聚 | AimRT proto 通道，非 SDK 面 |
| `/ota/gateway/heartbeat/pb_…OTAGatewayHeartbeatChannel` | RosMsgWrapper | OTA 网关心跳 | protobuf 包装 | 云端网关 | 内部 |
| `/x2robot/net/isfiveg` | IsFiveG | 5G 模组在位状态（仅订阅） | `header`；`five_g`；`five_g_available` | app_proxy 订阅 | 内部 |
| `/sys_guard/active_scene` | KeyValue | 当前活动场景（双板 sys_guard 发布） | `key`；`value`（JSON） | 2 订阅 | 资源调度依据 |
| `/robot/trace/events` | EventProbeInfoArray | 全链路埋点事件（9 模块发布） | `list[]` EventProbeInfo | cloud_proxy 汇聚上传 | 内部可观测性 |

#### 5.1.8 nav2 内部 / 调试 / 可视化（聚合）

| 接口（模式） | 类型 | 说明 | 注意事项 |
|---|---|---|---|
| `/*/transition_event`（10 个生命周期节点各一） | lifecycle_msgs/TransitionEvent | 生命周期状态迁移事件 | nav2 标准，调试用 |
| `/bond` | bond/Status | lifecycle 看护心跳（4 发 4 订） | nav2 标准 |
| `/cost_cloud`、`/evaluation`、`/marker`、`/forward_target_point` | PointCloud2 / LocalPlanEvaluation / MarkerArray / Marker | controller_server 调试输出 | RViz 用 |
| `/general_recover/look_ahead`、`/general_recover/ray_scan` | PointStamped / PointCloud2 | 恢复行为探测 | behavior_server |
| `/planner_server/jps_visual` | MarkerArray | JPS 规划可视化 | — |
| `/global_costmap/fov_polygon`、`published_footprint`、`/local_costmap/fov_polygon`、`published_footprint` | PolygonStamped | 视场多边形/脚印 | — |
| `/perception/follow_debug_cloud/_image/_markers`（×3） | PointCloud2 / Image / MarkerArray | 跟随算法调试流 | 高带宽，默认勿订阅 |
| `/follow/viz/cmd_vel`、`semantic_goal_resolution`、`semantic_planning_map`、`/follow/diag/semantic_costmap`、`/follow/transform_event` | TwistStamped / MarkerArray / OccupancyGrid / DiagnosticArray / String | 跟随可视化 5 路 | — |
| `/rosout`、`/parameter_events` | Log / ParameterEvent | ROS 标准日志/参数事件 | — |
| `/aima/hal/joint/command`（SDK 低层入口，**实机图上未出现**） | JointCommand[] | 关节控制命令（SDK §2.1.2 定义） | 当前无在线发布/订阅端；按 SDK 文档以 ~200Hz 方波规范发布 |

> **话题覆盖核对**：以上 8 组共 198 项（含按前缀聚合行），与 `ros2 topic list` 实测一一对应。

### 5.2 Service 接口全表（业务 147 项 + 平台内部 58 项 + 标准若干）

#### 5.2.1 运动控制（挂载 `/mc_ros2_node`，rk·CPU3）

| 接口 | 中文名 · 简介 | 入参（Request） | 出参（Response） | 注意事项 |
|---|---|---|---|---|
| `/aimdk_5Fmsgs/srv/SetMcAction` ★ | **设置运动模式/动作**（站起、躺下、变形、急停等） | `header`；`source` 触发源；`command.action.value`（McAction 枚举：1/2 被动·阻尼、3 四足躺、10-22 变形系列、100-124 四足动作、200-303 双足动作、400 转圈、1001-1014 Q 系列收储/走跑/模仿/站起/轻柔步态/越障/坐下/二开上肢）；`action_desc` | `response.header`；`task_id`；`state`（CommonState，见 §5.4） | ★官方；异步任务用 task_id 追踪；动作受白名单/形态/电量门控（reason 枚举）；不可打断动作返回 reason=28 |
| `/aimdk_5Fmsgs/srv/GetMcAction` ★ | **查询当前运动状态** | `header` | `header`；`info`（current_action/source/status/switch_ready） | ★官方；`status.value`：0 空闲/100 运行/200 切换中 |
| `/aimdk_5Fmsgs/srv/SetMcMotion` ★ | **设置全身动作**（模仿/自定义动作模型） | `header`；`source`；`type`(1 QY/2 灵创)；`motion` 动作名；`model`；`resource`；`interrupt` 可否打断；`operation`(1 预加载/2 执行) | `header`；`task_id`；`state` | ★官方；先 PRELOAD 再 EXECUTION 两段式 |
| `/aimdk_5Fmsgs/srv/GetMcMotions` | 查询已加载动作 | `header` | `response`；`motion[]` McMotion | 内部 |
| `/aimdk_5Fmsgs/srv/SetMcPresetMotion` ★ | **预设动作控制**（头/腰/左右手区域） | `header`；`source`；`area`（McControlArea 位掩码 1 左手/2 右手/4 头/8 腰）；`motion` McPresetMotion；`interrupt`；`ani_path` | `header`；`task_id`；`state` | ★官方 |
| `/aimdk_5Fmsgs/srv/GetMcPresetMotionState` | 查询预设动作状态 | `header.task_id` | `response`；`state` | 内部 |
| `/aimdk_5Fmsgs/srv/SetMcInputSource` ★ | **设置控制输入源**（抢占速度控制权） | `header`；`action`（McInputAction）；`input_source`（name/priority 0-100/timeout ms） | `header`；`task_id`；`state` | ★官方；**发 velocity 前必做**；优先级抢占制，timeout 到期自动释放 |
| `/aimdk_5Fmsgs/srv/GetCurrentInputSource` ★ | 查询当前输入源 | `header` | `response`；`input_source` | ★官方 |
| `/aimdk_5Fmsgs/srv/SetRobotRuntimeModel` | 设置运行模型（腰部/手部构型） | `header`；`runtime_model`（waist_status；left/right_hand_status 1000 灵巧手/2000 夹爪） | `header`；`task_id`；`state` | 内部（出厂构型） |
| `/aimdk_5Fmsgs/srv/GetRobotRuntimeModel` | 查询运行模型 | `header.task_id` | `response`；`runtime_model` | 内部 |
| `/aimdk_5Fmsgs/srv/McMotionRecord` | 上肢动作示教录制 | `header`；`record_area`(1 上肢/2 下肢/3 全身)；`operation_type`(1 开始/2 停止/3 暂停/4 继续)；`max_duration`；`folder_path`；`file_name` | `response` | 内部（与 task_engine 的 `RecordMotion` 分工） |

#### 5.2.2 语音 / 音频 / 屏幕交互（挂载 `/aimrt_agent_node` 与 `/audiomanager_ros2_node`，rk）

| 接口 | 中文名 · 简介 | 入参（Request） | 出参（Response） | 注意事项 |
|---|---|---|---|---|
| `/aimdk_5Fmsgs/srv/PlayTts` ★ | **语音合成播报** | `header`；`tts_req`（PlayTtsRequest：文本/优先级/trace_id/语速等） | `header`；`tts_resp`（PlayTtsResponse） | ★官方；完成态经 `/interaction/tts_status` 话题回报；打断用 `StopTtsTraceId` |
| `/aimdk_5Fmsgs/srv/GetTtsDuration` | 预估 TTS 时长 | `header`；`tts_req` | `header`；`text/domain/trace_id`；`estimated_duration` | 动画对齐用 |
| `/aimdk_5Fmsgs/srv/GetAudioStatus` | 查询 TTS 播报状态 | `header`；`text`；`trace_id` | `header`；`tts_status`（TtsStatus） | — |
| `/aimdk_5Fmsgs/srv/PlayMediaFile` | 播放媒体文件（音/视频混合任务） | `header`；`media_file_req` | `header`；`tts_resp` | — |
| `/aimdk_5Fmsgs/srv/GetAndPlayNlgText` | 生成并播报 NLG 文案 | `header`；`domain/scene_id`；`priority_level/weight`；`is_interrupted`；`trace_id`；`prefer_cloud_generation`；`filler_item_id`；`nlg_params[]`；`language_type` | `header`；`tts_resp` | 内部（Agent 话术系统） |
| `/aimdk_5Fmsgs/srv/SetTtsParameters` | 设置 TTS 参数（语速/音色/情感） | `header`；`tts_speed`；`voice_id`；`emotion` | `header` | — |
| `/aimdk_5Fmsgs/srv/StopTtsTraceId` | 按 trace_id 停止播报 | `header`；`trace_id` | `header` | — |
| `/aimdk_5Fmsgs/srv/StartVoiceInteraction` | 主动开启一轮语音交互 | `header`；`source_app`；`start_wakeup` | `header` | — |
| `/aimdk_5Fmsgs/srv/StartBiologicalEntry` | 启动生物录入（声纹/人脸） | `header`；`stage`(0 空闲/1 开箱/2 生物录入)；`trigger` | `header` | 进度看 `/aima/agent/biologic_state` |
| `/aimdk_5Fmsgs/srv/GetVolume` ★ | **获取音量** | `header` | `response`；`audio_volume` | ★官方 |
| `/aimdk_5Fmsgs/srv/SetVolume` ★ | **设置音量** | `header`；`audio_volume` | `response`；`audio_volume` | ★官方 |
| `/aimdk_5Fmsgs/srv/GetMute` ★ | **获取静音状态** | `header` | `response`；`is_mute` | ★官方 |
| `/aimdk_5Fmsgs/srv/SetMute` ★ | **设置静音** | `header`；`is_mute` | `response`；`is_mute` | ★官方 |
| `/aimdk_5Fmsgs/srv/PlayAudioFile` ★ | **播放音频文件** | `header`；`file`（AudioFile 路径等） | `response` | ★官方；另有 TCP 6001（PulseAudio simple）/4713（native）直连通道 |
| `/aimdk_5Fmsgs/srv/StopAudioPlay` | 停止播放 | `header` | `response` | — |
| `/aimdk_5Fmsgs/srv/SearchMusic` | 在线音乐搜索 | `header`；`query`；`sp`(0 QQ/1 喜马)；`size`(默认30)；`library_mode`(1 默认/2 儿童模式) | `response`；`search_command_id` | 结果经 `/aima/hal/audio/music_state` 异步回报 |
| `/aimdk_5Fmsgs/srv/ControlMusic` | 音乐播放控制 | `header`；`control_type`(1 播放/2 停/3 暂停/4 继续/5 上一首/6 下一首)；`play_index`；`no_auto_next` | `response`；`control_command_id` | — |
| `/aimdk_5Fmsgs/srv/GetMusicList` | 获取音乐列表 | `header` | `music_list[]`；`current_index`；`response` | — |
| `/aimdk_5Fmsgs/srv/GetMicSourceRequest` / `SetMicSourceRequest` | 麦克风源查询/设置 | `header`[；`mic_source`] | `header`[；`mic_source`] | 二开录音选路用 |
| `/aimdk_5Fmsgs/srv/GetSpkSinkRequest` / `SetSpkSinkRequest` | 扬声器 sink 查询/设置 | `header`[；`spk_sink`] | `header`[；`spk_sink`] | — |
| `/aimdk_5Fmsgs/srv/PlayEmotion` ★ | **播放表情/视频**（胸屏） | `header`；`type`；`emotion_ids[]`；`file_paths[]`；`priority`；`loop_count` | `header` | ★官方 |
| `/aimdk_5Fmsgs/srv/PlayEmotionByStr` | 按组名播表情 | `header`；`emotion_groups` | `header`；`success` | — |
| `/aimdk_5Fmsgs/srv/PlayDefaultEmoji` | 恢复默认表情 | `header` | `header`；`success`；`message` | — |
| `/aimdk_5Fmsgs/srv/SetFaceMode` | 屏幕表情模式（听/想/说） | `header`；`enable`；`mode`(1 听/2 想/3 说) | `header` | — |
| `/aimdk_5Fmsgs/srv/BusinessAgentControl` | 业务 Agent 控制指令透传 | `header`；`agent_name`；`payload_json` | `header` | 状态回看 business_agent_control_state 话题 |

#### 5.2.3 任务与仲裁（挂载 `/task_engine`，rk）

| 接口 | 中文名 · 简介 | 入参（Request） | 出参（Response） | 注意事项 |
|---|---|---|---|---|
| `/aimdk_5Fmsgs/srv/AmRequest` | **任务仲裁请求**（长任务统一入口） | `header`；`token_common`；`source_type`；`requester_module`；`am_mode`；`function_name`；`request_action_type/id`；`scene_tag`；`role_name` | `header`；`arbitration_result`(0-5：拒绝/覆盖/取消/二次确认/成功)；`arbitration_code`+`arbitration_reason`（66 个 reason 枚举，见 §5.4）；`extra_message`；`token` | 内部核心；同一时刻仅一个长任务（REASON_TASK_RUNNING） |
| `/aimdk_5Fmsgs/srv/AmCancel` | 取消长任务 | `header`；`token_common`；`source_type`；`cancel_reason`；`am_mode`(0 按动作 id/1 动画 id/2 任务名/3 资源类型/4 全部)；`cancel_target_id` | `header`；`arbitration_result`(1 已取消/2 失败)；`reason_message` | — |
| `/aimdk_5Fmsgs/srv/ControlTask` | 通用任务控制（起/停/暂停/取消×13 类任务） | `header`；`control_type`(1-4)；`task_type`(1 交互/2 跟随/3 文件/4 动画/5 移动/6 拍照/7 图片/8 建图/9 灯/10 召唤/11 环绕/12 希区柯克/13 合影)；`interaction_id`；`task_name`；`animation_id`；`direction/step/distance`；`token` | `header`；`success`；`info` | — |
| `/aimdk_5Fmsgs/srv/PlayMove` | **步进移动**（前进/后退/转向指定步数距离） | `header`；`direction`(0-7, 255 停止)；`step`；`distance`；`angle`；`token` | `header`；`success` | 与 `PlayMoveWithWalkMode` 的差别：后者可指定步态模式 |
| `/aimdk_5Fmsgs/srv/PlayMoveWithWalkMode` | 按步态模式移动 | 同上 + `walk_mode`(1 轻柔/2 越障/3 超能) | `header`；`success` | 模式与 `SetMode` 一致 |
| `/aimdk_5Fmsgs/srv/PlayAnimation` | **播放预设动画**（打招呼/握手/比心/跳舞/拍照姿势…） | `header`；`animation_id`(1 问候/2 握手/3 坐下/4 躺下/5 站起/6 双手比心/7 跳舞/8 旋转变形/20-24 拍照姿势/60 说话动作)；`interrupt`；`token` | `header`；`success` | — |
| `/aimdk_5Fmsgs/srv/InteractWithId` | 按库 ID 触发交互资产（行为/情绪/变形/舞曲） | `header`；`interaction_id`（含 1-5 行为、100-190 情绪、300-303 变形/舞曲段）；`type`(1 情绪/2 动作/3 音频/4 组)；`token` | `header`；`success` | — |
| `/aimdk_5Fmsgs/srv/RecordMotion` | 动作示教录制 | `header`；`record_area`(1 上肢/2 下肢/3 全身)；`operation_type`(1 开始/2 停止/3 中止)；`token` | `header` | — |
| `/aimdk_5Fmsgs/srv/PlayCustomMotion` | 播放自定义动作 | `header`；`operation_type`(1 开始/2 停止)；`resource_type`；`resource_name`；`token` | `header` | 资源列表见 `GetCustomMotionList` |
| `/aimdk_5Fmsgs/srv/GetMotionList` | 查询动作库 | `header`；`motion_type`；`motion_list_type` | `header`；`motion_list[]` MotionInfo | — |
| `/aimdk_5Fmsgs/srv/GetTaskInfo` | 查询长任务信息 | `header`；`task_type`（TaskState 枚举 0-12） | `header`；`data`（JSON） | — |
| `/aimdk_5Fmsgs/srv/AgentFlowTaskCtrl` | 图编程（AgentFlow）任务控制 | `header`；`control_type`(1 启动/2 停止/3 继续/4 取消)；`interrupt`；`json`；`program_id` | `header`；`success`；`program_id`；`info` | 进度看 `/aima/task_engine/agent_flow/task_info` |
| `/aimdk_5Fmsgs/srv/TaskInjection` | 任务注入（外部编排） | `header`；`token`；`data`（JSON） | `header`；`data` | — |
| `/aimdk_5Fmsgs/srv/TaskStatus` | 任务状态上报（task_trigger→Agent） | `header`；`msg_type`；`task_status`(1 开始/2 结束)；`agentflow_id`；`msg_content` | `header` | — |
| `/aimdk_5Fmsgs/srv/FaceGreet` | 人脸问候 | `header`；`id`；`greet_phrase[]` | `header` | — |
| `/aimdk_5Fmsgs/srv/SetMode` | **整机模式切换**（室内/户外/超能+限速） | `header`；`token`；`type`；`mode`；`robot_speed`；`change_speed` | `header` | 模式影响动作白名单（REASON_INDOOR_WHOLE_BODY 等） |
| `/aimdk_5Fmsgs/srv/TransformTask` | **形态变形任务**（四足↔双足） | `header`；`token`；`control_type`(1 双足/2 四足/3 自动) | `header` | 变形系列 McAction 的任务化封装；受姿态条件门控（reason=30/55） |
| `/aimdk_5Fmsgs/srv/ShutdownTask` ★ | **主动关机服务** | `header`；`token` | `header` | ★官方；真正的断电执行在 `SetPowerDown`/`SetPowerDownOrin` |
| `/aimdk_5Fmsgs/srv/ControlPowerSave` | 省电控制 | `header`；`token_common`；`operation`；`duration_seconds` | `header`；`result`；`data` | ota_master 承接 |
| `/aimdk_5Fmsgs/srv/TouchEnable` | 触摸使能开关 | `header`；`enable_state`(1 开/2 关) | `header` | — |
| `/aimdk_5Fmsgs/srv/SetAvoidance` | 避障开关 | `header`；`trigger`；`source` | `header` | coreon_fusion 承接 |
| `/aimdk_5Fmsgs/srv/SetAppConfig` | App 侧功能配置查询/更新 | `header`；`operation`(0 查询/1 更新)；`config_name`；`enable`；`source` | `header`；`current_enabled`；`data_json` | — |
| `/aimdk_5Fmsgs/srv/SpawnIntelligentMotionTask` ★ | **环绕运镜任务** | `header`；`task.action_type`(1/2/3)；`task.task_ctrl`(0 启动/1 急停) | `header`；`success`；`task_id` | ★官方；状态回看 `/aima/motion/intelligent_task_state`（trajectory_tracker 承接） |

#### 5.2.4 导航 · 建图 · 定位 · 跟随（挂载 `/task_engine`、`/robot_navigation`、`/map_server` 等）

| 接口 | 中文名 · 简介 | 入参（Request） | 出参（Response） | 注意事项 |
|---|---|---|---|---|
| `/navi/command`（`SetNaviTask`）★ | **导航任务控制** | `header`；`action`(0 开始/1 暂停/2 继续/3 终止/4 进低功耗/5 退低功耗)；`goal` PoseStamped（START 时有效）；`description` | `response` | ★官方；robot_navigation 承接；状态回看 `navi/state` |
| `/navi/relocate`（`SetNaviRelocate`）★ | **导航重定位控制** | `header`；`action`(0-3)；`description` | `response` | ★官方 |
| `/navi/loadmap`（`LoadNaviMap`）★ | **加载地图** | `header`；`map_path`；`mode`(0 全量/1 仅地图/2 仅虚拟墙) | `response`；`msg`；`rlt_code`(1 成功/0 失败) | ★官方；map_server 承接 |
| `/aimdk_5Fmsgs/srv/NaviTaskCtrl` | App 版导航控制（按点名/坐标/回充） | `header`；`ctrl_type`(1 开始/2 停/3 继续/4 取消)；`navi_type`(1 点名/2 坐标/3 回充)；`x/y/theta`；`position_name`；`token` | `header` | — |
| `/aimdk_5Fmsgs/srv/GetMapList` / `GetMapData` / `GetCurrentMap` | 地图列表/数据/当前地图 | `header`[；`map_id`] | `map_meta_infos[]` / `map_meta_info+map_info+png_data[]` / `map_id` | PNG 缩略图随响应传输 |
| `/aimdk_5Fmsgs/srv/MapCommand` / `MapCommandTask` | **建图任务**（开始/取消/保存；手动/自动探索） | `header`；`command`(1 开始/2 取消/3 保存)；`mapping_type`(1 手动/2 自动探索)；`task_id`；`map_name`[；`token`] | `header`；`task_id`；`map_name`；`save_time` | 进度看 `map/build_state` |
| `/aimdk_5Fmsgs/srv/MMStartLocalization` / `MMStopLocalization` | 开始/结束定位（map_manager 通道） | `header`[；`map_id`] | `header`；`msg`；`rlt_code` | — |
| `/aimdk_5Fmsgs/srv/MapLocalizationTask` | 定位任务（App 通道） | `header`；`command`(1 开始/2 停止)；`task_id`；`map_id`；`token` | `header`；`task_id`；`map_id`；`msg`；`rlt_code` | — |
| `/aimdk_5Fmsgs/srv/GetNaviPoints` / `SetNaviPoint` | 导航点查询/设置 | `header`；`map_id`[；`point` NaviPointInfo；`is_auto`] | `points[]` / `map_id`+`point_id` | — |
| `/aima/algo/chargepointsit/enable`（SetBool） | 对座功能使能 | `data` bool | `success`/`message` | — |
| `/aima/algo/chargepointsit/setchargepointsit`（`SetChargePointSit`） | **对座/出座任务** | `header`；`command`(1 开始/2 取消/3 停止)；`type`(1 粗对座 T 专用/2 精对座 Q·T 共用/3 出座 T 专用)；`task_id`；`map_id` | `header`；`task_id`；`map_id` | response 仅表示「已接收」；结果看 `navi_sit_state` |
| `/aima/algo/chargepoint/setchargepoint`（`SetChargePoint`） | 专座搜索算法控制 | `header`；`command`(1/2/3)；`task_id`；`map_id` | `header`；`task_id`；`map_id` | 结果看 `charge_point/recognition_state` |
| `/aimdk_5Fmsgs/srv/SetChargePointTask` | 专座任务（task_engine 通道） | 同上结构 | 同上 | — |
| `/aimdk_5Fmsgs/srv/FollowTaskCtrl` | **跟随任务控制**（跟随/召唤/乘梯） | `header`；`trigger`；`type`(1 跟随/2 召唤/3 人脸/4 人体/5 方向/6 进电梯/7 出电梯/8 拍照跟随)；`direction`(1 左/2 右/3 近/4 远)；`id`；`x/y/h/w` 框；`timestamp`；`token` | `header` | 状态看 `follow/state` |
| `/perception/set_follow_perception_task`（`SetFollowPerceptionTask`，双实例） | 跟随感知任务开关 | `header`；`trigger` | `response` | person_track 与 task_engine 双端提供 |
| `/aimdk_5Fmsgs/srv/SetFollowTaskBiped` | 双足跟随（follow_node） | 同 FollowTaskCtrl 字段子集 | `response` | — |

#### 5.2.5 相机 · 云台 · 拍照（挂载 `/hal_camera*`、`/picture_service`、`/insta360_ros2_node`）

| 接口 | 中文名 · 简介 | 入参（Request） | 出参（Response） | 注意事项 |
|---|---|---|---|---|
| `/aima/hal/camera/CaptureJpegImage` ★ | **获取相机单帧 JPEG** | `request`；`camera_id`；`timeout_ms` | `response`；`jpeg`（JpegImage） | ★官方；相机权限关闭时失败（permission_state 门控） |
| `/aima/hal/camera/HALCameraCommand` ★相关 | 相机通用命令（key-value） | `request`；`command`；`param_keys[]`；`param_values[]` | `response`；`result_keys[]`；`result_values[]` | rtsp_server 亦调用（拉流配置） |
| `/aima/hal/camera/SetHalCameraStream` | 相机供电/出流掩码控制 | `request`；`trace_id`；`req_type`(0 设置/1 查询)；`power_ctrl_mask`；`stream_ctrl_mask`（bit0-3 = CAMERA_0..3） | `response`；`req_result`；`target_state_mask`；`switch_state`(0 已切换/1 切换中) | SM 空闲态节能用 |
| `/aimdk_5Fmsgs/srv/SetHalTofStream` | ToF 供电/出流掩码控制 | `request`；`trace_id`；`power_ctrl_mask`；`stream_ctrl_mask`（bit0-4 = TOF_0..4） | `response`；`trace_id`；`switch_state`(0 失败/1 成功) | — |
| `/aimdk_5Fmsgs/srv/GetPictureList` / `GetPicture` / `DeletePicture` | 图片列表/获取/删除 | `header`[；`picture_name`；`page_size` / `picture_id` / `picture_ids[]`] | `picture_list[]`+容量 / `picture_data[]` / `deleted_ids[]`+`failed_ids[]` | — |
| `/aimdk_5Fmsgs/srv/PlayPhoto` | 拍照任务（单拍/连拍） | `header`；`control_type`(1 开始/2 停止)；`task_type`(1 拍照/2 图片) | `header` | — |
| `/aimdk_5Fmsgs/srv/GetResourcePath` / `RenameResource` / `DeleteResource` / `GetCustomMotionList` | 资源路径/重命名/删除/自定义动作列表 | `header`+各字段 | 各结构 | resource_type：1 图片/2 灵创/3 自定义动作 |
| `/aimdk_5Fmsgs/srv/AssignInsta360Task` | 进入/退出合影模式 | `request`；`task_type`(1 进入/2 退出) | `response` | — |
| `/aimdk_5Fmsgs/srv/ControlInsta360` | **云台相机控制**（拍照/录像/变焦/云台角/跟踪/急停） | `request`；`control_type`(0-20：3 拍照/4 开始录像/5 停止/6 回中/7 翻转/8 自动跟踪/9 关跟踪/10 变焦/15 云台设角/17 云台移动/18/19 机器人模式/20 急停)；`zoom_scale`；`continuous_zoom_direction`；`gimbal_roll/pitch/yaw`；`gimbal_up_down/left_right` | `response`；`uri`；`zoom_scale`；`gimbal_*` | — |
| `/aimdk_5Fmsgs/srv/GetInsta360WiFiInfo` | 云台 WiFi 信息 | `request` | `response`；`ssid`；`password` | — |

#### 5.2.6 HAL：传感器 · 电源 · 灯（挂载 `/hal_sensor_soc0`、`/hal_sensor_soc1`、`/coreon_fusion_ros2_node` 等）

| 接口 | 中文名 · 简介 | 入参（Request） | 出参（Response） | 注意事项 |
|---|---|---|---|---|
| `/aimdk_5Fmsgs/srv/SetServo` ★ | **舵机控制**（相机抬升） | `request`；`position`（0-100）；`speed` | `header` | ★官方 |
| `/aimdk_5Fmsgs/srv/SetNeckLight` ★ | **颈部灯光开关/亮度** | `request`；`enable`；`brightness` | `header` | ★官方；状态回看 `neck_light/state` |
| `/aimdk_5Fmsgs/srv/GetNeckLightState` | 查询颈部灯 | `request` | `header`；`enable`；`brightness` | — |
| `/aimdk_5Fmsgs/srv/LedStripCommand` ★ | **整机灯带灯语** | `request`；`led_strip_mode`（0-20 预设：关/绿流/彩虹/舞蹈/闪烁/充放电/低电/开机/白呼吸…；254 自定义呼吸/255 自定义闪烁）；`r/g/b`；`period` | `header` | ★官方 |
| `/aimdk_5Fmsgs/srv/PlayLight` | 尾灯/信号灯 | `header`；`color`(1 红/2 白/3 蓝)；`mode`(1 常亮/2 闪烁/3 呼吸)；`priority` | `header`；`success` | lumina 联动 |
| `/aimdk_5Fmsgs/srv/GetBatteryInfo` | 电池信息查询 | `request` | `response`；`manufacturer/serial_number/protocol_version/hardware_version/software_version` | 实时数据走 `bms/state` 话题 |
| `/aimdk_5Fmsgs/srv/SetJointPower` / `GetJointPowerState` | 关节电机上下电/状态 | `request`[；`input_source`；`command`(1 上电/2 断电)] | `header`[；`power_state`(1/2)] | 断电前确保安全姿态 |
| `/aimdk_5Fmsgs/srv/SetExt12vPower` | 外部 12V 输出开关 | `request`；`enable` | `response` | — |
| `/aimdk_5Fmsgs/srv/SetPowerDown` | rk 侧关机/重启（含紧急下电） | `request`；`command`(1 下电/2 取消/3 软关机/4 电量重启/5 关机重启/…3 紧急)；`input_source`；`power_reboot_interval` | `response` | 内部（ota_master/sm 调用） |
| `/aimdk_5Fmsgs/srv/SetPowerDownOrin` | orin 侧关机/重启 | `request`；`command`(1 下电/2 重启/3 紧急下电)；`input_source` | `response` | orin hal_sensor_soc1 承接 |
| `/aimdk_5Fmsgs/srv/ObstacleCheck` | 定向障碍检查 | `header`；`range`；`height`；`direction`（位掩码：1 前/2 后/4 左/8 右/16 上）；`form`(1 四足/2 双足) | `header`；`has_obstacle`；`obstacle_direction/confidence/type` | coreon_fusion 承接 |
| `/aimdk_5Fmsgs/srv/GetSceneList` / `GetSceneState` / `SceneCommand` | 场景列表/状态/控制 | `header`[；`available_only` / `scene_id` / `cmd_type`] | `scenes[]` / `scene_status+running_time_ms…` / `status+message+detail` | coreon_fusion 承接（场景引擎） |

#### 5.2.7 系统信息 · 网络 · 配置（挂载 `/housekeeper0`、`/app_proxy`、`/sm`、`/hds`、`/aimrt_agent_node`）

| 接口 | 中文名 · 简介 | 入参（Request） | 出参（Response） | 注意事项 |
|---|---|---|---|---|
| `/aimdk_5Fmsgs/srv/GetRobotInfo` | 机器人信息（SN/名称/系列/板型/MAC/版本/密钥） | `header` | `header`；`sn`；`name`；`series`；`board`；`soc_index`；`mac`；`software_version`；`hw_version`；`secret` | — |
| `/aimdk_5Fmsgs/srv/GetRobotState` | 机器人对外状态（含蓝牙名/热点名密码） | `header` | `header`；`sn/name/series/board/mac/software_version/bluetooth_name/hotspot_name/hotspot_password` | — |
| `/aimdk_5Fmsgs/srv/GetWholeRobotInfo` | 整机多板信息（sn0/1/2 + 板列表） | `header` | `sn0/1/2`；`sn_list[]`；`board_list[]`；`soc_index[]`；`mac` | — |
| `/aimdk_5Fmsgs/srv/SetRobotName` / `SetSN` / `SetBoardSN` / `SetDevSecret` | 改名/写 SN（按板）/写密钥 | `header` + 对应字段 | `header`；`msg` | 出厂/产线接口 |
| `/aimdk_5Fmsgs/srv/GetNetworkState` | 全部网口状态 | `header` | `interface_list[]` NetInterfaceState | — |
| `/aimdk_5Fmsgs/srv/GetWifiState` / `CtrlWifi` | WiFi 查询/控制 | `header`[；`ssid`] / `operation`(1 开/2 关/3 扫描/4 连接/5 断开/6 删除)+`connect_wifi_info`+`scan_flag` | `wifi_info`+`msg` / `header` | — |
| `/aimdk_5Fmsgs/srv/GetWifiHotspotState` / `CtrlWifiHotspot` / `SetHotspotInfo` | 热点查询/开关/改名 | `header` + 各字段 | `is_open/ssid/password/state`(0-4) 等 | p2p0 192.168.88.88 |
| `/aimdk_5Fmsgs/srv/CtrlScanBluetooth` / `ConnectBluetooth` / `DisConnectBluetooth` | 蓝牙扫描/连接/断开 | `start_scanning` / `mac_addr` / `mac_addr`+`del_history`+`reconnect` | `msg`[+`conn_code`] | 结果回看 `robot/scan/bluetooth` |
| `/aimdk_5Fmsgs/srv/SetBluetoothName` / `SetBleAlias` / `ReBroadcastBluetoothService` | 蓝牙名称/别名/重播服务 | 对应字段 | `msg` | — |
| `/aimdk_5Fmsgs/srv/CtrlModem` / `GetModemInfo` | 4G 模组控制/信息 | `operation`(1 PDN 使能/2 禁用/3 设 APN/4 复位/5-6 GNSS)+`apn` | `modem_info` | — |
| `/aimdk_5Fmsgs/srv/GetLbsLocation` | LBS 定位 | `header` | `lbs_info` LbsInfo | — |
| `/aimdk_5Fmsgs/srv/CtrlPermission` | 隐私权限（麦克风/相机/定位） | `operation`(0 授权/1 撤销/2 查询/3 清除)；`target`(0 麦克风/1 相机/2 定位)；`user_id` | — | 权限撤销后相机/麦克风停流 |
| `/aimdk_5Fmsgs/srv/JsonWebCheck` | JWT 校验 | `header`；`jwt` | `jwt_valid`；`allow_connection`；`user_name`；`device_id` | App 鉴权链 |
| `/aimdk_5Fmsgs/srv/ConnectRobot` / `DisconnectRobot` | App 连接/断开（占用/释放） | `header` | `allow_connection`+`user_name` / `result` | 单用户独占控制权 |
| `/aimdk_5Fmsgs/srv/GetSystemState` / `MigrateSystemState` | 整机状态机查询/迁移 | `header`[；`state`] | `cur_state`+`cur_status` | 状态机非法迁移会被拒 |
| `/aimdk_5Fmsgs/srv/GetAlertCodeJsonFile` | 告警码 JSON 全量 | `header` | `alert_code_json` | hds 承接 |
| `/aimdk_5Fmsgs/srv/ControlQuanji` | 圈圈设备控制（UWB/IMU/OTA/雷达） | `request`；`control_type`(0 UWB 启/1 停/2 IMU 启/3 停/4 OTA/5 雷达启/6 停/7 查询锚点版本)；`fw_path`；`anchor_version` | `response` | — |
| `/aimdk_5Fmsgs/srv/GetAgentPropertiesRequest` / `SetAgentPropertiesRequest` | Agent 属性读/写 | `property_ids[]` / `contents` AgentProperties | `contents` / `header` | — |
| `/aimdk_5Fmsgs/srv/SetAgentSuppression` | Agent 抑制（唤醒/问候/触摸静默期） | `suppression_type`(1 唤醒/2 问候/3 触摸)；`ttl_ms` | `header` | — |

#### 5.2.8 平台内部 proto 服务（`ros2_plugin_proto/srv/RosRpcWrapper`，共 58 个）

AimRT 的 protobuf RPC 经 ROS 2 桥暴露，**服务名是转义后的 proto 全名**（`aimdk.protocol.XxxService/Method` → `/aimdk_2Eprotocol_2EXxxService/Method`，`.`→`2E`、`_`→`5F`、`-`→`2D`），负载为 protobuf 序列化字节。**非 SDK 承诺面**。

| 服务（前缀 `/aimdk_2Eprotocol_2E…`） | 承载节点 | 功能 |
|---|---|---|
| `EmAppService/GetAllAppInfos`、`GetAppInfo`、`GetDevSettings`、`SetDevSettings`、`StartApp`、`StartApps`、`StopApp`、`StopApps` | `/sm` 调用 → process_manager | 应用启停/配置（em 体系管理面） |
| `SmNodeService/{agent, aima_2Dlegged_5Fodom_2Dapp, audio_5Fmanager, coreon_5Ffusion, data_5Fexporter, drp, hal_5Fcamera, hal_5Frgbd, hds, mc, ota_5Fmaster, ota_5Fslave, rc, resource_5Fmanager, sm, sys_5Fguard, task_5Fengine}/ExecuteAction` + `ExecuteFallback`（17 应用 ×2 = 34） | 各业务节点提供，`/sm` 调用 | 状态机对每个受管应用的动作执行/回退指令 |
| `/soc0|soc1/async/CreateAsyncTask`、`CancelAsyncTask`；`/soc0|soc1/sync/CreateSyncTask`、`GetTaskStatus` | `/data_exporter`×2（双板） | 跨板数据导出任务（async/sync） |
| `/soc0/cloud/platform/GetJWT`、`GetUploadPath`、`CombineFileSlices`、`UnbindProject`；`/soc0/cloud/result/UploadAsyncTaskProgress`、`UploadAsyncTaskResult` | `/data_exporter`(soc0) | 云端上传（JWT/分片合并/解绑） |
| `/aimomp_2Eprotocol_2EDevicePropertyService/SetProperty`（服务端）；`/aimomp_2Eprotocol_2ECloudPropertyService/ReportProperty`（客户端） | `/HK_ros2_rpc_node` | 设备属性设置与云端属性上报 |

#### 5.2.9 nav2 标准服务（聚合，非 aimdk）

| 模式 | 服务 | 说明 |
|---|---|---|
| 生命周期 ×10 节点（behavior_server、bt_navigator、controller_server、global/local_costmap、map_server、perception_node、planner_server、robot_navigation、smoother_server） | `<node>/change_state`、`get_state`、`get_available_states`、`get_available_transitions`、`get_transition_graph` | lifecycle_msgs 标准 5 件套 ×10 = 50 个 |
| 代价图 | `/{global,local}_costmap/clear_around_*`、`clear_entirely_*`、`clear_except_*`、`get_costmap` | nav2_msgs 清代价图 4 件套 ×2 |
| 地图 | `/map_server/load_map`（nav2 LoadMap）、`/map_server/map`（nav_msgs GetMap） | — |
| 生命周期管理 | `/lifecycle_manager_{navigation,t1_perception}/is_active`（Trigger）、`manage_nodes`（ManageLifecycleNodes） | 导航/感知两组受管节点 |
| ROS 参数（35 节点 ×6） | `<node>/{describe,get}_parameter{s,_types}`、`list_parameters`、`set_parameters{_atomically}` | rcl 标准 210 个 |

### 5.3 覆盖核对

- Topic：198/198（§5.1 表）；Service：aimdk 业务 147 + proto 58 + nav2/lifecycle/参数聚合行 = 480/480（`ros2 service list` 实测数）。
- 未实测项：话题频率/带宽（`ros2 topic hz/bw` 未跑，遵守只读最小化探测）、Action 接口（图上无 action 端点）。

### 5.4 公共响应模型（所有 aimdk 服务通用，判定准则）

**响应结构（`CommonResponse` / `CommonTaskResponse`）**

| 字段 | 类型 | 语义 |
|---|---|---|
| `header.stamp` | Time | 框架处理时间戳 |
| `header.code` | int64 | 框架返回码：**0=SUCCESS（调度成功）**、1=FAIL（被拦截） |
| `status`/`state` | CommonState | 业务状态（见下） |
| `message` | string | 失败时的诊断信息 |
| `task_id` | uint64 | 异步任务标识（仅 CommonTaskResponse） |

**CommonState.value 枚举**

| 值 | 枚举 | 建议 |
|---|---|---|
| 0 | UNKNOWN | 记录日志、自检 |
| 1 | SUCCESS | 成功 |
| 2 | FAILURE | 查 message |
| 3 | ABORTED | 被取消/抢占 |
| 4 | TIMEOUT | 查链路/负载 |
| 5 | INVALID | 查参数/状态机约束 |
| 6 | IN_MANUAL | 手动接管中 |
| 100 | NOT_READY | 未初始化完，延时重试 |
| 200 | PENDING | 排队中 |
| 300 | CREATED | 已创建待执行 |
| 400 | RUNNING | 执行中 |

**CommonState.reason 核心枚举（0-30；AmRequest 扩展到 0-66）**：0 成功；1 开箱中；2 开机自检中；3 关机中；4 形态不支持；5 低电量；6 充电中；7 动作不在白名单；8 HDS 故障；9 模式不支持；10-14 前后左右上有障碍；15 其他任务运行中；16 已是目标状态；17 未找到动作；18 无技能列表；19 需双足站立；20 动作不匹配；21 动作黑名单；22 无状态转移路径；23 无可用技能；24 室内模式限制；25 动作引擎未就绪；26 机型不支持；27 起立中；28 动画不可打断；29 打断 ID 无效；30 变形条件不满足；（31-66 扩展：App 断连/上限/跌倒守护/云端断连/建图系列/紧急停止/摇杆控制中/磁盘满等）。

**判定准则（官方）**：先 `header.code == 0`（框架调度成功）→ 再 `status.value == 1`（业务成功）；失败详情看 `message`，业务原因看 `status.reason`。⚠️ 特例：`GetMcAction`/`McActionStatus` 体系里 **100 = RUNNING**，勿与 CommonState 混淆。异步任务（SetMcAction/SetMcMotion/SpawnIntelligentMotionTask 等）以 `task_id` + 对应状态话题追踪。

---

<a id="sec6"></a>

## 6. 启元 T1 Pro 软件栈分析（包括：三方工具、软件包、框架，3 个 usecase 场景的启动、运行时状态、部署、静态资源占用）

> **采集方式**：本章数据来自 2026-10-30 对两板的新一轮**只读**探测（`dpkg-query -W`/`pip3 list`/`ldd`/`strings`/`find -printf`/`systemctl cat`/`du`/`/proc/*/environ`/`/sys/fs/cgroup`）+ 一轮**人工交互式动态采样**（用户现场语音触发三个场景，采样器全程连续记录：两板 `top -b -d 3` 全进程快照、orin `tegrastats` 2Hz、rk devfreq(Mali GPU)/debugfs(rknpu NPU) 3s 周期、ROS 2 事件流 `ros2 topic echo` 毫秒级时间戳对齐场景边界）。除只读订阅外无任何写入、无服务调用。原始数据存档于 `appendix-c-data/`（c2_* 为正式采样轮）。
> **版本基线**：与附录 A 相同固件 `qy2501_2_v2d2` soc0/soc1 `v1.0.0.5-R-202610050335`；全部 em 应用为 `release-v1.2.1.patch1004` 分支（2026-10-04/05 构建，每应用 `pipeline.json` 记录 commit），基础层 aimrte `v0.9.1-rc5.10`。

### 6.1 两板使用的工具、软件包与 AI 模型清单

#### 6.1.1 固件与系统级软件栈（两板对照，版本全部实测）

| 组件 | rk（soc0，运控板） | orin（soc1，大脑板） | 开源仓 / 来源 |
|---|---|---|---|
| OS / 内核 | Ubuntu 22.04.5，`6.1.141-rk3576` | Ubuntu 22.04.5，`5.15.148-tegra`（L4T **R36.4.4** ≈ JetPack 6.2，GPU 驱动 540.4.0） | Ubuntu / NVIDIA |
| ROS 2 | Humble `ros-base 0.10.0`（厂商 2026-03 重建包）、`rmw-fastrtps-cpp 6.2.10` | 同左 | ros2 / ros-infrastructure |
| DDS | eProsima **Fast DDS 2.6.12**（`ros-humble-fastrtps`） | 同左 | eProsima/Fast-DDS |
| 板内零拷贝 IPC | **iceoryx 2.95.4.2** + ros-humble-iceoryx-{hoofs,posh,binding-c} 2.0.5 | 同左 | eclipse-iceoryx/iceoryx |
| 运行时框架 | **AimRT**（开源）+ 内部增强层 **aimrte v0.9.1-rc5.10** + `aimrt_py 1.2.0`；每个应用自带 `aimrt_main` 启动器 / `aimrte` / `iox-roudi` / `*_pb_ts.so` 插件 | 同左 | github.com/AimRT/AimRT（智元开源）；aimrte 未开源 |
| 进程管理 | em/process_manager（自研，`em.service` 拉起） | 同左（soc1 实例） | 近亲开源：github.com/Link-U-OS/aimrt_process_manager（灵渠OS 组件） |
| AI 推理栈 | **onnxruntime 1.19.2**（mc/agent）、**RKNN Runtime 2.3.2**（NPU，`librknnrt.so` 429f97ae 2025-04-09 构建）、**MNN ≈3.5.0**（AEC，strings 提取）、**sherpa-onnx 1.12.15**（KWS） | **CUDA 12.6.x**（cudart/nvrtc 12.6.68）、**TensorRT 10.7.0.23**（+python3-libnvinfer）、**cuDNN 9.3.0.75**、cuBLAS 12.6.1.4、jetson-stats 7.2.1 | microsoft/onnxruntime、airockchip/rknn-toolkit2、alibaba/MNN、k2-fsa/sherpa-onnx、NVIDIA |
| OpenCV | 4.5.4（系统 dpkg）+ 4.8.1（agent 自带 `libopencv_*.so.408`） | 4.8.0（NVIDIA Jetson 版 `libopencv 4.8.0-1-g6371ee1`）+ 4.5.4（系统） | opencv/opencv |
| 多媒体 | librockchip-**mpp 1.5.0**、lib**rga 2.2.0**、GStreamer 1.22.9 + rockchip 插件 1.14、ffmpeg 4.4.2（系统）/ 6.0（agent 自带 libavcodec.so.60） | （解码走 NVDEC/VIC） | Rockchip（部分开源）、GStreamer、FFmpeg |
| 胸屏 UI | **flutter-pi**（自编译）+ Flutter `display_ui` bundle | — | github.com/ardera/flutter-pi |
| 音频服务 | **PipeWire 1.6.8** + pipewire-pulse + wireplumber（SDK 所述 PulseAudio TCP 6001/4713 即其兼容口） | — | PipeWire（开源） |
| 网络 / 时钟 | NetworkManager、hostapd/dnsmasq（p2p0 热点）、mosquitto 2.0.11（conduit_router TLS 实例）、linuxptp 3.1.1（ptp4l）、chrony 4.2、can-utils 2020.11.0 | NetworkManager、ptp4l、sync_time | 均开源 |
| 其他库 | Qt 5.15.3（依赖库） | numpy 1.21.5、python3-opencv 4.5.4 | — |
| 自研 / 商业（无公开仓） | tetherd（内网守护）、abox 日志、scene_engine、**connx（圈圈接入）**、secret_store、tc-engine、rtc_engine（TRRO 透传）、**omnis_sdk 4.0.0**（qiyuan-main-rc01，语音前端）、**科大讯飞 SDK**（libxf_vtn 唤醒 / evad / miniESR 5.1 / 听写 tingxie 2.0.16.6）、deepseek-harness-sdk 0.1.1rc1 | — | 启元/智元内部、讯飞商业授权 |

#### 6.1.2 AI 模型清单（文件实测，含大小 / 推理框架 / 用途 / 同款开源）

**rk 板（8 个神经网络 + 云端 LLM）：**

| 模型（实测文件） | 大小 | 模块 | 推理框架（实测） | 用途 | 同款开源 / 来源 |
|---|---|---|---|---|---|
| `rl_models/*.onnx` 共 **643 个**（激活目录 `qd1_t2d0` 264MB；6 套机型目录合计 ~1.0GB） | 2~23MB/个 | mc_app_main（CPU3 独占核） | **onnxruntime 1.19.2 @ CPU**（A53，实时环内推理） | RL 运动策略：`zoo_3500`/`q1_random_ground v2.0–v2.4`（地形步态）、`douyin_3500`（抖音舞）、`bi_dance_liuxingwu/haicao/meixi/qiumi/cluo`（双足舞曲）、`q1_danceGo`、`q1_ladingwu2`（拉丁舞）、`q1_run_jump` 等 | 自研 RL 训练产出（部署为 ONNX；训练框架未公开） |
| `omnis_sdk/…/silero_vad.onnx` | 2.3MB | Agent 语音前端 | onnxruntime @ CPU | 语音活动检测（VAD） | **开源同款** snakers4/silero-vad |
| `omnis_sdk/aec_models/dtln_aec_128_{1,2}.mnn` | 2.0/6.4MB | Agent | **MNN** @ CPU | 回声消除（DTLN 架构） | DTLN 公开架构、自训权重 |
| `sherpa-onnx-kws`（tokens/keywords 资源） | — | Agent | **sherpa-onnx** | 关键词唤醒（KWS） | **开源** k2-fsa/sherpa-onnx |
| 讯飞 `vtn/miniesr_5.1`、`evad.jet` | ~86MB | Agent third_party/iflytek_sdk | 讯飞私有引擎（libxf_vtn/libevad） | 唤醒 + 嵌入式识别 + 音频增强 | 商业 SDK（科大讯飞，appid 15d2493c） |
| `t1/perception/models/`：`face_detect.bin` 30MB、`face_encode.bin` 80MB、`gesture_cls.bin` 64MB、`hand_detector.bin` 2.2MB、`pose.bin` 7.4MB、`reid.bin` 4.6MB | 合计 ~190MB | Agent（libface_id 0.5.0 / libperception_sdk 0.5.0） | **RKNN @ rk3576 NPU**（librknnrt 2.3.2，双核 6TOPS） | 人脸检测 / 人脸识别（声纹+人脸档案）/ 手势分类 / 手部检测 / 人体姿态 / 行人 ReID | 自研、未公开（RKNN 部署格式） |
| `res/{bi,quad}_mimic_motion/*.csv`（如 `T1_bi_dance_two_three.csv`） | 数百 KB/个 | mc | 轨迹回放（非神经网络） | 模仿舞 / 示教动作库（`BIPED_MIMIC_DANCE_*`） | 自录 |
| 云端 LLM（无本地权重） | — | Agent（`llm.config`） | 云 API：`aiem-gw.agibot.com:443 /v1/session`（多模态 2048×1536 图像上传、opus 音频回传、`local_wakeup: true`） | 对话 / 意图 / VLM 问候（`enable_vlm_greeting_*`） | 启元云端；备选配置在案：火山方舟 `bot-20241125134226-8972b`（nlu `ep-20241118154845`、fc `ep-20240611061540`）、Azure OpenAI gpt-4o-mini |

**orin 板（24 个 TensorRT FP16 `.engine`）：**

| 模型（实测文件） | 大小 | 模块 | 推理框架 | 用途 | 同款开源 / 来源 |
|---|---|---|---|---|---|
| `perception/models/Stereo_v2.2.0.0.engine` | 35MB | percep | TensorRT 10.7 @ GPU | 双目深度估计 | 自研 |
| `perception/models/Segment_v0.5.12.0.engine` / `Segment_Qv0.2.1.0.engine` | 12MB | percep | TensorRT | 语义分割（`stereo_right/seg_mask`、语义代价图输入） | 自研 |
| `perception/models/detect_v0.2.1.0.engine` | 23MB | percep | TensorRT | 通用目标检测 | 自研 |
| `models/yolov8s-seg_640x480.engine` / `yolov8n-seg` / `yolov8n_nx` | 9~27MB | 跟随/跟踪链 | TensorRT | 行人检测 + 分割（`follow_candidates` 来源） | **开源同款** ultralytics YOLOv8 |
| `models/clip_reid_vit_b16_{b1,b3,b5}.engine` | 174~177MB | person_track（跟随重识别） | TensorRT | CLIP-ReID ViT-B/16 行人重识别（b1/b3/b5 = 批大小三档） | **开源同款** CLIP-ReID（Seyervised/CLIP-ReID） |
| `models/swin_tiny_solider_msmt17_{b1,b3,b5}.engine` | 59MB | person_track | TensorRT | SOLIDER 预训练 Swin-Tiny 行人 ReID（MSMT17） | **开源同款** SOLIDER（微软） |
| `aimrt_slam/…/eigenplaces_v2.engine`（+v1/x86） | 24~46MB | aimrt_slam（**部署未运行**） | TensorRT | 全局定位 / 场景识别 | **开源同款** EigenPlaces（gmberton） |
| `aimrt_slam/…/superpoint_v1/v2.engine` | 2.9~3.8MB | aimrt_slam（未运行） | TensorRT | 特征点提取 | **开源同款** SuperPoint（Magic Leap） |
| `aimrt_slam/…/yolo26n-seg_v1/v2.engine` | 8.8~9.5MB | aimrt_slam（未运行） | TensorRT | 语义分割 | **开源同款** Ultralytics YOLO26-nano-seg |
| `aprilgrid_loc/…/ppocrv6_tiny_det.fp16.engine`（arm/x86） | 3.3/10.9MB | aprilgrid_loc（部署未运行） | TensorRT | 文字检测（AprilGrid 定位配套） | **开源同款** PaddleOCR PP-OCR 系列 |

#### 6.1.3 推理框架汇总（一表看清「谁在哪儿跑什么」）

| 推理框架 | 版本（实测） | 运行位置 | 承载模型 | 开源仓 |
|---|---|---|---|---|
| onnxruntime | **1.19.2** | rk CPU（mc 独占核 CPU3 实时环 + Agent CPU） | mc 全部 RL 策略（643×onnx）、silero VAD | github.com/microsoft/onnxruntime |
| RKNN Runtime | **2.3.2** | rk **NPU**（双核，实测静止基线占用 Core0 ~26%） | Agent 六个视觉模型（face/gesture/hand/pose/reid） | github.com/airockchip/rknn-toolkit2（工具链） |
| MNN | ≈3.5.0（strings 提取） | rk CPU | DTLN AEC ×2 | github.com/alibaba/MNN |
| sherpa-onnx | **1.12.15** | rk CPU | KWS 唤醒 | github.com/k2-fsa/sherpa-onnx |
| TensorRT + CUDA 12.6 + cuDNN 9.3 | **10.7.0.23** | orin GPU（Ampere） | 全部 orin `.engine`（24 个） | NVIDIA（闭源、公开分发） |
| 讯飞嵌入式引擎 | miniESR 5.1 | rk CPU | VTN 唤醒 / evad / 听写 | 商业授权 |
| 云端 LLM pipeline | — | 启元 aiem-gw（agibot.com:443） | 对话 / 意图 / VLM | 闭源服务 |

#### 6.1.4 task_engine 溯源（专项核实）

- **身份与版本**：`/robot/software/task_engine/bin/task_engine`（19.9MB C++ 二进制），基于 **AimRT + aimrte v0.9.1-rc5.10** 构建；应用版本 `release-v1.2.1.patch1004`（2026-10-04 构建，`pipeline.json` 记录 commit）。二进制内残留构建路径 `…/software/qiyuan/modules/task_engine/build/_deps/aimrt-src/…`，表明在智元/启元内部 GitLab CI 上编译。
- **开源状态（2026-10-30 核实）**：智元系两个 GitHub 组织——`AgibotTech`（26 仓）与 `Link-U-OS`（灵渠OS，16 仓）——**均无 task_engine 公开仓库**。即 T1 Pro 的 task_engine 目前为**闭源自研模块**，无公开开源仓地址。
- **可对应的公开近亲**：中间件基座 **AimRT**（github.com/AimRT/AimRT，智元开源的高性能机器人运行时框架）；进程管理层 **aimrt_process_manager**（github.com/Link-U-OS/aimrt_process_manager，灵渠OS 组成部分，对应板上 em/process_manager 体系）。
- **形态要点**：规则即配置——`cfg/rule/{q1,t1}/*.yaml` 共 **23 条任务规则**（follow/posture/whole_body/move/mapping/navigation/motion_teach/…，含优先级、前置姿态/电量/牵手门控与仲裁策略），技能资产在 `q1_skill/`、动作示教 CSV 在 `t1_motion/`；`AmRequest` 仲裁 + `real_task_type`（AnimationTask/FollowTask/InteractionTask…）驱动。

### 6.2 三个典型 usecase 全链路（实测时间线 + 链路图）

#### 6.2.0 实测时间线（事件流毫秒级对齐，2026-10-30 下午）

| 阶段 | 时长 | 实测动作序列（`mc/common/state.action_info`，来源 c2_events.log） |
|---|---|---|
| 基线（趴卧静止） | 101s | QUADRUPED_GET_DOWN_DEFAULT(110) 趴卧 |
| **UC1 对话「请跳个舞」** | 120s | 语音后 **~62s** 语义链路往返（云端 LLM+确认）→ 14:30:38 `READY_STAND(115)→STAND(101)` 起身（趴姿先起立）→ 14:30:42.8 **`QUADRUPED_LOCOMOTION_DANCE(109)` 四足跳舞 18s** → 14:31:13 `QUADRUPED_TO_BIPED_ROTATE(11)` → 14:31:18 `BIPED_LOCOMOTION_WBC(300)` 双足站立收势 |
| **UC2a「趴下」** | 154s（含语音等待） | 14:33:01 `BIPED_TO_QUADRUPED_ROTATE(21)` 旋转变形 → 14:33:04 四足过渡(102) → **14:33:06 `GET_DOWN(110)` 趴稳（变形到趴稳 5s）** |
| **UC2b「站起来」** | 79s（含语音等待） | 14:34:47 `READY_STAND(115)` → 14:34:49 **`QUADRUPED_STAND_DEFAULT(101)` 四足站立** → 14:34:52 `LOCOMOTION(102)` 运动就绪（起立全程 5s） |
| **UC3 牵手跟随** | 326s | 14:35:13 `QUADRUPED_TO_BIPED_ROTATE(11)` 变形双足 → 14:35:18 `BIPED_LOCOMOTION_WBC(300)` → 14:36:11 跟随任务启动但 `follow/state=1 未发现目标`（全程未锁定）→ **实际为牵手带步**（用户握手、MC 力导引 WBC 步态随行）→ 14:40 结束 |

#### 6.2.1 UC1 对话「请跳个舞」全链路

**流程图 UC1「请跳个舞」——模块链路拓扑：语音 → Agent/云端 → task_engine → MC（按域分组，静态链路视角）**

```mermaid
flowchart LR
    USER(["👤 用户语音<br/>「请跳个舞」"])
    subgraph CLOUD["☁ 启元云端"]
        LLM["aiem-gw.agibot.com:443<br/>/v1/session 多模态 LLM<br/>(对话+意图+TTS opus 回传)"]
    end
    subgraph RK["🖥 运控板 rk"]
        direction TB
        subgraph RKI["Agent 交互链（aimrt_agent）"]
            FE["omnis_sdk 4.0.0 语音前端<br/>DTLN AEC(MNN) + silero VAD(ORT)"]
            WAKE["讯飞 VTN 唤醒 + KWS(sherpa-onnx)"]
            ASR["ASR（讯飞 ESR/听写）"]
            AGENT["Agent /aimrt_agent_node<br/>semantic_intent · agent_show_text"]
            TTSOUT["TTS 播报接收 + opus 解码"]
        end
        subgraph RKT["任务与运控"]
            TE["task_engine /task_engine<br/>规则: whole_body.yaml(pri 45)<br/>AmRequest 仲裁"]
            MC["MC /mc_ros2_node (CPU3)<br/>onnxruntime RL 跳舞策略"]
            CAN["CAN 中断 @CPU2 → 14×执行器"]
        end
        subgraph RKB["表现层"]
            AUDIO["audio_manager→PipeWire→扬声器"]
            DISP["flutter-pi 胸屏表情"]
            HAL0["hal_sensor 灯带/颈部灯"]
            LUM["lumina 灯语联动"]
        end
    end

    USER -- "4+2ref 麦克风阵列" --> FE --> WAKE --> ASR
    ASR -- "asr_result ★ / 语义" --> AGENT
    AGENT <-->|"HTTPS/TLS 会话<br/>(上传语音/图像 2048×1536)"| LLM
    AGENT -- "semantic_intent + agent_show_text" --> TE
    TE ==>|"★ SetMcAction(109 跳舞)<br/>★ SetMcMotion / (趴姿先 SetMcAction 起身)"| MC
    MC -- "1kHz 级关节指令" --> CAN
    TE -->|"★ PlayTts"| AGENT --> TTSOUT --> AUDIO
    TE -->|"PlayEmotion ★ / SetFaceMode"| DISP
    TE -->|"★ LedStripCommand(舞蹈灯语)"| HAL0
    LUM -.->|"订阅 play_state/interaction 等 9 路"| AUDIO
    TE -.->|"interaction/state ★ 任务状态"| LUM
```

**时序图 UC1「请跳个舞」——语音 → 云端意图（实测 ~62s）→ 任务仲裁 → 起身+四足跳舞（18s）→ 多模态反馈**

> 读法（纵向=时间；实线=请求、虚线=返回；Service=ROS 2 服务 / Topic=单向广播 / FuncCall=板内函数调用）。

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant AG as Agent (/aimrt_agent_node)
    participant LLM as 云端 aiem-gw（LLM pipeline）
    participant TE as task_engine (/task_engine)
    participant MC as MC (/mc_ros2_node · CPU3)
    participant AUDIO as audio_manager
    participant DISP as display_ui 胸屏
    participant HAL0 as hal_sensor soc0

    U->>AG: 语音「请跳个舞」（4+2ref 麦克风阵列）
    Note over AG: 板内 FuncCall 链：omnis AEC(MNN)→silero VAD(ORT)→讯飞 ESR 识别
    AG->>LLM: FuncCall HTTPS /v1/session（多模态会话请求）
    Note over AG,LLM: 实测往返 ~62s（意图判定+话术生成）
    LLM-->>AG: 意图=跳舞 + 话术 + TTS 音频流(opus)
    AG->>TE: Topic /aima/agent/semantic_intent（SemanticIntent：动作意图·跳舞）【单向广播，无返回】
    AG->>AUDIO: FuncCall PipeWire TCP 6001（播放 opus 话术）
    AUDIO-->>AG: Topic /aima/hal/audio/play_state（PlayStateChange：播放中→完成）
    TE->>TE: FuncCall AmRequest 仲裁（rule whole_body：姿态/电量/白名单校验→放行）
    TE->>MC: Service ★SetMcAction（action=101 起身至四足站立，趴卧前置）
    MC-->>TE: CommonTaskResponse{task_id, state=SUCCESS}
    MC->>TE: Topic ★/aima/mc/common/state（action_info：READY_STAND(115)→STAND(101)，switch_ready=true）
    TE->>MC: Service ★SetMcAction（action=109 QUADRUPED_LOCOMOTION_DANCE）
    MC-->>TE: CommonTaskResponse{task_id, state=SUCCESS}
    loop 跳舞执行 ~18s（1kHz 控制环）
        MC->>MC: FuncCall onnxruntime.Run（rl/quad_walk_dance→t2_quad_twist_xy.onnx）
        Note over MC: 关节指令→CAN@CPU2→14 执行器（不出 DDS）
    end
    par 并行表现（与跳舞同时下发）
        TE->>DISP: Service PlayEmotion（emotion_ids=舞蹈表情组）
        DISP-->>TE: CommonResponse{code=0}
    and
        TE->>HAL0: Service ★LedStripCommand（led_strip_mode=4 舞蹈灯语）
        HAL0-->>TE: CommonResponse{code=0}
    and
        MC->>TE: Topic ★/aima/mc/common/state（motion_status：RUNNING→完成）
    end
    MC->>TE: Topic ★mc/common/state（变形 11→BIPED_LOCOMOTION_WBC(300) 收势）
    TE->>AG: Service ★PlayTts（tts_req：完成播报）
    AG-->>TE: PlayTtsResponse{trace_id}
    AG->>AUDIO: FuncCall PipeWire（播放完成话术）
    Note over TE: Topic /aima/interaction/state★（任务状态广播→App/云镜像）
```

读图要点（实测佐证）：
1. **语义往返 ~62s**：语音指令到首个动作（起身）间隔 62s，为云端 LLM 会话（含意图判定与话术生成）+ 任务仲裁链路的总时延；本地链路（Agent→TE→MC）毫秒级。
2. **跳舞本体是 RL 策略**：`QUADRUPED_LOCOMOTION_DANCE(109)` 映射 `rl/quad_walk_dance.yaml`→onnxruntime 在隔离核 CPU3 实时推理（动作库含 `douyin_3500`、`q1_danceGo`、`q1_ladingwu2` 等多种舞曲策略，另有 CSV 轨迹模仿舞 `BIPED_MIMIC_DANCE_*`）。
3. **多模态反馈同步**：TTS（云端 opus→audio_manager 播放）、胸屏表情（PlayEmotion）、灯带灯语（LedStripCommand + lumina 联动）与动作并行下发——UC1 期间 display_ui CPU 9.1%→28.5%（表情渲染约 3 倍）、audio_manager 2.4%→3.0% 微升、agent 104.3%→128.5%（基线为快照重测值，见 6.3）。
4. **前置姿态处理**：机器人当时趴卧，TE 先经 SetMcAction 起身到四足站立（whole_body 规则 `block_posture: LYING_DEFAULT…` 趴卧禁止直接跳全身舞），舞毕自主 `QUADRUPED_TO_BIPED_ROTATE` 转双足站立收势。

#### 6.2.2 UC2 姿态切换「趴下 → 四足站立」

**流程图 UC2「趴下 → 四足站立」——模块链路拓扑：Agent → task_engine（规则门控）→ MC → CAN → 关节（静态链路视角）**

```mermaid
flowchart LR
    USER(["👤 语音「趴下」/「站起来」<br/>(或 App · 遥控器)"])
    subgraph RK["🖥 运控板 rk"]
        AGENT["Agent 意图(云端 LLM / 本地 NLU 规则)"]
        TE["task_engine<br/>规则: posture.yaml<br/>AnimationTask · pri 50 · 可抢占"]
        MC["MC /mc_ros2_node (CPU3)<br/>姿态 FSM + RL 策略"]
        CAN["CAN @CPU2 → 关节"]
    end
    USER --> AGENT -- "semantic_intent" --> TE
    TE ==>|"★ SetMcAction<br/>趴下: BIPED_TO_QUADRUPED_ROTATE(21)→GET_DOWN(110)<br/>站起: READY_STAND(115)→STAND(101)→LOCOMOTION(102)"| MC
    MC -- "关节指令" --> CAN
    MC ==>|"★ mc/common/state（action_info.switch_ready / posture / form_state）"| TE
    TE -.->|"完成播报 PlayTts"| USER
```

**时序图 UC2「趴下 → 四足站立」——规则门控（牵手/电量）→ ★SetMcAction → RL 姿态控制环 → 完成播报**

> 读法同 UC1 时序图。

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant AG as Agent (/aimrt_agent_node)
    participant TE as task_engine (/task_engine)
    participant MC as MC (/mc_ros2_node · CPU3)
    participant HAL0 as hal_sensor soc0

    U->>AG: 语音「趴下」（板内语音链同 UC1）
    Note over AG: 意图获取：云端 LLM 或本地 NLU 规则（nlu_rules.json，FuncCall）
    AG->>TE: Topic /aima/agent/semantic_intent（姿态意图·趴下）【单向广播】
    HAL0->>TE: Topic ★/aima/hal/bms/state（Bms：remaining_capacity_percentage，门控数据源）
    TE->>TE: FuncCall AmRequest 仲裁（rule posture.yaml）<br/>校验：allow_held_state=false（牵手拒绝）/posture_exemptions(4)=电量充电豁免→放行
    TE->>MC: Service ★SetMcAction（action=110 趴下；双足时 MC 内先变形 21）
    MC-->>TE: CommonTaskResponse{task_id, state=SUCCESS}
    loop 变形+趴下 ~5s（实测 21→110）
        MC->>MC: FuncCall onnxruntime.Run（姿态 RL 策略）
    end
    MC->>TE: Topic ★/aima/mc/common/state（action 完成，posture=趴卧）
    Note over U,MC: —— 用户再说「站起来」 ——
    U->>AG: 语音「站起来」
    AG->>TE: Topic /aima/agent/semantic_intent（姿态意图·四足站立）【单向】
    TE->>MC: Service ★SetMcAction（action=101 四足站立；MC 内先 READY_STAND(115) 预备）
    MC-->>TE: CommonTaskResponse{task_id, state=SUCCESS}
    loop 起立 ~5s（实测 115→101→102）
        MC->>MC: FuncCall onnxruntime.Run（起立 RL 策略）
    end
    MC->>TE: Topic ★/aima/mc/common/state（STAND(101)→LOCOMOTION(102)，posture=四足站立）
    TE->>AG: Service ★PlayTts（tts_req：完成播报）
    AG-->>TE: PlayTtsResponse{trace_id}
```

门控与要点（实测规则 + 事件流）：
1. **任务规则前置检查**（`rule/t1/posture.yaml`）：`allow_held_state: false`（**牵手状态下拒绝姿态切换**）；`posture_exemptions "4":[battery, charging]`（趴下动画豁免低电/充电态——趴下充电场景）；任意姿态可触发、可被 move/posture/upper_body 抢占。
2. **实测节奏**：双足→趴稳 5s（21→102→110）；趴→四足站立 5s（115→101→102）；动作完成以 `mc/common/state.action_info` 状态回报 TE 闭环。
3. 姿态切换全链路在 rk 板内闭环（不涉 orin），语义段走云端。

#### 6.2.3 UC3「牵着 T1 的手一起走」（跟随）

**流程图 UC3「牵着 T1 的手一起走」——双路径链路拓扑：视觉跟随路径（本例未生效，虚线）+ 牵手带步路径（实际生效，实线）**

```mermaid
flowchart LR
    USER(["👤 用户: 语音「跟着我」<br/>+ 牵手带步"])
    subgraph RK["🖥 运控板 rk"]
        AGENT["Agent 意图"]
        TE["task_engine<br/>规则: follow.yaml (pri 50)<br/>电量≥6% · odom 可信"]
        MC["MC /mc_ros2_node (CPU3)<br/>双足 WBC + 力导引牵行步态"]
        CAN["CAN @CPU2 → 关节"]
    end
    subgraph ORIN["🧠 大脑板 orin"]
        PTR["person_track<br/>yolov8s-seg + clip_reid/swin ReID<br/>(TensorRT @GPU)"]
        FLW["follow_node 语义跟随规划<br/>(decoupled-PID+零相位低通)"]
    end
    USER --> AGENT -- "semantic_intent" --> TE
    TE ==>|"FollowTaskCtrl(type=1 跟随)"| X1{"任务分发"}
    X1 ==>|"set_follow_perception_task(启搜)"| PTR
    X1 ==>|"SetFollowTaskBiped"| FLW
    PTR -.->|"follow_candidates ★ (本例未锁定目标<br/>follow/state=1 未发现)"| TE
    FLW -.->|"velocity(source=follow)→MC<br/>(视觉跟随路径·本例未生效)"| MC
    USER == "牵手（关节力矩感知）" ==> MC
    MC ==>|"held_state: McHandHeldState<br/>(LEFT/RIGHT_HELD · is_held)"| TE
    MC -- "WBC 牵行关节指令" --> CAN
    MC ==>|"★ mc/common/state(held_state/is_moving)"| TE
```

**时序图 UC3「牵着 T1 的手一起走」——跟随任务启停（视觉未锁定）→ 牵手 held_state 检测 → 双足 WBC 力导引带步**

> 读法同 UC1 时序图。

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant AG as Agent (/aimrt_agent_node)
    participant TE as task_engine (/task_engine)
    participant PTR as person_track（orin·GPU）
    participant FLW as follow_node（orin）
    participant ODOM as legged_odom（rk）
    participant MC as MC (/mc_ros2_node · CPU3)

    U->>AG: 语音「跟着我」
    AG->>TE: Topic /aima/agent/semantic_intent（跟随意图）【单向广播】
    TE->>TE: FuncCall AmRequest 仲裁（rule follow.yaml：电量≥6%、odom 可信）→放行
    TE->>PTR: Service /perception/set_follow_perception_task（trigger=启动）
    PTR-->>TE: CommonResponse{code=0}
    TE->>FLW: Service SetFollowTaskBiped（跟随任务参数）
    FLW-->>TE: CommonResponse{code=0}
    ODOM->>PTR: Topic /odom·/tf（里程计，跟踪坐标系）【单向】
    loop 目标搜索（本例持续未锁定）
        PTR->>PTR: FuncCall TensorRT 推理（yolov8s-seg 检测 + clip_reid/swin ReID）
        PTR-->>TE: Topic ★/perception/follow_candidates（PerceptionFollow 归一化框；本例为空）
    end
    FLW->>TE: Topic ★/aima/algo/follow/state（FollowState：state=1 未发现目标）
    Note over TE,FLW: 视觉速度路径未激活：FLW 不发 velocity，<br/>MC 输入源未被 follow 抢占（SetMcInputSource 未生效）
    U->>MC: 牵手（物理接触·非 ROS：MC 关节力矩感知）
    MC-->>TE: Topic ★/aima/mc/common/state（held_state：McHandHeldState=LEFT_HELD_ONLY，is_held=true）
    TE->>TE: FuncCall 任务门控（allow_held_state=false：牵手时拒绝姿态/示教类 8 种任务）
    MC->>MC: FuncCall 变形（QUADRUPED_TO_BIPED_ROTATE(11)→BIPED_LOCOMOTION_WBC(300)）
    loop 牵手带步 3~4 分钟（力导引，人主导）
        U->>MC: 牵引（速度/方向由人施加）
        MC->>MC: FuncCall onnxruntime.Run（双足 WBC 步态，1kHz 环）<br/>注：不经 /aima/mc/locomotion/velocity
    end
    U->>TE: 结束跟随（语音/手动）
    TE->>FLW: Service SetFollowTaskBiped（trigger=停止）
    FLW-->>TE: CommonResponse{code=0}
    TE->>PTR: Service /perception/set_follow_perception_task（trigger=停止）
    PTR-->>TE: CommonResponse{code=0}
    FLW->>TE: Topic ★/aima/algo/follow/state（state=0 空闲）
```

读图要点（本例为「未锁定目标的牵手带步」）：
1. **双路径设计**：实线 = 视觉跟随路径（person_track 检测+ReID → follow_candidates★ → follow_node 语义规划 → `velocity`→MC 按 `SetMcInputSource` 仲裁），虚线标注本例实际状态——**跟随任务已启动但全程 `follow/state=1 未发现目标`**（牵手位置遮挡相机视角），未产生跟随速度指令。
2. **实际生效机制 = 牵手带步**：MC 通过关节电流/力矩实时估计 `McHandHeldState`（UNKNOWN/BOTH_NOT_HELD/LEFT_HELD_ONLY/RIGHT_HELD_ONLY/BOTH_HELD + `is_held`），牵手后机器人先自主变形双足（实测 `QUADRUPED_TO_BIPED_ROTATE(11)`→`BIPED_LOCOMOTION_WBC(300)`），在 WBC 全身控制下以力导引随人迈步——**该路径不经 `velocity` 话题，完全在 MC 实时层闭环**。
3. **held_state 的反向作用**：任务规则层以 `allow_held_state: false` 门控姿态切换/示教/遥控等 8 类任务（牵手时拒绝），即牵手既是控制输入也是任务态约束。
4. **资源侧证据**：UC3 期间 orin `follow_node` 0.2%→10.4%、`percep` 9.0%→23.9%、nav2 组件容器 5.0%→14.1%（目标搜索与规划激活），rk `task_engine` 17.7%→31.6%、`legged_odom` 37.5%→43.0%（行走中里程计满负荷）；基线为快照重测值、UC 列为连续采样口径（见 6.3）。

#### 6.2.4 三 usecase 涉及的节点 / Topic / Service 明细表（与上图逐条对应）

**UC1 对话「请跳个舞」：**

| 板 | 节点（进程） | Topic | Service |
|---|---|---|---|
| 云端 | aiem-gw LLM 会话（非 ROS） | — | HTTPS/TLS `/v1/session` |
| rk | `/aimrt_agent_node`（Agent） | 发：`asr_result`★、`semantic_intent`、`agent_show_text`、`tts_status`；订：`sm/system_state`、`permission_state` | 服务端：`PlayTts`★、`GetAndPlayNlgText`、`SetTtsParameters`；客户端：`PlayEmotion`、`AmRequest`/`ControlTask` |
| rk | `/task_engine`（任务引擎） | 订：`semantic_intent`、`agent_show_text`、`interaction_state`、`biologic_state`、`mc/common/state`★、`display_state`；发：`interaction/state`★、`task_engine/task_status` | 客户端：`SetMcAction`★、`SetMcMotion`★、`SetMcPresetMotion`★、`PlayTts`★、`PlayEmotion`/`SetFaceMode`、`LedStripCommand`★、`AmRequest`、`ControlTask` |
| rk | `/mc_ros2_node`（MC·CPU3） | 发：`mc/common/state`★（action_info/进度）、`hds/diagnostic_list` | 服务端：`SetMcAction`★、`GetMcAction`★、`SetMcInputSource`★ |
| rk | `/audiomanager_ros2_node`（音频） | 发：`play_state`、`volume` | 服务端：`PlayAudioFile`★、`GetVolume/SetVolume`★（TTS 音频经 PipeWire TCP 6001/4713 直放） |
| rk | `/hal_sensor_soc0`（HAL） | 发：`bms/state`★ | 服务端：`LedStripCommand`★（舞蹈灯语） |
| rk | `/lumina_server`（灯效） | 订：`play_state`、`tts_status`、`interaction/state` | 客户端：`PlayEmotion`、`PlayLight` 等 13 路 |
| rk | 胸屏 display_ui（非 ROS） | `display_state`（屏侧回显） | 经 `PlayEmotion`★ 服务（TE 承接）联动 |
| rk | `/sm` | 发：`sm/system_state`（全链门控） | `SmNodeService/*` proto 广播 |
| rk | `/app_proxy`、`/cloud_proxy`（旁路镜像） | 订：`interaction/state`★、`trace/events` | App/云同步（不参与控制） |

**UC2 姿态切换「趴下→四足站立」：**

| 板 | 节点 | Topic | Service |
|---|---|---|---|
| rk | `/aimrt_agent_node` | 发：`semantic_intent` | （意图→TE） |
| rk | `/task_engine`（规则 `posture.yaml`） | 订：`mc/common/state`★、`bms/state`★（低电门控）；发：`interaction/state`★ | 客户端：`SetMcAction`★（21→110 趴下 / 115→101→102 站起）、`GetMcAction`★；服务端：`PlayAnimation`（App/语音入口同构） |
| rk | `/mc_ros2_node` | 发：`mc/common/state`★（`action_info.switch_ready`、`posture`、`form_state`）、`hds/diagnostic_list` | 服务端：`SetMcAction`★、`GetMcAction`★ |
| rk | `/hal_sensor_soc0` | 发：`bms/state`★（电量前置检查） | — |
| rk | `/sm` | 发：`sm/system_state` | `MigrateSystemState`（开机自检等状态门控） |

**UC3「牵着 T1 的手一起走」（跟随）：**

| 板 | 节点 | Topic | Service |
|---|---|---|---|
| rk | `/aimrt_agent_node` | 发：`semantic_intent` | 客户端：`FollowTaskCtrl`（→TE） |
| rk | `/task_engine`（规则 `follow.yaml`） | 订：`follow/state`★、`follow_candidates`★、`mc/common/state`★（**held_state 牵手态**）、`odom/status`；发：`interaction/state`★ | 服务端：`FollowTaskCtrl`；客户端：`/perception/set_follow_perception_task`、`SetFollowTaskBiped`、`SetMcInputSource`★（视觉路径抢占输入源） |
| orin | `/aimrt_person_track_node(+_srv)`（person_track） | 发：`follow_candidates`★、`target_info`；订：`enable_follow`、`follow_target_selection` | 服务端：`/perception/set_follow_perception_task` |
| orin | `/follow_node`（follow） | 发：`follow/state`★、`enable_follow`、`follow_target_selection`、`mc/locomotion/velocity`（source=follow） | 服务端：`SetFollowTaskBiped` |
| orin | `/perception`、`/coreon_fusion_ros2_node`（感知支撑） | `percep/local_costmap`、`target_info`、融合 27 路输入 | `ObstacleCheck`、`SetAvoidance`（避障联动） |
| orin | nav2 组件容器（避障通路） | `velocity`（source=pnc，视觉跟随时叠加避障） | `/navi/command`★ 系列（本例未用） |
| rk | `/mc_ros2_node`（MC·CPU3） | 订：`mc/locomotion/velocity`；发：`mc/common/state`★（`held_state: McHandHeldState`、`is_moving`） | 服务端：`SetMcInputSource`★、`GetCurrentInputSource`★ |
| rk | `/legged_odom_node` | 发：`/odom`、`/tf`（→orin 跟随/导航链） | — |

> 注：本例实际生效的**牵手带步不经过 ROS 接口**（MC 实时层关节力矩闭环），上表接口承载任务编排、目标搜索与状态广播；视觉速度路径（`velocity`←follow_node）本次未激活。

### 6.3 三个 usecase 下的运行资源占用（实测）

> **方法与口径**：① **BASE 基线 = 低开销离散快照法重测**（2026-10-30 17:44–17:50，机器人静止、开机 37 分钟：12 轮 × 30s，每轮仅一次 `top -b -n 2 -d 5` 取第 2 帧 5s 瞬时值 + GPU/NPU/内存一行，轮间 20s 完全静默）；**UC1–UC3 列沿用连续采样法**（`top -b -d 3 -w 220` + 5 路 `ros2 topic echo` 订阅，随场景 16 分钟）。**两法实测差异即采样器自身开销**：orin 板级 busy 高估约 **+40pp**（67.8%→26.7%）、GR3D +9pp、RAM +350MiB（echo 进程驻留）、功耗 +1.3W；rk 板级仅 +2.6pp（74.8%→72.2%）——**rk 常态高负载是真实情况，orin 基线则被上轮采样显著抬高**。进程级数值两法一致（采样器进程均已剔除）；**跨阶段对比以进程级列与相对变化为准，UC 列的板级绝对值含该恒定开销**。② rk GPU=devfreq `*gpu*/load`（Mali-G52），NPU=debugfs `rknpu/load`（双核）；③ orin GPU=`tegrastats GR3D_FREQ`、功耗=`VDD_IN`；④ 进程→模块归属按 `/robot/software/<app>`（cwd+args 双重判定）。
> **GPU「显存」口径**：两板均为**统一内存**架构（Jetson UMA / rk3576 CPU-GPU 共享 DDR），无独立显存统计——GPU/NPU 使用的内存就是进程 RSS 与整机内存里的同一池（orin `cudaMalloc`、rk NPU `dma_buf` 均从统一内存分配）。
> 阶段窗口：BASE=101s 静止基线；UC1=对话+跳舞；UC2a=趴下；UC2b=四足站立；UC3=牵手跟随带步（含任务启动与行走）。

**四个统计维度速查（单位与所在表）**：

| 要求的维度 | 单位 / 口径 | 进程级 | 子系统级 | 整机级 |
|---|---|---|---|---|
| **CPU 占用** | %，**100% = 1 个核打满**（rk 8 核满载 800%、orin 6 核 600%）；3s 周期采样、阶段内取平均 | 6.3.1 / 6.3.2 的「CPU%」五列 | 6.3.3 | 6.3.4 第 1~2 行 |
| **内存占用** | MiB；进程级 = **RSS 常驻内存**（含共享库映射；iox-roudi 为共享内存段）；整机级 = used/total | 6.3.1 / 6.3.2 的「内存 RSS」列 | 6.3.3「RSS」 | 6.3.4 第 3~4 行 |
| **GPU 算力占用** | 利用率 %（rk = Mali-G52、orin = GR3D/Ampere、NPU 双核各自 %）。**硬件只提供整机利用率计数器，无按进程的利用率**——进程级给「归因」（哪个进程持有 GPU/NPU，实测设备 fd + 加载的运行时库） | 6.3.1 / 6.3.2「GPU/NPU 归因」列 | — | 6.3.4 第 5~7 行 |
| ⚠️ rk 的 GPU 定位 | rk3576 集成的是**图形 GPU（Mali-G52 MC3）**——只做胸屏/UI 渲染（flutter-pi），**不做 AI 推理**、无 nvidia-smi；rk 的 AI 走 CPU（RL 策略）+ NPU（视觉模型）。orin 的 Ampere GPU 才是 AI 计算 GPU（TensorRT） | — | — | — |
| **GPU「显存」占用** | 两板统一内存：**无独立显存数值**，GPU/NPU 用量已含在内存列中（同池） | 同「内存 RSS」列 | — | 6.3.4 第 8 行（与 RAM 同数） |

#### 6.3.1 rk 板：软件模块/进程级（CPU 占用% × 5 阶段；内存 RSS；GPU/NPU 归因）

> 基线列为 2026-10-30 17:44 低开销快照重测值；UC1–UC3 为连续采样值。mc/device_manager 读数与机器人姿态/状态相关（上轮基线为趴卧态且叠加连续采样，本次为静止态快照），故 realtime 类数值下调属口径修正。

| 进程（模块） | 所属 slice | CPU% 基线 | CPU% UC1跳舞 | CPU% UC2a趴下 | CPU% UC2b站立 | CPU% UC3牵手 | 内存 RSS(MB) | GPU/NPU 归因 |
|---|---|---|---|---|---|---|---|---|
| **aimrt_agent**（Agent 对话智能体+语音前端+NPU 视觉） | interaction | 104.3 | **128.5** | 107.4 | 117.3 | 101.2 | **1229** | **NPU 独占**（6 个视觉模型，模型常驻即内存主项之一）+ CPU 推理（VAD/AEC/KWS） |
| **mc**（MC 运动控制 · CPU3 独占 · RL 实时推理） | realtime | 43.4 | 74.3 | 76.5 | 74.5 | 77.1 | 252 | 纯 CPU（onnxruntime @CPU3，无 GPU/NPU） |
| device_manager（设备管理） | realtime | 47.1 | 59.2 | 61.1 | 60.6 | 57.9 | 44 | — |
| legged_odom（足式里程计） | system | 37.5 | 42.2 | 39.1 | 41.9 | **43.0** | 64 | — |
| drp（rosbag 持续录包） | system | 28.7 | 32.3 | 33.3 | 33.0 | 31.2 | 70 | — |
| app_proxy（App 状态镜像 29 路订阅） | system | 28.7 | 30.5 | 30.2 | 30.1 | 30.3 | 123 | — |
| **task_engine**（任务引擎） | interaction | 17.7 | 27.5 | 24.7 | 26.8 | **31.6** | 87 | — |
| display_ui（flutter-pi 胸屏渲染） | interaction | 9.1 | **28.5** | 21.3 | 29.7 | 28.2 | 225 | **Mali GPU**（胸屏 2D/3D 渲染，rk GPU 占用主项） |
| hal_camera（rk 侧相机 HAL） | system | 13.3 | 15.0 | 14.6 | 15.1 | 14.8 | 451 | MPP/RGA 硬件通路（VPU/RGA，非 GPU） |
| lumina（灯效服务） | system | 10.9 | 12.0 | 12.3 | 12.2 | 13.2 | 92 | — |
| hal_rgbd（rk 侧，实为曝光控制 exposure①） | system | 9.0 | 10.2 | 9.8 | 10.1 | 10.0 | 40 | — |
| rc（体感遥控接入） | system | 6.2 | 7.0 | 6.8 | 7.1 | 6.8 | 52 | — |
| ota（ota_master） | system | 4.8 | 5.2 | 5.2 | 5.2 | 5.1 | 68 | — |
| cloud_proxy（云端代理） | system | 4.8 | 4.9 | 5.0 | 5.0 | 4.9 | 665④ | — |
| insta360（云台接入） | system | 3.6 | 4.0 | 4.0 | 3.9 | 3.9 | 44 | — |
| hal_sensor（HAL soc0：BMS/灯/舵机） | system | 3.4 | 3.6 | 3.5 | 3.6 | 3.5 | 74 | — |
| housekeeper（管家） | system | 3.1 | 3.4 | 3.0 | 3.1 | 3.1 | 131 | — |
| abox（日志） | system | 2.9 | 3.0 | 3.0 | 3.1 | 3.0 | 75 | — |
| sm（状态机） | system | 2.8 | 3.0 | 2.9 | 3.0 | 2.9 | 61 | — |
| audio_manager（音频管理/播放） | interaction | 2.4 | 3.0 | 2.7 | 2.7 | 2.7 | 58 | — |
| hds（健康诊断） | system | 2.3 | 2.4 | 2.3 | 2.3 | 2.4 | 54 | — |
| pipewire_audio（PipeWire 音频服务） | 系统 | 1.0 | 2.3 | 1.5 | 2.1 | 2.2 | 78 | — |
| connx（圈圈 quanji_uwb 接入②） | system | 1.5 | 1.7 | 1.7 | 1.6 | 1.7 | 74 | — |
| sys_guard（场景看护） | system | 0.9 | 1.9 | 1.2 | 1.6 | 1.2 | 50 | — |
| scene_engine（场景引擎） | system | 1.2 | 1.2 | 1.3 | 1.3 | 1.3 | 41 | — |
| data_exporter | auxiliary | 0.6 | 0.8 | 0.7 | 0.8 | 0.7 | 60 | — |
| resource_manager（picture_service） | system | 0.5 | 0.7 | 0.7 | 0.7 | 0.7 | 56 | — |
| mosquitto（conduit_router TLS） | 系统 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 13 | — |
| rtsp_server | system | 0.2 | 0.2 | 0.2 | 0.2 | 0.2 | 126 | MPP 硬编码（VPU） |
| iox-roudi（iceoryx 共享内存总线③） | 系统 | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 | 794 | — |
| process_manager | 系统 | 0.5 | 0.7 | 0.7 | 0.7 | 0.7 | 46 | — |
| 其他（ptp4l/hostapd/dnsmasq/chronyd/NetworkManager/secret_store 等） | 系统 | 各 <1 | | | | | ~120 合计 | — |

① rk 侧该进程实为 hal_rgbd 包内**曝光控制 exposure**（进程名 hal_rgbd；2026-10-31 实证：经 I2C/V4L2 直控 5×ToF 曝光并处理 inter_board 板间同步，持有 /dev/video2；soc0 manifest stage1 实含，§3.2 表未列，本次实测补录）。② 圈圈接入进程位于 connx 应用目录（`aimrt_main --cfg=quanji.yaml`）。③ RSS 主要为 iceoryx 共享内存段映射（全板共享），非私有内存。

#### 6.3.2 orin 板：软件模块/进程级（列含义同 6.3.1）

| 进程（模块） | CPU% 基线 | CPU% UC1跳舞 | CPU% UC2a趴下 | CPU% UC2b站立 | CPU% UC3牵手 | 内存 RSS(MB) | GPU 归因 |
|---|---|---|---|---|---|---|---|
| hal_camera（相机 HAL：双目/鱼眼/彩色+头部 IMU） | 42.6 | 40.6 | 41.4 | 41.5 | 41.5 | **693** | 持有 GPU 设备（图像处理/上传，RSS 含 CUDA 缓冲） |
| hal_rgbd（5 目 ToF HAL） | 38.3 | 38.8 | 38.9 | 38.9 | 38.7 | 356 | —（点云走 CPU/ISP） |
| nav2_perception（点云合并+perception_node） | 16.9 | 18.6 | 18.5 | 19.0 | 19.5 | 97 | — |
| **percep（感知算法：分割/深度/检测）** | 9.0 | 18.7 | 15.9 | 15.3 | **23.9** | 342 | **GR3D（TensorRT 推理，GPU 算力主项之一）** |
| coreon_fusion（多源融合 27 路输入） | 5.4 | 7.6 | 7.0 | 7.2 | 8.7 | 391 | —（融合走 CPU） |
| person_track（人体跟踪+ReID） | 3.3 | 4.5 | 4.3 | 4.4 | 5.1 | 311 | **GR3D（yolov8s-seg + clip_reid/swin ReID）** |
| **nav2 栈组件容器**（bt_navigator/controller/planner/behavior/smoother/map_server/双层 costmap）① | 5.0 | 9.8 | 7.3 | 8.6 | **14.1** | 114 | —（规划走 CPU） |
| follow（follow_node+launch，语义跟随规划） | 0.2 | 5.5 | 3.8 | 4.5 | **10.4** | 122 | — |
| brightness（亮度检测） | 1.4 | 2.0 | 1.9 | 2.0 | 2.2 | 38 | — |
| path_follow（trajectory_tracker 运镜） | 1.0 | 1.8 | 1.6 | 1.7 | 2.3 | 34 | — |
| hal_sensor（soc1：ADU/温度） | 1.4 | 1.5 | 1.5 | 1.5 | 1.5 | 42 | — |
| nav2_lifecycle_manager（t1_perception） | 1.2 | 1.0 | 1.0 | 1.0 | 1.0 | 35 | — |
| nav2 launch（ros2 launch python） | 0.3 | 0.4 | 0.4 | 0.4 | 0.4 | 80 | — |
| process_manager（soc1 实例） | 0.4 | 0.4 | 0.4 | 0.4 | 0.4 | 175 | — |
| sys_guard / ota_slave / data_exporter | 0.8 / 0.7 / 0.5 | → 微升 | → | → | → | 56 / 65 / 72 | — |
| iox-roudi（共享内存总线②） | 0.2 | 0.1 | 0.1 | 0.1 | 0.1 | 850 | — |
| 其他（ptp4l/NM/tetherd 等） | 各 <1 | | | | | ~20 | — |

① Nav2 生命周期节点以组件容器（component_container_mt，节点名 nav2_container）组合运行，仅 perception 链/lifecycle 独立进程——与 §3.3 进程表一致。② 同 rk 注③。

#### 6.3.3 子系统级（rk 按 em cgroup slice 汇总；CPU% 口径同上=单核 100%，为成员进程求和；RSS 为成员求和）

| 子系统（slice） | 成员 | CPU% 基线 | CPU% UC1跳舞 | CPU% UC2a趴下 | CPU% UC2b站立 | CPU% UC3牵手 | 内存 RSS(MB) |
|---|---|---|---|---|---|---|---|
| rk `robot_realtime.slice`（CPUWeight 10000 · MemoryMin 500MB） | mc + device_manager | 90.5⑤ | 133.5 | 137.6 | 135.2 | 135.0 | 296 |
| rk `robot_interaction.slice`（CPUWeight 3000） | agent + display_ui + audio_manager + task_engine | 133.5 | **187.6** | 156.0 | 176.5 | 163.6 | 1599 |
| rk `robot_auxiliary.slice`（CPUWeight 500 · **CPUQuota 40%**） | data_exporter | 0.6 | 0.8 | 0.7 | 0.8 | 0.7 | 60 |
| rk `robot_system.slice` + 系统服务（CPUWeight 5000/10000） | 其余全部业务 + iox-roudi/音频/网络等基座 | 171.4 | 192.8 | 187.8 | 191.4 | 190.0 | —（含 iox 794 共享） |
| orin 全部业务（robot_system.slice，soc1 manifest 未再分组） | 上表全部 | ~129 | ~150 | ~138 | ~143 | **~173** | — |

⑤ 基线列为静止态快照重测值；UC1–UC3 列为连续采样（含采样器恒定开销），realtime 类读数亦与机器人姿态相关（运动/变形期间 mc 满负荷）。

#### 6.3.4 整机级（单位见表内；板级 CPU 为「全部核合计」口径，100% = 整板所有核满载，与进程表的「单核=100%」不同）

| 维度（单位） | 板 | BASE（快照重测） | UC1 跳舞⑥ | UC2a 趴下 | UC2b 站立 | UC3 牵手 |
|---|---|---|---|---|---|---|
| **CPU 占用率**（busy%，全核合计） | rk（8 核） | **72.2%** | **83.6%** | 78.7% | 81.9% | 80.6% |
| | orin（6 核） | **26.7%** | 72.9% | 71.3% | 71.7% | **76.4%** |
| **内存占用**（used/total，MiB） | rk | 3616/7919 | 3846/7919 | 3852/7919 | 3850/7919 | 3853/7919 |
| | orin（tegrastats 口径） | 3385/7604 | 3751/7604 | 3752/7604 | 3756/7604 | 3771/7604 |
| **GPU 算力占用**（利用率 %） | rk Mali-G52（**图形 GPU，仅为胸屏渲染/UI**，非 AI；证据：/dev/dri + kbase 内核线程 + devfreq，§2.1） | 34% | 33% | 33% | 34% | 34% |
| | orin GR3D（Ampere，TensorRT 推理） | **5%** | 14% | 17% | 13% | 13% |
| **NPU 算力占用**（rk3576 双核各自利用率 %） | rk（仅 Agent 使用） | Core0 26 / Core1 12 | 31/5 | 33/9 | 33/6 | **36**/6 |
| **GPU「显存」占用** | rk / orin | **无独立数值**——统一内存，与上行「内存占用」同池同数（GPU/NPU 用量已含其中） | ← | ← | ← | ← |
| 整板功耗（W，仅 orin 可测 VDD_IN） | orin | **7.9** | 9.4 | 9.3 | 9.3 | 9.5 |

⑥ **UC1–UC3 列为连续采样法**，其板级绝对值含采样器恒定开销（实测 orin 板级 busy 约 +40pp、GR3D +9pp、RAM +350MiB、功耗 +1.3W；rk 约 +2.6pp）——**阶段间的相对变化有效**（如 orin UC3 较 UC 基线 +8.6pp），跨阶段对比请用相对增量与进程级列。

**结论要点**：① rk 常态即高负载（重测确认 **~72% busy 为真实水平**，采样开销仅 +2.6pp），常驻大头为 Agent（104%≈1 核 + 1.2GB，含语音前端与 NPU 视觉模型驻留）、device_manager 47%、mc 43%（静止态；运动/变形时升至 75%+）、odom 38%、drp 29%、app_proxy 29%；跳舞时增量主要落在 **interaction slice（133.5→187.6，+40%）**：Agent 语义链路 + 胸屏表情渲染翻倍 + task_engine。② **orin 真实基线很轻**（重测 26.7% busy、GR3D 5%、7.9W——上轮连续采样将其分别抬高约 40pp/9pp/1.3W）；任务增量集中在 **percep（+9~15pp）与 nav2 栈（+7~12pp）、follow（+10pp @UC3）**，UC3（牵手行走）是 orin 最重场景（板级相对基线 +8.6pp）。③ NPU 仅 Agent 使用（基线 Core0 26%），行走/交互时升至 36%（人脸+手势连续推理）。④ 内存对各 usecase 几乎不敏感（rk 3.6GiB、orin 3.3GiB 常驻，模型全部驻留）；GPU 利用率两板均处低位（rk 34% 图形渲染、orin 5% 推理），算力余量大。⑤ 功耗（orin 基线 7.9W，UC 期间 9.3~9.5W 含采样开销，真实增量约 +1W）。

### 6.4 启动脚本与部署资源

#### 6.4.1 开机自启链路（systemd 实测）

| 阶段 | rk（soc0） | orin（soc1） |
|---|---|---|
| 挂载/硬件初始化 | `system_entry.service`（`/robot/software/entry/system_entry.sh` 硬件上电初始化）；`robot-sdumount@.service`（SD 卡）；`camera-manage.service` | `robot-mount-software.service`（**按 A/B 槽位挂载软件分区**，Before local-fs）→ `system_entry.service`；`camera-start.service`（`/opt/camera_driver/load_module.sh` insmod 相机驱动） |
| 时钟/网络 | NetworkManager；`ptp4l`（linuxptp，PTP 主从）；chrony | `sync_time.service`（**阻塞等 ping 通 rk(10.1.1.100) 再执行** `/usr/local/bin/sync_time.sh`，Before ptp4l）+ ptp4l |
| 零拷贝总线 | `iox-roudi.service`：`/robot/software/entry/run_roudi.sh` → `/opt/iceoryx/bin/iox-roudi -c …/roudi_config.toml -m on`；User=1000；**OOMScoreAdjust=-1000**；Restart=on-failure×5 | 同左（soc1 实例） |
| 应用编排 | **`em.service`**：EnvironmentFile=`/robot/software/entry/cfg/em.env`；ExecStart=`/robot/software/process_manager/bin/start_process_manager.sh`；**Restart=always（5s）**·KillMode=control-group | 同左（After=system_entry+iox-roudi+camera-start+sync_time） |
| 板间/外围 | `tetherd.service`（内网以太网守护，`--bind 10.1.1.100`）；hostapd@/dnsmasq@（p2p0 热点）；mosquitto（conduit_router_tls.conf）；`fce863r-bt.service`（蓝牙） | `tetherd.service`（`--bind 10.1.1.101`）；robot_fancontrol/robot_ptp 等（disabled 备用） |
| 资源切片 | `robot.slice`（CPUWeight 10000）+ 四个业务子 slice（见 6.4.3） | `robot.slice`（soc1 业务不再分组） |

`start_process_manager.sh` 逻辑：source `/robot/software/entry/cfg/env.sh` → 按机型/硬件版本查 `CFG_MAP`（Q1/T1 × v1d0~v2d1 × soc0/1 → 对应 yaml；本机 `T1:T1_v2d0` → `t1_v2d0_soc0/soc1_config.yaml`）→ 启动 `process_manager`，其内置 AimRT net(HTTP 50080)/mqtt(127.0.0.1:1883)/ros2 三插件与 `EmAppService` RPC，再按 stages 逐个 `sudo -u #1000 … /robot/software/<app>/bin/em_run.sh` 拉起应用并守护。

#### 6.4.2 em 启动编排（`process_manager_config.yaml` + `t1_v2d0_soc{0,1}_config.yaml` 实测）

**rk 四个启动阶段（stage 间延时后串行）：**

| Stage | 延时 | 应用（按序） |
|---|---|---|
| 1 | +1500ms | sys_guard、**mc**、hal_rgbd、audio_manager、device_manager、hal_sensor、sm、hds、**agent**、display_ui |
| 2 | +5000ms | **task_engine**、housekeeper、app_proxy、rc、data_exporter、hal_camera、rtsp_server、drp、ota、odom、lumina、scene_engine、insta360、connx、resource_manager、abox |
| 3 | +5000ms | conduit_router（mosquitto TLS） |
| 4 | +8000ms | cloud_proxy |

**orin 两个启动阶段：**

| Stage | 延时 | 应用 |
|---|---|---|
| 1 | +1500ms | sys_guard、hal_rgbd、hal_camera、hal_sensor |
| 2 | +8000ms | data_exporter、ota、**coreon_fusion**、**follow**、path_follow、tracking(person_track)、brightness_detection、**navigation(nav2)**、**percep** |

> 部署在位但 manifest 注释未启动：orin 的 `slam`、`aprilgrid_loc`、`map_manager`、`recharge`、`drp`、`move_camera`/`face`/`yolov8`（rk 侧同）——与 6.3.2 资源表一致。另有低层二开模式开关：`MC_LOW_LEVEL_DEV=true` 时 PM 跳过自动启动 mc、mc 的 backend 从 iceoryx 切 ros2（em_run.sh 内置）。

**每个应用的启动脚本**统一为 `/robot/software/<app>/bin/em_run.sh`（cd 本目录 → 注入 `LOG_PATH`/`LD_LIBRARY_PATH=../lib:./` → 应用特有 env → `exec ./<binary> --cfg_file_path=cfg/*.yaml`）。两个代表性样例：
- `mc/bin/em_run.sh`：`SUB_NO_SCHEDULER=true`、`ROBOT_ENABLE_HDS/EVENT/MONITOR=false`、`AIMRT_PLUGIN_SHM_DEFAULT_WAIT_TIME_US=300`、**`AIMRTE_THREAD_SETTING_SHM/UDPany="[2,3]|SCHED_RR:80"`**（该预设部分生效：实测 mc 的 `rt_iceoryx_sub@85`、`rt_joint_pub`/`passive_default_t0@80` 3 线程为 SCHED_RR 钉 CPU3，device_manager 另有 6 个 RR 线程钉 CPU2，见 §2.4）、`TOPIC_ENABLE_BACKEND=iceoryx`。
- `task_engine/bin/em_run.sh`：通用模板 + SD 卡权限自修复（OTA 后 /robot/userdata/sd root→run）→ `./task_engine --cfg_file_path=cfg/interaction_module_config.yaml`。

**每个启动脚本（em 应用）拉起的进程 → ROS 2 节点 → 接口规模**（P/S/V/C = 发布/订阅/服务端/客户端，数据引自 §4.1/§4.2 实测；「非 ROS」= iceoryx 或私有协议通道）：

| rk 应用（em_run.sh） | 拉起节点 | P/S/V/C | orin 应用 | 拉起节点 | P/S/V/C |
|---|---|---|---|---|---|
| mc | `/mc_ros2_node` | 4/2/13/0 | sys_guard | `/sys_guard_node_soc1` | 3/1/2/0 |
| agent | `/aimrt_agent_node` | 15/8/18/24 | hal_rgbd | `/hal_rgbd_node(+_0)` | 9/17/3/1 |
| task_engine | `/task_engine` | 12/39/30/51 | hal_camera | `/hal_camera(+_1)` | 8/4/7/2 |
| sm | `/sm` | 3/2/4/28 | hal_sensor | `/hal_sensor_soc1` | 3/0/1/0 |
| audio_manager | `/audiomanager_ros2_node` | 9/1/15/1 | coreon_fusion | `/coreon_fusion_ros2_node` | 7/27/7/1 |
| hal_sensor | `/hal_sensor_soc0` | 6/2/9/0 | follow | `/follow_node` | 9/1/1/0 |
| device_manager | `/device_manager_node` | 1/0/0/1 | path_follow | `/trajectory_tracker` | 5/2/1/0 |
| hds | `/hds` | 2/3/3/1 | tracking | `/aimrt_person_track_node(+_srv)` | 6/4/1/1 |
| housekeeper | `/housekeeper0` | 6/0/23/2 | brightness_detection | `/aimrt_brightness_status_node(+_srv)` | 1/1/0/1 |
| app_proxy | `/app_proxy`(+`_client`) | 3/29/3/2 | navigation | Nav2 12+ 节点（`/robot_navigation`、`/map_server`、bt/controller/planner/behavior/smoother/双层 costmap、lifecycle） | 见 §4.4 |
| rc | `/rc` | 3/0/2/6 | percep | `/perception` | 10/5/1/0 |
| ota | `/ota_master` | 3/5/2/10 | data_exporter | `/data_exporter`（soc1 实例） | 1/0/12/10 |
| odom | `/legged_odom_node` | 8/1/2/0 | ota | `/ota_slave` | 2/0/2/0 |
| data_exporter | `/data_exporter`（soc0 实例） | 1/0/12/10 | | | |
| lumina | `/lumina_server` | 1/9/0/13 | | | |
| insta360 | `/insta360_ros2_node`(+`_gimbal_tts`) | 6/2/3/3 | | | |
| connx | `/quanji_uwb_ros2_node` | 5/0/1/0 | | | |
| resource_manager | `/picture_service` | 3/1/10/3 | | | |
| drp | `/record_ros2_node_*` | 1/4/2/0 | | | |
| cloud_proxy | `/cloud_proxy` | 3/7/0/1 | | | |
| rtsp_server | `/rtsp_video_server` | 0/0/0/1 | | | |
| sys_guard | `/sys_guard_node_soc0` | 3/1/2/0 | | | |
| display_ui / scene_engine / hal_camera(rk) / hal_rgbd(rk) / abox / conduit_router / secret_store | 非 ROS（iceoryx/私有协议/mosquitto） | — | | | |

#### 6.4.3 进程优先级与资源切片总表（实测 `ps -eLo` + cgroup）

| 优先级机制 | 实测值 | 说明 |
|---|---|---|
| 调度策略 | 业务线程绝大多数 **SCHED_OTHER、Nice 0**；例外：**mc 3 线程 + device_manager 6 线程为 SCHED_RR（rtprio 80/85）**，分别钉 CPU3/CPU2（§2.4 实测）；另有内核 FF 线程与 rtkit-daemon | mc 的 `AIMRTE_THREAD_SETTING_*=[2,3]\|SCHED_RR:80` 环境预设确有生效线程（`rt_*` 命名线程显式创建为 RR，尽管 `SUB_NO_SCHEDULER=true`） |
| CPU 亲和性 | 普通进程 `0,1,4-7`（isolcpus=2,3）；**mc 36 线程钉 CPU3**；**CAN0/CAN1 中断(irq83/84)+rk_timer 钉 CPU2** | §2.4 实测一致 |
| cgroup CPUWeight | `robot.slice` 10000 → realtime **10000**（+**MemoryMin=500MB**）/ system 5000 / interaction 3000 / auxiliary 500 + **CPUQuota 40%**（cpu.max=40000/100000） | realtime=mc+device_manager；interaction=agent+display_ui+audio_manager+task_engine；auxiliary=data_exporter；其余 → robot_system |
| 守护优先级 | em.service Restart=always/5s；iox-roudi OOMScoreAdjust=-1000（最后被杀）；process_manager 按配置重启子应用 | hds+sys_guard 健康诊断兜底 |

各启动脚本拉起的进程→ROS 2 节点→topic/service 全量挂载见 §4.1/§4.2（进程→节点总表）与 §5（接口全表）；每个 em 应用 = 一个 em_run.sh + 一组节点 + 一片接口面（如 task_engine = `/task_engine` 节点，P12/S39/V30/C51）。

#### 6.4.4 部署资源清单与大小（`du`/`find -printf` 实测）

**rk 板 `/robot/software` = 4.8G**（另有 `/opt/ros/humble` 276M、`/opt/iceoryx` 74M）：

| 目录 | 大小 | 内容要点 |
|---|---|---|
| mc | **1.3G** | 运控二进制 + 6 套机型 cfg（`qd1_t2d0` 激活 264M、`q1_t2d0` 298M、`qd1_t1d5` 205M、`q1_t0d0` 159M、其余 95M——主体为 **643 个 RL 策略 onnx** 与 mimic CSV） |
| aimrt_agent | 985M | Agent 二进制 + lib（MNN/ORT/RKNN/sherpa/讯飞/ffmpeg6.0/opencv4.8.1 自带）+ cfg 496M（omnis 模型、t1 人脸/手势 6 模型 ~190M）+ third_party（讯飞 ESR 93M + VTN SDK 86M） |
| scene_engine | 273M | 场景引擎（lib 223M；iceoryx 通道、无 ROS 节点） |
| task_engine | 230M | 二进制 19.9M + 规则/技能资产 + 74 个提示音 wav |
| cloud_proxy | 162M | 云端代理 |
| aimrt_pulsecore | 160M | 音频核（部署未运行） |
| ota 114M · hal_rgbd 108M · insta360 102M · connx 102M · drp 98M · audio_manager 98M · process_manager 96M · data_exporter 94M · resource_manager 92M · hds 90M · rc 89M · sm 86M · hal_camera 85M · legged_odom 84M · hal_sensor 84M · sys_guard 83M · display_ui 82M（Flutter bundle）· device_manager 77M · interface 49M（aimdk_msgs）· app_proxy 47M · lumina 34M · housekeeper 33M · rtsp_server 23M · conduit_router 4.2M · entry 2.7M · secret_store/abox/param <1M | 2.3G 合计 | 其余业务应用（每应用自带 AimRT 运行时三件套） |
| **按类型汇总** | — | 可执行+so ~1.55G（so 1697 个）；**onnx 模型 ~1.0G/643 个**；yaml 688；json 169；csv 581（示教动作）；**wav 105**（task_engine 74 + agent 31）；**mp4 45**（display_ui 表情视频 42 + task_engine 3） |

**orin 板 `/robot/software` = 3.0G**（另有 `/opt/ros/humble` 285M）：

| 目录 | 大小 | 内容要点 |
|---|---|---|
| algorithm | **1.2G** | colcon 工作空间（nav2 1.1.20 全家 + jps_planner/recharge 定制包 + follow/path_follow/percep/person_track/brightness/explore 等）+ **models 715M**（clip_reid ×3 + swin_solider ×3 + yolov8 系列）+ perception/models 79M（Stereo/Segment/detect） |
| aimrt_slam | 457M | **部署未运行**：support_files 183M（eigenplaces/superpoint/yolo26n-seg engine 双架构副本） |
| coreon_fusion | 286M | 融合算法 |
| ota 114M · aprilgrid_loc 107M（未运行，含 ppocr engine）· hal_rgbd 106M · drp 97M（未运行）· process_manager 95M · data_exporter 94M · hds 90M · hal_camera 85M · map_manager 84M（未运行）· hal_sensor 84M · sys_guard 83M · interface 49M · housekeeper 33M · entry/param/配置 <1M | 1.03G 合计 | 其余业务应用 |
| **按类型汇总** | — | so 1279 个；**TensorRT engine 24 个（~993M）**；yaml 393；json 60；png 7；csv 10 |

> 部署口径说明：① 两板 `/robot/software` 均由 A/B 槽位分区挂载（robot-mount-software），上述为当前激活槽；② 运行期数据（`/robot/persist` 日志/录包、`/robot/userdata`）不属于部署资源未计入；③ 每应用目录均自带 `aimrt_main/aimrte/iox-roudi` 三件套与 `*_pb_ts.so`（AimRT 插件），是 so 数量大的主因；④ 跨架构副本（aimrt_slam x86/arm、aprilgrid x86/arm）与多机型模型（mc 6 套）占部署空间约 40%，为「一套镜像适配多机型/研发调试」的代价。

---

> **本章数据存档**：动态采样原始日志（c2_rk_top/c2_rk_sys/c2_orin_top/c2_orin_tegra/c2_events/c2_markers + 进程与 cwd 快照）与分析脚本见仓库 `appendix-c-data/`；分析口径为阶段窗口均值，可按 markers 时间戳复算。
<a id="sec7"></a>

## 7. 整体架构与软件模块接口关系图（第 1–6 章实测的综合视图）

> 本章依据：① 本文 §1–§6 实机采集结果（**与其他文档冲突时以此为准**）；② SDK《README.md》与《PrimeBot_SDK快速入门与集成指南.md》§1 系统架构总览、§4 板载自启动接入；《接口说明.md》§1 API 列表（★ 官方 30 接口面）。
> 与官方文档的两处口径差异（均以实测为准）：官方示例节点名 `/hal_audio` 实测为 `/audiomanager_ros2_node`；官方未标注业务大脑（Agent/task_engine）的位置，实测它们部署在**运控板 rk**，「大脑板 orin」承载的是感知与导航算法。
> **本章三张图递进**：7.1 逻辑视图（分层总览）→ 7.2 软件模块连接图（模块与硬件/外部连接视角）→ 7.4 模块接口关系图（7.2 的模块级接口展开）；对应关系见 §7.5 表。

### 7.1 逻辑视图（双板纵切 × 五层横切 × 工具链纵贯）

**排版口径**：**纵向（列）**按部署域切分为三部分——运控板 rk 的「交互业务（非实时）」与「运控执行（实时）」两列 + 大脑板 orin 的「复杂感知与规划」一列；**横向（行）**自上而下为**具身业务层 → 具身框架层 → OS 层 → 驱动层 → 硬件层**五层（即自底向上：硬件→驱动→OS→框架→业务）；**最右侧为纵贯全部层级的「工具链」大框**。因 mermaid 自动布局无法锁定矩阵式行列顺序，本图由脚本生成 **SVG 矢量图**（`figures/gen_fig_7_1.py` → `figures/fig-7-1-layered-arch.svg`，仓库同时提供 PNG 版 `fig-7-1-layered-arch.png`），行列位置精确可控，并直接画出板内/跨板数据流与工具链注入连线。按 4+1 视图口径，**本图即逻辑视图**：以具身业务层的功能模块（职责+接口★）为主体，其下框架/OS/驱动层与硬件层作为物理承载一并框出（嵌套结构：**板框 ⊃ 部分框 ⊃ 业务/框架/OS/驱动四层；硬件层在板框内、部分框外**）；模块间接口协作另见 §7.4，场景串联见 §6.2。

![图 7-1 逻辑视图（双板纵切 × 五层横切 × 工具链纵贯）](figures/fig-7-1-layered-arch.png?v=20261031p)

> **图 7-1 读法**：**板框**（运控板 rk3576 / 大脑板 Orin Nano）⊃ **部分框**（交互业务（非实时）/ 运控（实时｜隔离核）/ 复杂感知与规划，各含具身业务→框架→OS→驱动四层，上层依赖下层）（正文为缩略显示，点击图片可放大至原生分辨率）；**硬件层**在板框内、部分框正下方（**rk 侧两格外包统一框：rk3576 8 核＝4×A72 大核＋4×A53 小核**，内格细分业务核〔4×A72＋2×A53〕与 2×隔离小核 CPU2/CPU3）；**OS 层 rk 两列同属一个 Ubuntu 22.04.5 实例**（外包统一框；两格内容侧重调度——业务格：interaction/system/auxiliary slice + CFS，运控格：isolcpus=2,3 + SCHED_RR 钉核）；**具身业务层内每模块独立成框，框序自上而下=依赖方向（上=对外接口/依赖方，下=被依赖底座与数据源）**；最右列为**工具链**纵贯（**开发工具 → 调测工具 → 运维链 → 三方依赖包**〔多媒体与图像 · UI 与音频 · 系统与网络服务〕**→ APP 应用 → SDK 二次开发包**；**传感直连内容已并入各板驱动层格**——rk 格以 hal_sensor/hal_camera(ox05bis)/exposure 为前，orin 格以 hal_rgbd/hal_camera(x5b) 为前）；**①②③通信线统一画在图底部**。
> **连线与交互**（图上带编号的箭头）：**① 板内**（rk 业务⇄运控，iceoryx/DDS）——task_engine→★SetMcAction/velocity ↓，mc→★mc/common/state ↑；**② 跨板·任务与感知**（rk 业务⇄orin，内部以太网 10.1.1.0/24 · FastDDS 域 0）——FollowTaskCtrl/★navi 指令 ↓，★follow_candidates/点云/代价图、/odom·/tf ↑（只走结果级数据，相机原图板内消化）；**③ 跨板·速度直连**（orin→MC）——nav2/follow/运镜发布 velocity(pnc/follow)，MC 按 SetMcInputSource 仲裁；**外部接入**——App→app_proxy(TRRO/鉴权)、**云 LLM⇄Agent（HTTPS/TLS 443 · aiem-gw.agibot.com /v1/session：对话/意图，顶部「启元云端大模型」带即此链路，仅连运控板；2026-10-31 实测大脑板零云进程/零外部连接）**、云→cloud_proxy(MQTT/TLS：状态/OTA)、遥控器→rc(蓝牙)、圈圈→connx(BLE+UWB)、Insta360→insta360(WiFi)，全部终结于 rk；PC ★接口面 FastDDS 直连双板；**虚线**=工具链注入。模块级全量接口见 §7.4，连接视角见 §7.2。

读图要点（含对本图排版方案的事实核对，依据 §1–§6 实测）：

1. **纵切核对 ✅ 符合事实**：「交互业务（非实时）vs 运控执行（实时）」的划分与 cgroup/slice 实证一致（§6.4.3：interaction/system/auxiliary=非实时业务，realtime=mc+device_manager）；运控执行列的实时性载体是 **CPU2（CAN 硬中断+dm 设备线程）/CPU3（mc 运控）隔离核 + 9×SCHED_RR**（§2.4）；「大脑板=复杂感知与规划」与 manifest 归类一致（感知独立模块/跟随算法节点/导航算法模块，§6.4.2）。补充事实：传感与相机直连链路（hal_sensor、hal_camera、exposure、hal_rgbd 等）按 2026-10-31 实证**画入驱动层格且以传感为重**（业务层不再单列「传感与外设/传感接入」模块）。
2. **五层横切核对 ✅ 基本合理**，与板上实际栈一致，但有三点按事实修正/注记：① **ROS 2/AimRT/iceoryx/推理运行时归「具身框架层」而非 OS 层**——OS 层只放内核+systemd+系统服务；② **运控执行列没有独立 RT 内核**（无 PREEMPT_RT，靠 isolcpus+SCHED_RR，已在该列 OS 层如实标注）；③ 按 4+1 严格口径，硬件/驱动/OS 层属物理/过程视图成分，**逻辑视图本体=具身业务层的功能模块及其接口**（图中已为每个业务模块标注职责与代表接口★），纯逻辑协作图见 §7.4。
3. **工具链框收拢为六块**（版本以 §6.1 实测为准）：**开发工具**（colcon+CMake 工作空间、AimRT RPC 代码生成、Rust 工具链）→ **调测工具**（ros2 CLI、tegrastats、jetson-stats 7.2.1、iox-introspection、ASan、AimRT monitor/viz）→ **运维链**（hds、cloud_proxy、ota、data_exporter、drp、abox）→ **三方依赖包**（外包「多媒体与图像 / UI 与音频 / 系统与网络服务」三组）→ **APP 应用**（PrimeBot App：操控/跟随/拍照/看相机；接入=app_proxy·TRRO+鉴权+热点直连）→ **SDK 二次开发包**（PC SDK：★官方 30 接口·服务/话题/RTSP；FastDDS 直连双板，aimdk_msgs，§5）；中间件与通信、AI 推理运行时、商业 SDK 三组不再单列成框——推理运行时要点已见于框架层单元格，完整版本与开源仓以 §6.1 为准。
4. **跨板边界只走结果级数据**：rk↔orin 经内部以太网 FastDDS 域 0 交换目标/点云/代价图与任务指令；相机原图等大带宽在 orin 板内经 iceoryx 消化（§4.0）。**2026-10-31 实测板间链路流量 ≈2 Mbps**（orin unused0 口 5s 采样：下行 155KB/s + 上行 80KB/s，时值两板视觉同时持续运行——rk Agent 105% CPU/NPU 29%+9%、orin hal_camera 43%/hal_rgbd 39%/percep 10%）：远低于任何一路视频（864×648@10fps NV12 ≈67Mbps、5MP 原始 ≈750Mbps），证明**视频零跨板**；percep 订阅 orin 本地 `head_stereo_left/right` + 5×ToF 点云（cfg 实证），Agent 订阅 rk 本地 `bgr_camera_l`（agent.yaml 实证）。
5. **传感器直连拓扑（2026-10-31 板上实证：fuser/dmesg/配置三重核验）**：**运控板 rk 直连**——头部相机（ox05bis 5MP，max96724 GMSL→rkcif，hal_camera 持有 /dev/video0-1，2592×1944@10fps）+ **BMI08 头部 IMU** + **5 目 ToF 控制面**（hal_rgbd 包内 `exposure` 进程经 I2C（i2c-0/i2c-5）+ V4L2 直控 5 颗 ToF 曝光/滤波，并处理 `/ofilm/tof/inter_board` 板间同步）+ 本体传感（BMS/触摸/灯/舵机，hal_sensor_soc0）；图像经 iceoryx/dmabuf 板内供给 Agent 视觉（NPU 实测 Core0 25%/Core1 15% 常跑）与 rtsp_server（MPP 编码→App）。**大脑板 orin 直连**——OFILM GMSL 模组：2× ofilm_x5b 5MP RGB（2592×1944 UYVY，hal_camera 持有 video0-1）+ **5 目 ToF 数据面**（ofilm_tof/tofb 驱动，hal_rgbd 持有 video2-7→coreon_fusion 融合）+ ADU/温度（hal_sensor_soc1）。**跨板只走结果级数据与同步信号**（soc_sync/ready、inter_board、点云/目标/指令），图像话题 ROS 侧 0 发布者＝原图不跨板；ToF 为「控制面在 rk、数据面在 orin」分工。头部 IMU 归属修正：由 rk hal_camera 发布（orin 配置中 imu_head 已注释）。外部佐证：[欧菲光「七路融合感知」方案（双目 RGB + 5 路 ToF + 6 轴 IMU）](https://www.chinanews.com.cn/cj/2026/10-30/10723321.shtml)。
5. **一套以太网、一个 ROS 2 域**：PC/rk/orin 同处 `10.1.1.0/24`（§1）；rk 是对外网关，**对外接入（App/云/遥控器/圈圈/Insta360）全部终结于运控板**。
6. **两列同板共享 OS/驱动/硬件**：运控执行列的 OS/驱动/硬件层标注「与左列共享」及该列特有部分（隔离调度、CAN 中断、执行器总线），避免误解为独立系统。
7. **与后文四节的对应**：§7.2 软件模块连接图为本图业务层的「模块—硬件—外部」连接视角；§7.3 模块清单的功能分组（交互与业务/系统与运维/传感与外设接入/实时运控/感知与融合/导航与跟随）即本图各列「具身业务层」内的分组；§7.4 为业务层的模块级接口展开；§7.5 为对应与校验。

### 7.2 软件模块连接图（三层四域：模块与硬件、外部的连接视角）

「三层」= 机器人外部的**外部生态域**、双板内部的**业务/算法层**、最底部的**本体硬件域**；「四域」= 外部生态域、运控板域、大脑板域、本体硬件域。本图承接 7.1 总览，聚焦**软件模块与硬件、外部设备之间经何种接口相连**；模块间的业务接口全貌见 §7.4。

```mermaid
flowchart TB
    subgraph EXT["☁ 外部生态域（机器人之外）"]
        PC["🖥 开发 PC / SDK 用户程序<br/>Ubuntu 22.04 + ROS 2 Humble<br/>有线直连 10.1.1.99"]
        APP["📱 手机 PrimeBot App<br/>连 rk p2p0 热点 192.168.88.x"]
        CLOUD["☁ 启元云端<br/>aiem-gw.primebot.cn"]
        RCDEV["🎮 体感遥控器<br/>蓝牙私有协议"]
        QJDEV["⭕ 「圈圈」UWB 随行标签<br/>（quanji，板上代号）BLE+UWB 测距测角<br/>IMU+雷达活体检测+麦克风"]
        I360["📷 Insta360 云台相机<br/>WiFi 接入"]
    end

    subgraph TOOLS["🧰 三方工具链（纵贯两板的运行时底座 · 版本与开源仓实测见 §6.1）"]
        direction TB
        T_MW["📡 中间件与通信<br/>ROS 2 Humble（ros-base 0.10.0）· Fast DDS 2.6.12<br/>rmw-fastrtps 6.2.10 · iceoryx 2.95.4.2<br/>AimRT（开源）+ aimrt_py 1.2.0"]
        T_AI["🧠 AI 推理运行时<br/>onnxruntime 1.19.2 · RKNN Runtime 2.3.2（rk NPU）<br/>MNN · sherpa-onnx 1.12.15（rk）<br/>TensorRT 10.7 · CUDA 12.6 · cuDNN 9.3（orin）"]
        T_MM["🎬 多媒体与图像<br/>OpenCV 4.5.4/4.8 · GStreamer 1.22.9（+rockchip 插件）<br/>ffmpeg 4.4.2/6.0 · MPP 1.5.0 · RGA 2.2.0（rk）"]
        T_UI["🖼 UI 与音频<br/>flutter-pi + Flutter（display_ui）· Qt 5.15.3<br/>PipeWire 1.6.8（PulseAudio 兼容口 6001/4713）"]
        T_SYS["⚙ 系统与网络服务<br/>systemd（em.service + robot.slice）· NetworkManager<br/>hostapd/dnsmasq · mosquitto 2.0.11 · ptp4l/chrony · can-utils"]
        T_SDK["🔑 商业 SDK 与语言构建<br/>科大讯飞 VTN/ESR/听写 SDK · omnis_sdk 4.0.0（自研）<br/>Python 3.10 · C++20 · colcon · jetson-stats 7.2.1"]
    end

    subgraph RK["🖥 运控板 rk3576（soc0 · 10.1.1.100 · 8 核，CPU2/3 隔离）"]
        direction TB
        subgraph RK_INT["交互与业务层（用户感知应用 · robot_interaction.slice）"]
            AGENT["Agent 对话智能体<br/>aima-agent-app · /aimrt_agent_node"]
            TE["任务引擎 task_engine<br/>/task_engine（长任务编排仲裁）"]
            DISP["胸屏 display_ui<br/>（flutter-pi，非 ROS）"]
            AUDIO["音频管理 audio_manager<br/>/audiomanager_ros2_node（PulseAudio TCP 6001/4713）"]
            LUM["灯效 lumina<br/>（订阅 9 路状态联动灯语，slice=system）"]
            SCE["场景引擎 scene_engine<br/>（iceoryx，无 ROS 节点，slice=system）"]
        end
        subgraph RK_SYS["系统与运维层（robot_system / auxiliary.slice）"]
            SM["状态机 aim_sm_app · /sm<br/>（系统状态监控：含 hds 健康诊断 · sys_guard 场景看护）"]
            HK["管家 housekeeper + App 代理 app_proxy<br/>（网络/蓝牙/权限/App 接入鉴权）"]
            OPS["运维链：cloud_proxy 云端代理 · ota_master<br/>data_exporter 数据导出（auxiliary）· drp 数据录制 · abox 日志"]
        end
        subgraph RK_SEN["传感与外设接入层"]
            HAL0["传感器 HAL soc0<br/>/hal_sensor_soc0（BMS/触摸/灯带/颈部灯/舵机/上下电）"]
            HALC0["相机 HAL rk 实例<br/>（相机设备规则/曝光，iceoryx 为主）"]
            HALR0["ToF/RGBD HAL rk 实例<br/>（与 orin 同一交付物按机型配置）"]
            ODOM["足式里程计 legged_odom<br/>/legged_odom_node（odom/tf · 跟随算法节点@rk）"]
            ACC["配件接入：rc 遥控器 · connx 圈圈（quanji）<br/>insta360 云台 · rtsp_server · resource_manager 资源/拍照"]
        end
        subgraph RK_RT["实时运控层（robot_realtime.slice · 隔离核）"]
            MC["运动控制 mc_app_main<br/>CPU3 独占 · /mc_ros2_node"]
            DM["设备管理 device_manager<br/>/device_manager_node（与 mc 同 realtime.slice）"]
            CANIRQ["CAN0/CAN1 硬中断<br/>CPU2 独占"]
        end
        PM0["板基座：process_manager0（em 守护）· sys_guard<br/>iox-roudi（iceoryx 零拷贝）· conduit_router(mosquitto MQTT/TLS)<br/>hostapd/dnsmasq(热点) · tetherd(内网) · secret_store"]
    end

    subgraph ORIN["🧠 大脑板 Jetson Orin Nano（soc1 · 10.1.1.101 · 6×A78AE + Ampere GPU）"]
        direction TB
        subgraph OR_HAL["传感器接入层（manifest 归类：传感器）"]
            HALR["ToF/RGBD HAL<br/>/hal_rgbd_node（头部 5 目 ToF）"]
            HALC["相机 HAL<br/>/hal_camera·/hal_camera_1（双目/鱼眼/彩色+头部 IMU）"]
            HAL1["传感器 HAL soc1<br/>/hal_sensor_soc1（ADU 电源板/温度）"]
        end
        subgraph OR_ALGO["感知与融合层（感知独立模块 + 跟随算法节点）"]
            FUS["多源融合 coreon_fusion<br/>/coreon_fusion_ros2_node（27 路输入）"]
            PTR["人体跟踪 person_track<br/>+ 亮度检测 brightness_detection"]
            PRC["感知算法 percep<br/>（分割/室内外/楼梯/语义代价图）"]
            PCOMB["nav2_perception<br/>（点云合并 + perception_node）"]
        end
        subgraph OR_NAV["导航与跟随层（导航算法模块 + 跟随算法节点）"]
            NAV["Nav2 导航栈（12 节点）<br/>对外封装 /robot_navigation · /map_server"]
            FLW["跟随控制 follow_node"]
            TRJ["轨迹跟踪 trajectory_tracker（环绕运镜）"]
        end
        PM1["板基座：process_manager1 · sys_guard<br/>iox-roudi · ota_slave · data_exporter"]
    end

    subgraph HW["⚙ 本体硬件域（机身之内）"]
        JOINT["14× 关节执行器 + 主控 MCU<br/>挂 CAN0/CAN1 总线"]
        SENS["本体传感器：BMS · 触摸 · 灯带<br/>舵机 · 躯干 IMU · 5 目 ToF · 相机阵列 · ADU"]
    end

    %% 工具链（纵贯侧框，按层注入两板）
    TOOLS -. "三方工具/包/框架按层注入<br/>（中间件/AI 运行时/UI/系统服务）" .-> RK
    TOOLS -. "同左（TensorRT·CUDA·jetson-stats 侧）" .-> ORIN

    %% 外部 → rk
    PC <-. "有线直连 10.1.1.0/24<br/>FastDDS · ROS 2 域 0（★官方 30 接口面）" .-> RK
    APP <-. "p2p0 热点 WiFi" .-> HK
    CLOUD <-. "WiFi 上联 · MQTT over TLS + 云端 RPC" .-> PM0
    RCDEV -. "蓝牙" .-> ACC
    QJDEV -. "BLE + UWB" .-> ACC
    I360 -. "WiFi" .-> ACC

    %% 双板互联
    RK <-->|"内部以太网 10.1.1.0/24<br/>FastDDS 域 0 · PTP(ptp4l)+chrony 时钟同步<br/>（相机图/点云/目标↓ · 状态/指令↑）"| ORIN

    %% rk 内部 → 硬件
    MC -- "关节指令（1kHz 级实时环）" --> CANIRQ -- "CAN 总线" --> JOINT
    HAL0 -- "I2C/SPI/UART" --> SENS

    %% orin 内部 → 传感器
    HALR -- "MIPI-CSI/USB" --> SENS
    HALC -- "MIPI-CSI" --> SENS
    HAL1 -- "串口" --> SENS
```

读图要点（与 §1–§3、§6.4 实测对应）：

1. **三方工具链框**：图右侧纵贯框为两板共用的三方底座，六组按职能归类（中间件通信 / AI 推理运行时 / 多媒体图像 / UI 音频 / 系统网络服务 / 商业 SDK 与语言构建），全部版本与开源仓为板载实测（详见 §6.1；构建/调试类工具见 7.1 工具链框）。注意其中 **AI 推理运行时按板分化**：rk 走 onnxruntime+RKNN+MNN+sherpa-onnx（CPU/NPU），orin 走 TensorRT+CUDA（GPU）。
2. **rk 分层依据 = 启动清单 + cgroup 实证**：层级与成员按 `t1_v2d0_soc0_config.yaml` 的 stages 注释（用户感知应用/系统状态监控/数据导出/数据录制/云端代理/交互应用/跟随算法节点…）与 cgroup slice 归属（`robot_realtime.slice`=mc+device_manager、`robot_interaction.slice`=agent+display_ui+audio_manager+task_engine、`robot_auxiliary.slice`=data_exporter，其余 system）重划，见 §6.4.2/6.4.3。
3. **rk 是四层三明治**：交互与业务层（用户感知：Agent/任务引擎/胸屏/音频，含灯效与场景引擎）→ 系统与运维层（状态机/管家/云端/OTA/导出/录包/日志）→ 传感与外设接入层（HAL soc0、相机/ToF rk 实例、里程计、配件）→ 实时运控层（隔离核：mc+device_manager@CPU3、CAN 中断@CPU2）。越往下实时性越强；audio_manager/display_ui 按官方 interaction.slice 归入交互层。
4. **orin 是感知导航算法舱**：分层名与 manifest 注释对齐（传感器 / 感知独立模块+跟随算法节点 / 导航算法模块）。5 目 ToF 与多路相机原始大带宽经板内 iceoryx 消化，仅结果级数据进 DDS 域（§4.0）。
5. **一套以太网、一个 ROS 2 域**：PC、rk、orin 同处 `10.1.1.0/24`、`ROS_DOMAIN_ID=0` + FastDDS 互通（§4.0）；**rk 是对外网关**（WiFi/热点/RNDIS 在 rk），orin 仅一张内网口（§1.1）。
6. **所有业务进程由 em/process_manager 统一拉起守护**（`/robot/software/<app>/bin/em_run.sh`，分阶段延时启动，见 §6.4.2）；**对外硬件接口（App/云/遥控器/圈圈/Insta360）全部终结在 rk**。

### 7.3 软件模块清单（与 7.2 图中的框一一对应）

> 进程/节点列全部来自 §3/§4 实测；「代表性接口」可在 §5 全表中展开。★ = SDK 官方对外接口（《接口说明.md》§1）。「7.2 图中位置」列的功能分组（交互与业务/系统与运维/传感与外设接入/实时运控/感知与融合/导航与跟随）对应 7.2 图中的层/域分组（即 7.1 图各列具身业务层内的分组）。

| 7.2 图中位置 | 软件模块 | 进程（实测） | ROS 2 节点 | 职责 | 代表性接口 |
|---|---|---|---|---|---|
| 外部·PC | SDK 用户程序 | 开发者自建 | 用户自定义 | 二次开发入口（PC/rk/orin 三处均可部署） | ★30 接口面：`SetMcAction`、`velocity`、`PlayTts`… |
| 外部·APP | PrimeBot App | 手机端 | — | 遥控/配置/看画面 | 经 app_proxy：`ConnectRobot`、TRRO 透传 |
| 外部·CLOUD | 启元云端 | — | — | 云协同/OTA/数据 | 经 cloud_proxy/ota_master（MQTT/TLS） |
| 外部·配件 | 遥控器 / 圈圈 / Insta360 | 外部设备 | — | 体感操控 / UWB 随行 / 云台拍摄 | 经 rc / quanji / insta360 接入模块 |
| 工具链 | 开发/调测/运维与三方依赖包 | 随各模块链接/加载 | — | 开发工具（colcon/Rust）· 调测工具（ros2 CLI/tegrastats 等）· 运维链（hds/cloud_proxy/ota/drp/abox）· 依赖包（多媒体图像 / UI 音频 / 系统网络服务）；中间件与 AI 运行时等版本见 §6.1 | 版本与开源仓见§6.1–6.1.3 |
| rk·交互与业务层 | Agent 对话智能体 | aima-agent-app | `/aimrt_agent_node` | LLM 语音对话、语义意图、交互决策 | ★`PlayTts`、★`asr_result`、`semantic_intent` |
| rk·交互与业务层 | 任务引擎 | task_engine | `/task_engine` | 长任务编排与仲裁中枢（P12/S39/V30/C51 全图最重） | `AmRequest`、`PlayMove`、`NaviTaskCtrl`、★`ShutdownTask` |
| rk·交互与业务层 | 胸屏 UI | flutter-pi(display_ui) | —（非 ROS） | 胸屏表情渲染 | 经 `display_state`/`PlayEmotion` 链联动 |
| rk·交互与业务层 | 音频管理 | aimrt_main(audio_manager) | `/audiomanager_ros2_node` | 采音/播放/在线音乐（官方名 `/hal_audio`） | ★`PlayAudioFile`、★`Get/SetVolume`、TCP 6001/4713 |
| rk·交互与业务层 | 灯效服务 | lumina_server | `/lumina_server` | 订阅 9 路状态做灯语联动（slice=system） | `PlayLight`、客户端 13 路（PlayEmotion 等） |
| rk·交互与业务层 | 场景引擎 | scene_engine | —（iceoryx，无 ROS 节点） | 场景 tick/切换（slice=system） | 板内 iceoryx 通道 |
| rk·系统与运维层 | 状态机 SM | aim_sm_app | `/sm` | 整机状态机、按 `SmNodeService` 驱动各应用（系统状态监控） | `GetSystemState`、`sm/system_state` |
| rk·系统与运维层 | 管家 + App 代理 | housekeeper、local_app_proxy | `/housekeeper0`、`/app_proxy`(+`_client`) | WiFi/蓝牙/4G/权限；App 接入鉴权与状态镜像 | `CtrlWifi`、`ConnectRobot`/`JsonWebCheck` |
| rk·系统与运维层 | 运维链 | hds、cloud_proxy、ota_master、data_exporter、drp、abox | `/hds`、`/cloud_proxy`、`/ota_master`、`/data_exporter`、`/record_*` | 系统状态监控（hds）、云端代理、OTA、数据导出（auxiliary）、录包、日志 | `GetAlertCodeJsonFile`、`alert_code_list` |
| rk·传感与外设接入层 | 传感器 HAL soc0 | aimrt_main(hal_sensor) | `/hal_sensor_soc0` | BMS/触摸/灯带/颈部灯/舵机/关节上下电 | ★`bms/state`、★`touch/state`、★`SetServo`、★`LedStripCommand` |
| rk·传感与外设接入层 | 相机 HAL rk 实例 | aima-hal_camera-app-main(rk) | —（iceoryx 为主） | 相机设备规则（udev）/曝光管理 | 板内通道（与 orin 同一交付物按机型配置） |
| rk·传感与外设接入层 | ToF/RGBD HAL rk 实例 | aimrt_main(hal_rgbd rk) | —（iceoryx 为主） | rk 侧 ToF/相机曝光支撑（cfg 含 exposure/） | 板内通道 |
| rk·传感与外设接入层 | 足式里程计 | aima-legged_odom-app | `/legged_odom_node` | `/odom`、`/tf`、躯干位姿（跟随算法节点@rk） | `/odom`、`/tf` |
| rk·传感与外设接入层 | 配件接入 | aima-rc-app、connx(quanji)、insta360、rtsp×2、resource_manager | `/rc`、`/quanji_uwb_*`、`/insta360_*`、`/rtsp_video_server`、`/picture_service` | 遥控/圈圈/云台/视频流/资源与拍照管理 | `ControlQuanji`、`ControlInsta360`、RTSP `live_<id>`、`PlayPhoto` |
| rk·实时运控层 | **MC 运动控制** | **mc_app_main（CPU3 独占）** | `/mc_ros2_node` | 实时运控内核：动作/步态/关节伺服 | ★`SetMcAction`、★`velocity`、★`joint/command`、★`mc/common/state` |
| rk·实时运控层 | 设备管理 | aimrt_main(t1_v2d0) | `/device_manager_node` | 设备轮询管理（与 mc 同属 realtime.slice） | 客户端 `GetJointPowerState` |
| rk·实时运控层 | CAN 中断收发 | （内核 irq83/84 @CPU2） | — | CAN0/CAN1 总线硬中断 | 14 关节执行器链路（不暴露 DDS） |
| rk·板基座 | 进程管理/守护 | process_manager、sys_guard、iox-roudi、conduit_router(mosquitto)、tetherd、secret_store、hostapd/dnsmasq | `/process_manager0`、`/sys_guard_node_soc0` | em 体系拉起守护、场景看护、零拷贝总线、MQTT、内网/热点 | `EmAppService` proto、`active_scene` |
| orin·传感器接入层 | ToF/RGBD HAL | aimrt_main(hal_rgbd) | `/hal_rgbd_node`(+`_0`) | 5 目 ToF（main/front/rear/left/right）供电出流 | ★相关 `SetHalTofStream`、`sensor_info` |
| orin·传感器接入层 | 相机 HAL | aima-hal_camera-app-main | `/hal_camera`(+`_1`) | 双目/鱼眼/中央彩色相机 + 头部 IMU、双板同步 | ★`CaptureJpegImage`、`SetHalCameraStream` |
| orin·传感器接入层 | 传感器 HAL soc1 | aimrt_main(hal_sensor orin) | `/hal_sensor_soc1` | ADU 电源板、orin 温度、orin 侧关机 | `adu/state`、`thermal/soc1`、`SetPowerDownOrin` |
| orin·感知与融合层 | 多源融合 | coreon_fusion | `/coreon_fusion_ros2_node` | 27 路输入（5×ToF+相机+IMU+UWB）融合避障（跟随算法节点） | `ObstacleCheck`、`SetAvoidance`、`target_info` |
| orin·感知与融合层 | 人体跟踪+亮度 | aimrt_main×2（target_follow_perception、brightness_detection） | `/aimrt_person_track_*`、`/aimrt_brightness_*` | 跟随候选检测、过暗门控 | ★`follow_candidates`、`follow_brightness_dark` |
| orin·感知与融合层 | 感知算法 | percep | `/perception` | 语义分割/室内外/楼梯/语义局部代价图（感知独立模块） | `percep/local_costmap`、`detect/env` |
| orin·感知与融合层 | 点云合并 | aimrt_main(nav2_perception) | `/pointcloud_combiner_node`、`/perception_node` | 前/后 ToF 合并、点云过滤、关节状态桥 | `percep/tof/combined/point_cloud`、`pointcloud_filter` |
| orin·导航与跟随层 | Nav2 导航栈 | ros2 launch nav2_bringup | `/robot_navigation` 等 12+ 节点 | 建图加载/定位/路径规划/轨迹控制（导航算法模块） | ★`/navi/command`、★`navi/state`、★`/navi/loadmap` |
| orin·导航与跟随层 | 跟随控制 | ros2 launch follow | `/follow_node` | 语义跟随规划（跟随算法节点） | ★`follow/state`、`SetFollowTaskBiped` |
| orin·导航与跟随层 | 轨迹跟踪 | path_follow_node | `/trajectory_tracker` | 环绕/后退等智能运镜（跟随算法节点） | ★`SpawnIntelligentMotionTask`、★`intelligent_task_state` |
| orin·板基座 | 进程管理/守护 | process_manager、sys_guard、iox-roudi、ota_slave、data_exporter | `/process_manager1`、`/sys_guard_node_soc1`、`/ota_slave`、`/data_exporter` | 同 rk + orin 侧升级执行 | `EmAppService` proto |
| 硬件域 | 关节执行器/MCU | 14×执行器 @CAN0/1 | —（CAN，不上 DDS） | 关节伺服与本体控制 | 由 MC 经 CPU2 中断收发 |
| 硬件域 | 本体传感器 | BMS/触摸/灯/舵机/IMU/ToF/相机 | —（经各 HAL 接入） | 机身传感与受控设备 | 由 soc0/soc1 HAL 与 orin HAL 读控 |

### 7.4 软件模块接口关系图（7.2 的模块级展开）

> 连线标注 = 实际承载的接口（topic / service 名或总线类型，★ 为 SDK 官方面）；箭头方向 = 数据流或调用方向；双线粗箭头 `==>` 表示二开接入面。模块框与 7.2 的域/层框一一对应。

```mermaid
flowchart LR
    %% ===== 外部生态域（对应 7.1 EXT）=====
    APP(["📱 手机 App"])
    SDK(["🖥 SDK 用户程序<br/>(PC / rk / orin)"])
    CLOUD(["☁ 启元云端"])

    subgraph RK["🖥 运控板 rk（soc0）——对应 7.2 RK 域"]
        direction TB
        subgraph RK_INT["交互与业务层（interaction.slice）"]
            AGENT["Agent<br/>/aimrt_agent_node"]
            TE["Task Engine 任务引擎<br/>/task_engine"]
            DISP["胸屏 display_ui<br/>(flutter-pi，非 ROS)"]
            AUDIO["音频管理<br/>/audiomanager_ros2_node"]
            LUM["灯效 lumina<br/>(slice=system)"]
            SCE["场景引擎 scene_engine<br/>(iceoryx，slice=system)"]
        end
        subgraph RK_SYS["系统与运维层（system/auxiliary.slice）"]
            SM["SM 状态机 /sm"]
            HK["housekeeper + app_proxy"]
            OPS["hds · cloud_proxy · ota_master<br/>data_exporter · drp · abox"]
        end
        subgraph RK_SEN["传感与外设接入层"]
            HAL0["传感器 HAL soc0"]
            HALC0["相机 HAL rk 实例<br/>(iceoryx 为主)"]
            HALR0["ToF HAL rk 实例"]
            ODOM["足式里程计<br/>(跟随算法节点@rk)"]
            ACC["配件接入<br/>rc · connx(quanji) · insta360 · rtsp<br/>resource_manager"]
        end
        subgraph RK_RT["实时运控层（realtime.slice）"]
            MC["MC 运动控制 /mc_ros2_node<br/>(CPU3)"]
            DM["设备管理 device_manager<br/>(同 realtime.slice)"]
            CANIRQ["CAN 中断 (CPU2)"]
        end
        PM0["process_manager0<br/>sys_guard · iox-roudi · conduit_router"]
    end

    subgraph ORIN["🧠 大脑板 orin（soc1）——对应 7.2 ORIN 域"]
        direction TB
        subgraph OR_HAL["传感器接入层"]
            HALR["ToF HAL /hal_rgbd_node"]
            HALC["相机 HAL /hal_camera"]
            HAL1["传感器 HAL soc1"]
        end
        subgraph OR_ALGO["感知与融合层"]
            FUS["融合 coreon_fusion"]
            PTR["人体跟踪+亮度"]
            PRC["感知 percep"]
            PCOMB["点云合并 nav2_perception"]
        end
        subgraph OR_NAV["导航与跟随层"]
            NAV["Nav2 栈 /robot_navigation"]
            FLW["跟随 follow_node"]
            TRJ["轨迹跟踪 trajectory_tracker"]
        end
        PM1["板基座：process_manager1<br/>sys_guard · iox-roudi · ota_slave"]
    end

    JOINT(["⚙ 14× 关节执行器/MCU<br/>(CAN0/1 @CPU2)"])
    SENS(["⚙ 本体传感器"])

    %% ===== 外部接入（全部终结在 rk）=====
    APP -->|"ConnectRobot / JsonWebCheck（鉴权）<br/>trro_push / trro_pull（TRRO 透传）"| HK
    CLOUD <-. "MQTT/TLS + 云端 RPC" .-> OPS
    SDK ==>|"★ SetMcAction / PlayTts / ShutdownTask …<br/>★ velocity / joint/command / asr_result / bms/state …<br/>FastDDS 域 0（ROS 2 服务+话题）"| RK
    SDK ==>|"★ CaptureJpegImage / RTSP live_id …"| ORIN

    %% ===== rk 内部（业务与交互层）=====
    AGENT -->|"asr_result · semantic_intent（意图）"| TE
    TE -->|"PlayTts（播报）"| AGENT
    AGENT <-->|"play_state / tts_status / music_state"| AUDIO
    SM -->|"SmNodeService/&lt;app&gt;/ExecuteAction·Fallback（proto）"| TE
    SM -.->|"EmAppService：StartApp/StopApp（proto）"| PM0
    PM0 -.->|"em_run.sh 拉起/守护（进程树）"| RK_INT
    PM0 -.->|"em_run.sh 拉起/守护（进程树）"| RK_SYS
    LUM -.->|"订阅 play_state/tts_status 等 9 路（灯语联动）"| AUDIO
    HK -->|"network/state · basic_info · permission_state"| TE
    OPS -->|"alert_code_list（hds 汇总 14 路诊断）"| TE

    %% ===== rk 内部（业务层 → 基础服务/运控层）=====
    TE -->|"PlayAudioFile / ControlMusic / SetVolume ★"| AUDIO
    TE -->|"LedStripCommand ★ / SetServo ★ / SetNeckLight ★ / TouchEnable"| HAL0
    HAL0 -->|"bms/state ★ · touch/state ★ · servo/state · rgb/state"| TE
    TE -->|"SetMcAction ★ / SetMcMotion ★ / SetMcPresetMotion ★（服务）"| MC
    TE -->|"velocity（source=interaction）"| MC
    MC -->|"mc/common/state ★（运控全量状态）"| TE
    MC -->|"mc/common/state"| ODOM
    ODOM -->|"/odom · /tf"| TE
    ACC -->|"rc：velocity + SetMcAction 客户端"| MC
    ACC -->|"quanji：uwb · radar · state"| TE
    ACC -->|"insta360：task_state · track_frame"| TE
    TE -->|"AssignInsta360Task / CaptureJpegImage ★"| ACC
    TE -->|"PlayEmotion ★ / SetFaceMode（胸屏表情）"| DISP
    DISP -.->|"display_state（表情回显，屏侧）"| TE
    HAL0 ---|"I2C/SPI/UART"| SENS

    %% ===== rk 实时运控层 → 硬件 =====
    MC -->|"关节指令 1kHz 级"| CANIRQ -->|"CAN 总线"| JOINT

    %% ===== 跨板 rk ↔ orin（10.1.1.0/24 FastDDS）=====
    HALR ==>|"ToF sensor_info + 点云（板内 iceoryx 桥接后上 DDS）"| FUS
    HALC ==>|"相机图像 5 路 + imu_head + module_info"| FUS
    HAL1 -->|"thermal/soc1 → rk 温控"| HAL0
    PTR -->|"follow_candidates ★（跟随候选）"| TE
    FUS -->|"target_info · coreon_fusion/state（避障中）"| TE
    FUS -->|"fused_obstacle_cloud"| PRC
    PCOMB -->|"pointcloud_filter（过滤点云）"| TRJ
    PRC -->|"percep/local_costmap（语义代价图）"| NAV
    PRC -->|"detect/env（室内外判定）"| TE
    TE ==>|"★ /navi/command（SetNaviTask）· /navi/loadmap ★<br/>FollowTaskCtrl · SpawnIntelligentMotionTask ★"| OR_NAV
    NAV -->|"navi/state ★ · navi/relocate/state ★"| TE
    FLW -->|"follow/state ★"| TE
    TRJ -->|"intelligent_task_state ★"| TE
    FLW -->|"enable_follow · follow_target_selection（选定目标）"| PTR
    NAV -->|"velocity（source=pnc）"| MC
    FLW -->|"velocity（跟随速度）"| MC
    TRJ -->|"velocity（运镜速度）"| MC
```

> 图中未画出的次级连线（避免过密）：SM 的 `sm/system_state` 广播至全图 8 个订阅者、`robot/trace/events` 埋点汇聚到 cloud_proxy、`permission_state` 对相机/ToF 出流的门控、lumina 灯效订阅 9 路状态做灯语联动等——均见 §4.3 各节点接口挂载明细。

### 7.5 图间对应关系与一致性校验

**7.2 框 → 7.4 展开 对照表**（7.2 的外部生态与工具链为聚合框，下表按 7.4 的展开粒度对应）：

| 7.2 软件模块连接图中的框 | 7.3 模块关系图中的展开 | 模块间连接的接口类型 |
|---|---|---|
| 三方工具链框（纵贯侧框） | 六组工具/包/框架（中间件/AI 运行时/多媒体/UI/系统服务/SDK） | 以链接库与运行时形式注入各模块（版本见§6.1） |
| 外部生态域·手机 App | APP → housekeeper/app_proxy | ROS 2 service（鉴权三件套）+ TRRO 二进制话题 |
| 外部生态域·SDK 用户程序（PC） | SDK ⇒（双线粗箭头）rk / orin | ★官方 30 接口（§5 表中标★行）：服务 + 话题 + RTSP/TCP |
| 外部生态域·云端 | CLOUD ⇄ 运维链（hds/cloud_proxy/ota_master） | MQTT over TLS + 云端 RPC（mosquitto conduit_router） |
| 外部生态域·遥控器/圈圈/Insta360 | → 配件接入框（rc/quanji/insta360） | 蓝牙/BLE+UWB/WIFI 私有协议 → 各接入模块转成 DDS |
| rk·交互与业务层 | Agent / Task Engine / 胸屏 / 音频 / 灯效 / 场景引擎 | service 调用 + 状态话题 + 音频 TCP 6001/4713 + 非 ROS 渲染 |
| rk·系统与运维层 | SM / 管家+App代理 / 运维链（hds·cloud·ota·exporter·drp·abox） | 状态话题 + SmNodeService/EmApp proto + MQTT/TLS |
| rk·传感与外设接入层 | HAL soc0 / 相机 HAL rk 实例 / ToF HAL rk 实例 / 里程计 / 配件接入 | service + 状态话题 + iceoryx 板内通道（rk 实例不上 DDS） |
| rk·实时运控层 | MC（CPU3）+ device_manager → CAN 中断（CPU2）→ 关节执行器 | 仲裁后的 `SetMcAction`/`velocity` → CAN（不出板、不上 DDS） |
| rk·板基座 | process_manager0 / sys_guard / iox-roudi / conduit_router | em 进程树 + iceoryx 板内零拷贝 + proto 桥 + MQTT |
| orin·传感器接入层 | ToF HAL / 相机 HAL / HAL soc1 | 板内 iceoryx 大带宽 + `sensor_info` 等轻量 DDS 话题 |
| orin·感知与融合层 | 融合 ← ToF/相机；跟踪 → 跟随；percep → Nav2 | 点云/图像（板内为主）+ 结果级话题（跨板 DDS） |
| orin·导航与跟随层 | Nav2 / follow / trajectory_tracker | nav2 标准链路 + `/navi/*` 封装服务 + `velocity` 输出 |
| orin·板基座 | process_manager1 / sys_guard / iox-roudi / ota_slave | 同 rk 板基座 |
| 本体硬件域 | 关节执行器框 + 本体传感器框 | CAN 总线（对 DDS 域不可见）/ I2C/SPI/UART/MIPI |

**一致性校验**（7.4 与 7.2 对照，本文 §3–§5 实测）：

1. **每条连线均有实测依据**：7.4 中模块间的 pub/sub 与 client/service 关系与 §4.3/§4.4 的节点接口挂载一一对应（如 task_engine 的 30 路服务端/51 路客户端、SM 的 28 路 proto 客户端、coreon_fusion 的 27 路订阅）。
2. **`velocity` 多源汇聚与 7.2 的分层一致**：`/aima/mc/locomotion/velocity` 有 11 个发布者（task_engine/app_proxy/rc/agent 侧/nav2/follow/轨迹跟踪/Insta360…），7.4 中体现为「业务层（interaction）+ 导航规划层（pnc/follow/运镜）+ 配件（rc/insta360）多源 → MC」，MC 按 `SetMcInputSource` 优先级仲裁（§5.1.1）。
3. **跨板边界与 7.2 的双板框一致**：跨板连线只承载结果级/指令级数据（点云、目标、代价图、`navi/*`、`velocity`）；相机原图等大带宽流量在 orin 板内经 iceoryx 消化，与 §4.0「rk 侧观察不到 orin 全部本地节点」互证。
4. **与官方文档的三处口径差异（已在两图中按实测修正）**：① 业务大脑 Agent/task_engine/SM 在 **rk** 而非 orin（官方《快速入门》§1 仅笼统说运控板含「交互基础服务」）；② 官方示例音频节点名 `/hal_audio` 实测为 `/audiomanager_ros2_node`；③ HAL 按 soc0/soc1 双实例拆分（`hal_sensor_soc0/soc1`、`process_manager0/1`），官方图未体现。
5. **二开接入位与 7.2 的「一个 ROS 2 域」一致**：SDK 用户程序从 PC 通过 ★接口面直连任一板的任意服务/话题；板载部署时按官方《快速入门》§4 落位 `/robot/software/<your_module>/` 并注册进 process_manager 配置，即成为 7.2 图中 rk/orin 域内的新模块。
6. **模块归类以启动清单为权威依据（2026-10-30 复核）**：rk 分层与成员按 `t1_v2d0_soc0_config.yaml` stages 注释 + cgroup slice（§6.4.2/6.4.3）重划——`audio_manager`/`display_ui` 属 interaction.slice 故从「基础服务层」移入交互层；`mc`+`device_manager` 同属 realtime.slice 故同层；`sm`/`hds` 在 manifest 注释中为「系统状态监控」故移入系统与运维层；`lumina`/`scene_engine` 功能属交互表现但 slice=system（图中已注记）。此前遗漏的 **device_manager、scene_engine、resource_manager、abox、rk 侧 hal_camera/hal_rgbd 实例**已补齐（manifest stages 全部应用与图中框一一核对无缺漏；`aimrt_pulsecore` 部署未启动不入图）。子系统更名对照：业务与交互层→**交互与业务层**、基础服务层→**传感与外设接入层**（原音频/胸屏上移、系统类移出）、感知算法层→**感知与融合层**、导航规划层→**导航与跟随层**（对齐 manifest「跟随算法节点/导航算法模块」语义）。

---

<a id="sec8"></a>

## 附录 A. 采集方法与只读合规说明

1. **只读原则**：全程未对两板写入/重启/kill/服务调用（无 `ros2 service call`、无 `topic pub`、无参数修改）。`ros2 node/topic/service list` 属图发现查询；orin 侧复用了板上已存在的 ros2-daemon（pid 3066，非本次创建）。
2. **已知副作用（向用户报备）**：rk 上一次 `ros2 node list` 曾把输出重定向到 `/tmp/.nl.txt`（空文件，重启即清）；rk 侧 `--no-daemon` 查询不落盘。除此之外无任何写入。
3. **采集命令**（可复现）：`ssh t1pro_orin 'source /opt/docker/env.sh && ros2 node list'`、`ros2 topic list -v`、`ros2 service list -t`、`ros2 node info <node>`；定义文件位于板上 `/robot/software/interface/aimdk_msgs/share/aimdk_msgs/{msg,srv}[/<子系统>]` 与 `/robot/software/algorithm/aimdk_msgs/share/aimdk_msgs/`。
4. **归属核实结果（2026-10-31 板上实证，原推断清单全部闭环）**：`/HK_ros2_rpc_node`＝管家/云属性桥（SetProperty 服务端 + CloudPropertyService/ReportProperty 客户端）；`/task_trigger_ros2_node`＝**Agent 交互触发链**（订阅 `/agent/task_operation`、`/aima/agent/face_info`、`/aima/interaction/state`，`TaskStatus` 服务客户端，无独立进程＝进程内节点——原推断 task_engine 侧有误）；`/trro_recv_dedicated`＝App 通道接收器（订阅 `trro_pull_message`，无独立进程，随 app_proxy 域）；rk `hal_camera(rk)`＝头部相机（ox05bis）+BMI08 IMU 直连采集（§3.2，图像走 iceoryx 故不在 ROS 图）；`exposure`＝5×ToF 曝光/板间同步控制面（I2C/V4L2 直控）；`scene_engine`＝ros2 通道订阅 `interaction/state`、`agent/face_info`（cfg 实证）。
5. **版本基线**：采集于整机固件 `t2-3`（rk 设备树型号）/ orin JetPack（driver 540.4.0）；接口清单与该版本强相关。

<a id="sec9"></a>

## 附录 B. 与既有调研文档的关系

- 本文是 `t1-pro-developer-sdk-analysis.md`（推导口径）的**实机校验版**：§5.2/§5.3 的进程部署推导与实测一致（Agent/task_engine 在 rk=运控板侧、感知 nav2 在 orin=大脑板侧——注意官方「大脑板」指 orin，但**业务大脑 Agent 实际跑在 rk**，与推导文档 M1 标注的「大脑板进程 agent」需修正为 rk）。
- SDK 官方 30 接口（★）与《接口说明.md》§1 全部在实机图上得到印证，另发现官方未文档化的内部接口 ~370 个。

---

