#!/usr/bin/env python3
# 解析 T1 Pro 附录C 第二轮采样数据（c2_*）→ 各阶段 资源占用表
import re, sys
from collections import defaultdict

D = '/home/liqiliang/workspace/lql/projects/myrobot/appendix-c-data/'

PHASES = [
    ("BASE", "14:27:53", "14:29:35"),
    ("UC1",  "14:29:35", "14:31:36"),
    ("UC2a", "14:31:36", "14:33:35"),
    ("UC2b", "14:33:35", "14:34:54"),
    ("UC3",  "14:34:54", "14:40:20"),
]

def sec(t):
    h, m, s = t.split(":")
    return int(h)*3600 + int(m)*60 + float(s)

PS = [(sec(a), sec(b), n) for n, a, b in PHASES]

def in_phase(t):
    for a, b, n in PS:
        if a <= t < b:
            return n
    return None

# ---------- pid -> app（cwd 优先，args 兜底） ----------
PKG_ALIAS = {
    'target_follow_perception': 'person_track', 'perception': 'percep',
    'path_follow_node': 'path_follow', 'follow': 'follow',
    'brightness_detection': 'brightness',
}

def load_ps(path, cwdfile):
    cwd = {}
    for line in open(cwdfile, errors='replace'):
        f = line.split(None, 1)
        if len(f) == 2 and f[0].isdigit():
            cwd[int(f[0])] = f[1].strip()
    app = {}
    for line in open(path, errors='replace'):
        parts = line.split(None, 5)
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        pid, args = int(parts[0]), parts[5]
        # 排除采样器自身、包装进程（sudo/em_run.sh/启动脚本）与系统杂项
        if re.search(r'top -b|tegrastats|ros2 topic echo|ps -eo|analyze_c2|systemd-cgls|readlink /proc', args):
            app[pid] = None; continue
        if re.search(r'sudo -u|em_run\.sh|start_\w+\.sh|/bin/bash|bash /robot', args) and 'flutter' not in args:
            app[pid] = None; continue
        # -1) 特例：app_proxy 以 python -m 启动、cwd 继承 PM
        if 'local_app_proxy' in args:
            app[pid] = 'app_proxy'; continue
        # 0) ros2 launch 与组件容器（cwd 继承自 PM，会误判为 process_manager）
        if 'component_container' in args:
            app[pid] = 'nav2_stack(容器)'; continue
        if 'ros2 launch' in args:
            for k, v in [('nav2_bringup', 'nav2_stack'), ('follow', 'follow'),
                         ('path_follow_node', 'path_follow'), ('percep', 'percep')]:
                if k in args:
                    app[pid] = v; break
            else:
                app[pid] = 'ros2_launch(其他)'
            continue
        # 1) args 内含绝对路径的（ros2 launch 子节点等）优先按 args 判定
        m = re.search(r'/robot/software/algorithm/([A-Za-z0-9_]+)/', args)
        if m:
            n = m.group(1)
            app[pid] = PKG_ALIAS.get(n, n)
            continue
        m = re.search(r'/robot/software/([A-Za-z0-9_]+)/', args)
        if m and m.group(1) != 'software':
            app[pid] = m.group(1)
            continue
        # 2) 相对路径启动的 aimrt_main 类，按 cwd 判定
        w = cwd.get(pid, '')
        m = re.match(r'^/robot/software/([A-Za-z0-9_]+)(/bin)?$', w) or \
            re.match(r'^/robot/software/algorithm/([A-Za-z0-9_]+)/bin$', w)
        if m:
            n = m.group(1)
            app[pid] = PKG_ALIAS.get(n, n)
            continue
        for k, v in [('flutter-pi', 'display_ui'), ('mosquitto', 'mosquitto(conduit)'),
                     ('hostapd', 'hostapd'), ('dnsmasq', 'dnsmasq'),
                     ('/robot/software/entry/run_roudi', 'iox-roudi'), ('iox-roudi -c', 'iox-roudi'),
                     ('tetherd', 'tetherd'), ('pipewire', 'pipewire_audio'), ('wireplumber', 'pipewire_audio'),
                     ('NetworkManager', 'NetworkManager'), ('ptp4l', 'ptp4l'), ('chronyd', 'chronyd'),
                     ('component_container', 'nav2_stack(容器)'), ('follow_node', 'follow'),
                     ('trajectory_tracker', 'path_follow'), ('lifecycle_manager', 'nav2_lifecycle'),
                     ('perception_node', 'nav2_perception'), ('pointcloud_combiner', 'nav2_perception'),
                     ('nav2', 'nav2_stack'), ('start_real_navigation', 'nav2_stack'),
                     ('nav2_bringup', 'nav2_stack'), ('follow.launch', 'follow'),
                     ('path_follow_node trajectory', 'path_follow'), ('nav2_lifecycle_manager', 'nav2_perception'),
                     ('quanji.yaml', 'quanji'), ('local_app_proxy', 'app_proxy'),
                     ('abox', 'abox'), ('secret_store', 'secret_store'),
                     ('kworker', None), ('sshd', None), ('systemd', None), ('[', None),
                     ('housekeeper', 'housekeeper'), ('lumina', 'lumina'), ('drp_main', 'drp')]:
            if k in args:
                app[pid] = v
                break
        else:
            app[pid] = None
    return app

