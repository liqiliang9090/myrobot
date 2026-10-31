#!/usr/bin/env python3
# 生成 §7.1 分层系统架构图（双板纵切 × 五层横切 × 工具链纵贯）SVG
# 用法：python3 figures/gen_fig_7_1.py  →  figures/fig-7-1-layered-arch.svg
OVERFLOW = []
FONT = "Noto Sans CJK SC"

def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def est_w(s, fs):
    """估算行宽（CJK≈1.0em，ASCII≈0.55em），用于自检是否溢出单元格"""
    w = 0.0
    for ch in s:
        w += 1.0 if ord(ch) > 0x2E80 else (0.62 if ch.isascii() and not ch.isalnum() else 0.56)
    return w * fs

def text_block(x, y, lines, fs=12, lh=None, color='#1f2933', maxw=None, tag=''):
    """lines: [(text, bold)]；返回 svg 与结束 y"""
    lh = lh or int(fs * 1.42)
    out = []
    for t, b in lines:
        if maxw:
            w = est_w(t, fs)
            if w > maxw:
                OVERFLOW.append((round(w), maxw, t))
        out.append(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{fs}" '
                   f'fill="{color}"{" font-weight=\"700\"" if b else ""}>{esc(t)}</text>')
        y += lh
    return '\n'.join(out), y

def box(x, y, w, h, fill, stroke='#5f6b7a', sw=1, rx=6, dash=''):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>'

def arrow(x1, y1, x2, y2, color='#35495e', both=True, dash='', sw=1.6):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    m = 'marker-start="url(#ah)" ' if both else ''
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}" '
            f'{m}marker-end="url(#ah)"{d}/>')

S = []
S.append('<?xml version="1.0" encoding="UTF-8"?>')
S.append('<svg xmlns="http://www.w3.org/2000/svg" width="1560" height="872" viewBox="0 0 1560 872">')
S.append('<defs><marker id="ah" markerWidth="9" markerHeight="8" refX="8" refY="4" orient="auto">'
         '<path d="M0,0 L9,4 L0,8 z" fill="#35495e"/></marker></defs>')
S.append(f'<rect x="0" y="0" width="1560" height="872" fill="#ffffff"/>')

# ---------- 版面常量 ----------
LBL_X, LBL_W = 20, 92                 # 层标签列
C1_X, C1_W = 116, 378                 # rk·交互业务
C2_X, C2_W = 506, 282                 # rk·运控执行
C3_X, C3_W = 796, 320                 # orin
TC_X, TC_W = 1140, 400                # 工具链（纵贯）
EXT_Y, EXT_H = 20, 46                 # 外部生态带
HDR_Y, HDR_H = 84, 34                 # 板头行
SUB_Y, SUB_H = 122, 28                # 部分行
STRIP_A_Y, STRIP_B_Y, STRIP_C_Y = 160, 180, 200  # 连接条带三行
LAYER_Y0 = 214                        # 业务层顶
LAYERS = [('具身业务层', 290, '#eaf3ff'), ('具身框架层', 74, '#f0f7ea'),
          ('OS 层', 74, '#fdf6e3'), ('驱动层', 66, '#f5eff7'), ('硬件层', 88, '#fdeee6')]
# 层 y 累积
ys = []
y = LAYER_Y0
for _, h, _ in LAYERS:
    ys.append(y); y += h + 4
MAT_BOT = y - 4                       # 矩阵底
TC_BOT = MAT_BOT

# ---------- 外部生态带（跨矩阵三列宽） ----------
S.append(box(LBL_X, EXT_Y, (C3_X + C3_W) - LBL_X, EXT_H, '#e8f1fb', '#7fa8cc', 1.2))
t, _ = text_block(LBL_X + 14, EXT_Y + 29,
                  [('外部生态：开发 PC（★接口面可直连双板）· PrimeBot App · 启元云端 · 体感遥控器 ·「圈圈」UWB 标签 · Insta360 —— 对外接入全部终结于运控板', True)],
                  fs=13, color='#1c3d5f', maxw=(C3_X + C3_W) - LBL_X - 28, tag='ext')
S.append(t)
for ax in (900, 1000, 1060):                      # 外部→rk 三个小箭头（右移避让接入行文字）
    S.append(arrow(ax, EXT_Y + EXT_H + 1, ax, HDR_Y - 2, both=False, sw=1.2))
t, _ = text_block(LBL_X, 78, [('[接入] App→app_proxy(TRRO/鉴权) · 云→cloud_proxy(MQTT/TLS) · 遥控器→rc(蓝牙) · 圈圈→connx(BLE+UWB) · Insta360→insta360(WiFi) · PC→★接口面(FastDDS 直连双板)', False)],
                  fs=10.5, color='#4a5b6c')
