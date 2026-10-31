#!/usr/bin/env python3
# 生成 §7.1 逻辑视图（双板纵切 × 五层横切 × 工具链纵贯）SVG/PNG
# 结构：板框 ⊃ 部分框（业务/框架/OS/驱动 四层）+ 硬件层格；①②③通信线画在图底部
# 用法：python3 figures/gen_fig_7_1.py  →  figures/fig-7-1-layered-arch.svg / .png
FONT = "Noto Sans CJK SC"

def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def est_w(s, fs):
    w = 0.0
    for ch in s:
        w += 1.0 if ord(ch) > 0x2E80 else (0.62 if not ch.isalnum() else 0.56)
    return w * fs

def text_block(x, y, lines, fs=12, lh=None, color='#1f2933', maxw=None, center=None):
    lh = lh or int(fs * 1.42)
    out = []
    for t, b in lines:
        if maxw:
            w = est_w(t, fs)
            assert w <= maxw, f'溢出({w:.0f}>{maxw}): {t}'
        tx = x if center is None else center - est_w(t, fs) / 2
        out.append(f'<text x="{tx:.0f}" y="{y}" font-family="{FONT}" font-size="{fs}" '
                   f'fill="{color}"{" font-weight=\"700\"" if b else ""}>{esc(t)}</text>')
        y += lh
    return '\n'.join(out), y

def box(x, y, w, h, fill, stroke='#5f6b7a', sw=1, rx=6, dash=''):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>'

def _head(x, y, fx, fy, color='#35495e', size=9):
    """在 (x,y) 画指向 (从 fx,fy 来向) 的实心三角箭头（不依赖 defs/marker）"""
    dx, dy = x - fx, y - fy
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    dx, dy = dx / n * size, dy / n * size
    px, py = -dy * 0.45, dx * 0.45
    return (f'<polygon points="{x:.0f},{y:.0f} {x - dx + px:.0f},{y - dy + py:.0f} '
            f'{x - dx - px:.0f},{y - dy - py:.0f}" fill="{color}"/>')

