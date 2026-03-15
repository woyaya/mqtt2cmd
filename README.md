# MQTT订阅器 - 使用指南

## 项目简介

基于MQTT的命令执行订阅器，支持订阅MQTT主题并根据接收到的payload执行预定义的shell命令。

## 核心特性

- 多MQTT服务器并发支持
- 指数退避自动重连（1/2/4/8/16/32秒）
- String和JSON payload验证
- 顺序和并行命令执行
- 灵活的变量系统（ENV/PAYLOAD/YAML/EXEC）
- 配置继承机制
- 工作目录和环境变量支持
- 全局用户切换（root启动时）
- 并发消息处理（可配置限制）
- 命令超时控制
- 大输出自动截断
- 完善的错误处理和日志记录

## 系统要求

### Python版本
- Python 3.7 或更高版本
- 推荐: Python 3.8+

### 依赖库

| 库名 | 最低版本 | 推荐版本 | 说明 |
|------|---------|---------|------|
| paho-mqtt | 1.6.1 | 2.1.0+ | MQTT客户端库 |
| PyYAML | 6.0 | 6.0.1+ | YAML配置解析 |

### 系统依赖
- MQTT Broker (如Mosquitto)
- mosquitto-clients (用于测试)

## 安装步骤

### 1. 安装Python依赖

```bash
# 使用pip安装
pip3 install paho-mqtt>=1.6.1 PyYAML>=6.0

# 或使用requirements.txt
pip3 install -r requirements.txt
```

### 2. 安装MQTT Broker

#### Ubuntu/Debian
```bash
sudo apt-get update
sudo apt-get install mosquitto mosquitto-clients
sudo systemctl start mosquitto
sudo systemctl enable mosquitto
```

#### macOS
```bash
brew install mosquitto
brew services start mosquitto
```

#### 验证安装
```bash
# 检查Mosquitto状态
systemctl status mosquitto  # Linux
brew services list | grep mosquitto  # macOS

# 测试MQTT连接
mosquitto_pub -h 127.0.0.1 -t test -m "hello"
mosquitto_sub -h 127.0.0.1 -t test
```

### 3. 配置应用

```bash
# 复制示例配置
cp config-example.yaml config.yaml

# 编辑配置文件
nano config.yaml

# 创建密码文件（如果需要）
mkdir -p .kiro/datas
nano .kiro/datas/passwords.yaml
```

## 快速开始

### 启动应用

```bash
# 使用默认配置文件 (./config.yaml)
python3 main.py

# 或指定配置文件
python3 main.py -c /path/to/config.yaml
```

### 发送测试消息

```bash
# String payload
mosquitto_pub -h 127.0.0.1 -u test -P test123 \
  -t "test/command/string" -m "execute_test"

# JSON payload
mosquitto_pub -h 127.0.0.1 -u test -P test123 \
  -t "test/command/json" -m '{"action":"deploy","version":"1.0.0"}'
```

## 变量系统

### 变量类型

应用支持四种变量源，使用统一的语法：

| 变量源 | 语法 | 示例 | 说明 |
|--------|------|------|------|
| YAML配置 | `${YAML:key}` 或 `${key}` | `${app_name}` | 从配置文件读取 |
| Payload | `${PAYLOAD:key}` | `${PAYLOAD:version}` | 从MQTT消息读取 |
| 环境变量 | `${ENV:VAR}` | `${ENV:HOME}` | 从系统环境读取 |
| 执行上下文 | `${EXEC:STDOUT}` | `${EXEC:RESULT}` | 从前一条命令读取（仅sequential模式） |

### 默认值语法

所有变量都支持默认值：

```yaml
commands:
  - 'echo "Version: ${PAYLOAD:version:-1.0.0}"'
  - 'echo "User: ${ENV:USER:-nobody}"'
  - 'echo "App: ${app_name:-myapp}"'
```

### 变量示例

#### 1. YAML变量