S.append(t)

# ---------- 板头行 ----------
S.append(box(C1_X, HDR_Y, (C2_X + C2_W) - C1_X, HDR_H, '#dce9f7', '#5b87b5', 1.2))
t, _ = text_block(C1_X + 12, HDR_Y + 22, [('运控板 rk3576（soc0 · 10.1.1.100 · 8 核 · CPU2/3 隔离）', True)],
                  fs=13, maxw=(C2_X + C2_W) - C1_X - 24)
S.append(t)
S.append(box(C3_X, HDR_Y, C3_W, HDR_H, '#eadff5', '#8d6fb4', 1.2))
t, _ = text_block(C3_X + 10, HDR_Y + 22, [('大脑板 Orin Nano Super（soc1）', True)], fs=13, maxw=C3_W - 20)
S.append(t)
# 工具链条头
S.append(box(TC_X, HDR_Y, TC_W, HDR_H, '#fdf3d0', '#c9a24b', 1.2))
t, _ = text_block(TC_X + 12, HDR_Y + 22, [('◆ 三方工具链（纵贯两板 · 版本见 §6.1）', True)], fs=13, maxw=TC_W - 24)
S.append(t)

# ---------- 部分行 ----------
S.append(box(C1_X, SUB_Y, C1_W, SUB_H, '#f2f7fc', '#8aa8c4'))
t, _ = text_block(C1_X + 10, SUB_Y + 19, [('交互业务（非实时）interaction/system/auxiliary.slice', True)], fs=11.5, maxw=C1_W - 20)
S.append(t)
S.append(box(C2_X, SUB_Y, C2_W, SUB_H, '#fdf1ea', '#c49a82'))
t, _ = text_block(C2_X + 10, SUB_Y + 19, [('运控执行（实时）realtime.slice · 隔离核', True)], fs=11.5, maxw=C2_W - 20)
S.append(t)
S.append(box(C3_X, SUB_Y, C3_W, SUB_H, '#f6f0fb', '#a48ec4'))
t, _ = text_block(C3_X + 10, SUB_Y + 19, [('复杂感知与规划', True)], fs=11.5, maxw=C3_W - 20)
S.append(t)

# ---------- 连接条带（板内 / 跨板 / 工具链注入） ----------
c1c, c2c, c3c = C1_X + C1_W // 2, C2_X + C2_W // 2, C3_X + C3_W // 2
t, _ = text_block(c1c - 185, STRIP_A_Y - 5, [('① 板内（iceoryx/DDS）：task_engine→★SetMcAction/velocity ↓ · mc/common/state★ ↑', False)], fs=10.8, color='#35495e')
S.append(t); S.append(arrow(c1c - 120, STRIP_A_Y + 3, c2c, STRIP_A_Y + 3))
t, _ = text_block(c1c - 185, STRIP_B_Y - 5, [('② 跨板·任务与感知（FastDDS 域0 · 10.1.1.0/24）：FollowTaskCtrl/★navi 指令 ↓ · follow_candidates★/点云/代价图//odom·/tf ↑', False)], fs=10.8, color='#35495e')
S.append(t); S.append(arrow(c1c, STRIP_B_Y + 3, c3c, STRIP_B_Y + 3))
t, _ = text_block(c2c + 40, STRIP_C_Y - 5, [('③ 跨板·速度直连：orin velocity(pnc/follow/运镜)→MC（SetMcInputSource 仲裁）', False)], fs=10.8, color='#35495e')
S.append(t); S.append(arrow(c3c, STRIP_C_Y + 3, c2c, STRIP_C_Y + 3, both=False))
S.append(arrow(TC_X, STRIP_B_Y + 3, C3_X + C3_W, STRIP_B_Y + 3, dash='5,4', sw=1.3))

