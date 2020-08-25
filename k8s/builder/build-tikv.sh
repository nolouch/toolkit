#!/bin/bash
set -e
help() {
	echo "Usage:"
	echo "build-tikv.sh -n NAME [-p FILE_PATH] [-u HUB_PATH]"
	echo "Description:"
	echo "NAME, the name of the image."
	echo "FILE_PATH,the path of the TiKV binary."
	exit -1
}

FILE_PATH="./bin/tikv-server"
HUB_PATH="csn/tikv"

while getopts 'n:p:u:h' OPT; do
	case $OPT in
		n) IMAGE_NAME="$OPTARG";;
		p) FILE_PATH="$OPTARG";;
		u) HUT_PATH="$OPTARG";;
		h) help;;
		?) help;;
	esac
done

[ ! -n "$IMAGE_NAME" ] && help

HASH=$($FILE_PATH --version |grep Hash| cut -c20-26)
IMAGE="hub.pingcap.net/$HUB_PATH:$IMAGE_NAME-$HASH"

echo "##### Building Docker image ${IMAGE}#####"
dockerFile=$(cat << EOF 
FROM pingcap/alpine-glibc
COPY bin/tikv-server /preset_daemon/tikv/bin/tikv-server
RUN ln -s /preset_daemon/tikv/bin/tikv-server /tikv-server
RUN apk add --no-cache libstdc++
WORKDIR /
EXPOSE 20160
ENTRYPOINT ["/tikv-server"]
EOF
)

echo "$dockerFile"| docker build -t ${IMAGE} -f- .
echo "##### Pushing Docker image ${IMAGE} #####"
docker push ${IMAGE}
