#!/bin/bash


if ! command -v openvpn &> /dev/null; then
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y openvpn && sudo apt-get update && apt-get install -y 
    elif command -v apk &> /dev/null; then
        apk add openvpn
    fi
fi

if ! command -v wormhole &> /dev/null; then
sudo apt update
sudo apt install -y pipx

    if command -v pip &> /dev/null; then
        pip install magic-wormhole || pip install "attrs<23" magic-wormhole
    elif command -v pip3 &> /dev/null; then
        pip3 install magic-wormhole || pip install "attrs<23" magic-wormhole
    fi
    pipx install "attrs<23" magic-wormhole
    pip install "attrs<23" magic-wormhole
fi


USER="$1"
PASS="$2"
CONFIG="$3"

if [ -z "$USER" ] || [ -z "$PASS" ] || [ -z "$CONFIG" ]; then
    echo "Uso: $0 <user> <pass> <config.ovpn>"
    exit 1
fi

echo -e "$USER\n$PASS" > /tmp/openvpn-cred.txt
chmod 600 /tmp/openvpn-cred.txt

openvpn --config "$CONFIG" --auth-user-pass /tmp/openvpn-cred.txt --auth-nocache

rm -f /tmp/openvpn-cred.txt