import socket
for host in ['bfl-gateway.nousresearch.com', 'api.bfl.ai', 'gateway.nousresearch.com', 'flux3-gateway.nousresearch.com']:
    try:
        ip = socket.gethostbyname(host)
        print(f'{host} -> {ip}')
    except socket.gaierror as e:
        print(f'{host} -> UNRESOLVABLE: {e}')
