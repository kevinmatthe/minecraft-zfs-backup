#!/usr/bin/env python3
"""Install a persistent host agent service; optionally register TrueNAS POSTINIT."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess


def systemd_quote(value):
    text = str(value)
    if any(character in text for character in ('\n', '\r', '\0')):
        raise ValueError('control characters are not allowed in unit paths')
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def service_name(value):
    value = value.removesuffix('.service')
    if not re.fullmatch(r'minecraft-zfs-[A-Za-z0-9_.-]+', value):
        raise ValueError('service name must start with minecraft-zfs- and contain only letters, digits, _, . or -')
    return value + '.service'


def render_unit(repo, config):
    command = ' '.join(systemd_quote(value) for value in
                       ('/usr/bin/python3','-m','minecraft_zfs_agent','--config',config,'run'))
    return '\n'.join([
        '[Unit]',
        'Description=Minecraft ZFS snapshot and replication host agent',
        'After=local-fs.target zfs-mount.service',
        '',
        '[Service]',
        'Type=simple',
        'User=root',
        'Group=root',
        'WorkingDirectory=' + systemd_quote(repo),
        'Environment=' + systemd_quote('PYTHONPATH=' + str(repo/'agent')),
        'ExecStart=' + command,
        'Restart=always',
        'RestartSec=5',
        'TimeoutStopSec=30',
        'KillMode=control-group',
        'UMask=0077',
        'NoNewPrivileges=true',
        '',
        '[Install]',
        'WantedBy=multi-user.target',
        '',
    ])


def postinit_command(repo, config, name, uid, gid):
    service_name(name)
    return shlex.join(['/usr/bin/python3',str(repo/'deploy/install.py'),'--repo',str(repo),
                       '--config',str(config),'--name',name,'--game-uid',str(uid),'--game-gid',str(gid)])


def run(*args):
    subprocess.run(args, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--name',default='minecraft-zfs-agent')
    parser.add_argument('--game-uid',type=int,default=1000)
    parser.add_argument('--game-gid',type=int,default=1000)
    parser.add_argument('--truenas',action='store_true',help='Register/update a persistent TrueNAS POSTINIT command')
    parser.add_argument('--print',dest='print_only',action='store_true',help='Print unit without installing')
    args = parser.parse_args()
    repo = args.repo.resolve()
    config = args.config.resolve()
    name = service_name(args.name)
    unit = render_unit(repo,config)
    if args.print_only:
        print(unit,end=''); return
    if os.geteuid() != 0: parser.error('installation requires root for systemd and ZFS access')
    if min(args.game_uid,args.game_gid) < 0: parser.error('UID/GID must be nonnegative')
    if not (repo/'agent/minecraft_zfs_agent/__main__.py').is_file(): parser.error('agent package is missing')
    data = json.loads(config.read_text())
    if config.stat().st_uid != 0 or config.stat().st_mode & 0o022:
        parser.error('agent configuration must be root-owned and not group/world writable')
    mailbox = Path(data['mailbox_path'])
    state = Path(data['state_dir'])
    for folder in (mailbox,state):
        if not folder.is_absolute(): parser.error('mailbox/state directories must be absolute')
        if folder.is_symlink(): parser.error('mailbox/state directories cannot be symlinks')
        folder.mkdir(parents=True,exist_ok=True)
    for folder,uid,gid in ((mailbox,args.game_uid,args.game_gid),(state,0,0)):
        descriptor = os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            os.fchown(descriptor,uid,gid);os.fchmod(descriptor,0o700)
        finally: os.close(descriptor)
    # /run is generated on each boot. TrueNAS POSTINIT recreates this unit from
    # the persistent repository and config; ordinary Linux can use the same hook.
    target = Path('/run/systemd/system')/name
    temporary = target.with_suffix('.tmp')
    temporary.write_text(unit)
    os.replace(temporary,target)
    run('systemctl','daemon-reload')
    run('systemctl','restart',name)
    run('systemctl','is-active','--quiet',name)
    if args.truenas:
        command = postinit_command(repo,config,args.name,args.game_uid,args.game_gid)
        comment = 'Minecraft ZFS agent service: '+name
        tasks = json.loads(subprocess.check_output(['midclt','call','initshutdownscript.query'],text=True))
        existing = [task for task in tasks if task['comment']==comment]
        if len(existing)>1: raise RuntimeError('multiple matching POSTINIT entries; refusing to modify them')
        payload = {'type':'COMMAND','command':command,'when':'POSTINIT','enabled':True,
                   'timeout':60,'comment':comment}
        if existing:
            run('midclt','call','initshutdownscript.update',str(existing[0]['id']),json.dumps(payload))
        else:
            run('midclt','call','initshutdownscript.create',json.dumps(payload))
    print('Installed',name,'using',config)


if __name__ == '__main__': main()