```yaml
global:
  variables:
    app_name: my-application
    deploy_user: deployer

mqtt_servers:
  server1:
    subscriptions:
      deploy/app:
        handlers:
          - commands:
              - 'echo "Deploying ${app_name}"'  # 默认YAML变量
              - 'echo "By: ${YAML:deploy_user}"'  # 显式YAML变量
```

#### 2. Payload变量

```yaml
handlers:
  - payload_type: json
    commands:
      # 访问JSON字段
      - 'echo "Version: ${PAYLOAD:version}"'
      # 访问嵌套字段
      - 'echo "DB: ${PAYLOAD:config.database.host}"'
      # 引用整个payload
      - 'logger "${PAYLOAD:PAYLOAD}"'
```

测试:
```bash
mosquitto_pub -t "deploy/app" -m '{
  "version": "2.0.0",
  "config": {
    "database": {
      "host": "db.example.com"
    }
  }
}'
```

#### 3. 环境变量

```yaml
handlers:
  - commands:
      - 'echo "Home: ${ENV:HOME}"'
      - 'echo "User: ${ENV:USER:-unknown}"'
```

#### 4. EXEC变量（执行上下文）

在sequential模式下，可以引用前一条命令的执行结果：

```yaml
handlers:
  - payload_type: string
    commands:
      - 'echo "Hello World"'
      - 'echo "Previous output: ${EXEC:STDOUT}"'
      - 'echo "Previous result: ${EXEC:RESULT}"'
      - 'date +%Y-%m-%d'
      - 'echo "Today is ${EXEC:STDOUT}"'
    execution_mode: sequential
```

EXEC变量说明：
- `${EXEC:STDOUT}` - 前一条命令的标准输出
- `${EXEC:STDERR}` - 前一条命令的标准错误
- `${EXEC:OUTPUT}` - 前一条命令的组合输出（stdout + stderr）
- `${EXEC:RESULT}` - 前一条命令的退出码（0表示成功）

重要规则：
- 仅在sequential模式下可用
- 第一条命令的EXEC值为空（RESULT=0）
- 输出大小限制为max_exec_output_size（默认5MB），超出会截断
- 在parallel模式下使用EXEC变量必须提供默认值，否则报错

parallel模式示例：
```yaml
handlers:
  - commands:
      - 'echo "Task 1: ${EXEC:STDOUT:-no_previous}"'  # 必须有默认值
      - 'echo "Task 2: ${EXEC:RESULT:-0}"'
    execution_mode: parallel
```

#### 5. 混合使用

```yaml
handlers:
  - payload_type: json
    commands:
      - 'deploy.sh --app ${app_name} --version ${PAYLOAD:version} --user ${ENV:USER}'
      - 'echo "Deploy result: ${EXEC:RESULT}"'
      - 'echo "Deploy output: ${EXEC:STDOUT}"'
    execution_mode: sequential
    env_vars:
      APP_NAME: '${app_name}'
      VERSION: '${PAYLOAD:version}'
      DEPLOY_USER: '${ENV:USER:-system}'
```

### 特殊字符处理

Payload和环境变量会自动转义，防止命令注入：

```yaml
# Payload: {"message": "hello; rm -rf /"}
commands:
  - 'echo ${PAYLOAD:message}'
# 实际执行: echo 'hello; rm -rf /'  (安全转义)
```

## 配置继承

配置支持三层继承，优先级从高到低：

1. Handler级别配置（最高优先级）
2. Server级别配置
3. Global级别配置（最低优先级）

### 可继承的配置项

- `username` - MQTT用户名
- `password` - MQTT密码
- `client_id` - 客户端ID
- `keepalive` - 保活间隔
- `use_tls` - TLS加密
- `execution_mode` - 执行模式
- `ignore_errors` - 错误处理
- `working_dir` - 工作目录
- `env_vars` - 环境变量

### 继承示例

