#!/bin/bash
set -e
help() {
	echo "Usage:"
	echo "build-pd.sh -n NAME [-p FILE_PATH] [-u HUB_PATH]"
	echo "Description:"
	echo "NAME, the name of the image."
	echo "FILE_PATH,the path of the PD binary."
	exit -1
}

FILE_PATH="./bin/pd-server"
HUB_PATH="csn/pd"

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

HASH=$($FILE_PATH --version |grep Hash| cut -c18-24)
IMAGE="hub.pingcap.net/$HUB_PATH:$IMAGE_NAME-$HASH"

echo "##### Building Docker image ${IMAGE}#####"
dockerFile=$(cat << EOF 
FROM hub.pingcap.net/pingcap/alpine-glibc
COPY $FILE_PATH /preset_daemon/pd/bin/pd-server
RUN ln -s /preset_daemon/pd/bin/pd-server /pd-server
WORKDIR /
EXPOSE 2379 2380
ENTRYPOINT ["/pd-server"]
EOF
)

echo "$dockerFile"| docker build -t ${IMAGE} -f- .
echo "##### Pushing Docker image ${IMAGE} #####"
docker push ${IMAGE}

