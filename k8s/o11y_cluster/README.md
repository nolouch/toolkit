# TiDB 日志收集系统测试部署指南

这份文档旨在指导测试人员在自己的 Kubernetes 环境中部署和测试 TiDB 日志收集系统。

## 📋 目录

- [环境准备](#环境准备)
- [配置参数](#配置参数)
- [部署流程](#部署流程)
- [功能验证](#功能验证)
- [故障排查](#故障排查)
- [测试数据生成](#测试数据生成)
- [清理环境](#清理环境)

---

## 环境准备

### 1. 获取 Kubernetes 集群

推荐通过 TCMS (TiDB Cloud Management System) 创建测试用的 Kubernetes 集群。

**步骤:**
1. 登录 TCMS 平台创建集群，确保状态为 `Running`。
2. 下载集群的 `kubeconfig` 文件到本地。
3. 设置环境变量指向该配置文件：

```bash
# 请替换为实际的文件路径
export KUBECONFIG=~/Downloads/kubeconfig.yml

# 验证连接
kubectl get pod
kubectl get nodes
```

### 2. 准备项目代码

确保你已经获取了部署相关的代码文件，并进入部署目录：

```bash
# 假设代码已下载到本地
git clone https://github.com/nolouch/toolkit.git
cd toolkit/k8s/011y_cluster
```

### 3. 确认 TiDB 集群信息

在部署日志收集组件前，请确认当前 kubectl 上下文已指向正确的 Namespace，并获取 TiDB 集群名称。

```bash
# 查看当前 Namespace
kubectl config view --minify | grep namespace

# 查看当前 Namespace 下的 TiDB 集群
kubectl get tc

# 记录下集群名称
# 示例输出:
# NAME   READY   ...
# tc     True    ...
```

---

## 配置参数

本指南中使用以下占位符，请在执行命令时替换为你的实际值，或者提前导出为环境变量：

| 参数 | 说明 | 示例值 |
|------|------|--------|
| `CLUSTER_NAME` | TiDB 集群名称 | `tc` |
| `MINIO_USER` | MinIO 用户名 | `minioadmin` |
| `MINIO_PASS` | MinIO 密码 | `minioadmin` |

**建议设置环境变量以便后续复制命令:**

```bash
# === 请根据你的实际环境修改以下值 ===
export CLUSTER_NAME=<你的集群名称>
# ======================================
```

---

## 部署流程

### 1. 部署 MinIO 存储

MinIO 用于存储收集到的各类日志文件。

```bash
# 部署 MinIO
kubectl apply -f 01-minio.yaml

# 等待 MinIO 就绪
kubectl wait --for=condition=available deployment/minio --timeout=120s

# 验证
kubectl get pods -l app=minio
```

### 2. 部署 Vector Sidecar 配置

```bash
# 部署 ConfigMap
kubectl apply -f 04-vector-sidecar-config.yaml
```

### 3. Patch TiDBCluster (核心步骤)

这一步将向 TiDB 集群注入 Vector Sidecar 容器，用于采集日志。

**为什么需要 Sidecar?**
这里不使用通用的日志采集器 (`03-vector-logs.yaml`)，是因为我们需要采集 **Statement Summary** 和 **结构化慢日志**。这些数据由 TiDB 写入本地文件系统 (`/var/log/auditlog/`)，不会输出到标准输出 (Stdout)。只有通过 Sidecar 模式挂载同一卷才能获取这些关键诊断数据。

**注意**: 执行此步骤会触发 TiDB 组件滚动重启。

```bash
# 使用 kubectl patch 命令
kubectl patch tc $CLUSTER_NAME --type=merge --patch-file=05-tidbcluster-patch.yaml

# 观察滚动重启进度 (可能需要 3-5 分钟)
kubectl get pods -l app.kubernetes.io/component=tidb -w
```

**验证重启完成**:
等待所有 TiDB Pod 状态变为 `Running`，且 `READY` 包含 4 个容器（例如 `4/4`）。

```bash
# 检查某个 TiDB Pod 的容器列表
POD_NAME=$(kubectl get pods -l app.kubernetes.io/component=tidb -o jsonpath='{.items[0].metadata.name}')
kubectl get pod $POD_NAME -o jsonpath='{.spec.containers[*].name}' | tr ' ' '\n'
```
*预期输出应包含: `tidb`, `slowlog`, `statementlog`, `vector`*

### 4. 部署 Diagnosis Query 服务

**作用说明**:
这是一个**无状态查询引擎**，基于 **DuckDB** 构建。
*   **计算存储分离**: 它不存储数据，而是直接通过 S3 API 读取 MinIO 中的 JSON 或 Parquet 文件进行现场计算。
*   **统一接口**: 对外提供 HTTP API (`/api/v1/statements/...`)，屏蔽了底层的存储格式差异。

```bash
# 部署服务
kubectl apply -f 06-diagnosis-query.yaml

# 等待就绪
kubectl wait --for=condition=available deployment/diagnosis-query --timeout=120s
```

### 5. 部署 Delta Lake Converter

**作用说明**:
原始日志以 JSON 格式存储，虽然易读但查询性能较差。Delta Lake Converter 是一个**ETL 作业**，它定期将 MinIO 中的 JSON 日志转换为 **Delta Lake (Parquet)** 格式。
*   **性能提升**: 列式存储 (Parquet) 让 DuckDB 的聚合查询速度提升 10-100 倍。
*   **数据治理**: 支持 ACID 事务和数据版本控制。

如果你需要测试高性能查询或长期存储分析：

```bash
kubectl apply -f 07-delta-converter.yaml
```

### 6. 部署 Statement 前端页面

**作用说明**:
这是 Statement 分析功能的可视化**前端界面** (UI)。
*   **功能**: 提供 SQL 列表、详情、执行计划等图形化展示。
*   **架构**: 包含 Nginx 反向代理，将 API 请求自动转发给后端的 **Diagnosis Query** 服务。
*   **注意**: 当前版本仅打包了 **Statement** 相关的页面模块。

```bash
kubectl apply -f 08-statement-ui-dev.yaml

# 等待就绪
kubectl wait --for=condition=available deployment/statement-ui-dev --timeout=120s
```

---

## 功能验证

### 验证 1: 访问 MinIO 控制台

由于是在 K8s 内部署，通常需要通过端口转发来访问 Web 界面。

```bash
# 开启端口转发 (保持终端运行，或在后台运行)
kubectl port-forward svc/minio 9001:9001 9000:9000
```

1. 打开浏览器访问: [http://localhost:9001](http://localhost:9001)
2. 登录账号: `minioadmin` / `minioadmin`
3. 检查 Bucket: 确认存在名为 `tidb-logs` 的 bucket。

### 验证 2: 检查日志生成与上传

Vector Sidecar 配置为每 60 秒或满 10MB 上传一次日志。

1. **生成一些流量**: 连接数据库执行几条 SQL (参考[测试数据生成](#测试数据生成)章节)。
2. **等待上传**: 等待约 1-2 分钟。
3. **检查文件**:
   - 在 MinIO Web 控制台查看 `tidb-logs` bucket。
   - 路径结构应为: `statement/<pod_name>/<year>/<month>/<day>/<hour>/...`

### 验证 3: Diagnosis Query API 测试

```bash
# 为 API 服务开启端口转发
kubectl port-forward svc/diagnosis-query 8081:8081
```

**测试 API 连接:**
```bash
# 获取 Statement 列表
curl "http://localhost:8081/api/v1/statements/list?limit=5"
```

如果返回了 JSON 格式的数据列表，说明整个链路（TiDB -> Sidecar -> MinIO -> Diagnosis Query）工作正常。

### 验证 4: Statement 前端访问

```bash
# 为前端开启端口转发
kubectl port-forward svc/statement-ui-dev 8080:80
```

1.  打开浏览器访问: [http://localhost:8080](http://localhost:8080)
2.  **预期结果**: 应该能看到 Statement Dashboard 页面，且数据列表不为空（只要 Diagnosis Query API 正常）。

---

## 故障排查

### 常见问题

**Q1: TiDB Pod 重启后只有 2 个容器，缺少 sidecar？**
*   **原因**: Patch 可能未成功应用，或者被 Operator 还原。
*   **排查**: 检查 `kubectl get tc $CLUSTER_NAME -o yaml` 中是否包含 `additionalContainers`及 `vector` 配置。
*   **解决**: 重新执行 Patch 命令。

**Q2: MinIO 中始终没有文件生成？**
*   **排查步骤**:
    1.  检查 Vector 容器日志: `kubectl logs $POD_NAME -c vector`
    2.  确认 TiDB 内部是否生成了审计日志: `kubectl exec $POD_NAME -c tidb -- ls -lh /var/log/auditlog/`
    3.  确认配置文件中的 MinIO 地址是否正确（默认配置为集群内 DNS `http://minio:9000`）。

**Q3: Diagnosis Query 报错 "Table not found" 或 S3 连接错误？**
*   **原因**: DuckDB 初始化脚本未能正确连接 MinIO。
*   **解决**: 检查 `06-diagnosis-query.yaml` 中的环境变量 `MINIO_ENDPOINT` 是否与你的 Service 名称匹配。

---

## 测试