```yaml
global:
  execution_mode: sequential  # 全局默认
  ignore_errors: false
  working_dir: /opt/app

mqtt_servers:
  server1:
    execution_mode: parallel  # 覆盖全局设置
    
    subscriptions:
      topic1:
        handlers:
          - commands: [...]
            # 使用server1的parallel模式
          
          - commands: [...]
            execution_mode: sequential  # 覆盖server设置
            # 使用sequential模式
```

## 工作目录和环境变量

### 工作目录

指定命令执行的目录：

```yaml
handlers:
  - commands:
      - 'ls -la'
      - 'pwd'
    working_dir: /opt/myapp
    # 命令将在/opt/myapp目录执行
```

支持变量：
```yaml
working_dir: '/opt/${PAYLOAD:app_name}'
```

### 环境变量

为命令添加环境变量：

```yaml
handlers:
  - commands:
      - 'echo $APP_ENV'
      - 'echo $VERSION'
    env_vars:
      APP_ENV: production
      VERSION: '${PAYLOAD:version}'
      LOG_LEVEL: info
```

### 以指定用户运行命令

当以root权限运行应用时，可以在启动时切换到指定用户，所有后续命令都以该用户身份执行：

```yaml
global:
  # 全局用户切换（仅root启动时有效）
  run_as_user: appuser

mqtt_servers:
  server1:
    subscriptions:
      deploy/app:
        handlers:
          - commands:
              - 'whoami'  # 将显示 'appuser'
              - '/opt/app/deploy.sh'
            working_dir: /opt/app
```

**重要说明：**
- **启动时切换**: 如果以root启动且配置了run_as_user，应用会在启动时切换到指定用户
- **非Root限制**: 如果以非root用户启动，且run_as_user配置的用户与当前用户不一致，应用会退出并报错
- **用户验证**: 配置的用户必须在系统中存在
- **安全优势**: 避免以高权限运行命令，降低安全风险；避免每次命令都sudo，提升性能
- **全局配置**: run_as_user仅支持在global部分配置，不支持在handler级别配置

**使用场景：**
```bash
# 场景1: 以root启动，切换到appuser
sudo python3 main.py  # 配置: run_as_user: appuser
# 结果: 所有命令以appuser身份执行

# 场景2: 以appuser启动，配置匹配
python3 main.py  # 配置: run_as_user: appuser
# 结果: 正常运行，所有命令以appuser身份执行

# 场景3: 以webuser启动，配置不匹配
python3 main.py  # 配置: run_as_user: appuser
# 结果: 应用退出并报错

# 场景4: 未配置run_as_user
python3 main.py  # 配置: 无run_as_user
# 结果: 命令以当前用户身份执行
```
## 自动重连机制

When the MQTT server disconnects, the system automatically reconnects using an exponential backoff strategy:

- 1st attempt: Reconnect after 1 second
- 2nd attempt: Reconnect after 2 seconds
- 3rd attempt: Reconnect after 4 seconds
- 4th attempt: Reconnect after 8 seconds
- 5th attempt: Reconnect after 16 seconds
- 6th attempt and beyond: Reconnect after 32 seconds (default maximum)

The maximum reconnection delay can be configured:

```yaml
global:
  max_reconnect_delay: 60  # Maximum delay in seconds (default: 60)

# Or per-server configuration
mqtt_servers:
  server1:
    max_reconnect_delay: 30  # Override global setting
```

After a successful connection, the reconnection interval resets to 1 second.

## 并发消息处理

应用支持并发处理多个MQTT消息，避免消息处理阻塞：

### 工作原理

- 每个接收到的消息在独立的线程中处理
- 使用信号量限制最大并发数，防止资源耗尽
- 当达到并发限制时，新消息会等待直到有空闲槽位
- 线程按需创建，处理完成后自动销毁

### 配置

```yaml
global:
  # 最大并发处理器数量（默认: 20）
  max_concurrent_handlers: 20
```

### 使用场景

适合以下场景：
- 命令执行时间较长（如部署、备份）
- 多个消息同时到达
- 需要避免消息处理相互阻塞