def arrow(x1, y1, x2, y2, color='#35495e', both=True, dash='', sw=1.6):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    out = [f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"{d}/>']
    out.append(_head(x2, y2, x1, y1, color))
    if both:
        out.append(_head(x1, y1, x2, y2, color))
    return ''.join(out)

S = []   # 图体元素（svg 开闭标签在末尾按实际画布高拼装）

# ---------- 版面常量 ----------
LBL_X, LBL_W = 20, 92
C1_X, C1_W = 116, 378                 # rk·交互业务
C2_X, C2_W = 506, 282                 # rk·运控
C3_X, C3_W = 796, 320                 # orin
TC_X, TC_W = 1140, 400                # 工具链（纵贯）
PAD = 8
BOARD_Y0, BOARD_TITLE_H, PART_TITLE_H = 96, 30, 24
ROW_H = [420, 78, 102, 70]            # 业务(模块框化)/框架/OS(含外包框标题带)/驱动（部分框内四层）
HW_H = 112                            # 硬件层（板框内、部分框外；rk 侧含外包框标题带）
EXT_Y, EXT_H = 20, 46

PART_Y = BOARD_Y0 + BOARD_TITLE_H                     # 126
row_y = []
_y = PART_Y + PART_TITLE_H                            # 150
for h in ROW_H:
    row_y.append(_y); _y += h + 4
PART_BOT = row_y[-1] + ROW_H[-1] + 8                  # 底部留 8px，部分框完整包住子框
CELL_PAD = 6                                          # 部分框内子框横向内缩
HW_Y = PART_BOT + 14                                  # 680
HW_BOT = HW_Y + HW_H                                  # 768
BOARD_BOT = HW_BOT + PAD                              # 776

# ---------- 内容 ----------
BIZ_COLS = [
 [  # rk·交互业务：对外接口与依赖方在上，数据源在下（运维链移入工具链列，板基座不画）
  ('housekeeper + app_proxy', ['App 接入鉴权 · 网络/蓝牙/权限（ConnectRobot）']),
  ('Agent 智能体', ['★asr_result/PlayTts · LLM 对话与意图']),
  ('task_engine 任务引擎', ['AmRequest 仲裁 · 长任务编排']),
  ('SM 状态机', ['sm/system_state · SmNodeService 驱动各应用']),
  ('交互表现', ['audio_manager（★播放/音量）· display_ui 胸屏', 'lumina 灯效 · scene_engine 场景']),
  ('传感与外设', ['hal_sensor（★bms/touch · SetServo · 灯语）', 'hal_camera：头部相机(ox05bis)+IMU 直连', 'exposure：5×ToF 曝光/板间同步（控制面）', '配件：rc · 圈圈 · insta360 · resource_manager']),
 ],
 [  # rk·运控：mc 对上暴露 ★接口；CPU2/CPU3 隔离核业务显式标注
  ('mc 运动控制', ['★SetMcAction · velocity（指令下发）', '★mc/common/state（全量状态上报）', 'CPU3 独占：RL 策略 · WBC · 动作 FSM', 'CPU2：CAN 硬中断 + dm 设备 IO']),
  ('device_manager 设备实时 IO', ['SCHED_RR 钉 CPU2 · realtime.slice', '（与 mc 同 slice，§2.4）']),
 ],
 [  # orin：任务产出层在上，感知融合居中，传感数据源在下
  ('导航与跟随', ['Nav2 1.1.20（★navi 指令/地图加载）', 'follow_node（★follow/state）', 'trajectory_tracker（环绕运镜）']),
  ('感知与融合', ['percep（分割/深度/检测@TRT）', 'coreon_fusion（27 路融合）', 'person_track+ReID（★follow_candidates）', 'brightness · nav2_perception（点云合并）']),
  ('传感接入', ['hal_rgbd 5 目 ToF 数据面（ofilm 直连）', 'hal_camera 2×5MP RGB（x5b 直连）', 'hal_sensor_soc1（ADU/温度）']),
 ],
]

fw_c1 = [
 ('AimRT+aimrte · ROS 2 Humble+FastDDS · iceoryx', False),
 ('em/process_manager0 + aimdk_msgs', False),
 ('推理运行时：ORT · RKNN · MNN · sherpa', False),
]
fw_c2 = [('iceoryx 零拷贝实时通道', False), ('onnxruntime 实时推理', False)]
fw_c3 = [('ROS2+Nav2 框架', False), ('TensorRT 10.7 + CUDA 12.6 + cuDNN 9.3', False), ('OpenCV · AimRT · em/process_manager1', False)]
os_c1 = [('systemd（em+robot.slice）· interaction.slice', False),
         ('system.slice：PipeWire/NM/mosquitto 等系统服务', False),
         ('auxiliary 40% 限流 · 全部 SCHED_OTHER（CFS）', False)]
os_c2 = [('无 PREEMPT_RT · isolcpus=2,3 隔离', False),
         ('mc：33×CFS+3×SCHED_RR(85/80)@CPU3', False),
         ('dm：SCHED_RR@CPU2（§2.4）', False)]
os_c3 = [('Ubuntu + L4T R36.4.4（JetPack 6.2）', False),
         ('systemd · NV 多媒体栈', False),
         ('（NVDEC/NVENC/NVJPG/VIC/OFA）· ptp4l', False)]
dr_c1 = [('Mali kbase · RKNPU v0.9.8 · CAN · stmmac+YT9215RB', False), ('V4L2/USB 相机 · I2C/SPI/UART · MPP/RGA', False)]
dr_c2 = [('CAN0/1 驱动（硬中断 irq83/84 @CPU2）', False)]
dr_c3 = [('nvgpu 540.4.0 · V4L2/NvArgus · GMSL 相机', False),
         ('（ox05bis/max96724）· NVDEC/NVENC', False),
         ('千兆以太', False)]
hw_c1 = [('业务核 = 4×A72 大核 + 2×A53 小核', True),
         ('Mali-G52 图形 GPU · NPU 6TOPS · 统一 DDR 8GB', False),
         ('eMMC 116.5G · WiFi/BT · YT9215RB+RJ45', False),
         ('头部相机+IMU · 本体传感（GMSL/rkcif 直连）', False)]
hw_c2 = [('隔离小核 ×2（CPU2/CPU3）', True),
         ('CPU2：CAN0/1 硬中断 + dm 实时线程', False),
         ('CPU3：MC 运控独占（36 线程）', False),
         ('CAN0/1 总线 → 14× 关节执行器 + MCU', False)]
hw_c3 = [('Orin（6×A78AE · Ampere 1024CUDA+32TC）', False),
         ('统一 LPDDR5 8GB · 102.4GB/s · NVMe 238.5G', False),
         ('OFILM GMSL 模组：2×5MP RGB + 5 目 ToF', False)]

ROWS = [('具身框架层', (fw_c1, fw_c2, fw_c3)),
        ('OS 层', (os_c1, os_c2, os_c3)),
        ('驱动层', (dr_c1, dr_c2, dr_c3))]
FILLS = ['#f6faf2', '#fefaf0', '#faf6fb']
HW_FILL = '#fef6f1'
LBL_FILLS = ['#eaf3ff', '#f0f7ea', '#fdf6e3', '#f5eff7', '#fdeee6']

# ---------- 外部生态带 + 接入行 ----------
S.append(box(LBL_X, EXT_Y, (C3_X + C3_W) - LBL_X, EXT_H, '#e8f1fb', '#7fa8cc', 1.2))
t, _ = text_block(LBL_X + 14, EXT_Y + 29,
                  [('外部生态：开发 PC（★接口面可直连双板）· PrimeBot App · 启元云端 · 体感遥控器 ·「圈圈」UWB 标签 · Insta360 —— 对外接入全部终结于运控板', True)],
                  fs=13, color='#1c3d5f', maxw=(C3_X + C3_W) - LBL_X - 28)
S.append(t)
for ax in (900, 1000, 1060):
    S.append(arrow(ax, EXT_Y + EXT_H + 1, ax, BOARD_Y0 - 2, both=False, sw=1.2))
t, _ = text_block(LBL_X, 88, [('[接入] App→app_proxy(TRRO/鉴权) · 云→cloud_proxy(MQTT/TLS) · 遥控器→rc(蓝牙) · 圈圈→connx(BLE+UWB) · Insta360→insta360(WiFi) · PC→★接口面(FastDDS 直连双板)', False)],
                  fs=10.5, color='#4a5b6c')
S.append(t)

# ---------- 板框（含部分框四层 + 硬件层） ----------
def draw_board(bx, bw, title, stroke):
    S.append(box(bx, BOARD_Y0, bw, BOARD_BOT - BOARD_Y0, '#ffffff', stroke, 2, rx=10))
    t, _ = text_block(0, BOARD_Y0 + 20, [(title, True)], fs=13, color='#1c3d5f',
                      center=bx + bw // 2, maxw=bw - 20)
    S.append(t)

draw_board(C1_X - PAD, (C2_X + C2_W + PAD) - (C1_X - PAD),
           '运控板 rk3576（8×ARM + NPU 6TOPS + GPU集成显卡）', '#5b87b5')
draw_board(C3_X - PAD, (C3_X + C3_W + PAD) - (C3_X - PAD),
           '大脑板 Orin Nano（6×ARM + Orin 67TOPS）', '#8d6fb4')

PARTS = [(C1_X, C1_W, '交互业务（非实时）', '#f2f7fc', '#8aa8c4'),
         (C2_X, C2_W, '运控（实时 | 隔离核）', '#fdf1ea', '#c49a82'),
         (C3_X, C3_W, '复杂感知与规划', '#f6f0fb', '#a48ec4')]
for px, pw, pname, pfill, pstroke in PARTS:
    S.append(box(px, PART_Y, pw, PART_BOT - PART_Y, pfill, pstroke, 1.4, rx=8))
    t, _ = text_block(0, PART_Y + 17, [(pname, True)], fs=12, color='#33414e',
                      center=px + pw // 2, maxw=pw - 12)
    S.append(t)

# 具身业务层：每模块独立成框（上=对外接口/依赖方，下=被依赖底座/数据源）
# 可读性口径：文档正文里整图缩至 ~0.65×（PNG 0.43×），故模块框用衬底+深色粗边+大字号
def draw_module(px, pw, y, title, lines):
    TH, TLH = 19, 17                   # 标题行高 / 内容行高
    h = 8 + TH + TLH * len(lines) + 6
    S.append(box(px + 6, y, pw - 12, h, '#e9f2fc', '#4f7396', 2, rx=5))
    t, _ = text_block(px + 15, y + 21, [(title, True)], fs=13.6, color='#17456e', maxw=pw - 24)
    S.append(t)
    t, _ = text_block(px + 15, y + 21 + TH, [(l, False) for l in lines],
                      fs=12.6, color='#2c3a47', maxw=pw - 24)
    S.append(t)
    return y + h + 5

yt = row_y[0]
S.append(box(LBL_X, yt, LBL_W, ROW_H[0], LBL_FILLS[0], '#9aa8b5', 1))
t, _ = text_block(0, yt + ROW_H[0] // 2 + 4, [('具身业务层', True)], fs=13, color='#33414e',
                  center=LBL_X + LBL_W // 2, maxw=LBL_W - 10)
S.append(t)
for ci, (px, pw, *_ ) in enumerate(PARTS):
    S.append(box(px + CELL_PAD, yt, pw - 2 * CELL_PAD, ROW_H[0], '#f7fafe', '#a8b4c0', 1))
    y = yt + 8
    for title, lines in BIZ_COLS[ci]:
        y = draw_module(px + CELL_PAD, pw - 2 * CELL_PAD, y, title, lines)
    assert y <= yt + ROW_H[0] + 4, f'业务层模块纵向溢出(列{ci})'

# 其余三层单元格（三列共享行坐标；row_y[0] 为业务行，框架/OS/驱动自 row_y[1] 起）
for ri, (lname, cont) in enumerate(ROWS):
    yt = row_y[ri + 1]; h = ROW_H[ri + 1]
    S.append(box(LBL_X, yt, LBL_W, h, LBL_FILLS[ri + 1], '#9aa8b5', 1))
    t, _ = text_block(0, yt + h // 2 + 4, [(lname, True)], fs=13, color='#33414e',
                      center=LBL_X + LBL_W // 2, maxw=LBL_W - 10)
    S.append(t)
    if ri == 1:
        # OS 层：rk 两列同属一个 Ubuntu 22.04.5 实例 → 外包统一框（落在运控板框内）
        WH = 24
        S.append(box(112, yt, 680, h, 'none', '#5b87b5', 1.8, rx=6))
        t, _ = text_block(0, yt + 16, [('Ubuntu 22.04.5', True)], fs=12,
                          color='#1c3d5f', center=452, maxw=660)
        S.append(t)
        for ci in (0, 1):
            px, pw, *_ = PARTS[ci]
            ih = h - WH - 4
            S.append(box(px + CELL_PAD, yt + WH, pw - 2 * CELL_PAD, ih, FILLS[ri], '#a8b4c0', 1))
            t, endy = text_block(px + CELL_PAD + 10, yt + WH + 17, cont[ci],
                                 fs=12.2, maxw=pw - 2 * CELL_PAD - 18)
            S.append(t)
            assert endy <= yt + WH + ih + 6, f'{lname} 纵向溢出'
        px, pw, *_ = PARTS[2]
        S.append(box(px + CELL_PAD, yt, pw - 2 * CELL_PAD, h, FILLS[ri], '#a8b4c0', 1))
        t, endy = text_block(px + CELL_PAD + 10, yt + (h - 17 * len(cont[2])) // 2 + 14,
                             cont[2], fs=12.2, maxw=pw - 2 * CELL_PAD - 18)
        S.append(t)
        assert endy <= yt + h + 6, f'{lname} 纵向溢出'
        continue
    for ci, (px, pw, *_ ) in enumerate(PARTS):
        S.append(box(px + CELL_PAD, yt, pw - 2 * CELL_PAD, h, FILLS[ri], '#a8b4c0', 1))
        t, endy = text_block(px + CELL_PAD + 10, yt + 19, cont[ci],
                             fs=12.2, maxw=pw - 2 * CELL_PAD - 18)
        S.append(t)
        assert endy <= yt + h + 6, f'{lname} 纵向溢出'

# 硬件层（板框内、部分框外）；rk 侧两格外包「rk3576 8 核」统一框（落在运控板框内）
S.append(box(LBL_X, HW_Y, LBL_W, HW_H, LBL_FILLS[4], '#9aa8b5', 1))
t, _ = text_block(0, HW_Y + HW_H // 2 + 4, [('硬件层', True)], fs=13, color='#33414e',
                  center=LBL_X + LBL_W // 2, maxw=LBL_W - 10)
S.append(t)
WHW = 24
S.append(box(112, HW_Y, 680, HW_H, 'none', '#5b87b5', 1.8, rx=6))
t, _ = text_block(0, HW_Y + 16, [('rk3576 SoC · 8 核 = 4 大核（A72）+ 4 小核（A53）', True)],
                  fs=12, color='#1c3d5f', center=452, maxw=660)
S.append(t)
for px, pw, cont in ((C1_X, C1_W, hw_c1), (C2_X, C2_W, hw_c2)):
    ih = HW_H - WHW - 4
    S.append(box(px + CELL_PAD, HW_Y + WHW, pw - 2 * CELL_PAD, ih, HW_FILL, '#a8b4c0', 1))
    t, endy = text_block(px + CELL_PAD + 10, HW_Y + WHW + 17, cont,
                         fs=12.2, maxw=pw - 2 * CELL_PAD - 18)
    S.append(t)
    assert endy <= HW_Y + WHW + ih + 6, '硬件层纵向溢出'
px, pw = C3_X, C3_W
S.append(box(px + CELL_PAD, HW_Y, pw - 2 * CELL_PAD, HW_H, HW_FILL, '#a8b4c0', 1))
t, endy = text_block(px + CELL_PAD + 10, HW_Y + (HW_H - 17 * len(hw_c3)) // 2 + 14,
                     hw_c3, fs=12.2, maxw=pw - 2 * CELL_PAD - 18)
S.append(t)
assert endy <= HW_Y + HW_H + 6, '硬件层纵向溢出'

# ---------- 工具链纵贯列（开发工具 / 调测工具 / 三方依赖包） ----------
S.append(box(TC_X, BOARD_Y0, TC_W, BOARD_BOT - BOARD_Y0, '#fffdf4', '#c9a24b', 1.2, rx=10))
t, _ = text_block(0, BOARD_Y0 + 22, [('◆ 工具链（纵贯两板 · 版本见 §6.1）', True)], fs=13,
                  color='#7a5c14', center=TC_X + TC_W // 2, maxw=TC_W - 20)
S.append(t)
GAP = 10
gy = BOARD_Y0 + 36

def group_box(x, y, w, h, title, lines, stroke='#d8c48a'):
    S.append(box(x, y, w, h, '#ffffff', stroke, 1, rx=5))
    content_h = 15 + int(16.6 * (1 + len(lines)))
    ty = y + max(14, (h - content_h) // 2 + 15)
    t, _ = text_block(x + 10, ty, [(title, True)] + [(l, False) for l in lines],
                      fs=11.8, lh=16.6, maxw=w - 20)
    S.append(t)

top_boxes = [
 ('◆ 开发工具', ['colcon+CMake 工作空间 · AimRT RPC 代码生成', 'Rust 工具链（orin cargo/rustup）']),
 ('◆ 调测工具', ['ros2 CLI · tegrastats · jetson-stats 7.2.1', 'iox-introspection · ASan · monitor/viz']),
 ('◆ 运维链', ['hds · cloud_proxy · ota · data_exporter', 'drp · abox']),
]
dep_groups = [
 ('多媒体与图像', ['OpenCV 4.5.4/4.8 · GStreamer+rockchip · ffmpeg', 'MPP 1.5.0 · RGA 2.2.0']),
 ('UI 与音频', ['flutter-pi + Flutter · Qt 5.15.3', 'PipeWire 1.6.8（PA 兼容 6001/4713）']),
 ('系统与网络服务', ['systemd · NM · hostapd/dnsmasq · mosquitto', 'ptp4l/chrony · can-utils']),
]
TB_H = 150
wrapper_h = (BOARD_BOT - gy) - TB_H * 2 - GAP * 2
for title, lines in top_boxes:
    group_box(TC_X + 8, gy, TC_W - 16, TB_H, title, lines)
    gy += TB_H + GAP
# 「三方依赖包」大框：把三组依赖包在一起
S.append(box(TC_X + 4, gy, TC_W - 8, wrapper_h, 'none', '#b08e30', 1.6, rx=8))
t, _ = text_block(0, gy + 18, [('◆ 三方依赖包', True)], fs=12.5, color='#7a5c14',
                  center=TC_X + TC_W // 2, maxw=TC_W - 20)
S.append(t)
sy = gy + 30
sub_h = (wrapper_h - 30 - 8 - GAP * (len(dep_groups) - 1)) // len(dep_groups)
for title, lines in dep_groups:
    group_box(TC_X + 12, sy, TC_W - 24, sub_h, title, lines, stroke='#e0cf96')
    sy += sub_h + GAP

# ---------- ①②③通信线（图底部） ----------
c1c, c2c, c3c = C1_X + C1_W // 2, C2_X + C2_W // 2, C3_X + C3_W // 2
AY1, AY2, AY3 = BOARD_BOT + 30, BOARD_BOT + 54, BOARD_BOT + 78
t, _ = text_block(c1c - 185, AY1 - 6, [('① 板内（iceoryx/DDS）：task_engine→★SetMcAction/velocity ↓ · mc/common/state★ ↑', False)], fs=10.8)
S.append(t); S.append(arrow(c1c - 120, AY1, c2c, AY1))
t, _ = text_block(c1c - 185, AY2 - 6, [('② 跨板·任务与感知（FastDDS 域0 · 10.1.1.0/24）：FollowTaskCtrl/★navi 指令 ↓ · follow_candidates★/点云/代价图//odom·/tf ↑', False)], fs=10.8)
S.append(t); S.append(arrow(c1c, AY2, c3c, AY2))
t, _ = text_block(c2c + 40, AY3 - 6, [('③ 跨板·速度直连：orin velocity(pnc/follow/运镜)→MC（SetMcInputSource 仲裁）', False)], fs=10.8)
S.append(t); S.append(arrow(c3c, AY3, c2c, AY3, both=False))
S.append(arrow(TC_X, AY2, C3_X + C3_W, AY2, dash='5,4', sw=1.3))

# ---------- 图例 ----------
t, _ = text_block(LBL_X, BOARD_BOT + 110, [
    ('图例：箭头①②③＝板内/跨板数据流（★为官方接口）；虚线＝工具链注入（运行时库/构建产物/调测工具）；层间自上而下＝上层依赖下层。', False),
    ('业务层模块框序：自上而下＝依赖方向（上＝对外接口/依赖方，下＝被依赖底座与数据源）。', False),
    ('列内链：运控列自上而下＝mc→CAN 驱动→14 执行器（1kHz 实时环）；相机/ToF 原图均板内 iceoryx 消化不跨板：rk 头部相机→Agent 视觉/rtsp·App，orin GMSL 模组→感知融合；ToF 控制面在 rk（exposure）、数据面在 orin。', False)],
    fs=11.2, color='#5b6b7a', lh=16.6)
S.append(t)

S.append('</svg>')
H = BOARD_BOT + 176                                   # 底部通信线 78 + 图例 3 行 + 边距
head = ['<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1560" height="{H}" viewBox="0 0 1560 {H}">',
        f'<rect x="0" y="0" width="1560" height="{H}" fill="#ffffff"/>']
out = '\n'.join(head + S)
open('figures/fig-7-1-layered-arch.svg', 'w', encoding='utf-8').write(out)
try:
    import cairosvg
    cairosvg.svg2png(url='figures/fig-7-1-layered-arch.svg', write_to='figures/fig-7-1-layered-arch.png', scale=2.0)
except Exception as e:
    print('PNG 跳过（无 cairosvg）:', e)
print(f'OK -> figures/fig-7-1-layered-arch.svg  ({len(out)} bytes, 画布 1560×{H}, 板底 {BOARD_BOT}px, 依赖子框高 {sub_h}px')
