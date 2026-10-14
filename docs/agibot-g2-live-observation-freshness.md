# G2 live 观测滞后与 Setup 卡住问题复盘

本文记录 2026-10-13 对 G2 live 推理链路的诊断、真机实验、根因和修复。目标是让每个 16 步策略 chunk 使用当时最新的相机与关节状态，并让网页刷新不会打断 REAL Setup。

## 现象

长时间运行时，机器人会在每个新 chunk 开始时往旧位姿拉。一次 1000 帧测试实际记录了 877 个动作帧和 54 个 chunk 边界；执行期间没有再次调用机器人回位，也没有再次 reset 策略缓存，但 54 个边界全部朝同一个旧输入 state 回拉。

另一个现象是网页长期停在 `AUTO · PREPARING ROBOT`。2026-10-13 23:01 的日志显示任务和 REAL 模式已经选中，随后浏览器的新 client 会话在 23:07 自动发送了 `select_mode sim`，使正在执行的 REAL reset trajectory 被中断。策略隧道尚未完成密码认证时也会表现为 `policy DOWN`。

## 实验方法

### 1. 还原旧观测队列

G2 硬件节点每次发布一条完整快照，包含三路相机和全部 24 维关节状态。旧的策略 reader 只在推理前读取一次，而反馈 reader 在每个动作步都会读取。策略 reader 因此持续积压旧消息。

在同一个真实 G2 publisher 上同时使用旧队列 reader 和只保留最新快照的 reader，每隔 1 秒读取一次并比较快照时间戳。原始数据在 [g2-live-refresh-before-fix.json](../logs/g2-live-refresh-before-fix.json)。

| 读取点 | 旧队列帧龄 | 最新快照帧龄 |
|---|---:|---:|
| 初始 | 0.278 s | 0.053 s |
| 1 秒后 | 1.263 s | 0.096 s |
| 2 秒后 | 2.099 s | 0.061 s |
| 3 秒后 | 2.785 s | 0.045 s |

### 2. 验证策略输入

修复后直接使用真实 G2 transport 组装 StarVLA observation，不调用策略服务、不发布动作。四次读取均包含三路 `224×224 RGB uint8` 图像和 `float32[24]` state。输入帧最大帧龄为 0.285 秒，state 与独立实时反馈最大误差为 0.00142 rad。原始结果在 [g2-live-refresh-after-fix.json](../logs/g2-live-refresh-after-fix.json)。

### 3. 真实机器人闭环执行

使用正常配置的 reset 位姿完成 REAL Setup，然后通过 EVA 的 REAL/ZMQ/GDK 链路执行 32 个动作帧，即两个完整的 16 步 chunk。测试只把 `max_run_frames` 临时缩短到 32，其他控制路径与正式 live 配置一致。

结果：

- Setup 仅执行一次，发布 100 个平滑回位点；回位后的手臂最大误差为 0.00485 rad。
- 策略输入发生在 step 0 和 step 16，帧龄分别为 0.148 秒和 0.121 秒。
- 两次输入的右臂关节变化最大为 0.3754 rad，证明第二个 chunk 使用了运动后的 state。
- 策略输入与同步关节反馈最大误差为 0.00830 rad。
- 32 个动作全部达到同步阈值，0 次超时；单步目标与下发前实测位姿最大差为 0.0796 rad，低于 0.3 rad 限幅。
- 第一个边界的右臂 joint 1 从上一块末尾目标 `-1.5104` 连续到下一块首目标 `-1.4546`，没有朝旧位姿回拉。

完整结果在 [g2-live-real-test-20261013_224754.result.json](../logs/g2-live-real-test-20261013_224754.result.json)，逐帧日志在 [g2-live-real-test-20261013_224754.log](../logs/g2-live-real-test-20261013_224754.log)。旧故障的 54 个边界统计在 [g2-eva-reset-analysis-20261013_204715.json](../logs/g2-eva-reset-analysis-20261013_204715.json)。

## 根因

### live state 不更新

EVA 为策略推理、关节反馈、采集和网页预览分别建立独立的 ZMQ SUB reader。G2 以约 30 Hz 发布完整快照，策略推理每执行完 16 步才消费一次，因此策略 reader 的队列消费速度低于生产速度。有限批量 drain 只能减慢积压，无法追上实时流。同步反馈 reader 消费频繁，所以日志中的实际位姿在更新，而发给策略服务器的 state 仍停留在旧快照。

