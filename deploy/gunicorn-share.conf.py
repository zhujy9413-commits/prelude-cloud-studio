"""Temporary sharing: separate loopback sockets prevent public Host spoofing."""
from pathlib import Path
import runpy
import os

globals().update({key: value for key, value in runpy.run_path(str(Path(__file__).with_name('gunicorn.conf.py'))).items()
                  if not key.startswith('__')})
bind = ['127.0.0.1:' + os.environ.get('PRELUDE_LOCAL_PORT', '4173'),
        '127.0.0.1:' + os.environ.get('PRELUDE_TUNNEL_PORT', '4181')]
proxy_protocol = False
control_socket_disable = True
header_map = 'drop'
