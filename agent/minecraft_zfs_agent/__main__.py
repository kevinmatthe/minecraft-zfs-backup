import argparse
import json
import logging
import os
from .config import Config
from .daemon import run, status


def main():
    os.environ['PATH']='/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/bin'
    parser=argparse.ArgumentParser(description='Persistent host agent for mod-coordinated Minecraft ZFS snapshots')
    parser.add_argument('--config',required=True,help='absolute path to root-owned JSON administrator configuration')
    parser.add_argument('mode',choices=('run','status'))
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(threadName)s %(message)s')
    try:
        config=Config.load(args.config)
        if args.mode=='run':run(config)
        else:print(json.dumps(status(config),indent=2))
    except (OSError,ValueError,RuntimeError) as error:
        logging.error('%s',error)
        raise SystemExit(1) from None


if __name__=='__main__':main()