# ---------- top 解析 ----------
def parse_res(v):
    v = v.strip()
    if v.endswith('g'): return float(v[:-1]) * 1024 * 1024
    if v.endswith('m'): return float(v[:-1]) * 1024
    return float(v)

def parse_top(path, appmap):
    proc = defaultdict(lambda: defaultdict(list))  # app->phase->[(cpu)]
    rss = defaultdict(dict)  # app->pid->res
    blocks = []  # (t, us, sy, id, mem_used, mem_total)
    t = None
    for line in open(path, errors='replace'):
        m = re.match(r'^top - (\d\d:\d\d:\d\d)', line)
        if m:
            t = sec(m.group(1)); continue
        if t is None: continue
        m = re.match(r'^%Cpu\(s\): ([\d.]+) us,\s*([\d.]+) sy.*?([\d.]+) id', line)
        if m:
            blocks.append([t] + [float(x) for x in m.groups()]); continue
        m = re.match(r'^MiB Mem :\s*([\d.]+) total,\s*([\d.]+) free,\s*([\d.]+) used', line)
        if m:
            if blocks and len(blocks[-1]) == 4:
                blocks[-1] += [float(m.group(3)), float(m.group(1))]
            continue
        f = line.split()
        if len(f) >= 11 and f[0].isdigit():
            try:
                pid = int(f[0]); cpu = float(f[8]); resv = f[5]
            except ValueError:
                continue
            a = appmap.get(pid)
            if a is None: continue
            ph = in_phase(t)
            if ph is None: continue
            proc[a][ph].append(cpu)
            rss[a][pid] = parse_res(resv) / 1024.0  # MB
    return proc, rss, blocks

def mean(x):
    return sum(x) / len(x) if x else 0.0

def report(board, psf, topf, cwdfile):
    appmap = load_ps(psf, cwdfile)
    proc, rss, blocks = parse_top(topf, appmap)
    rows = []
    for a in sorted(proc.keys(), key=lambda x: -mean([c for v in proc[x].values() for c in v])):
        r = [a]
        for _, _, n in PS:
            r.append(round(mean(proc[a].get(n, [])), 1))
        r.append(round(sum(rss[a].values()), 0))
        rows.append(r)
    hdr = ["app"] + [n for _, _, n in PS] + ["RSS_MB"]
    print(f"\n===== {board} per-app CPU% (均值) / RSS =====")
    print("\t".join(hdr))
    for r in rows[:34]:
        print("\t".join(str(x) for x in r))
    # 板级
    print(f"===== {board} board-level =====")
    for _, _, n in PS:
        bs = [b for b in blocks if in_phase(b[0]) == n]
        if not bs: continue
        us = mean([b[1] for b in bs]); sy = mean([b[2] for b in bs]); idl = mean([b[3] for b in bs])
        mu = mean([b[4] for b in bs]); mt = mean([b[5] for b in bs])
        print(f"{n}: CPU us={us:.1f}% sy={sy:.1f}% busy={100-idl:.1f}%  RAM {mu:.0f}/{mt:.0f}MiB")
    return rows

