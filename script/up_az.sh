#!/bin/bash
set -e
help() {
    echo "Usage:"
    echo "up_az.sh -p PD_ADDR -k LABEL_KEY -v LABEL_VALUE"
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

STORE_RAW_DATA=$(curl -sl "$PD_ADDR/pd/api/v1/stores?state=1&state=0&state=2"  | jq "{stores: [.stores[] | {id: .store.id, address: .store.address,  state_name: .store.state_name,  label_key: .store.labels[]?|select(.key == \"$LABEL_KEY\" and .value == \"$LABEL_VALUE\")}]}")
STORE_IDS=($(echo $STORE_RAW_DATA| jq ".stores[]|.id"))
STORE_ADDRS=($(echo $STORE_RAW_DATA| jq -r ".stores[]|.address"))
STORE_StateS=($(echo $STORE_RAW_DATA| jq ".stores[]|.state_name"))
DEPLOY_DETAILS=$(tiup cluster display $CLUSTER_NAME -R tikv)
SKIPED_STORES=()

for i in ${!STORE_IDS[@]}
do
	STORE_ID=${STORE_IDS[$i]}
	STORE_ADDR=${STORE_ADDRS[$i]}
    # clean input
    read -t 1 -n 10000 discard || true
    read -n 1 -p "recover the store $STORE_ID, address: $STORE_ADDR (y/n)" action
    printf "\n"
    if [ "$action" != "${action#[Yy]}" ] ;then
        echo "  - recovering $STORE_ID, addres: $STORE_ADDR"
		if  [ ${STORE_StateS[$i]} == "Up" ]; then
            echo "  - the store $STORE_ID already up"
			continue
		fi
		DATA_DIR=$(echo "$DEPLOY_DETAILS"|grep "$STORE_ADDR"|awk '{print $7}')
		HOST=$(echo "$DEPLOY_DETAILS"|grep tikv|grep "$STORE_ADDR"|awk '{print $3}')
        dir_count=$(echo "$DEPLOY_DETAILS"|grep "$DATA_DIR"|wc -l)
        state=$(echo "$DEPLOY_DETAILS"|grep "$DATA_DIR"|awk '{print $6}')
        if [ $dir_count -gt 1 ] ; then
            echo "  - [warn] there are other stores have same data directory with $STORE_ID, path: $DATA_DIR"
            SKIPED_STORES+=("$STORE_ID")
            continue
        fi
        if [ "$state" != "Tombstone" ] ; then
            echo "  - [warn] there is a $state store with same data directory. path: [$HOST:$DATA_DIR]"
            SKIPED_STORES+=("$STORE_ID")
            if [ "$state" != "Up" ] ; then
                echo "  - [warn] start the $state store $STORE_ID with same data directory. path: [$HOST:$DATA_DIR]"
		        tiup cluster start $CLUSTER_NAME -N ${STORE_ADDRS[$i]} &>> /tmp/up_az.log
            fi
            continue
        fi
	    tiup cluster stop $CLUSTER_NAME -N $STORE_ADDR >> /tmp/up_az.log
		echo "  - clean the data of the store $STORE_ID path: [$HOST:$DATA_DIR]" 
		tiup cluster exec $CLUSTER_NAME -N $HOST --command "rm -rf $DATA_DIR" >> /tmp/up_az.log
		tiup cluster start $CLUSTER_NAME -N ${STORE_ADDRS[$i]} &>> /tmp/up_az.log
        echo "  - started the store"
    else
        SKIPED_STORES+=("$STORE_ID")
        echo "-- Skip store ${STORE_IDS[$i]}, address: ${STORE_ADDRS[$i]}..."
    fi
done
echo "Finished, and skiped stores $SKIPED_STORES"

read -t 1 -n 10000 discard || true
read -n 1 -p "set replicas number to 5 (y/n)" action
printf "\n"
if [ "$action" != "${action#[Yy]}" ] ;then
   tiup ctl pd -u $PD_ADDR config set max-replicas 5 >> /tmp/up_az.log
   echo "set replicas number to 5"
   tiup ctl pd -u $PD_ADDR config set replica-schedule-limit 64 >> /tmp/up_az.log
   echo "set replica-schedule-limit to 64"
else
    echo "Skip set replicas number..."
fi


read -t 1 -n 10000 discard || true
read -n 1 -p "prune the tombstone stores (y/n)" action
printf "\n"
if [ "$action" != "${action#[Yy]}" ] ;then
    tiup ctl pd -u  $PD_ADDR store remove-tombstone >> /tmp/up_az.log
    echo "clean all tombstones in pd"
fi

echo "Finished, Detail log in: /tmp/up_az.log"
