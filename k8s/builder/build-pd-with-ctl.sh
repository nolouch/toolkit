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
		u) HUB_PATH="$OPTARG";;
		h) help;;
		?) help;;
	esac
done

[ ! -n "$IMAGE_NAME" ] && help
CTL_PATH=$(echo $FILE_PATH|sed  s/pd-server/pd-ctl/g)

HASH=$($FILE_PATH --version |grep Hash| cut -c18-24)
IMAGE="hub-new.pingcap.net/$HUB_PATH:$IMAGE_NAME-$HASH"

echo "##### Building Docker image ${IMAGE}#####"
dockerFile=$(cat << EOF 
FROM pingcap/alpine-glibc
COPY $FILE_PATH /pd-server
COPY $CTL_PATH /pd-ctl
WORKDIR /
EXPOSE 2379 2380
ENTRYPOINT ["/pd-server"]
EOF
)

echo "$dockerFile"| docker build -t ${IMAGE} -f- .
echo "##### Pushing Docker image ${IMAGE} #####"
docker push ${IMAGE}

