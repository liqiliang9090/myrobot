#!/usr/bin/env python3
# 解析 c3 低开销离散快照基线（每 burst 取 top -n2 的第 2 帧=真实 5s 瞬时值）
import re
from collections import defaultdict

D = '/home/liqiliang/workspace/lql/projects/myrobot/appendix-c-data/'

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
        if re.search(r'top -b|tegrastats|ros2 topic echo|ps -eo|analyze_c|systemd-cgls|readlink /proc|seq 1 12|free -b', args):
            app[pid] = None; continue
        if re.search(r'sudo -u|em_run\.sh|start_\w+\.sh|/bin/bash|bash /robot', args) and 'flutter' not in args:
            app[pid] = None; continue
        if 'local_app_proxy' in args:
            app[pid] = 'app_proxy'; continue
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
        m = re.search(r'/robot/software/algorithm/([A-Za-z0-9_]+)/', args)
        if m:
            n = m.group(1); app[pid] = PKG_ALIAS.get(n, n); continue
        m = re.search(r'/robot/software/([A-Za-z0-9_]+)/', args)
        if m and m.group(1) != 'software':
            app[pid] = m.group(1); continue
        w = cwd.get(pid, '')
        m = re.match(r'^/robot/software/([A-Za-z0-9_]+)(/bin)?$', w) or \
            re.match(r'^/robot/software/algorithm/([A-Za-z0-9_]+)/bin$', w)
        if m:
            n = m.group(1); app[pid] = PKG_ALIAS.get(n, n); continue
        for k, v in [('flutter-pi', 'display_ui'), ('mosquitto', 'mosquitto(conduit)'),
                     ('hostapd', 'hostapd'), ('dnsmasq', 'dnsmasq'),
                     ('/robot/software/entry/run_roudi', 'iox-roudi'), ('iox-roudi -c', 'iox-roudi'),
                     ('tetherd', 'tetherd'), ('pipewire', 'pipewire_audio'), ('wireplumber', 'pipewire_audio'),
                     ('NetworkManager', 'NetworkManager'), ('ptp4l', 'ptp4l'), ('chronyd', 'chronyd'),
                     ('follow_node', 'follow'), ('trajectory_tracker', 'path_follow'),
                     ('lifecycle_manager', 'nav2_perception'), ('perception_node', 'nav2_perception'),
                     ('pointcloud_combiner', 'nav2_perception'), ('nav2', 'nav2_stack'),
                     ('quanji.yaml', 'quanji'), ('abox', 'abox'), ('secret_store', 'secret_store'),
                     ('kworker', None), ('sshd', None), ('systemd', None), ('[', None),
                     ('housekeeper', 'housekeeper'), ('lumina', 'lumina'), ('drp_main', 'drp')]:
            if k in args:
                app[pid] = v; break
        else:
            app[pid] = None
    return app

def parse_res(v):
    v = v.strip()
    if v.endswith('g'): return float(v[:-1]) * 1024 * 1024
    if v.endswith('m'): return float(v[:-1]) * 1024
    return float(v)

def mean(x):
    return sum(x) / len(x) if x else 0.0

RK_SLICES = {
    'realtime': ['mc', 'device_manager'],
    'interaction': ['aimrt_agent', 'display_ui', 'audio_manager', 'task_engine'],
    'auxiliary': ['data_exporter'],
}

