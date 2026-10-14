[← 返回 README](../README.md) · [快速启动](quick-start.md)

# G2 手动控制与 VR 遥操作

本文记录当前 G2 主机 `/data/cym_ws/EVA-CLIENT` 的实际配置。EVA、VR 输入节点和硬件节点均运行在 G2 上；头显通过 USB 连接 G2。电脑使用 VS Code Remote - SSH 查看网页。

## 环境与端口

| 组件 | 本机配置 |
|---|---|
| EVA / VR 输入节点 | 项目 `.venv`，Python 3.11 |
| G2 硬件节点 | `examples/hardware/agibot_g2/.venv`，Python 3.10 |
| GDK | `/home/agi/app/gdk`；现有原生模块仅支持 Python 3.10 |
| 观测 / 动作 ZMQ | `127.0.0.1:5555` / `127.0.0.1:5556` |
| EVA 网页 | `http://127.0.0.1:8080` |
| 头显 WebXR 页面 | `http://127.0.0.1:43876`，包含每次启动生成的 token |
| VR 输入 / 事件 ACK | `127.0.0.1:8875` / `127.0.0.1:8876` |
| VR 采集目录 | `/data/collection/agibot_g2` |

本机 `8765/8766` 被其他服务占用，因此 VR 节点和 EVA 必须同时使用 `8875/8876`。手动控制和 VR 遥操作均不需要策略服务器。

`tools/local_g2/` 启动脚本和 `*.local.py` 是被 Git 忽略的本机文件。当前机器已有这些文件；新克隆需要自行恢复。下文提供等价启动命令和本地配置内容。

## 共同准备：硬件节点与网页访问

所有启动命令都在 **G2 的远程终端**执行。先检查已有进程，避免重复启动或端口冲突：

```bash
cd /data/cym_ws/EVA-CLIENT
pgrep -af '[/]agibot_g2/node.py|[/]vr_webxr/node.py|[/]bin/eva'
```

硬件节点已经运行时保留它；没有运行时，在终端 1 启动：

```bash
bash tools/local_g2/run_hardware.sh
```

脚本加载 GDK 环境、选择 `10.42.1.*` 网卡作为 `LOCATOR_IP`，并使用独立 Python 3.10 环境。可通过 `G2_LOCATOR_IP` 覆盖地址。等价命令如下：

```bash
cd /data/cym_ws/EVA-CLIENT
source /home/agi/app/gdk/scripts/env.sh /home/agi/app
export LOCATOR_IP="$(ip -o -4 addr list | awk '$4 ~ /^10[.]42[.]1[.]/ {split($4,a,"/"); print a[1]; exit}')"
export PYTHONPATH="$PWD/src:$PWD:${PYTHONPATH:-}"
mkdir -p logs
cd logs
../examples/hardware/agibot_g2/.venv/bin/python ../examples/hardware/agibot_g2/node.py
```

成功日志包含 `GDK initialized successfully` 和 `Entering 100.0Hz servo control loop`。节点以当前关节姿态初始化目标，然后等待 EVA 指令。

电脑的 VS Code 使用 Remote - SSH 连接 G2、打开项目，在 **Ports / 端口**转发 `8080`。打开该行浏览器按钮，或在本地端口为 8080 时访问 `http://localhost:8080`。头显直接接 G2 时不需要通过电脑转发 `43876`。

## MANUAL 手动控制

在终端 2 启动 EVA：

```bash
cd /data/cym_ws/EVA-CLIENT
.venv/bin/eva --config configs/01_deploy/agibot_g2/openpi_qpos.py --web-port 8080
# 本机快捷命令：bash tools/local_g2/run_eva.sh
```

1. 打开网页，进入 **02 MANUAL**。确认相机有画面、顶部 `TX zmq` 在线。
2. 点击 **CONNECT REAL**；成功信号为 **REAL ROBOT · LIVE**。
3. 调整关节滑块，先查看目标姿态预览。
4. 点击 **SEND TO REAL** 开始发送目标。发送期间按钮显示 **STOP**，滑块变化会继续更新目标。
5. 点击 **STOP** 停止发送；点击 **DISCONNECT** 退出网页的实体连接状态。

