FROM ubuntu:22.04

ENV container docker
RUN rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt-get update && \
    apt-get install -y systemd sudo mysql-server vim iproute2 tzdata haproxy && \
    apt-get install -y openssh-server gdb iptables ipset iproute2 sysvinit-utils && \
    sed -e 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/g' -i /etc/ssh/sshd_config && \
    systemctl enable ssh.service && \
    ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone

WORKDIR /root

# use static ssh-key pair, so all baseImage WorkloadNode and VirtualMachineWorkloadNode are inter-operable; VM: https://github.com/pingcap/test-infra/blob/67d18b95ed46447ecb94fb88e3563767d535d7bb/examples/testbed/virtualmachine_workloadnode.yaml
RUN mkdir -p /root/.ssh
COPY baseimage.id_rsa /root/.ssh/id_rsa
COPY baseimage.id_rsa.pub /root/.ssh/id_rsa.pub
RUN chmod 0600 /root/.ssh/id_rsa && \
    cat ~/.ssh/id_rsa.pub > ~/.ssh/authorized_keys

# Install tiup
RUN curl --proto '=https' --tlsv1.2 -sSf https://tiup-mirrors.pingcap.com/install.sh | sh

EXPOSE 22
ENV PATH="/root/.tiup/bin:${PATH}"
VOLUME [ "/sys/fs/cgroup" ]
CMD ["/sbin/init"]