# ---------- 五层 ----------
# 业务层内容（三列）
biz_c1 = [
 ('业务大脑', True),
 ('Agent 对话智能体（★asr_result/PlayTts）', False),
 ('task_engine 任务引擎（AmRequest 仲裁）', False),
 ('SM 状态机（sm/system_state）', False),
 ('交互表现', True),
 ('audio_manager（★播放/音量）· display_ui 胸屏', False),
 ('lumina 灯效 · scene_engine 场景引擎', False),
 ('系统与运维', True),
 ('housekeeper+app_proxy · hds · cloud_proxy · ota', False),
 ('data_exporter · drp 录包 · abox 日志', False),
 ('板基座：sys_guard · iox-roudi · tetherd · secret_store', False),
 ('传感与外设', True),
 ('hal_sensor（★bms/touch · SetServo · LedStripCommand）', False),
 ('legged_odom（/odom·/tf）· hal_camera/hal_rgbd rk 实例', False),
 ('配件：rc ·「圈圈」connx · insta360 · rtsp', False),
 ('resource_manager 资源/拍照', False),
]
biz_c2 = [
 ('mc 运动控制（/mc_ros2_node）', True),
 ('★SetMcAction · velocity（指令下发）', False),
 ('★mc/common/state（全量状态上报）', False),
 ('RL 策略 onnxruntime@CPU3 实时推理', False),
 ('WBC 全身控制 · 动作 FSM', False),
 ('device_manager（设备实时 IO）', True),
 ('——同 realtime.slice（§2.4）', False),
 ('9×SCHED_RR 钉隔离核', False),
]
biz_c3 = [
 ('感知与融合', True),
 ('percep（分割/深度/检测@TensorRT）', False),
 ('coreon_fusion（27 路融合）· person_track+ReID', False),
 ('（★follow_candidates）· brightness', False),
 ('nav2_perception（点云合并）', False),
 ('导航与跟随', True),
 ('Nav2 1.1.20（★/navi/command · /navi/loadmap）', False),
 ('follow_node（★follow/state）', False),
 ('trajectory_tracker（环绕运镜）', False),
 ('传感接入', True),
 ('hal_rgbd 5目ToF · hal_camera 相机阵列+IMU', False),
 ('hal_sensor_soc1（ADU/温度）', False),
 ('板基座：sys_guard · ota_slave · data_exporter', False),
 ('（部署未运行的清单见 §6.4.2）', False),
]
fw_c1 = [
 ('AimRT+aimrte · ROS 2 Humble+FastDDS · iceoryx', False),
 ('em/process_manager0 + aimdk_msgs', False),
 ('推理运行时：ORT · RKNN · MNN · sherpa', False),
]
fw_c2 = [('iceoryx 零拷贝实时通道', False), ('onnxruntime 实时推理', False)]
fw_c3 = [('ROS2+Nav2 框架 · TensorRT 10.7+CUDA12.6+cuDNN', False), ('OpenCV · AimRT · em/process_manager1', False)]
os_c1 = [('Ubuntu 22.04.5（6.1.141）· systemd（em+robot.slice）', False),
          ('PipeWire · NM/hostapd/dnsmasq · mosquitto', False),
          ('ptp4l + chrony 时钟同步', False)]
os_c2 = [('与左列同板共享内核（无 PREEMPT_RT）', False),
          ('实时性 = isolcpus=2,3', False),
          ('+ SCHED_RR 80/85（§2.4）', False)]
os_c3 = [('Ubuntu + L4T R36.4.4（JetPack 6.2）· systemd', False),
          ('NV 多媒体栈（NVDEC/NVENC/NVJPG/VIC/OFA）', False),
          ('ptp4l 时钟同步', False)]
dr_c1 = [('Mali kbase · RKNPU v0.9.8 · CAN · stmmac+YT9215RB', False), ('V4L2/USB 相机 · I2C/SPI/UART · MPP/RGA', False)]
dr_c2 = [('CAN0/1 驱动（硬中断 irq83/84 @CPU2）', False)]
dr_c3 = [('nvgpu 540.4.0 · V4L2/NvArgus · GMSL 相机', False),
          ('（ox05bis/max96724）· NVDEC/NVENC', False),
          ('千兆以太', False)]
hw_c1 = [('rk3576 SoC（8核 A53+A72 big.LITTLE）', False),
          ('Mali-G52 图形 GPU · NPU 6TOPS · 统一 DDR 8GB', False),
          ('eMMC 116.5G · WiFi/BT · YT9215RB+RJ45', False),
          ('本体传感器（BMS/触摸/IMU…）', False)]
hw_c2 = [('CAN0/CAN1 总线', False), ('14× 关节执行器 + 主控 MCU', False)]
hw_c3 = [('Orin（6×A78AE · Ampere 1024CUDA+32TC）', False),
         ('统一 LPDDR5 8GB · 102.4GB/s · NVMe 238.5G', False),
         ('头部相机阵列（双目/鱼眼/彩色/5目ToF）', False)]

