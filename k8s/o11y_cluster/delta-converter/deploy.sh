#!/bin/bash
# Statement Coverage 开发环境部署脚本

set -e

IMAGE="hub.pingcap.net/csn/statement-coverage-dev:latest"

echo "=== 1. 构建 Docker 镜像 (amd64) ==="
cd "$(dirname "$0")"
docker buildx build --platform linux/amd64 -f Dockerfile.ubuntu -t $IMAGE . --load

echo ""
echo "=== 2. 推送镜像 ==="
echo "请手动执行: docker push $IMAGE"

echo ""
echo "=== 3. 部署到 K8s ==="
read -p "推送完成后按回车继续..."
kubectl apply -f 10-coverage-dev.yaml

echo ""
echo "=== 4. 等待 Pod 就绪 ==="
kubectl wait --for=condition=ready pod -l app=coverage-dev --timeout=60s

echo ""
echo "=== 部署完成！==="
echo ""
echo "进入容器:"
echo "  kubectl exec -it deployment/coverage-dev  -- bash"
echo ""
echo "运行覆盖率计算 (最近30分钟):"
echo "  kubectl exec -it deployment/coverage-dev -- bash -c 'END_TIME=\$(date +%s) && START_TIME=\$((END_TIME - 1800)) && python3 /workspace/calculate_coverage.py --start-time \$START_TIME --end-time \$END_TIME'"
