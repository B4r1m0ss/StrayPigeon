#!/usr/bin/env bash
# Reproducible NAT/P2P testbed using Linux user+mount+network namespaces.
#
# Usage:
#   scripts/netns_testbed.sh lan
#   scripts/netns_testbed.sh eim
#   scripts/netns_testbed.sh symmetric
#   scripts/netns_testbed.sh firewall
#
# Requires unprivileged user namespaces (tested with unshare -Urnm).
set -euo pipefail

MODE="${1:-lan}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
SHARED="${SHARED:-/tmp/straypigeon-netns-$(date +%s)-$$}"
PORT="${PORT:-40000}"
SIZE="${SIZE:-4194304}"
CAPTURE="${CAPTURE:-1}"
TCPDUMP="${TCPDUMP:-0}"
SECOND_PUNCH="${SECOND_PUNCH:-0}"
IDLE="${IDLE:-0}"
mkdir -p "$SHARED"
if [[ ! -f "$SHARED/psk.bin" ]]; then
  head -c 32 /dev/urandom > "$SHARED/psk.bin"
fi
export ROOT PY SHARED PORT SIZE MODE SECOND_PUNCH IDLE CAPTURE TCPDUMP

exec unshare -Urnm bash -c '
set -euo pipefail
mount --make-rprivate /
mount -t tmpfs tmpfs /run
mkdir -p /run/netns

ip link set lo up

make_ns() {
  ip netns add "$1"
  ip -n "$1" link set lo up
}

make_ns ns_a
make_ns ns_b

if [[ "$MODE" == "lan" ]]; then
  ip link add br0 type bridge
  ip link set br0 up
  ip addr add 192.168.1.1/24 dev br0
  for spec in "ns_a 192.168.1.2" "ns_b 192.168.1.3"; do
    ns="${spec%% *}"; ip="${spec##* }"
    ip link add "veth_$ns" type veth peer name eth0
    ip link set eth0 netns "$ns"
    ip link set "veth_$ns" master br0
    ip link set "veth_$ns" up
    ip -n "$ns" addr add "$ip/24" dev eth0
    ip -n "$ns" link set eth0 up
    ip -n "$ns" route add default via 192.168.1.1
  done
  A_ADV=192.168.1.2
  B_ADV=192.168.1.3
  CAP_IF_A=eth0
  CAP_IF_B=eth0
  STUN_ARG=""
else
  make_ns ns_r
  ip link add a0 type veth peer name ra
  ip link add b0 type veth peer name rb
  ip link set a0 netns ns_a
  ip link set ra netns ns_r
  ip link set b0 netns ns_b
  ip link set rb netns ns_r
  ip -n ns_a addr add 192.168.1.2/24 dev a0
  ip -n ns_a link set a0 up
  ip -n ns_b addr add 10.0.0.2/24 dev b0
  ip -n ns_b link set b0 up
  ip -n ns_r addr add 192.168.1.1/24 dev ra
  ip -n ns_r addr add 10.0.0.1/24 dev rb
  ip -n ns_r link set ra up
  ip -n ns_r link set rb up
  ip netns exec ns_r sh -c "echo 1 > /proc/sys/net/ipv4/ip_forward"
  ip -n ns_a route add default via 192.168.1.1
  ip -n ns_b route add default via 10.0.0.1

  if [[ "$MODE" == "symmetric" ]]; then
    NAT_FLAG="--random"
  else
    NAT_FLAG=""
  fi
  ip netns exec ns_r iptables -t nat -A POSTROUTING -s 192.168.1.0/24 -o rb -j MASQUERADE $NAT_FLAG
  ip netns exec ns_r iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -o ra -j MASQUERADE $NAT_FLAG
  if [[ "$MODE" == "firewall" ]]; then
    ip netns exec ns_r iptables -A FORWARD -p udp --dport "$PORT" -j DROP
  fi
  A_ADV=192.168.1.2
  B_ADV=10.0.0.2
  CAP_IF_A=a0
  CAP_IF_B=b0
  ip netns exec ns_b env PYTHONPATH="$ROOT" "$PY" "$ROOT/scripts/stun_probe_server.py" --port 3478 &
  STUN_PID=$!
  sleep 0.2
  STUN_ARG="--stun 10.0.0.2:3478"