CONTENT = [  # (层序号, 三列内容, 字号)
 (0, (biz_c1, biz_c2, biz_c3), 11.6),
 (1, (fw_c1, fw_c2, fw_c3), 11.6),
 (2, (os_c1, os_c2, os_c3), 11.6),
 (3, (dr_c1, dr_c2, dr_c3), 11.6),
 (4, (hw_c1, hw_c2, hw_c3), 11.6),
]
FILLS = ['#f7fafe', '#f6faf2', '#fefaf0', '#faf6fb', '#fef6f1']
for i, (name, h, _) in enumerate(LAYERS):
    ytop = ys[i]
    # 层标签
    S.append(box(LBL_X, ytop, LBL_W, h, LAYERS[i][2], '#9aa8b5', 1))
    t, _ = text_block(LBL_X + 10, ytop + h // 2 + 4, [(name, True)], fs=12, color='#33414e', maxw=LBL_W - 16)
    S.append(t)
    # 三列单元格
    for cx, cw, cont, fill in ((C1_X, C1_W, CONTENT[i][1][0], FILLS[i]),
                               (C2_X, C2_W, CONTENT[i][1][1], FILLS[i]),
                               (C3_X, C3_W, CONTENT[i][1][2], FILLS[i])):
        S.append(box(cx, ytop, cw, h, fill, '#a8b4c0', 1))
        t, endy = text_block(cx + 10, ytop + 18, cont, fs=CONTENT[i][2], maxw=cw - 20)
        S.append(t)
        assert endy <= ytop + h + 6, f'{name} 单元格纵向溢出: {endy} > {ytop + h}'

# ---------- 工具链纵贯列（8 组小框） ----------
groups = [
 ('◆ 中间件与通信', ['ROS 2 Humble · Fast DDS 2.6.12 · rmw-fastrtps', 'iceoryx 2.95.4.2 · AimRT + aimrt_py']),
 ('◆ AI 推理运行时', ['onnxruntime 1.19.2 · RKNN 2.3.2 · MNN · sherpa-onnx', 'TensorRT 10.7 · CUDA 12.6 · cuDNN 9.3']),
 ('◆ 多媒体与图像', ['OpenCV 4.5.4/4.8 · GStreamer+rockchip · ffmpeg', 'MPP 1.5.0 · RGA 2.2.0']),
 ('◆ UI 与音频', ['flutter-pi + Flutter · Qt 5.15.3', 'PipeWire 1.6.8（PA 兼容 6001/4713）']),
 ('◆ 系统与网络服务', ['systemd · NM · hostapd/dnsmasq · mosquitto', 'ptp4l/chrony · can-utils']),
 ('◆ 商业 SDK 与语言运行时', ['讯飞 VTN/ESR/听写 · omnis_sdk 4.0.0', 'Python 3.10 · C++20']),
 ('◆ 构建与代码生成', ['colcon+CMake 工作空间 · AimRT RPC 代码生成', 'Rust 工具链（orin cargo/rustup）']),
 ('◆ 调试与观测', ['ros2 CLI · tegrastats · jetson-stats 7.2.1', 'iox-introspection · ASan · monitor/viz']),
]
gy = HDR_Y + HDR_H + 6
avail = TC_BOT - gy
gh = min(66, (avail - 7 * 5) // 8)
for title, lines in groups:
    S.append(box(TC_X, gy, TC_W, gh, '#fffdf4', '#d8c48a', 1))
    t, endy = text_block(TC_X + 10, gy + 17, [(title, True)] + [(l, False) for l in lines],
                         fs=11.4, maxw=TC_W - 20)
    S.append(t)
    gy += gh + 5

# ---------- 底部图例 ----------
t, _ = text_block(LBL_X, MAT_BOT + 22, [
    ('图例：箭头①②③＝板内/跨板数据流（★为官方接口）；虚线＝工具链注入（运行时库/构建产物/调测工具）；层间自上而下＝上层依赖下层。', False),
    ('列内链：运控执行列自上而下＝mc→CAN 驱动→14 执行器（1kHz 实时环）；orin 板内相机/ToF 原图经 iceoryx 直供感知融合（§4.0）。', False)],
    fs=11, color='#5b6b7a', lh=16)
S.append(t)

S.append('</svg>')
out = '\n'.join(S)
open('figures/fig-7-1-layered-arch.svg', 'w', encoding='utf-8').write(out)
try:
    import cairosvg
    cairosvg.svg2png(url='figures/fig-7-1-layered-arch.svg', write_to='figures/fig-7-1-layered-arch.png', scale=1.5)
except Exception as e:
    print('PNG 跳过（无 cairosvg）:', e)
if OVERFLOW:
    for w, m, t in OVERFLOW: print(f'溢出 {w}>{m}: {t}')
    raise SystemExit(1)
print(f'OK -> figures/fig-7-1-layered-arch.svg  ({len(out)} bytes, 底边 {MAT_BOT}px)')