MANUAL 的 HOME 用于复位手动预览目标；将该目标发送到实体机器人会产生运动。网页 STOP 结束 EVA 的动作发布，硬件节点仍会保持最后的目标，不等同于硬件急停。

## 从 MANUAL 切换到 VR

先停止手动发送；如正在采集，先结束并等待保存完成。切换时只停止 EVA，保留硬件节点。

优先在 EVA 所在终端按 Ctrl-C。若 EVA 在后台，可停止当前项目的 EVA 进程：

```bash
pkill -TERM -f '^/data/cym_ws/EVA-CLIENT/.venv/bin/python /data/cym_ws/EVA-CLIENT/.venv/bin/eva '
```

## VR 遥操作启动

### 1. 确认采集目录权限

```bash
sudo mkdir -p /data/collection/agibot_g2
sudo chown "$(id -u):$(id -g)" /data/collection/agibot_g2
test -w /data/collection/agibot_g2 && echo '采集目录可写'
```

目录由 `configs/02_collection/agibot_g2_vr.py` 的 `collection.storage.log_dir` 指定。路径变更需要重启 EVA；旧数据不会自动迁移。

### 2. 启动 VR 输入节点

在终端 2 执行：

```bash
cd /data/cym_ws/EVA-CLIENT
bash examples/input_sources/vr_webxr/run_node.sh \
  --endpoint tcp://127.0.0.1:8875 \
  --ack-endpoint tcp://127.0.0.1:8876
# 本机快捷命令：bash tools/local_g2/run_vr.sh
```

保持运行，记录 `WebXR node ready` 后的 URL 和 token。每次重新启动节点，token 都可能变化。

### 3. 启动 EVA VR 配置

确认 `configs/02_collection/agibot_g2_vr.local.py` 内容如下；该文件被 Git 忽略：

```python
_base_ = ["agibot_g2_vr.py"]

collection = dict(
    teleop=dict(
        client=dict(
            endpoint="tcp://127.0.0.1:8875",
            ack_endpoint="tcp://127.0.0.1:8876",
        ),
    ),
)
```

在终端 3 执行：

```bash
cd /data/cym_ws/EVA-CLIENT
.venv/bin/eva --config configs/02_collection/agibot_g2_vr.local.py --web-port 8080
# 本机快捷命令：bash tools/local_g2/run_eva_vr.sh
```

首次 IK 编译需要等待。电脑强制刷新网页（Ctrl + Shift + R），进入 **03 COLLECT**。

### 4. 在头显打开 WebXR

头显开启开发者模式和 USB 调试，通过 USB 连接 **G2**，并在头显里允许调试。在 G2 的另一个终端执行：

```bash
cd /data/cym_ws/EVA-CLIENT
adb devices -l
export VR_TOKEN='本次VR节点日志中的实际token'
VR_MODE=vr bash examples/input_sources/vr_webxr/open_pico.sh
```

`adb devices` 中设备应显示 `device`，而非 `unauthorized`。多个设备时给脚本追加设备序列号。

脚本建立 `adb reverse tcp:43876 tcp:43876`，并在头显打开 localhost 页面。点击 **ENTER VR** 进入会话。若浏览器支持 AR/MR，可改用 `VR_MODE=ar`，再点击 **ENTER MR**；出现 `The specified session configuration is not supported` 时先使用 VR 模式。

成功信号：头显状态为 **STREAMING**，EVA COLLECT 显示 **VR LINKED**。页面仅显示 READY 时，不能据此认定已进入 XR 或正在发送手柄位姿。直接通过 G2 的局域网 IP 访问头显页面需要 HTTPS/WSS；USB localhost 路径无需此配置。

### 5. 开启手臂跟随

保持手柄稳定，按下面顺序操作：