fi

export A_ADV B_ADV
echo "mode=$MODE shared=$SHARED"
if [[ "$TCPDUMP" == "1" ]]; then
  ip netns exec ns_a tcpdump -i "$CAP_IF_A" -n -s0 -U -Z root -w "$SHARED/a.pcap" >"$SHARED/tcpdump_a.log" 2>&1 & TP_A=$!
  ip netns exec ns_b tcpdump -i "$CAP_IF_B" -n -s0 -U -Z root -w "$SHARED/b.pcap" >"$SHARED/tcpdump_b.log" 2>&1 & TP_B=$!
fi
if [[ "$CAPTURE" == "1" ]]; then
  ip netns exec ns_a env PYTHONPATH="$ROOT" "$PY" "$ROOT/scripts/packet_capture.py" --iface "$CAP_IF_A" --seconds 8 --out "$SHARED/cap_a.json" >"$SHARED/cap_a.log" 2>&1 &
  CAP_A=$!
  ip netns exec ns_b env PYTHONPATH="$ROOT" "$PY" "$ROOT/scripts/packet_capture.py" --iface "$CAP_IF_B" --seconds 8 --out "$SHARED/cap_b.json" >"$SHARED/cap_b.log" 2>&1 &
  CAP_B=$!
fi
ip netns exec ns_a env PYTHONPATH="$ROOT" "$PY" "$ROOT/scripts/nat_experiment.py" \
  --role initiator --shared "$SHARED" --result "$SHARED/a.json" \
  --port "$PORT" --advertise "$A_ADV" --file-size "$SIZE" $STUN_ARG $([[ "$SECOND_PUNCH" == "1" ]] && echo --second-punch) --idle "$IDLE" >"$SHARED/a.log" 2>&1 &
PA=$!
ip netns exec ns_b env PYTHONPATH="$ROOT" "$PY" "$ROOT/scripts/nat_experiment.py" \
  --role responder --shared "$SHARED" --result "$SHARED/b.json" \
  --port "$PORT" --advertise "$B_ADV" --file-size "$SIZE" $STUN_ARG $([[ "$SECOND_PUNCH" == "1" ]] && echo --second-punch) --idle "$IDLE" >"$SHARED/b.log" 2>&1 &
PB=$!
set +e
wait "$PA"; RA=$?
wait "$PB"; RB=$?
set -e
if [[ "${CAPTURE:-0}" == "1" ]]; then wait "${CAP_A:-}" "${CAP_B:-}" || true; fi
if [[ "${TCPDUMP:-0}" == "1" ]]; then kill -INT "${TP_A:-}" "${TP_B:-}" 2>/dev/null || true; wait "${TP_A:-}" "${TP_B:-}" 2>/dev/null || true; fi
if [[ -n "${STUN_PID:-}" ]]; then kill "$STUN_PID" 2>/dev/null || true; fi
echo "initiator_exit=$RA responder_exit=$RB"
if [[ -f "$SHARED/a.json" ]]; then echo "--- a.json"; cat "$SHARED/a.json"; fi
if [[ -f "$SHARED/b.json" ]]; then echo "--- b.json"; cat "$SHARED/b.json"; fi
if [[ "${TCPDUMP:-0}" == "1" && -f "$SHARED/a.pcap" ]]; then
  echo "tcpdump_marker_hits_a=$(tcpdump -A -r "$SHARED/a.pcap" 2>/dev/null | grep -c STRAYPIGEON_FILE_MARKER || true)"
  echo "tcpdump_packets_a=$(tcpdump -r "$SHARED/a.pcap" 2>/dev/null | wc -l)"
fi
if [[ "$RA" -ne 0 || "$RB" -ne 0 ]]; then
  echo "--- a.log"; tail -40 "$SHARED/a.log" || true
  echo "--- b.log"; tail -40 "$SHARED/b.log" || true
fi
exit $(( RA != 0 || RB != 0 ))
' "$MODE"
