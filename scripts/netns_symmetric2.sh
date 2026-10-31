#!/usr/bin/env bash
# True symmetric-NAT / APDM testbed (requires root).
# Two routers, one public bridge, two single-homed STUN namespaces.
# Expectation: both Sessions classify apdm and exit E_SYMMETRIC_NAT.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
SHARED="${SHARED:-/tmp/straypigeon-symmetric-$(date +%s)-$$}"
PORT="${PORT:-40000}"
SIZE="${SIZE:-4194304}"
mkdir -p "$SHARED"
if [[ ! -f "$SHARED/psk.bin" ]]; then
  head -c 32 /dev/urandom > "$SHARED/psk.bin"
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root: sudo bash scripts/netns_symmetric2.sh" >&2
  exit 2
fi

for ns in ns_a ns_b ns_r1 ns_r2 ns_s1 ns_s2; do
  ip netns del "$ns" 2>/dev/null || true
done
ip link del br_pub 2>/dev/null || true

for ns in ns_a ns_b ns_r1 ns_r2 ns_s1 ns_s2; do
  ip netns add "$ns"
  ip -n "$ns" link set lo up
done

ip link add br_pub type bridge
ip link set br_pub up

ip link add r1pub type veth peer name pub1
ip link set r1pub netns ns_r1
ip link set pub1 master br_pub
ip link set pub1 up
ip link add r2pub type veth peer name pub2
ip link set r2pub netns ns_r2
ip link set pub2 master br_pub
ip link set pub2 up
ip link add s1pub type veth peer name pub3
ip link set s1pub netns ns_s1
ip link set pub3 master br_pub
ip link set pub3 up
ip link add s2pub type veth peer name pub4
ip link set s2pub netns ns_s2
ip link set pub4 master br_pub
ip link set pub4 up

ip link add a0 type veth peer name ra
ip link set a0 netns ns_a
ip link set ra netns ns_r1
ip link add b0 type veth peer name rb
ip link set b0 netns ns_b
ip link set rb netns ns_r2

ip -n ns_a addr add 192.168.1.2/24 dev a0
ip -n ns_a link set a0 up
ip -n ns_a route add default via 192.168.1.1
ip -n ns_b addr add 10.0.1.2/24 dev b0
ip -n ns_b link set b0 up
ip -n ns_b route add default via 10.0.1.1

ip -n ns_r1 addr add 192.168.1.1/24 dev ra
ip -n ns_r1 addr add 10.0.0.1/24 dev r1pub
ip -n ns_r1 link set ra up
ip -n ns_r1 link set r1pub up
ip -n ns_r2 addr add 10.0.1.1/24 dev rb
ip -n ns_r2 addr add 10.0.0.2/24 dev r2pub
ip -n ns_r2 link set rb up
ip -n ns_r2 link set r2pub up

ip -n ns_s1 addr add 10.0.0.3/24 dev s1pub
ip -n ns_s1 link set s1pub up
ip -n ns_s2 addr add 10.0.0.4/24 dev s2pub
ip -n ns_s2 link set s2pub up

ip netns exec ns_r1 sh -c "echo 1 > /proc/sys/net/ipv4/ip_forward"
ip netns exec ns_r2 sh -c "echo 1 > /proc/sys/net/ipv4/ip_forward"
ip netns exec ns_r1 iptables -t nat -A POSTROUTING -s 192.168.1.0/24 -o r1pub -j MASQUERADE --random
ip netns exec ns_r2 iptables -t nat -A POSTROUTING -s 10.0.1.0/24 -o r2pub -j MASQUERADE --random

ip netns exec ns_s1 env PYTHONPATH="$ROOT" "$PY" "$ROOT/scripts/stun_probe_server.py" --port 3478 &
P1=$!
ip netns exec ns_s2 env PYTHONPATH="$ROOT" "$PY" "$ROOT/scripts/stun_probe_server.py" --port 3478 &
P2=$!
sleep 0.5

STUN_ARG="--stun 10.0.0.3:3478,10.0.0.4:3478"
ip netns exec ns_a env PYTHONPATH="$ROOT" "$PY" "$ROOT/scripts/nat_experiment.py" \
  --role initiator --shared "$SHARED" --result "$SHARED/a.json" \
  --port "$PORT" --advertise 192.168.1.2 --file-size "$SIZE" $STUN_ARG \
  >"$SHARED/a.log" 2>&1 &
PA=$!
ip netns exec ns_b env PYTHONPATH="$ROOT" "$PY" "$ROOT/scripts/nat_experiment.py" \
  --role responder --shared "$SHARED" --result "$SHARED/b.json" \
  --port "$PORT" --advertise 10.0.1.2 --file-size "$SIZE" $STUN_ARG \
  >"$SHARED/b.log" 2>&1 &
PB=$!
set +e
wait "$PA"; RA=$?
wait "$PB"; RB=$?
set -e
kill "$P1" "$P2" 2>/dev/null || true
echo "mode=symmetric2 shared=$SHARED"
echo "initiator_exit=$RA responder_exit=$RB"
for f in "$SHARED/a.log" "$SHARED/b.log"; do
  echo "--- $f"; tail -30 "$f" || true
done
for f in "$SHARED/a.json" "$SHARED/b.json"; do
  [[ -f "$f" ]] && { echo "--- $f"; cat "$f"; }
done
exit $(( RA != 0 || RB != 0 ))
