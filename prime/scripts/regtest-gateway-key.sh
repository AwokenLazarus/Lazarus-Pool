#!/usr/bin/env bash
# End to end on regtest with real Knots, real datum_gateway builds and real hashing: gateways with
# and without their own xor_key on one primed, then a restart, then the same data dir on an old build.
#
#   regtest-gateway-key.sh mixed      a keyed gateway and a stock one on one pool, restart, rollback
#   regtest-gateway-key.sh stock      a stock gateway alone; run on OLD and on NEW and compare
#   regtest-gateway-key.sh load       time the start-up read of block headers for shown keys
#
# Environment (all required unless a default is shown):
#   BIN_DIR        holds bitcoind, bitcoin-cli, stratum-grind, tee.py, lib/
#   PRIMED_NEW     primed under test
#   PRIMED_OLD     primed from before the change
#   GW_KEYED       datum_gateway that asks for a key, with the lab overlay (DATUM_SOLO_BLIND_EXTRA_BITS)
#   GW_STOCK       datum_gateway that does not ask
#   PROBE_BIN      the gateway_key_e2e test binary (cargo test --no-run), for probe_a_running_primed_with_a_key
#   OUT            where logs and results go
#   BASE_PORT      default 35600. Uses 30 ports from there
#   EXTRA_BITS     default 1. A share is a pretend win one time in 2^EXTRA_BITS, so that non-wins exist
#                  on regtest, where every share with its top 32 bits clear meets the real target
#   RUN_SECS       default 1500. How long each phase may wait for its shares
set -u
MODE=${1:?mixed or stock}
: "${BIN_DIR:?}" "${PRIMED_NEW:?}" "${OUT:?}" "${GW_STOCK:?}"
BASE_PORT=${BASE_PORT:-35600}
EXTRA_BITS=${EXTRA_BITS:-1}
RUN_SECS=${RUN_SECS:-1500}
HERE=$(cd "$(dirname "$0")" && pwd)
export LD_LIBRARY_PATH=$BIN_DIR/lib
mkdir -p "$OUT"
D=$(mktemp -d /tmp/gwkey-XXXXXX)
PIDS=()
cleanup() {
	for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done
	sleep 3
	for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill -9 "$p" 2>/dev/null; done
	"$BIN_DIR/bitcoin-cli" -datadir="$D/node" stop >/dev/null 2>&1
	sleep 2
	pkill -f "datadir=$D" 2>/dev/null
	rm -rf "$D"
}
trap cleanup EXIT
say() { echo "$(date -u +%T) $*" | tee -a "$OUT/summary.log"; }
die() { say "FAIL: $*"; exit 1; }

RPC=$((BASE_PORT + 1)) P2P=$((BASE_PORT + 2)) PRIME=$((BASE_PORT + 3)) STATS=$((BASE_PORT + 4))
ST_K=$((BASE_PORT + 5)) API_K=$((BASE_PORT + 6)) TEE_K=$((BASE_PORT + 7))
ST_S=$((BASE_PORT + 8)) API_S=$((BASE_PORT + 9)) TEE_S=$((BASE_PORT + 10))

mkdir -p "$D/node" "$D/prime"
cat > "$D/node/bitcoin.conf" <<EOF
regtest=1
server=1
listenonion=0
discover=0
dnsseed=0
fixedseeds=0
fallbackfee=0.0002
txindex=1
blake2b_headline=Lazarus
[regtest]
listen=1
bind=127.0.0.1
port=$P2P
rpcbind=127.0.0.1
rpcallowip=127.0.0.1
rpcport=$RPC
testactivationheight=blake2b@101
maxtipage=315360000
EOF
"$BIN_DIR/bitcoind" -datadir="$D/node" -daemon > /dev/null || die "no node"
cli() { "$BIN_DIR/bitcoin-cli" -datadir="$D/node" "$@"; }
for _ in $(seq 1 60); do cli getblockcount > /dev/null 2>&1 && break; sleep 0.5; done
say "knots $("$BIN_DIR/bitcoind" --version | head -1)"
cli createwallet w > /dev/null
A_K=$(cli -rpcwallet=w getnewaddress "" bech32)
A_S=$(cli -rpcwallet=w getnewaddress "" bech32)
PAY=$(cli -rpcwallet=w getnewaddress "" bech32)
cli -rpcwallet=w generatetoaddress 220 "$PAY" > /dev/null
say "height $(cli getblockcount) keyed miner $A_K stock miner $A_S pool $PAY"

