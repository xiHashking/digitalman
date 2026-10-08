#!/usr/bin/env bash
set -euo pipefail

lan_ip="${1:-}"
if [[ -z "$lan_ip" ]]; then
    echo "Usage: $0 <LAN_IP>" >&2
    exit 1
fi

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cert_dir="$project_dir/.certs"
mkdir -p "$cert_dir"

openssl genrsa -out "$cert_dir/local-ca.key" 2048
openssl req -x509 -new -sha256 -days 3650 \
    -key "$cert_dir/local-ca.key" \
    -subj "/CN=LiveTalking Local CA" \
    -out "$cert_dir/local-ca.crt"

openssl genrsa -out "$cert_dir/server.key" 2048
openssl req -new \
    -key "$cert_dir/server.key" \
    -subj "/CN=$lan_ip" \
    -out "$cert_dir/server.csr"

extension_file="$(mktemp)"
trap 'rm -f "$extension_file"' EXIT
printf '%s\n' \
    '[server_cert]' \
    'basicConstraints=CA:FALSE' \
    'keyUsage=digitalSignature,keyEncipherment' \
    'extendedKeyUsage=serverAuth' \
    "subjectAltName=IP:$lan_ip,IP:127.0.0.1,DNS:localhost" \
    > "$extension_file"

openssl x509 -req -sha256 -days 825 \
    -in "$cert_dir/server.csr" \
    -CA "$cert_dir/local-ca.crt" \
    -CAkey "$cert_dir/local-ca.key" \
    -CAcreateserial \
    -extfile "$extension_file" \
    -extensions server_cert \
    -out "$cert_dir/server.crt"

chmod 600 "$cert_dir/local-ca.key" "$cert_dir/server.key"
echo "Created HTTPS certificate for $lan_ip in $cert_dir"