| 控件 | 操作 | 成功信号 / 行为 |
|---|---|---|
| 右手 B | 短按并松开，少于 1 秒 | 全局 MOTION 切换到 ENABLED |
| 左 / 右手侧握键 | 长按约 1 秒，再松开 | 对应 ARM 从 DISABLED 变为 READY / ACTIVE |
| 手柄位姿 | 小幅移动和旋转 | 已授权的对应机械臂跟随相对位姿 |
| 食指扳机 | 按压 | 对应夹爪开合 |
| 右手 B | 再次短按并松开 | MOTION 关闭，清除双手累计偏移和参考位姿 |
| 侧握键 | 再次长按约 1 秒 | 关闭对应手臂授权 |

侧握键是手柄侧面、中指按的键，不是食指扳机。全局 MOTION 和对应手臂授权都打开才会跟随；只有 MOTION ENABLED、两臂仍为 DISABLED 时不动是正常的。关闭再开启 MOTION 后，需要重新授权对应手臂。

当前 G2 VR 配置控制双臂和夹爪；头部、躯干保持参考姿态。手柄震动取决于浏览器支持，不能只靠震动判断是否成功，以网页状态和反馈为准。

## 用 episode 2 做策略推理并下发到 G2

`configs/01_deploy/agibot_g2/agibot_g2_v1_dataset_ep2.py` 将指定 LeRobot 数据集的观测帧送入 EVA 的策略预处理和推理接口，同时保留 G2 实时 ZMQ 作为动作下发通道。先启动 G2 硬件节点、推理服务和到服务端 `10093` 的 SSH 隧道；本地 `127.0.0.1:11093` 可连通后，在终端启动：

```bash
cd /data/cym_ws/EVA-CLIENT
.venv/bin/eva --config configs/01_deploy/agibot_g2/agibot_g2_v1_dataset_ep2.py --web-port 8080 --execute
```

不带 `--execute` 启动时，REAL/STEP 的数据集策略运行会被拦截；这避免离线验证意外驱动机器人。带上参数后，在网页选择 **REAL**，完成 **SETUP** 后再点击 **RUN**。动作经 EVA 的 `OutputTarget.REAL` 硬件发布链路发送，经过夹爪处理和每关节 0.3 限幅。

episode 2 有 357 个 30Hz 帧；配置以 10Hz 控制，每个推理块只执行 8 步，然后用第 9 个 10Hz 观测启动下一次推理。也就是每次输入后跨 24 个原始数据帧取下一次输入，最多下发 14 个完整块、112 条动作。由于 G2 服务端一次推理会推进 16 步 rolling cache，EVA 会在每个后续数据观测前发送 reset，避免缓存沿用未执行的 8 步。基配置的 300 帧上限仍生效，但该 episode 的数据长度会先把本次运行上限收紧到 112。输入给模型的是录制数据里的相机和关节状态，机器人执行不会用 G2 当前相机画面闭环。

运行停止、达到帧数上限或 EVA 退出时，会在 `work_dirs/.../policy_observation_reports/` 保存 PNG 和 JSON。曲线按数据集动作标签对齐，右臂各关节和夹爪面板右上角显示各自 MSE，整张图右上角显示预测动作总体 MSE；JSON 另外记录限幅后实际下发命令的总体及逐关节 MSE。

## 用 G2 实时观测进行推理并控制

`agibot_g2_v1_h20_live.py` 使用 G2 当前相机帧和关节状态作为模型输入，推理结果通过 EVA 的 REAL 硬件链路下发。该配置连接本机 `127.0.0.1:11094`；先保持 G2 硬件节点和下方的 H20 SSH 隧道运行，再启动：

```bash
cd /data/cym_ws/EVA-CLIENT
.venv/bin/eva --config configs/01_deploy/agibot_g2/agibot_g2_v1_h20_live.py --web-port 8080
```

网页选择 **REAL**，确认三路相机、G2 transport 和 policy 均已连接，完成 **SETUP** 后点击 **RUN**。该配置每次推理使用实时观测，单次运行上限为 1000 个动作帧；它不读取 LeRobot episode 数据。这里不要加 `--execute`：该参数专用于数据集观测模式，实时真机模式直接由网页的 REAL/RUN 控制。