示例：
```bash
# 同时发送5条消息
for i in {1..5}; do
  mosquitto_pub -t "test/concurrent" -m "message_$i" &
done
```

所有5条消息会并发处理，互不阻塞。

## 命令超时控制

可以为命令执行设置超时时间，防止命令无限期运行：

```yaml
global:
  # 命令超时时间（秒），0表示禁用（默认: 0）
  command_timeout: 30
```

- 如果命令运行时间超过超时值，会被强制终止
- 设置为0表示禁用超时
- 超时的命令会记录ERROR日志

## 大输出处理

为防止命令产生过大输出导致内存问题，系统会自动截断：

```yaml
global:
  # EXEC输出最大大小，支持K/M单位（默认: 5M）
  max_exec_output_size: 5M    # 5MB
  # 或使用其他单位
  # max_exec_output_size: 1024K  # 1MB
  # max_exec_output_size: 10M    # 10MB
  # max_exec_output_size: 5242880  # 直接指定字节数
```

- 当命令输出超过此大小时，会被截断并添加[truncated]标记
- 会记录WARNING日志
- 仅影响EXEC变量，不影响命令实际执行
- 支持K（KB）、M（MB）单位或直接使用字节数

## 多服务器支持

支持同时连接多个MQTT服务器，每个服务器独立运行：

```yaml
mqtt_servers:
  server1:
    host: 192.168.1.100
    subscriptions: [...]
  
  server2:
    host: 192.168.1.101
    subscriptions: [...]
```

特性：
- 独立的连接管理
- 独立的重连机制
- 独立的topic订阅
- 共享日志系统

## 配置说明

详细配置请参考 `config-example.yaml`，其中包含所有配置项的详细说明和示例。

### 从v3.x迁移到v4.0

v4.0版本引入了重大架构改进，需要更新配置：

#### 1. run_as_user配置迁移

v3.x（handler级别配置）：
```yaml
handlers:
  - commands: [...]
    run_as_user: appuser  # 旧方式：每个handler配置
```

v4.0（全局配置）：
```yaml
global:
  run_as_user: appuser  # 新方式：全局配置，启动时切换

handlers:
  - commands: [...]
    # 不再支持handler级别的run_as_user
```

迁移步骤：
1. 将所有handler中的run_as_user移除
2. 在global部分添加统一的run_as_user配置
3. 如果不同handler需要不同用户，需要为每个用户启动独立的应用实例

#### 2. 新增全局配置项

在global部分添加以下可选配置：

```yaml
global:
  max_concurrent_handlers: 20    # 并发处理限制
  max_exec_output_size: 5M       # EXEC输出大小限制（支持K/M单位）
  command_timeout: 0             # 命令超时（0=禁用）
```

#### 3. EXEC变量使用

v4.0新增EXEC变量，可在sequential模式下引用前一条命令的输出：

```yaml
handlers:
  - commands:
      - 'date +%Y-%m-%d'
      - 'echo "Today is ${EXEC:STDOUT}"'
    execution_mode: sequential
```

在parallel模式下使用EXEC变量必须提供默认值：

```yaml
handlers:
  - commands:
      - 'echo "Result: ${EXEC:RESULT:-0}"'
    execution_mode: parallel
```

### 最小配置

```yaml
global:
  log_level: INFO
  log_file: logs/app.log
  log_retention_days: 7

mqtt_servers:
  localhost:
    host: 127.0.0.1
    port: 1883
    username: test
    password: test123
    
    subscriptions:
      test/topic:
        qos: 0
        handlers:
          - payload_type: string
            payload: "hello"
            commands:
              - 'echo "Hello World"'
```

### 完整配置示例

参见 `config-example.yaml` 文件，包含：
- 全局默认配置
- 多服务器配置
- 变量使用示例
- 配置继承示例
- 所有可选参数说明

## 版本信息

### 当前版本
- 应用版本: 4.0.0
- Python要求: 3.7+
- 最后更新: 2026-03-11