RK_SLICES = {
    'robot_realtime (mc+device_manager)': ['mc', 'device_manager'],
    'robot_interaction (agent+display+audio+task_engine)': ['aimrt_agent', 'display_ui', 'audio_manager', 'task_engine'],
    'robot_auxiliary (data_exporter)': ['data_exporter'],
}
def slices_report(board, psf, topf, cwdfile, slices):
    appmap = load_ps(psf, cwdfile)
    proc, rss, blocks = parse_top(topf, appmap)
    print(f"\n===== {board} 子系统(slice) CPU% 汇总 =====")
    named = set()
    for _, v in slices.items():
        named.update(v)
    for sn, members in slices.items():
        vals = []
        for _, _, n in PS:
            vals.append(round(sum(mean(proc[m].get(n, [])) for m in members), 1))
        rssm = round(sum(sum(rss[m].values()) for m in members), 0)
        print(sn, "\t", "\t".join(str(x) for x in vals), f"\tRSS={rssm}MB")
    vals = []
    others = set(proc.keys()) - named
    for _, _, n in PS:
        vals.append(round(sum(mean(proc[m].get(n, [])) for m in others), 1))
    print("robot_system+其他 (其余全部业务+基座+系统服务)", "\t", "\t".join(str(x) for x in vals))

report("RK", D+'c2_rk_ps.txt', D+'c2_rk_top.log', D+'c2_rk_cwd.txt')
slices_report("RK", D+'c2_rk_ps.txt', D+'c2_rk_top.log', D+'c2_rk_cwd.txt', RK_SLICES)
report("ORIN", D+'c2_orin_ps.txt', D+'c2_orin_top.log', D+'c2_orin_cwd.txt')

# ---------- rk sys (GPU/NPU/MEM) ----------
print("\n===== RK GPU/NPU/MEM =====")
acc = defaultdict(lambda: defaultdict(list))
for line in open(D+'c2_rk_sys.log', errors='replace'):
    m = re.match(r'^@@ (\d\d:\d\d:\d\d) GPU=(\d+)@\d+Hz NPU=NPU load:\s+Core0:\s+(\d+)%, Core1:\s+(\d+)%', line)
    if not m: continue
    t, g, n0, n1 = sec(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    ph = in_phase(t)
    if ph:
        acc[ph]['gpu'].append(g); acc[ph]['npu0'].append(n0); acc[ph]['npu1'].append(n1)
    mm = re.search(r'MEMbytes=(\d+) (\d+)', line)
    if mm and ph:
        acc[ph]['mem'].append(int(mm.group(2)) / 1048576)
for _, _, n in PS:
    if acc[n]:
        print(f"{n}: GPU={mean(acc[n]['gpu']):.0f}%  NPU c0={mean(acc[n]['npu0']):.0f}% c1={mean(acc[n]['npu1']):.0f}%  memused={mean(acc[n]['mem']):.0f}MiB")

# ---------- orin tegrastats ----------
print("\n===== ORIN tegrastats (GR3D/RAM/CPU/功耗) =====")
acc2 = defaultdict(lambda: defaultdict(list))
for line in open(D+'c2_orin_tegra.log', errors='replace'):
    m = re.match(r'^\d\d-\d\d-\d+ (\d\d:\d\d:\d\d) RAM (\d+)/(\d+)MB.*?CPU \[([\d%@,]+)\] GR3D_FREQ (\d+)%.*?VDD_IN (\d+)mW', line)
    if not m: continue
    t = sec(m.group(1))
    ph = in_phase(t)
    if not ph: continue
    cpus = [int(x.split('@')[0].strip('%')) for x in m.group(4).split(',')]
    acc2[ph]['gr3d'].append(int(m.group(5)))
    acc2[ph]['ram'].append(int(m.group(2)))
    acc2[ph]['cpu'].append(sum(cpus) / len(cpus))
    acc2[ph]['pw'].append(int(m.group(6)))
for _, _, n in PS:
    if acc2[n]:
        print(f"{n}: GR3D={mean(acc2[n]['gr3d']):.0f}%  RAM={mean(acc2[n]['ram']):.0f}MiB  CPU均={mean(acc2[n]['cpu']):.0f}%  P={mean(acc2[n]['pw'])/1000:.1f}W")