def analyze(board, psf, cwdf, topf):
    appmap = load_ps(psf, cwdf)
    proc = defaultdict(list); rss = defaultdict(dict)
    blocks = []  # (burst, us, sy, idl, mem_used, mem_total)
    burst = None; blk = 0; keep = False
    for line in open(topf, errors='replace'):
        if line.startswith('@BURST'):
            burst = line.split()[1]; blk = 0; keep = False; continue
        m = re.match(r'^top - \d\d:\d\d:\d\d', line)
        if m:
            blk += 1; keep = (blk == 2); continue
        if not keep or burst is None: continue
        m = re.match(r'^%Cpu\(s\): ([\d.]+) us,\s*([\d.]+) sy.*?([\d.]+) id', line)
        if m:
            blocks.append([burst] + [float(x) for x in m.groups()]); continue
        m = re.match(r'^MiB Mem :\s*([\d.]+) total,\s*[\d.]+ free,\s*([\d.]+) used', line)
        if m and blocks:
            blocks[-1] += [float(m.group(2)), float(m.group(1))]; continue
        f = line.split()
        if len(f) >= 11 and f[0].isdigit():
            try:
                pid = int(f[0]); cpu = float(f[8])
            except ValueError:
                continue
            a = appmap.get(pid)
            if a is None: continue
            proc[a].append(cpu)
            rss[a][pid] = parse_res(f[5]) / 1024.0
    print(f"\n===== {board} BASE（离散快照 12 轮，取第 2 帧）=====")
    rows = []
    for a in sorted(proc.keys(), key=lambda x: -mean(proc[x])):
        rows.append((a, round(mean(proc[a]), 1), round(sum(rss[a].values()), 0)))
    for a, c, r in rows[:34]:
        print(f"{a}\t{c}\t{r}")
    print(f"-- 板级: busy={100-mean([b[3] for b in blocks]):.1f}%  us={mean([b[1] for b in blocks]):.1f} sy={mean([b[2] for b in blocks]):.1f}  RAM {mean([b[4] for b in blocks]):.0f}/{mean([b[5] for b in blocks]):.0f}MiB")
    # slices
    named = set()
    for v in RK_SLICES.values(): named.update(v)
    for sn, members in RK_SLICES.items():
        print(f"slice {sn}: {sum(mean(proc[m]) for m in members if m in proc):.1f}")
    others = set(proc.keys()) - named
    print(f"slice system+其他: {sum(mean(proc[m]) for m in others):.1f}")
    return proc, rows

analyze("RK", D+'c3_rk_ps.txt', D+'c3_rk_cwd.txt', '/tmp/c3_rk_base.log')
analyze("ORIN", D+'c3_orin_ps.txt', D+'c3_orin_cwd.txt', '/tmp/c3_orin_base.log')

# rk GPU/NPU/MEM
print("\n===== RK GPU/NPU/MEM =====")
g, n0, n1, mem = [], [], [], []
for line in open('/tmp/c3_rk_base.log', errors='replace'):
    m = re.match(r'^@GPU (\d+)@', line)
    if m: g.append(int(m.group(1)))
    m = re.match(r'^@NPU NPU load:\s+Core0:\s+(\d+)%, Core1:\s+(\d+)%', line)
    if m: n0.append(int(m.group(1))); n1.append(int(m.group(2)))
    m = re.match(r'^@MEM (\d+) (\d+)', line)
    if m: mem.append(int(m.group(2)) / 1048576)
print(f"GPU={mean(g):.0f}%  NPU c0={mean(n0):.0f}% c1={mean(n1):.0f}%  memused={mean(mem):.0f}MiB  (n={len(g)})")

# orin tegrastats
print("\n===== ORIN tegrastats =====")
gr, ram, cpu, pw = [], [], [], []
for line in open('/tmp/c3_orin_base.log', errors='replace'):
    m = re.match(r'^@TEG \S+ \S+ RAM (\d+)/(\d+)MB.*?CPU \[([\d%@,]+)\] GR3D_FREQ (\d+)%.*?VDD_IN (\d+)mW', line)
    if not m: continue
    ram.append(int(m.group(1)))
    cpus = [int(x.split('@')[0].strip('%')) for x in m.group(3).split(',')]
    cpu.append(sum(cpus)/len(cpus)); gr.append(int(m.group(4))); pw.append(int(m.group(5)))
print(f"GR3D={mean(gr):.0f}%  RAM={mean(ram):.0f}MiB  CPU均={mean(cpu):.0f}%  P={mean(pw)/1000:.1f}W  (n={len(gr)})")
