# Host daemon

The Python agent runs continuously as a systemd service. The game mod supplies backup timing. No cron job or console log parser is used.

Copy `agent.example.json` to a persistent administrator-owned location and set actual dataset names, world mount, shared mailbox and private state directory. Set configuration ownership to root and mode 0600. Do not put local configuration or state in Git.

```sh
sudo python3 deploy/install.py --config /etc/minecraft-zfs-agent.local.json \
  --game-uid 1000 --game-gid 1000
```

The installer creates a runtime unit in `/run/systemd/system`, prepares shared/private directories, starts the daemon and confirms systemd active state. Check the mailbox heartbeat to confirm the source dataset is ready; systemd active alone does not prove that a world is mounted.

On TrueNAS, use a persistent config path on a data pool and add `--truenas`. This records a POSTINIT task in TrueNAS's configuration database that recreates the runtime service after reboot. Repeated installation updates the same named task instead of creating duplicates.

On ordinary Linux, copy the rendered unit (`--print`) into your distribution's persistent systemd unit directory and enable it, or arrange an equivalent boot hook. The repository must remain available at its installed absolute path.

```sh
systemctl status minecraft-zfs-agent
journalctl -u minecraft-zfs-agent -n 50
PYTHONPATH=agent python3 -m minecraft_zfs_agent --config /etc/minecraft-zfs-agent.local.json status
```

Install the server mod separately. Its mailbox path is relative to the Minecraft server directory and must refer to the same directory as the host's `mailbox_path`. The default mod directory is `.zfs-snapshot-bridge`. The mailbox directory must be writable by the game UID; host state and configuration remain administrator-owned.

Validate with a disposable source dataset and a separate test server before adopting an existing world. Preserve an independent recovery point. Disable previous console/cron orchestration only after a real mod-requested snapshot has committed and replicated successfully.