### 版本历史

#### v4.0.0 (2026-03-11)
- 新增EXEC变量系统（STDOUT/STDERR/OUTPUT/RESULT）
- 全局run_as_user配置，启动时切换用户
- 并发消息处理，支持可配置的并发限制
- 命令超时控制
- 大输出自动截断
- 性能优化：消除所有sudo调用开销
- 安全增强：避免以高权限运行命令

#### v3.0.0 (2026-03-08)
- 新增统一变量系统（ENV/PAYLOAD/YAML）
- 支持配置继承机制
- 支持工作目录和环境变量配置
- 支持 ${PAYLOAD:PAYLOAD} 引用完整payload
- 全局默认配置支持

#### v2.0.0 (2026-03-08)
- 指数退避重连机制
- 多服务器并发支持
- 默认配置文件改为./config.yaml
- 密码文件配置化

#### v1.0.0 (2026-03-08)
- 基本MQTT订阅功能
- String/JSON payload验证
- 顺序/并行命令执行
- 错误处理机制

## 依赖版本说明

### Python版本兼容性

| Python版本 | 支持状态 | 说明 |
|-----------|---------|------|
| 3.7 | ✅ 支持 | 最低要求版本 |
| 3.8 | ✅ 支持 | 推荐版本 |
| 3.9 | ✅ 支持 | 推荐版本 |
| 3.10 | ✅ 支持 | 推荐版本 |
| 3.11+ | ✅ 支持 | 最新版本 |

### 依赖库版本

#### paho-mqtt

| 版本 | 状态 | 说明 |
|------|------|------|
| < 1.6.1 | ❌ 不支持 | 功能不完整 |
| 1.6.1 | ✅ 支持 | 最低要求 |
| 2.0.0+ | ✅ 推荐 | 新版本，性能更好 |
| 2.1.0 | ✅ 推荐 | 最新稳定版 |

安装命令：
```bash
# 安装最低版本
pip3 install paho-mqtt==1.6.1

# 安装推荐版本
pip3 install paho-mqtt>=2.0.0

# 安装最新版本
pip3 install paho-mqtt --upgrade
```

#### PyYAML

| 版本 | 状态 | 说明 |
|------|------|------|
| < 6.0 | ❌ 不支持 | 安全漏洞 |
| 6.0 | ✅ 支持 | 最低要求 |
| 6.0.1+ | ✅ 推荐 | 修复了已知问题 |

安装命令：
```bash
# 安装最低版本
pip3 install PyYAML==6.0

# 安装推荐版本
pip3 install PyYAML>=6.0.1

# 安装最新版本
pip3 install PyYAML --upgrade
```

### 验证安装

```bash
# 检查Python版本
python3 --version

# 检查已安装的包
pip3 list | grep paho-mqtt
pip3 list | grep PyYAML

# 验证导入
python3 -c "import paho.mqtt.client; print('paho-mqtt:', paho.mqtt.client.__version__)"
python3 -c "import yaml; print('PyYAML: OK')"
```

### 订阅配置

```yaml
subscriptions:
  topic/name:               # MQTT主题
    qos: 1                  # QoS级别: 0, 1, 2
    handlers:               # 处理器列表
      - payload_type: string        # payload类型: string 或 json
        payload: "expected_value"   # 期望的payload内容
        commands:                   # 要执行的命令列表
          - echo "Command 1"
          - echo "Command 2"
        execution_mode: sequential  # 执行模式: sequential 或 parallel
        ignore_errors: false        # 是否忽略错误（仅sequential模式）
```

## Payload类型

### String类型

精确匹配字符串：

```yaml
payload_type: string
payload: "exact_match_string"
```

### JSON类型

匹配JSON结构（支持嵌套）：

```yaml
payload_type: json
payload:
  key1: value1
  key2:
    nested_key: nested_value
```

## 命令执行模式

### Sequential（顺序执行）

命令按顺序依次执行：