NOW=$(date +%s)
echo "{\"credits\":[{\"ts\":$((NOW - 120)),\"identity\":\"$PAY\",\"work\":1000000000,\"source\":2}]}" > "$D/prime/seed.json"
write_prime_toml() { # gateway-key value
	cat > "$D/prime/prime.toml" <<EOF
listen = "127.0.0.1:$PRIME"
stats-listen = "127.0.0.1:$STATS"
advertise-address = "127.0.0.1:$PRIME"
data-dir = "$D/prime/data"
motd = "Lazarus"
headline = "Lazarus"
payout-address = "$PAY"
coinbase-tag = "Lazarus"
prime-id = 134
network = "regtest"
window = 8
window-min-work = 50000000
min-payout = 546
min-diff = 1
fee-bps = 0
house-loopback = false
rpc = "http://127.0.0.1:$RPC"
rpc-cookie = "$D/node/regtest/.cookie"
poll = 0.5
EOF
	[ -n "${1:-}" ] && echo "gateway-key = $1" >> "$D/prime/prime.toml"
}
start_prime() { # binary, log, gateway-key value or empty
	write_prime_toml "${3:-}"
	RUST_LOG=info,primed::session=debug "$1" -c "$D/prime/prime.toml" run >> "$2" 2>&1 &
	PRIME_PID=$!
	PIDS+=("$PRIME_PID")
	for _ in $(seq 1 60); do curl -fsS -m 1 "http://127.0.0.1:$STATS/healthz" -o /dev/null 2>/dev/null && return 0; sleep 0.5; done
	die "primed did not start"
}
stop_prime() {
	kill -TERM "$PRIME_PID" 2>/dev/null
	wait "$PRIME_PID" 2>/dev/null
}
stats() { curl -fsS -m 5 "http://127.0.0.1:$STATS/stats.json"; }
jq_py() { python3 -I -c "import json,sys;d=json.load(sys.stdin);print($1)"; }

gateway_conf() { # stratum port, api port, tag
	cat <<EOF
{"bitcoind":{"rpccookiefile":"$D/node/regtest/.cookie","rpcurl":"http://127.0.0.1:$RPC","work_update_seconds":5,"notify_fallback":true},
 "stratum":{"listen_addr":"127.0.0.1","listen_port":$1,"vardiff_min":1,"fingerprint_miners":false},
 "mining":{"pool_address":"bc1qt5praystcdle0nq04e3h02yjszha82uzhww85x6972lcy40k4eyqz9jfaq","coinbase_tag_primary":"Lazarus","coinbase_tag_secondary":"$3"},
 "api":{"listen_addr":"127.0.0.1","listen_port":$2,"admin_password":""},
 "logger":{"log_to_console":true,"log_to_file":false,"log_level_console":1},
 "datum":{"pool_host":"127.0.0.1","pool_port":$PRIME,"pool_pubkey":"$PUB","pool_pass_workers":true,"pool_pass_full_users":true,"pooled_mining_only":true,"protocol_global_timeout":60}}
EOF
}
wait_port() { for _ in $(seq 1 120); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0; sleep 0.5; done; die "port $1 never opened"; }
start_gateway() { # binary, conf name, stratum port, api port, tag, log, env
	gateway_conf "$3" "$4" "$5" > "$D/$2.json"
	env ${7:-X=1} "$1" -c "$D/$2.json" >> "$6" 2>&1 &
	PIDS+=("$!")
	wait_port "$3"
}
start_miner() { # port, user, cores, log
	taskset -c "$3" nice -n 15 "$BIN_DIR/stratum-grind" --host 127.0.0.1 --port "$1" --user "$2" >> "$4" 2>&1 &
	PIDS+=("$!")
}
spend() { cli -rpcwallet=w sendtoaddress "$PAY" 0.5 > /dev/null 2>&1 || true; }
# wait until python expression $1 over stats d holds, or RUN_SECS. Keep a transaction in the mempool.
wait_for() {
	local t=$SECONDS
	while [ $((SECONDS - t)) -lt "$RUN_SECS" ]; do
		if stats 2>/dev/null | python3 -I -c "import json,sys;d=json.load(sys.stdin);t=d['totals'];sys.exit(0 if ($1) else 1)" 2>/dev/null; then return 0; fi
		spend
		sleep 5
	done
	return 1
}

write_prime_toml
mkdir -p "$D/prime/data"
PUB=$("$PRIMED_NEW" -c "$D/prime/prime.toml" pubkey) || die "no pubkey"
"$PRIMED_NEW" -c "$D/prime/prime.toml" import-ledger "$D/prime/seed.json" >> "$OUT/primed.log" 2>&1 || die "no import"

finish_results() {
	stats > "$OUT/stats-$1.json" 2>/dev/null
	cp "$D/prime/data/blocks.jsonl" "$OUT/blocks-$1.jsonl" 2>/dev/null
	cp "$D/prime/data/window.json" "$OUT/window-$1.json" 2>/dev/null
}

