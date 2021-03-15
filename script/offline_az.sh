#!/bin/bash
set -e
help() {
    echo "Usage:"
    echo "offline_az.sh -p PD_ADDR -k LABEL_KEY -v LABEL_VALUE"
    echo "Options:"
    echo "  -p, PD_ADDR is the address of the pd. default: http://127.0.0.1:2379"
    echo "  -k, LABEL_KEY is the key of the host's label, default: \"\""
    echo "  -v, LABEL_VALUE is the value of the label, default: \"\""
    exit -1
}

PD_ADDR="http://127.0.0.1:2379"

while getopts 'p:k:v:h' OPT; do
	case $OPT in
		p) PD_ADDR="$OPTARG";;
		k) LABEL_KEY="$OPTARG";;
		v) LABEL_VALUE="$OPTARG";;
		h) help;;
		?) help;;
	esac
done


[ ! -n "$PD_ADDR" ] && help
[ ! -n "$LABEL_KEY" ] && help
[ ! -n "$LABEL_VALUE" ] && help

STORE_RAW_DATA=$(tiup ctl pd -u $PD_ADDR store --jq="{stores: [.stores[] | {id: .store.id, address: .store.address, label_key: .store.labels[]?|select(.key == \"$LABEL_KEY\" and .value == \"$LABEL_VALUE\")}]}"|grep -v "Starting")
STORE_IDS=($(echo $STORE_RAW_DATA| jq ".stores[]|.id"))
STORE_ADDRS=($(echo $STORE_RAW_DATA| jq ".stores[]|.address"))
echo "Offline 
  - store ids: [${STORE_IDS[*]}]
  - address: [${STORE_ADDRS[*]}]
total ${#STORE_IDS[@]}"
read -t 1 -n 10000 discard || true
read -n 1 -p "offline all stores with the label $LABEL_KEY:$LABEL_VALUE (y/n)" action
printf "\n"
if [ "$action" != "${action#[Yy]}" ] ;then
	for i in ${!STORE_IDS[@]}
	do
		echo "Offline store ${STORE_IDS[$i]}, address: ${STORE_ADDRS[$i]} "
		tiup ctl pd -u $PD_ADDR store delete ${STORE_IDS[$i]} >> /tmp/offline_az.log
	done
else 
	echo "Skiped"
fi


read -t 1 -n 10000 discard || true
read -n 1 -p "Set Replicas number to 3 (y/n)" action
printf "\n"
if [ "$action" != "${action#[Yy]}" ] ;then
   tiup ctl pd -u $PD_ADDR config set max-replicas 3 >> /tmp/offline_az.log
   echo "set replicas number to 3"
   tiup ctl pd -u $PD_ADDR config set replica-schedule-limit 1000  >> /tmp/offline_az.log
   echo "set replica-schedule-limit to 1000"
else
    echo "Skip..."
fi

echo "Finished, Detail log in: /tmp/offline_az.log"