```yaml
execution_mode: sequential
ignore_errors: false  # false: 遇到错误停止; true: 忽略错误继续
```

### Parallel（并行执行）

命令同时并行执行：

```yaml
execution_mode: parallel
# ignore_errors在parallel模式下不适用
```

## 密码管理

### 密码文件格式

```yaml
# passwords.yaml
passwords:
  key1: password1
  key2: password2
```

### 在主配置中引用

```yaml
global:
  password_file: passwords.yaml  # 指定密码文件路径

mqtt_servers:
  server1:
    password: ${key1}  # 引用passwords.yaml中的key1
```

## 测试场景

### 场景1: 简单字符串触发

```bash
# 配置
payload_type: string
payload: "deploy"

# 发送
mosquitto_pub -h 127.0.0.1 -u test -P test123 -t "app/deploy" -m "deploy"
```

### 场景2: JSON触发

```bash
# 配置
payload_type: json
payload:
  action: restart
  service: nginx

# 发送
mosquitto_pub -h 127.0.0.1 -u test -P test123 \
  -t "app/control" -m '{"action":"restart","service":"nginx"}'
```

### 场景3: 多个处理器

```yaml
# 同一个topic可以有多个处理器，根据不同payload执行不同命令
handlers:
  - payload_type: string
    payload: "start"
    commands: ["systemctl start myapp"]
  
  - payload_type: string
    payload: "stop"
    commands: ["systemctl stop myapp"]
```

### 场景4: 多服务器配置

```yaml
mqtt_servers:
  server1:
    host: 192.168.1.100
    port: 1883
    username: user1
    password: ${pass1}
    subscriptions:
      topic1:
        qos: 1
        handlers: [...]
  
  server2:
    host: 192.168.1.101
    port: 1883
    username: user2
    password: ${pass2}
    subscriptions:
      topic2:
        qos: 1
        handlers: [...]
```

两个服务器会同时运行，互不影响。

## 日志查看

```bash
# 实时查看日志
tail -f logs/mqtt_subscriber.log

# 查看最近50行
tail -50 logs/mqtt_subscriber.log

# 搜索错误
grep ERROR logs/mqtt_subscriber.log
```

## 故障排查

### 连接失败

```
ERROR - Failed to connect to MQTT broker
```

解决方案：
1. 检查MQTT broker是否运行: `systemctl status mosquitto`
2. 验证host和port配置
3. 检查用户名密码是否正确
4. 系统会自动重连，观察重连日志

### 自动重连日志

```
INFO - [server_name] Reconnecting in 1 seconds...
INFO - [server_name] Attempting to reconnect to 127.0.0.1:1883
INFO - [server_name] Reconnecting in 2 seconds...
INFO - [server_name] Reconnecting in 4 seconds...
...
INFO - [server_name] Successfully connected to MQTT broker
```

Reconnection intervals: 1 → 2 → 4 → 8 → 16 → 32 → 60 seconds (capped at configured maximum, default 60s)

### 认证失败

```
ERROR - Failed to connect: Connection refused - bad username or password
```

解决方案：
1. 验证username和password配置
2. 检查密码文件引用是否正确
3. 确认MQTT broker的用户配置

### Payload验证失败

```
WARNING - Payload validation failed for topic 'xxx'
```

解决方案：
1. 检查payload是否完全匹配（区分大小写）
2. JSON格式是否正确
3. JSON结构是否完全匹配（包括嵌套）

### 命令执行失败

```
ERROR - Command failed with code X: command
```

解决方案：
1. 检查命令语法是否正确
2. 验证命令权限
3. 查看错误输出了解具体原因
4. 考虑使用ignore_errors=true继续执行

## 高级用法

### 配置层级覆盖

子配置会覆盖父配置中的相同项：

```yaml
global:
  log_level: INFO  # 全局日志级别

mqtt_servers:
  server1:
    # 可以在这里覆盖log_level（如果实现了此功能）
```

### 命令超时