# probe <key hex> <expected reject code>: a keyed share on the next block, under that key
probe() {
	local tip height
	tip=$(cli getbestblockhash); height=$(( $(cli getblockcount) + 1 ))
	PMK_PROBE="$PRIME $D/prime/data $tip $height 207fffff $1 $2" "$PROBE_BIN" --ignored --exact probe_a_running_primed_with_a_key --nocapture 2>&1 | grep -E "^PROBE|panicked|test result" | tee -a "$OUT/summary.log"
	return "${PIPESTATUS[0]}"
}
keyed_block() { # height hash key of the keyed gateway's first block
	stats | python3 -I -c "import json,sys,subprocess
d=json.load(sys.stdin)
b=min((b for b in d['blocks'] if b['gateway']=='$KEYED_GW'), key=lambda b: b['height'])
h=subprocess.run(['$BIN_DIR/bitcoin-cli','-datadir=$D/node','getblockheader',b['hash'],'false'],capture_output=True,text=True).stdout.strip()
print(b['height'], b['hash'], h[224:256])"
}

if [ "$MODE" = load ]; then
	cli -rpcwallet=w generatetoaddress 2100 "$PAY" > /dev/null
	say "load: height $(cli getblockcount), every block since 101 a v2 header"
	T0=$(date +%s%N)
	start_prime "$PRIMED_NEW" "$OUT/primed.log" true
	T1=$(date +%s%N)
	for _ in $(seq 1 120); do grep -q "xor_keys: read" "$OUT/primed.log" && break; sleep 0.5; done
	say "load: primed answered on its stats port $(( (T1 - T0) / 1000000 )) ms after start"
	say "load: $(grep -o 'xor_keys: read.*' "$OUT/primed.log" | head -1)"
	grep -q "xor_keys: read 2016 block header" "$OUT/primed.log" || die "the start-up read did not read 2016 headers"
	exit 0
fi

if [ "$MODE" = stock ]; then
	start_prime "$PRIMED_NEW" "$OUT/primed.log" ""
	start_gateway "$GW_STOCK" gw "$ST_S" "$API_S" stock "$OUT/gw-stock.log"
	start_miner "$ST_S" "$A_S" 0-7 "$OUT/miner-stock.log"
	wait_for "t['shares_accepted'] >= ${STOCK_SHARES:-4}" || die "stock gateway: not enough shares"
	sleep 8
	finish_results stock
	python3 -I "$HERE/regtest-gateway-key-check.py" stock "$OUT" "$BIN_DIR/bitcoin-cli" "$D/node" "$A_S"
	rc=$?
	say "stock check rc=$rc"
	exit $rc
fi

: "${GW_KEYED:?}" "${PRIMED_OLD:?}"
# 1. mixed: one pool, a gateway that asks and a stock one
start_prime "$PRIMED_NEW" "$OUT/primed.log" true
python3 -I "$BIN_DIR/tee.py" "$TEE_K" "$ST_K" "$OUT/lines-keyed.log" &
PIDS+=("$!")
start_gateway "$GW_KEYED" gw-k "$ST_K" "$API_K" keyed "$OUT/gw-keyed.log" "DATUM_SOLO_BLIND_EXTRA_BITS=$EXTRA_BITS"
start_gateway "$GW_STOCK" gw-s "$ST_S" "$API_S" stock "$OUT/gw-stock.log"
wait_port "$TEE_K"
start_miner "$TEE_K" "$A_K" 0-5 "$OUT/miner-keyed.log"
start_miner "$ST_S" "$A_S" 6-8 "$OUT/miner-stock.log"
say "mixed: mining"
# every job's disclosure while the gateway still holds it, for the key checks
( while :; do python3 -I "$HERE/regtest-gateway-key-check.py" jobs "$OUT" "$OUT/lines-keyed.log" "$API_K" >> "$OUT/jobs.jsonl" 2>/dev/null; sleep 4; done ) &
PIDS+=("$!")
H0=$(cli getblockcount)
# a keyed gateway's blocks and non-wins, a stock block, and at least one template check
wait_for "t['gateway_key_blocks'] >= 1 and t['gateway_key_shares'] > t['gateway_key_blocks'] and t['shares_accepted'] > t['gateway_key_shares'] and t['template_checks'] >= 1" \
	|| { finish_results mixed; die "mixed: never saw a keyed block, a keyed non-win, a stock share and a template check: $(stats)"; }
