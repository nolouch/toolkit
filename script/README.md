# 使用说明

脚本主要用于 5 副本 3 中心的部署方式，在单个 az 级别的容灾情况下进行快速恢复。
当一个 az 发生故障是，可以将指定 az 快速下线，并且将 5 副本切换为 3 副本。
当这个 az 又恢复时，快速将之前的数据进行清理，同时拉起 tikv 并将 3 副本重新设置为 5 副本。

## 环境需求

需要在安装了 tiup 的中控节点执行，环境需要安装好 [`jq` 命令](https://stedolan.github.io/jq/download/)
## AZ 故障快速下线

使用 offline_az.sh 脚本进行恢复.

```
Usage:
offline_az.sh -p PD_ADDR -k LABEL_KEY -v LABEL_VALUE
Options:
  -p, PD_ADDR is the address of the pd. default: http://127.0.0.1:2379
  -k, LABEL_KEY is the key of the host's label, default: ""
  -v, LABEL_VALUE is the value of the label, default: ""
```
其中：
  - -p 指定 PD 的地址，默认是 http://127.0.0.1:2379
  - -k 指定 az level 对应的 label key， 比如 `zone`
  - -v 指定 az level 对应的 label value, 比如 `az_bj`

例如：
```
./offline_az.sh -p http://127.0.0.1:2379 -k zone -v az_bj
```

**NOTE**: 请按照提示核对需要下线节点是否正确


## AZ 故障恢复

使用 up_az.sh 脚本进行恢复. 快速将之前的数据进行清理，同时拉起 tikv 并将 3 副本重新设置为 5 副本。

```
Usage:
up_az.sh -p PD_ADDR -k LABEL_KEY -v LABEL_VALUE
Options:
  -p, PD_ADDR is the address of the pd. default: http://127.0.0.1:2379
  -n, CLUSTER_NAME is the name of the cluster. default: ""
  -k, LABEL_KEY is the key of the host's label, default: ""
  -v, LABEL_VALUE is the value of the label, default: ""
```
其中：
  - -p 指定 PD 的地址，默认是 http://127.0.0.1:2379
  - -k 指定 az level 对应的 label key， 比如 `zone`
  - -v 指定 az level 对应的 label value, 比如 `az_bj`

例如：
```
./up_az.sh -p http://172.16.4.6:5379 -k zone -v az_bj -n test-cluster
```

**NOTE**: 请按照提示核对需要下线节点是否正确