StarVLA 输出绝对关节目标。每次新 chunk 都基于旧 state 规划时，首个动作会重新接近旧位姿，看起来像 EVA 在中途执行 reset；实际上运行期间没有机器人 reset。

### Setup 卡在准备中

网页启动顺序原来是先 `renderConfig()`，后读取 `/api/status`。当本地状态还为空时，`renderConfig()` 会选默认 SIM 并向后端发送 `select_mode sim`。刷新一个正在进行 REAL Setup 的页面时，这条命令会中断真实回位。

此外，SSH 会在密码认证完成前占住本地转发端口。因此看到 `127.0.0.1:11094` 正在 LISTEN，或 `nc` 能连接本地端口，并不能证明隧道已经认证成功。终端仍停在 `password:`、出现 `Permission denied` 或 `Connection closed` 时，EVA 仍会收到 `Connection refused`。

## 解决方案

### ZMQ 只保留最新完整快照

通用默认配置增加 `conflate_observations=False`，仅 G2 配置开启 `True`。G2 每条消息都是完整快照，因此策略、关节反馈和网页 reader 可以使用 ZMQ `CONFLATE` 丢弃未消费的旧消息。采集 reader 保持非 conflate，继续按顺序保存每一帧，采集数据不会因此丢帧。

实现位置：

- [defaults.py](../configs/00_base/defaults.py)：通用安全默认值。
- [G2 _base.py](../configs/01_deploy/agibot_g2/_base.py)：G2 完整快照开启最新帧模式。
- [zmq.py](../src/transport/zmq.py)：为策略、反馈和辅助 reader 应用该配置，采集 reader 保留 backlog。

### 页面先恢复后端状态

网页 boot 现在先读取 `/api/status`，再渲染模式默认值。已处于 REAL 时刷新页面会保持 REAL，不再发送 SIM。首个 status 请求失败时也不会猜测模式。

控制类 POST 增加 10 秒超时。Setup 的确认丢失时停止显示无限期的“准备中”，显示错误并允许人工重试；由于请求是否已经到达机器人存在不确定性，客户端不会自动重发 Setup。

实现位置：

- [main.js](../src/core/app/console/static/js/main.js)：先读取后端状态，再渲染配置。
- [core.js](../src/core/app/console/static/js/core.js)：控制请求超时并释放串行请求队列。
- [run.js](../src/core/app/console/static/js/run.js)：显示 Setup 请求错误和 RETRY。

## 验证

相关数据、采集、网页和 live reader 回归共通过 136 项：网页生命周期与静态检查 111 项、数据与采集检查 24 项、真实 ZMQ socket 最新快照检查 1 项。JavaScript 语法、Ruff 和 `git diff --check` 也通过。

浏览器生命周期测试覆盖：刷新时保留 REAL、初始 status 不可用时不自动选 SIM、Setup 确认超时后释放 POST 队列并显示错误。ZMQ socket 测试覆盖：reader 闲置且解码变慢时仍取最新完整快照、发送给策略的 state/三路图像来自同一最新帧、采集 reader 仍保留原始顺序。

## 标准启动与判断信号

先启动硬件节点。已运行时不要重复启动：

```bash
cd /data/cym_ws/EVA-CLIENT
bash tools/local_g2/run_hardware.sh
```

在单独终端建立到 H20 策略服务的隧道。`-f` 会在认证和本地转发建立成功后返回 shell，保活参数会让断链退出：

```bash
ssh -fNT \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 127.0.0.1:11094:192.168.68.15:11094 \
  root@115.190.193.44
```

当前服务的真实握手应返回 `protocol=agibot_g2.v1`、`action_chunk_size=16` 和 step-20000 checkpoint。最后启动 EVA；live 模式不加 `--execute`：

```bash
cd /data/cym_ws/EVA-CLIENT
.venv/bin/eva \
  --config configs/01_deploy/agibot_g2/agibot_g2_v1_h20_live.py \
  --web-port 8080
```

首次应用网页修复时按 `Ctrl+F5` 强制刷新。选择 REAL 后，Setup 正常阶段应依次显示连接策略、回到初始位姿和验证推理，最后状态变为 READY。正式配置每个 chunk 执行 16 步，最多执行 1000 个动作帧，单步关节限幅为 0.3 rad。
