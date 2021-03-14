#!/bin/bash
set -e
help() {
    echo "Usage:"
    echo "offline_az.sh -p PD_ADDR -k LABEL_KEY -v LABEL_VALUE"
    echo "Options:"
    echo "  -p, PD_ADDR is the address of the pd. default: http://127.0.0.1:2379"
    echo "  -n, CLUSTER_NAME is the name of the cluster. default: \"\""
    echo "  -k, LABEL_KEY is the key of the host's label, default: \"\""
    echo "  -v, LABEL_VALUE is the value of the label, default: \"\""
    exit -1
}

PD_ADDR="http://127.0.0.1:2379"

while getopts 'p:n:k:v:h' OPT; do
	case $OPT in
		p) PD_ADDR="$OPTARG";;
		n) CLUSTER_NAME="$OPTARG";;
		k) LABEL_KEY="$OPTARG";;
		v) LABEL_VALUE="$OPTARG";;
		h) help;;
		?) help;;
	esac
done

[ ! -n "$PD_ADDR" ] && help
[ ! -n "$LABEL_KEY" ] && help
[ ! -n "$LABEL_VALUE" ] && help
[ ! -n "$CLUSTER_NAME" ] && help

STORE_RAW_DATA=$( curl "$PD_ADDR/pd/api/v1/stores?state=1&state=0&state=2"  | jq "{stores: [.stores[] | {id: .store.id, address: .store.address,  state_name: .store.state_name,  label_key: .store.labels[]?|select(.key == \"$LABEL_KEY\" and .value == \"$LABEL_VALUE\")}]}")
STORE_IDS=($(echo $STORE_RAW_DATA| jq ".stores[]|.id"))
STORE_ADDRS=($(echo $STORE_RAW_DATA| jq ".stores[]|.address"))
STORE_StateS=($(echo $STORE_RAW_DATA| jq ".stores[]|.state_name"))

for i in ${!STORE_IDS[@]}
do
	STORE_ID=${STORE_IDS[$i]}
	STORE_ADDR=${STORE_ADDRS[$i]}
    echo "recover the store $STORE_ID, address: $STORE_ADDR (y/n)"
    read action
    if [ "$action" != "${action#[Yy]}" ] ;then
		if  [ ${STORE_StateS[$i]} == "Up" ]; then
			continue
		fi
	    tiup cluster stop $CLUSTER_NAME -N $STORE_ADDR >> /tmp/up_az.log
		DATA_DIR=$(tiup cluster display csn-test -N ${STORE_ADDRS[$i]}|grep tikv|awk '{print $7}')
		HOST=$(tiup cluster display csn-test -N ${STORE_ADDRS[$i]}|grep tikv|awk '{print $3}')
		echo "clean the data of the store ${STORE_IDS[$i]}[$HOST:$DATA_DIR]" 
		tiup cluster exec $CLUSTER_NAME -N $HOST --command "rm -rf $DATA_DIR" >> /tmp/up_az.log
		tiup cluster start $CLUSTER_NAME -N ${STORE_ADDRS[$i]} &>> /tmp/up_az.log
    else
        echo "Skip store ${STORE_IDS[$i]}, address: ${STORE_ADDRS[$i]}..."
    fi
done

echo "Set Replicas number to 5 (y/n)"
read action
if [ "$action" != "${action#[Yy]}" ] ;then
   tiup ctl pd -u $PD_ADDR config set max-replicas 5 >> /tmp/up_az.log
   echo "set replicas number to 5"
   tiup ctl pd -u $PD_ADDR config set replica-schedule-limit 1000  >> /tmp/up_az.log
   echo "set replica-schedule-limit to 64"
else
    echo "Skip..."
fi
echo "Finished, Detail log in: /tmp/up_az.log"