KEYED_GW=$(stats | jq_py "[c['gateway'] for c in d['clients'] if c.get('gateway_key_shares')][0]")
# a keyed job through the node's proposal check: the header has clear_bits and no key yet
T0=$SECONDS
until grep -q "$KEYED_GW: template at height .* passes" "$OUT/primed.log" || [ $((SECONDS - T0)) -gt "$RUN_SECS" ]; do spend; sleep 5; done
# jobs disclosed at a height past the keyed gateway's first block, so key reuse can be looked for
FIRST_KEYED=$(stats | jq_py "min(b['height'] for b in d['blocks'] if b['gateway'] == '$KEYED_GW')")
T0=$SECONDS
until python3 -I -c "import json,sys
for l in open('$OUT/jobs.jsonl'):
    r = json.loads(l)
    if r['status'] == 200 and json.loads(r['api'])['height'] > $FIRST_KEYED: sys.exit(0)
sys.exit(1)" 2>/dev/null || [ $((SECONDS - T0)) -gt "$RUN_SECS" ]; do spend; sleep 5; done
sleep 8
finish_results mixed
say "mixed: height $H0 -> $(cli getblockcount)"
python3 -I "$HERE/regtest-gateway-key-check.py" mixed "$OUT" "$BIN_DIR/bitcoin-cli" "$D/node" "$A_K" "$A_S"
rc=$?
say "mixed check rc=$rc"
[ $rc = 0 ] || exit $rc

# 2. restart: books survive, the gateway reconnects and is told again, keyed shares carry on
BEFORE=$(stats | jq_py "d['window']['work']")
stop_prime
say "restart: window work $BEFORE"
start_prime "$PRIMED_NEW" "$OUT/primed.log" true
wait_for "t['gateway_key_shares'] >= 1 and t['shares_accepted'] > t['gateway_key_shares']" \
	|| { finish_results restart; die "after a restart the keyed and stock gateways did not both get shares in: $(stats)"; }
sleep 3
finish_results restart
AFTER=$(stats | jq_py "d['window']['work']")
say "restart: window work $AFTER, refused $(stats | jq_py "d['totals']['gateway_key_refused']")"
[ "$AFTER" -gt "$BEFORE" ] || die "restart lost work ($BEFORE -> $AFTER)"
[ "$(grep -c 'DATUM Pool gateway keys: *yes' "$OUT/gw-keyed.log")" -ge 2 ] || die "the keyed gateway was not told again after the restart"
read -r KH KHASH KKEY <<< "$(keyed_block)"
say "restart: the keyed block at $KH showed key $KKEY; a share under it on the next block, after the restart:"
probe "$KKEY" 45 || die "a key shown before the restart was taken after it"

# 2b. reorg: the keyed block leaves the chain. Its key was broadcast all the same and stays refused,
# from the book on disk, after another restart, at heights the new branch reaches.
cli invalidateblock "$KHASH"
cli -rpcwallet=w generatetoaddress $(( $(cli getblockcount) - KH + 3 + 1 )) "$PAY" > /dev/null
[ "$(cli getblockheader "$KHASH" | jq_py "d['confirmations']")" = -1 ] || die "block $KHASH is still in the chain"
say "reorg: block $KH ($KHASH) invalidated, the chain is at $(cli getblockcount) on another branch"
stop_prime
start_prime "$PRIMED_NEW" "$OUT/primed.log" true
for _ in $(seq 1 60); do [ "$(stats | jq_py "d['node']['height']")" = "$(cli getblockcount)" ] && break; sleep 0.5; done
say "reorg: after a restart primed holds $(stats | jq_py "d['pool']['revealed_keys']") shown key(s)"
probe "$KKEY" 45 || die "an orphaned block's key was taken after the reorg"
probe "00112233445566778899aabbccddeeff" 27 || die "a key never shown is not judged as any share (this probe pays a script the pool never issued)"
cli reconsiderblock "$KHASH"
wait_for "t['gateway_key_shares'] >= 1 and t['shares_accepted'] >= 1" || die "after the reorg the gateways did not get shares in"
finish_results reorg
BEFORE=$(stats | jq_py "d['window']['work']")

# 3. rollback: the data dir on the old build. The keyed gateway must fall back to the null key.
stop_prime
: > "$OUT/primed-old.log"
start_prime "$PRIMED_OLD" "$OUT/primed-old.log" ""
: > "$OUT/after-rollback.marker"
wait_for "t['shares_accepted'] >= 2" || { finish_results rollback; die "after a rollback the old build took no shares: $(stats)"; }
sleep 3
finish_results rollback
AFTER=$(stats | jq_py "d['window']['work']")
say "rollback: window work $BEFORE -> $AFTER on the old build; malformed lines $(grep -c 'malformed mining message' "$OUT/primed-old.log")"
[ "$AFTER" -gt "$BEFORE" ] || die "rollback lost work ($BEFORE -> $AFTER)"
[ "$(grep -c 'malformed mining message' "$OUT/primed-old.log")" = 0 ] || die "old build was sent keyed sections"
grep -q 'DATUM Pool gateway keys: *no' "$OUT/gw-keyed.log" || die "the keyed gateway never said it was off keys after the rollback"
say "PASS"
