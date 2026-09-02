#!/usr/bin/env bash
set -Eeuo pipefail

node_version="v22.19.0"
node_archive="node-${node_version}-linux-x64.tar.xz"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl xz-utils python3-venv

if [[ ! -x /usr/local/bin/node ]] || [[ "$(/usr/local/bin/node --version)" != "$node_version" ]]; then
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' EXIT
  cd "$temp_dir"

  downloaded=0
  for base_url in \
    "https://nodejs.org/dist/$node_version" \
    "https://npmmirror.com/mirrors/node/$node_version"; do
    if curl --fail --location --retry 3 --connect-timeout 10 \
      --output "$node_archive" "$base_url/$node_archive" && \
      curl --fail --location --retry 3 --connect-timeout 10 \
      --output SHASUMS256.txt "$base_url/SHASUMS256.txt"; then
      downloaded=1
      break
    fi
  done
  if [[ $downloaded -ne 1 ]]; then
    echo "unable to download Node.js $node_version" >&2
    exit 1
  fi

  grep " $node_archive\$" SHASUMS256.txt | sha256sum --check --strict
  tar -xJf "$node_archive" -C /usr/local --strip-components=1
  cd /
  rm -rf "$temp_dir"
  trap - EXIT
fi

if ! id -u bacoach >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/bacoach \
    --shell /usr/sbin/nologin bacoach
fi

install -d -m 0750 -o bacoach -g bacoach \
  /opt/bacoach /opt/bacoach/releases /var/lib/bacoach
install -d -m 0750 -o root -g bacoach /etc/bacoach

if ! swapon --show=NAME --noheadings | grep -qx '/swapfile'; then
  if [[ ! -f /swapfile ]]; then
    fallocate -l 2G /swapfile
    chmod 0600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
fi
if ! grep -qE '^/swapfile\s' /etc/fstab; then
  printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab
fi
printf '%s\n' 'vm.swappiness=10' > /etc/sysctl.d/99-bacoach.conf
sysctl --system >/dev/null

/usr/local/bin/node --version
/usr/local/bin/npm --version
python3 --version
free -h