默认命令超时为300秒（5分钟），在payload_handler.py中可修改：

```python
timeout=300  # 修改此值调整超时时间
```

### 通配符订阅

MQTT支持通配符订阅（标准MQTT功能）：

```yaml
subscriptions:
  "sensor/+/temperature":  # + 匹配单层
    qos: 0
  
  "device/#":              # # 匹配多层
    qos: 1
```

## 生产环境建议

1. 使用INFO或WARNING日志级别（避免DEBUG产生过多日志）
2. 配置适当的log_retention_days
3. 使用TLS加密连接（use_tls: true）
4. 设置合理的keepalive值
5. 使用QoS 1或2确保消息可靠传递
6. 定期监控日志文件大小
7. 为关键命令设置ignore_errors: false
8. 测试所有命令的执行权限

## 示例应用场景

### 场景1: CI/CD部署触发

```yaml
subscriptions:
  ci/deploy/production:
    qos: 2
    handlers:
      - payload_type: json
        payload:
          project: myapp
          version: "1.0.0"
        commands:
          - git pull origin main
          - docker-compose down
          - docker-compose up -d
        execution_mode: sequential
        ignore_errors: false
```

### 场景2: 系统监控告警

```yaml
subscriptions:
  monitor/alert:
    qos: 1
    handlers:
      - payload_type: string
        payload: "high_cpu"
        commands:
          - ps aux --sort=-%cpu | head -10
          - echo "High CPU alert triggered"
        execution_mode: sequential
        ignore_errors: true
```

### 场景3: 定时任务触发

```yaml
subscriptions:
  cron/backup:
    qos: 1
    handlers:
      - payload_type: string
        payload: "daily_backup"
        commands:
          - tar -czf backup.tar.gz /data
          - scp backup.tar.gz user@backup-server:/backups/
        execution_mode: sequential
        ignore_errors: false
```

## 性能优化

1. 使用合适的QoS级别（QoS 0最快但可能丢消息）
2. 并行执行独立命令可提高效率
3. 避免在命令中使用长时间阻塞操作
4. 合理设置keepalive避免频繁重连

## 安全注意事项

1. 密码文件权限应设置为600: `chmod 600 .kiro/datas/passwords.yaml`
2. 避免在日志中记录敏感信息
3. 使用TLS加密MQTT连接
4. 限制命令执行权限
5. 验证所有输入payload
6. 定期更新依赖库

## 维护

### 查看日志

```bash
# 查看当前日志
cat logs/mqtt_subscriber.log

# 查看历史日志
ls -lh logs/
```

### 清理旧日志

日志会根据log_retention_days自动清理，也可手动清理：

```bash
find logs/ -name "mqtt_subscriber.log.*" -mtime +7 -delete
```

### 重启服务

```bash
# 停止当前运行的订阅器（Ctrl+C）
# 然后重新启动
python3 main.py -c .kiro/datas/config.yaml -p .kiro/datas/passwords.yaml
```

## 常见问题

Q: 如何添加新的topic订阅？
A: 在配置文件的subscriptions部分添加新的topic配置，重启应用即可。

Q: 如何修改命令执行超时时间？
A: 编辑payload_handler.py中的timeout参数（默认300秒）。

Q: 支持多少个MQTT服务器？
A: 无限制。所有配置的服务器会并发运行，每个服务器独立管理连接和订阅。

Q: 如何调试payload不匹配问题？
A: 设置log_level为DEBUG，查看接收到的实际payload内容。

Q: 命令执行失败怎么办？
A: 检查日志中的错误输出，验证命令语法和权限，考虑使用ignore_errors。

Q: 连接断开后会自动重连吗？
A: 是的。系统会自动重连，重连间隔采用指数退避策略(1/2/4/8/16/32/60秒...)，最大间隔可通过 `max_reconnect_delay` 配置(默认60秒)。

Q: 如何指定密码文件？
A: 在配置文件的global部分设置password_file路径。
