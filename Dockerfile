FROM debian:trixie-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin

# contrib supplies userspace tools only. The host supplies its existing ZFS kernel module.
RUN sed -i 's/^Components: main$/Components: main contrib/' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3 util-linux zfsutils-linux \
    && dpkg-query -W -f='${Version}\n' zfsutils-linux | grep -E '^2\.3\.' \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/minecraft-zfs-agent
COPY agent/minecraft_zfs_agent/ /opt/minecraft-zfs-agent/minecraft_zfs_agent/

ENTRYPOINT ["/usr/bin/python3", "-m", "minecraft_zfs_agent"]
CMD ["--config", "/etc/minecraft-zfs-agent.json", "run"]
