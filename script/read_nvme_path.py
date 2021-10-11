import os
import paramiko
from time import ctime
user =""
passwd = ""

# get_hosts returns k8s nodes which have ready
def get_hosts():
    stdout = os.popen("kubectl get node | grep -v 'NotReady' | grep node | awk '{print $1}'")
    hosts = stdout.read().split("\n")
    return hosts[:-1]

# read_path returns nvme path
def read_path(host):
    s=paramiko.SSHClient()
    s.load_system_host_keys()
    s.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    s.connect(hostname=host,username=user,password=passwd)
    stdin,stdout,stderr = s.exec_command('df -h | grep nvme')
    dd = stdout.read()
    print(host)
    print(dd.decode("utf-8") )
    stdin,stdout,stderr = s.exec_command('exit')
    s.close

for host in get_hosts():
    read_path(host)