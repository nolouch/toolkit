#!/bin/bash
set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 设置 kubeconfig
export KUBECONFIG=/Users/nolouch/test/testcluster/stmt/idc/kubeconfig.yml

print_info "Using kubeconfig: $KUBECONFIG"
print_info "Current namespace: $(kubectl config view --minify -o jsonpath='{.contexts[0].context.namespace}')"

# Step 1: 部署 MinIO
print_info "Step 1: Deploying MinIO..."
kubectl apply -f "$SCRIPT_DIR/01-minio.yaml"

print_info "Waiting for MinIO to be ready..."
kubectl wait --for=condition=available deployment/minio --timeout=180s || {
    print_warn "MinIO not ready yet, continuing..."
}

# Step 2: 创建 MinIO Buckets
print_info "Step 2: Creating MinIO buckets..."
sleep 10  # 等待 MinIO 完全启动

kubectl exec deploy/minio -- sh -c "
  mc alias set local http://localhost:9000 minioadmin minioadmin 2>/dev/null || true
  mc mb -p local/topsql 2>/dev/null || echo 'topsql bucket may already exist'
  mc mb -p local/tidb-logs 2>/dev/null || echo 'tidb-logs bucket may already exist'
  echo 'Buckets ready'
" || print_warn "Could not create buckets, will retry later"

# Step 3: 部署 Vector TopSQL
print_info "Step 3: Deploying Vector TopSQL collector..."
kubectl apply -f "$SCRIPT_DIR/02-vector-topsql.yaml"

# Step 4: 部署 Vector Logs
print_info "Step 4: Deploying Vector Logs collector..."
kubectl apply -f "$SCRIPT_DIR/03-vector-logs.yaml"

# Step 5: 等待部署完成
print_info "Step 5: Waiting for deployments..."

sleep 5

kubectl wait --for=condition=available deployment/vector-topsql --timeout=180s || {
    print_warn "vector-topsql not ready, check logs"
}

print_info "Checking DaemonSet status..."
kubectl get daemonset vector-logs || true

# 输出状态
echo ""
print_info "=========================================="
print_info "Deployment completed!"
print_info "=========================================="
echo ""
echo "Check status:"
echo "  kubectl --kubeconfig=$KUBECONFIG get pods | grep -E 'minio|vector'"
echo ""
echo "View logs:"
echo "  kubectl --kubeconfig=$KUBECONFIG logs -l app=vector-topsql -f"
echo "  kubectl --kubeconfig=$KUBECONFIG logs -l app=vector-logs -f"
echo ""
echo "Check MinIO data:"
echo "  kubectl --kubeconfig=$KUBECONFIG exec deploy/minio -- mc ls local/topsql/"
echo "  kubectl --kubeconfig=$KUBECONFIG exec deploy/minio -- mc ls local/tidb-logs/"
echo ""
echo "Get MinIO NodePort:"
echo "  kubectl --kubeconfig=$KUBECONFIG get svc minio"