如果刷新网页后 Setup 被打断、页面长期停在 PREPARING，或新 chunk 朝旧位姿回拉，请看 [G2 live 观测滞后与 Setup 卡住问题复盘](agibot-g2-live-observation-freshness.md)。该文档包含真实 G2 实验数据、根因、修复和隧道成功判断方式。

### H20 推理服务

H20 的推理服务地址是 `192.168.68.15:11094`，SSH 公网入口是 `root@115.190.193.44:22`。在 G2 上用一个终端保持 SSH 隧道运行，将本机 `127.0.0.1:11094` 转发到 H20 的服务端口：

```bash
ssh -NT -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:11094:192.168.68.15:11094 \
  root@115.190.193.44
```

隧道建立后，在 G2 另一个终端确认本地端口可连通：

```bash
nc -vz 127.0.0.1 11094
```

保持隧道终端运行，并确认 G2 硬件节点正在运行；若它尚未运行，再启动（已运行时不要重复启动）。H20 专用配置继承 episode 2 数据源及原有 10Hz、8 步控制设置，只将策略端点改为隧道地址：

```bash
cd /data/cym_ws/EVA-CLIENT
bash tools/local_g2/run_hardware.sh
```

在另一个终端启动 EVA 控制台：

```bash
cd /data/cym_ws/EVA-CLIENT
.venv/bin/eva --config configs/01_deploy/agibot_g2/agibot_g2_v1_dataset_ep2_h20.py --web-port 8080 --execute
```

网页选择 **REAL**，确认策略连接和 G2 状态正常，完成 **SETUP** 后再手动点击 **RUN**。如果隧道认证失败，先在 G2 安装/授权用于登录 H20 的 SSH key；本机当前 `H20` SSH 别名解析为 `agi@h20`，与上面的 `root@115.190.193.44` 不同，因此这里使用完整 SSH 目标。不要把 `11094` 直接开放到公网；隧道应在整个推理运行期间保持连接。

### 6. 录制与保存

先在 COLLECT 选择数据集和任务，再使用手柄或网页按钮：

| 控件 | 操作 | 行为 |
|---|---|---|
| 右手 A | 短按并松开 | 开始录制；再次短按结束并保存 |
| 右手 A | 长按约 1 秒 | 取消当前录制 |
| 左手 X | 短按并松开 | 执行采集回位，会产生实体运动 |

不开录制也可以遥操作。保存目录为 `/data/collection/agibot_g2` 下的数据集目录；观察 CONVERT QUEUE 的保存进度，完成后再退出。结束录制不会自动替代关闭 MOTION。

## 停止与排查

退出前先关闭 MOTION，结束或取消录制，等待保存队列完成。再在 EVA 和 VR 节点终端按 Ctrl-C；需要停止硬件节点时，在其终端按 Ctrl-C 释放 GDK。

| 现象 | 检查方式 |
|---|---|
| `Address already in use` | `ss -ltnp` 检查监听；已有节点无需重复启动。本机必须配对使用 8875/8876 |
| 顶部状态不更新、相机 AWAITING FRAME | 检查硬件节点、EVA 日志及 5555 观测流；强制刷新网页 |
| VR 已连接但机械臂不动 | 确认 MOTION ENABLED 和对应 ARM READY / ACTIVE，检查 `teleop.last_fault` |
| 动作断续 | 分别检查 VR RX 的 rate/interval、EVA 的 published_actions 增量、输入超时及 IK 耗时 |
| 保存失败 | 检查 `/data` 写权限、保存队列错误、`df -h / /data` |

EVA 动作发布配置为 30 Hz，G2 伺服配置为 100 Hz；实际发送频率受输入和计算耗时影响。网页右上角 image Hz 是相机频率。100 Hz 伺服可重复执行同一目标，不代表 EVA 每秒发送 100 个新目标。

本机脚本后台运行时日志可见 `logs/g2-hardware.log`、`logs/g2-eva.log`、`logs/g2-vr.log`；手动启动时以对应终端输出为准。更多输入协议说明见 [WebXR VR 节点 README](../examples/input_sources/vr_webxr/README.md)。
