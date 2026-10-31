# c4：传感器直连拓扑板上实证（2026-10-31）

> 第 4 轮板上实测（c1 作废 / c2 usecase 连续采样 / c3 基线快照 / **c4 本轮**）。
> 目的：闭环 §7.1 图注 5 的「Agent 视觉/rtsp 帧到达路径」未确证项 + §7.5 归属推断清单。
> 方法：**全程只读**（`ps` / `fuser` / `dmesg` / 配置文件读取 / `ros2 node|topic info`），无任何写入。
> 结论已同步至文档 §3.2 / §3.3 / §5 / §6.3 注① / §7.1 图注 5 / §7.5。

## 1. 设备持有关系（fuser 实证）

| 板 | 设备 | 设备真身（/sys/class/video4linux/*/name） | 持有进程 | 采集内容 |
|---|---|---|---|---|
| rk | /dev/video0-1 | stream_cif_mipi_id0/id1（max96724_b@4f 解串 + ox05bis@27 → rkcif-mipi-lvds1） | hal_camera (PID 3903) | 头部相机 2592×1944@10fps |
| rk | /dev/bmi08_imu | — | hal_camera (PID 3903) | 头部 IMU（BMI08，100Hz） |
| rk | /dev/video2 | stream_cif_mipi_id2 | hal_rgbd (PID 2685)＝**exposure 进程**（进程名 hal_rgbd） | main ToF 曝光控制通道 |
| orin | /dev/video0-1 | vi-output, ofilm_x5b 7-0039 / 7-003a | hal_camera (PID 1511) | 2×5MP RGB（2592×1944 UYVY） |
| orin | /dev/video2-7 | vi-output, ofilm_tof 7-003b/003c + ofilm_tofb 2-0019/001a/001b/001c | hal_rgbd (PID 1481) | 5×ToF 深度数据面 |

- rk 内核：`csi2-dphy3 ↔ max96724_b@4f`、`csi2-dphy0 ↔ ox05bis@27`、`rkcif driver v00.02.00`（dmesg）。
- orin 内核：`insmod ko/ofilm_x5b.ko`（2× Detected GMSL Sensors）+ `ofilm_tof_slave.ko`（2×）+ `ofilm_tofb_slave.ko`（3×）；tegra-video 驱动。
- orin 相机配置中的鱼眼条目（fisheye l/r，IMX900/SC132GS 双型号）指向 **不存在的 /dev/video8/9** —— 其他机型（Q1 等）遗留配置，本机无鱼眼。

## 2. rk hal_camera 配置实证（cfg/hal_camera_module_config_rk_t1_v2.yaml）

- `head_stereo_left`：enable **true**，system: RK，/dev/video1，2592×1944@10fps，topic `/aima/hal/camera/bgr_camera_l` + NV12 `/aima/hal/camera/nv12_l`（864×648）。
- `head_stereo_right`：enable **false**（板上仅一颗 ox05bis）。
- `imu_head`：BMI08 @ /dev/bmi08_imu，100Hz → `/aima/hal/imu/imu_head`（**orin 配置中该项被注释 → imu_head 归属 rk**）。
- 通道后端：`(/aima/hal/camera.*)` **仅 iceoryx**（另有 dmabuf 零拷贝选项）——图像不进 ROS 图。
- `max_wait_soc_sync_timeout_seconds: 86400`：采集等 soc_sync 门控。

## 3. rk exposure 配置实证（hal_rgbd 包内 cfg/exposure/exposure_config.yaml）

5×ToF 控制面映射（全部 enable: true）：

| ToF | I2C | V4L2 | sensor_info 话题 |
|---|---|---|---|
| main | /dev/i2c-5:0x3b | /dev/video2 | /ofilm/tof/main/sensor_info |
| right | /dev/i2c-0:0x19 | /dev/video11 | /ofilm/tof/right/sensor_info |
| rear | /dev/i2c-0:0x1a | /dev/video12 | /ofilm/tof/rear/sensor_info |
| front | /dev/i2c-0:0x1b | /dev/video13 | /ofilm/tof/front/sensor_info |
| left | /dev/i2c-0:0x1c | /dev/video14 | /ofilm/tof/left/sensor_info |

订阅 `/ofilm/tof/inter_board`（板间同步）+ 5 路 sensor_info；发布 /aima/hds/diagnostic_list。主线程名 `hal_rgbd`（→ §6.3 资源表中"rk 侧 hal_rgbd"实为此曝光进程）。

## 4. 帧路径闭环（Agent 视觉 / rtsp）

- Agent（aimrt_agent/bin/cfg/t1/agent.yaml）：`image_capture_topic: /aima/hal/camera/bgr_camera_l`（"默认使用左侧摄像头"）。
- rtsp_server（rtsp_server_t1_v2d0_sub.yaml）：**纯 NV12 源，经 AimRT iceoryx 从 hal_camera 订阅** nv12_l/nv12_r，MPP 编码供 App（端口 2554）。
- NPU 实时负载（静置）：Core0 25% / Core1 15% —— Agent 视觉常跑 → 帧持续板内流动。
- ROS 图像话题 `/aima/hal/camera/head_stereo_left`（sensor_msgs/Image）：**Publisher count 0**（订阅 1）→ orin 图像同样只走 iceoryx，不跨板。

## 5. 跨板边界

- 跨板仅有：结果级数据（点云/目标/代价图/指令，FastDDS 域 0）+ `soc_sync/ready`（双板相机同步就绪）+ `/ofilm/tof/inter_board`（ToF 板间同步）。
- ToF 分工：**控制面在 rk（exposure，I2C/V4L2 直控）、数据面在 orin（ofilm_tof/tofb → hal_rgbd → coreon_fusion）**。
- 图像零跨板；两板 TCP 连接为空（ss -tn），DDS 走 UDP 组播。

## 6. 归属核实（ros2 node info / ps，原 §7.5 推断清单闭环）

| 节点/进程 | 实证结论 | 证据 |
|---|---|---|
| /HK_ros2_rpc_node | 管家/云属性桥（推断→实证） | 服务端 DevicePropertyService/SetProperty + 客户端 CloudPropertyService/ReportProperty |
| /task_trigger_ros2_node | **Agent 交互触发链**（原推断 task_engine 侧有误） | 订阅 /agent/task_operation、/aima/agent/face_info、/aima/interaction/state；TaskStatus 服务客户端；两板均无同名独立进程＝进程内节点 |
| /trro_recv_dedicated | App 通道接收器，app_proxy 域 | 订阅 /aima/app_proxy/trro_pull_message；无独立进程 |
| scene_engine | ros2 通道订阅（非 iceoryx 推断） | cfg：sub interaction/state、agent/face_info（ros2 后端） |
| rk hal_camera(rk) | 头部相机+IMU 直连采集（非"供电/管理"） | 本文件 §1-§2 |
| rk hal_rgbd(rk) | 实为 exposure 曝光控制进程 | 本文件 §3 |

## 7. 外部佐证

- [中新网：启元第 5000 台下线，欧菲光全栈感知方案](https://www.chinanews.com.cn/cj/2026/10-30/10723321.shtml)——「七路融合感知：双目 RGB + 5 路 ToF + 6 轴 IMU」。
- 本机实测合计：3×5MP RGB（rk 1×ox05bis + orin 2×ofilm_x5b）+ 5×ToF + BMI08 IMU（rk）。

## 8. 视频流路径补充实测（同日晚间，视频流是否跨板专项）

问题：两板都有 hal_camera，视频流是否在板间流动？

**进程活跃度（ps pcpu，静置态持续运行）：**
- rk：aima-agent-app **105%**（交互视觉+语音前端持续跑，NPU Core0 29%/Core1 9%）、hal_camera(rk) **13.9%**（ox05bis 持续采集）、rtsp_server 0.5%（待 App 连接）
- orin：hal_camera(orin) **42.9%**（2×x5b 持续采集）、hal_rgbd **38.9%**（5×ToF 数据面）、percep ~10% + nav2_perception ~6%

**板间链路流量（orin unused0 口 /proc/net/dev 5s 采样）：**
下行 155KB/s + 上行 80KB/s ≈ **2 Mbps** —— 远低于任何一路视频流
（864×648@10fps NV12 ≈67Mbps；2592×1944 原始 ≈750Mbps）→ **视频零跨板**，
板间只跑结果级数据（点云/目标/代价图/指令）+ soc_sync/inter_board 同步信号。

**消费方配置实证：**
- orin percep（percep_aimrt.yaml）：订阅 `/aima/hal/camera/head_stereo_left/right`
  （orin 本地双目）+ `/ofilm/tof/{main,rear,front}/point_cloud`；输出 /percep/cloud、
  /percep/detect/env 等 —— ROS 图上 head_stereo_left 订阅数=1 即 percep，
  发布者走 iceoryx（0 ROS 发布者）
- rk Agent（agent.yaml）：`image_capture_topic: /aima/hal/camera/bgr_camera_l`
- rk rtsp（rtsp_server_t1_v2d0_sub.yaml）：iceoryx 订阅 nv12_l/nv12_r → MPP 编码供 App

**结论**：两板 hal_camera 同时各自采集，但读的是各自直连的不同相机——
rk=交互视觉（人脸/手势，NPU 就地推理），orin=感知/导航（双目+ToF，TRT 就地推理）。
"计算跟着传感器走"，无跨板视频，无性能瓶颈。
