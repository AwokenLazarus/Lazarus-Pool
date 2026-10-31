//! End-to-end: gateways blinding their jobs with an `xor_key` of their own, against a real
//! `primed` and a stand-in node, and gateways that do not, whose traffic and books must not move.
//!
//! The tests that need no work run by default: what a gateway is told, and every refusal, which
//! is judged before the hash. The rest grind real diff-1 shares (~2^32 BLAKE2b hashes each, a
//! minute or so across all cores), so they are ignored:
//!
//!     cargo test --release -p primed --test gateway_key_e2e -- --ignored --nocapture --test-threads=1
//!
//! `GATEWAY_KEY_OLD_PRIMED=/path/to/primed` names a build from before this change for the
//! before-and-after test, which is skipped without it.

mod common;

use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use common::*;
use datum_wire::coinbase::{self, TxOut};
use datum_wire::coinbaser::Output;
use datum_wire::crypto::Identity;
use datum_wire::mining::{self, CoinbaseSection, GatewayKey, PowSubmit};
use datum_wire::pow::{self, Hash};
use tides::{Ledger, SOURCE_DATUM};

const REGTEST_BITS: u32 = 0x207f_ffff;
const TIP: Hash = [0x42; 32];
const KEY: [u8; 16] = [0x5a; 16];
const MINER_A: &str = "bc1qk3kxstl02hqnhynwtx0zws7merw6ynut52vtzs";
const MINER_B: &str = "bc1qpxcy2pgedcfccfpw0p9xpzm3edkgajmjl5xe02";

/// `PRIMED_BIN` is read by the harness when a Prime starts, and is process-wide.
static BIN: Mutex<()> = Mutex::new(());

fn unix_now() -> u32 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() as u32
}

fn node() -> MockNode {
    MockNode::start(Chain {
        height: HEIGHT - 1,
        tip: node_hex(&TIP),
        parent: node_hex(&[0x41; 32]),
        bits: REGTEST_BITS,
        next_bits: Some(REGTEST_BITS),
    })
}

fn keyed_config(node: &MockNode) -> String {
    format!("{}\nhouse-loopback = false\ngateway-key = true", node.config(0.2))
}

fn wait_for_tip(p: &Primed) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while stats(p)["node"]["height"].as_u64().unwrap_or(0) == 0 {
        assert!(Instant::now() < deadline, "primed never read the node's tip: {}", stats(p));
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn share_on(slot: u8, prev: Hash, now: u32, key: Option<&[u8; 16]>) -> PowSubmit {
    let mut s = pool_only_share(slot, HEIGHT, prev, REGTEST_BITS, 0, now);
    s.gateway_key = key.map(|k| GatewayKey { clear_bits: 32, key_hash: pow::xor_key_hash(k) });
    s
}

/// A gateway that does not ask is sent what it always was, whether or not this Prime takes keys;
/// one that asks is told only by a Prime that does. The marker is after the 0xFE an old parser
/// stops at, and `ABW_DISABLED` stays set: a Convoy gateway with it clear waits for an assignment
/// that never comes.
#[test]
fn only_a_gateway_that_asks_is_told_and_nothing_else_it_is_sent_changes() {
    let (pool, id) = (Identity::generate(), Identity::generate());
    let n = node();
    let (_on, on_port) = start_primed(&pool, &keyed_config(&n));
    let (_off, off_port) = start_primed(&pool, &format!("{}\nhouse-loopback = false", n.config(0.2)));

    let tail = [mining::CONFIG_FLAG_ABW_DISABLED, mining::END];
    let (_g, plain) = Gateway::connect_convoy_as(on_port, &pool, &id, "stock/1");
    assert_eq!(&plain[plain.len() - 2..], &tail, "a stock Convoy gateway's configure ends where it always did");
    let (_g, off) = Gateway::connect_keyed(off_port, &pool, &id);
    assert_eq!(&off[off.len() - 2..], &tail, "asked, and this Prime has them off: not told");
    assert_eq!(off.len(), plain.len());

    let (_g, told) = Gateway::connect_keyed(on_port, &pool, &id);
    assert_eq!(told.len(), plain.len() + mining::CONFIG_MARKER_GATEWAY_KEY.len());
    let (head, marker) = told.split_at(plain.len());
    assert_eq!(marker, mining::CONFIG_MARKER_GATEWAY_KEY);
    assert_eq!(&head[head.len() - 2..], &tail, "the configure proper is unchanged");
    // apart from the session's own resume token (bytes 14 + script length, 40 long) it is the same message
    let skip = 3 + plain[2] as usize + 8;
    assert_eq!(&head[..skip], &plain[..skip]);
    assert_eq!(&head[skip + mining::RESUME_TOKEN_LEN..], &plain[skip + mining::RESUME_TOKEN_LEN..]);

    // OCEAN lineage has no flag byte to ask with, and gets the v1 layout as ever
    let ocean = Gateway::connect(on_port, &pool, &id);
    drop(ocean);
    let (_g, v1) = Gateway::connect_as(on_port, &pool, &id, "ocean/1");
    assert_eq!(v1[1], 1);
    assert_eq!(&v1[v1.len() - 2..], &[0, mining::END]);
}

/// Every claim that doesn't stand is answered with its reason and earns nothing. These need no
/// work: they are refused before the hash, so a false claim costs Prime no hashing either.
#[test]
fn a_claim_that_does_not_stand_is_refused_and_earns_nothing() {
    let (pool, id) = (Identity::generate(), Identity::generate());
    let n = node();
    let (primed, port) = start_primed(&pool, &keyed_config(&n));
    wait_for_tip(&primed);
    let now = unix_now();
    let refused = (mining::REJECTED, mining::REJECT_BAD_GATEWAY_KEY);

    // this gateway never asked
    let mut stock = Gateway::connect(port, &pool, &id);
    assert_eq!(stock.submit(&share_on(0, TIP, now, Some(&KEY))), refused, "a gateway that was not told");
    drop(stock);

    let (mut gw, _) = Gateway::connect_keyed(port, &pool, &id);
    let good = share_on(0, TIP, now, Some(&KEY));
    for bits in [28u8, 31, 33, 36, 72, 0] {
        let mut s = good.clone();
        s.gateway_key.as_mut().unwrap().clear_bits = bits;
        assert_eq!(gw.submit(&s), refused, "clear_bits {bits} at a share of 32");
    }
    let mut null = good.clone();
    null.gateway_key.as_mut().unwrap().key_hash = pow::xor_key_hash(&[0u8; 16]);
    assert_eq!(gw.submit(&null), refused, "the null key");
    let mut tampered = good.clone();
    tampered.xor_key = Some([0x5b; 16]);
    assert_eq!(gw.submit(&tampered), refused, "a key that is not the committed one");
    let mut bare = good.clone();
    bare.gateway_key = None;
    bare.xor_key = Some(KEY);
    assert_eq!(gw.submit(&bare), refused, "a key with no commitment");
    let mut claim = good.clone();
    claim.flags |= mining::FLAG_BLOCK;
    assert_eq!(gw.submit(&claim), refused, "a block claimed with no key");

    let st = settled_stats(&primed);
    assert_eq!(st["totals"]["gateway_key_refused"], 11, "{st}");
    assert_eq!(st["totals"]["work_accepted"], 0);
    assert_eq!(st["totals"]["shares_accepted"], 0);
    assert_eq!(st["totals"]["block_candidates"], 0);
    assert_eq!(st["blocks"].as_array().map_or(0, Vec::len), 0);
}

/// The whole path on real work. A keyed share with the key unshown is credited and is no block,
/// though its raw hash meets this target. The same key on another block is refused. A tampered key
/// is refused on real work, and that work is not spent: shown properly the share is a block,
/// recorded under the masked hash.
#[test]
#[ignore]
fn a_keyed_share_earns_its_credit_and_a_shown_block_is_recorded_by_its_real_hash() {
    let (pool, id) = (Identity::generate(), Identity::generate());
    let n = node();
    let (primed, port) = start_primed(&pool, &keyed_config(&n));
    wait_for_tip(&primed);
    let now = unix_now();
    let (mut gw, cfg) = Gateway::connect_keyed(port, &pool, &id);
    assert!(cfg.ends_with(mining::CONFIG_MARKER_GATEWAY_KEY));

    let mut share = share_on(1, TIP, now, Some(&KEY));
    grind_diff1(&mut share);
    let (status, code) = gw.submit(&share);
    assert!(status == mining::ACCEPTED || status == mining::ACCEPTED_TENTATIVELY, "0x{status:02x} code {code}");
    let st = settled_stats(&primed);
    assert_eq!(st["totals"]["work_accepted"], 1, "credited as any diff-1 share: {st}");
    assert_eq!(st["totals"]["gateway_key_shares"], 1);
    assert_eq!(st["totals"]["gateway_key_work"], 1);
    assert_eq!(st["totals"]["block_candidates"], 0, "no key, no block, though the raw hash meets this target");
    assert_eq!(st["blocks"].as_array().map_or(0, Vec::len), 0);

    assert_eq!(gw.submit(&share), (mining::REJECTED, mining::REJECT_DUPLICATE_WORK), "the same work again");
    let mut late = share.clone();
    late.xor_key = Some(KEY);
    assert_eq!(gw.submit(&late), (mining::REJECTED, mining::REJECT_DUPLICATE_WORK), "the key can't be shown later");

    // A second job on another parent under the same key. Refused before the hash, so unground.
    let other_parent = share_on(2, [0x99; 32], now, Some(&KEY));
    assert_eq!(gw.submit(&other_parent), (mining::REJECTED, mining::REJECT_BAD_GATEWAY_KEY), "a key kept across blocks");

    // A fresh key and fresh work: a tampered reveal is refused, then the real one is taken.
    let key2 = [0x6b; 16];
    let mut win = share_on(3, TIP, now, Some(&key2));
    grind_diff1(&mut win);
    win.flags |= mining::FLAG_BLOCK;
    let mut tampered = win.clone();
    tampered.xor_key = Some([0x6c; 16]);
    assert_eq!(gw.submit(&tampered), (mining::REJECTED, mining::REJECT_BAD_GATEWAY_KEY), "tampered key on real work");
    let st = settled_stats(&primed);
    assert_eq!(st["totals"]["work_accepted"], 1, "and it earned nothing");
    assert_eq!(st["blocks"].as_array().map_or(0, Vec::len), 0);

    win.xor_key = Some(key2);
    let (status, code) = gw.submit(&win);
    assert!(status == mining::ACCEPTED || status == mining::ACCEPTED_TENTATIVELY, "0x{status:02x} code {code}");
    let st = settled_stats(&primed);
    assert_eq!(st["totals"]["work_accepted"], 2);
    assert_eq!(st["totals"]["gateway_key_blocks"], 1);
    let blocks = st["blocks"].as_array().expect("a block record");
    assert_eq!(blocks.len(), 1, "{st}");

    // what a node would call it: the masked hash, not the raw one
    let raw = {
        let mut slot = datum_wire::verify::JobSlot::default();
        slot.absorb(&win).unwrap();
        let jw = datum_wire::verify::job_work_for(&slot, &win, false).unwrap();
        let b2 = win.blake2b.as_ref().unwrap();
        jw.hash(&b2.nonce, &b2.ntime)
    };
    let masked = pow::masked_hash_le(&raw, &key2, 32);
    assert_ne!(masked, raw);
    assert_eq!(blocks[0]["hash"].as_str().unwrap(), node_hex(&masked), "{st}");
}

fn seed(dir: &std::path::Path, t: u32) {
    let mut l = Ledger::open(dir).unwrap();
    l.credit(MINER_A, 600_000, HEIGHT, t, SOURCE_DATUM).unwrap();
    l.credit(MINER_B, 400_000, HEIGHT, t, SOURCE_DATUM).unwrap();
    l.persist_window().unwrap();
}

/// Drop what is about the run and not about the money. Everything else in `stats.json` has to match.
fn figures(v: &mut serde_json::Value) {
    const VOLATILE: &[&str] = &[
        "ts", "uptime_s", "started_ts", "connected_ts", "connected_s", "last_share_ts", "last_share_s", "remote",
        "hashrate_ghs", "tip_age_s", "seen_ts", "coinbaser_max_ms", "coinbaser_base_builds", "last_seen", "seen_shares",
        "gateway_key", "gateway_key_shares", "gateway_key_work", "gateway_key_blocks", "gateway_key_false_blocks",
        "gateway_key_refused", "template_checks", "template_checks_failed", "coinbaser_slow", "hashrate", "port",
    ];
    match v {
        serde_json::Value::Object(o) => {
            o.retain(|k, _| !VOLATILE.contains(&k.as_str()));
            o.values_mut().for_each(figures);
        }
        serde_json::Value::Array(a) => a.iter_mut().for_each(figures),
        _ => {}
    }
}

fn split_share(outputs: &[Output], slot: u8, now: u32) -> PowSubmit {
    let outs: Vec<TxOut> = outputs.iter().map(|o| TxOut { value: o.sats, script: o.script.clone() }).collect();
    let mut s = pool_only_share(slot, HEIGHT, TIP, REGTEST_BITS, 0, now);
    let (cb, tidx, split_at) = coinbase::build(HEIGHT, b"Lazarus", &outs, 0);
    s.coinbase = Some(CoinbaseSection {
        coinbase_id: 0,
        coinb1: cb[..split_at].to_vec(),
        coinb2: cb[split_at + coinbase::EXTRANONCE_SLOT..].to_vec(),
    });
    let job = s.job.as_mut().unwrap();
    job.target_byte_index = tidx as u16;
    s
}

/// Run one build through the same shares on the same window: a split, a pool-only block, the
/// replay of it and a refused one. Returns its coinbaser, receipts and whole books.
fn run_no_key_gateway(
    bin: Option<&str>,
    pool: &Identity,
    id: &Identity,
    node: &MockNode,
    window_ts: u32,
    shares: &mut Option<(PowSubmit, PowSubmit)>,
    now: u32,
) -> serde_json::Value {
    let _g = BIN.lock().unwrap_or_else(|e| e.into_inner());
    match bin {
        Some(b) => std::env::set_var("PRIMED_BIN", b),
        None => std::env::remove_var("PRIMED_BIN"),
    }
    let cfg = format!("{}\nhouse-loopback = false\nwindow-min-work = 100000000\nmin-payout = 546", node.config(0.2));
    let (primed, port) = start_primed_seeded(pool, &cfg, |d| seed(d, window_ts));
    wait_for_tip(&primed);
    let (mut gw, configure) = Gateway::connect_convoy_as(port, pool, id, "stock/1");
    let (cb_id, outputs) = gw.request_coinbaser_outputs(VALUE, &TIP);
    let (split, pool_only) = shares.get_or_insert_with(|| {
        let mut split = split_share(&outputs, 0, now);
        split.job.as_mut().unwrap().coinbaser_id = cb_id;
        grind_diff1(&mut split);
        let mut pool_only = pool_only_share(1, HEIGHT, TIP, REGTEST_BITS, 0, now);
        grind_diff1(&mut pool_only);
        (split, pool_only)
    });
    let mut receipts = vec![];
    receipts.push(gw.submit(split));
    receipts.push(gw.submit(pool_only));
    receipts.push(gw.submit(split));
    let mut high = pool_only.clone();
    high.nonce32 ^= 1;
    high.blake2b.as_mut().unwrap().nonce[0] ^= 1;
    receipts.push(gw.submit(&high));
    let mut st = settled_stats(&primed);
    figures(&mut st);
    // the configure, apart from the session's random resume token
    let mut cfg_bytes = configure;
    let skip = 3 + cfg_bytes[2] as usize + 8;
    cfg_bytes.drain(skip..skip + mining::RESUME_TOKEN_LEN);
    let mut blocks: Vec<serde_json::Value> = std::fs::read_to_string(primed.dir.join("blocks.jsonl"))
        .unwrap_or_default()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    blocks.iter_mut().for_each(figures);
    serde_json::json!({
        "configure": hex::encode(cfg_bytes),
        "coinbaser": outputs.iter().map(|o| (o.sats, hex::encode(&o.script))).collect::<Vec<_>>(),
        "receipts": receipts,
        "stats": st,
        "blocks_log": blocks,
    })
}

/// A gateway with no key, before and after: same configure, same coinbaser, same receipts and the
/// same books, sat for sat, on the same shares. Needs `GATEWAY_KEY_OLD_PRIMED`.
#[test]
#[ignore]
fn a_gateway_with_no_key_gets_the_same_money_and_credit_as_before() {
    let Ok(old) = std::env::var("GATEWAY_KEY_OLD_PRIMED") else {
        eprintln!("skipped: GATEWAY_KEY_OLD_PRIMED is not set");
        return;
    };
    let (pool, id) = (Identity::generate(), Identity::generate());
    let n = node();
    let now = unix_now();
    let mut shares = None;
    let before = run_no_key_gateway(Some(&old), &pool, &id, &n, now - 600, &mut shares, now);
    let after = run_no_key_gateway(None, &pool, &id, &n, now - 600, &mut shares, now);
    std::env::remove_var("PRIMED_BIN");
    eprintln!("receipts {} coinbaser {}", before["receipts"], before["coinbaser"]);
    eprintln!("work {} blocks {}", before["stats"]["totals"]["work_accepted"], before["stats"]["blocks"]);
    assert_eq!(before["stats"]["totals"]["work_accepted"], 2, "both ground shares credited: {before}");
    assert_eq!(before["stats"]["blocks"].as_array().map_or(0, Vec::len), 2, "both found: {before}");
    let mut differ = vec![];
    differences("", &before, &after, &mut differ);
    assert!(differ.is_empty(), "before and after differ at: {differ:#?}");
}

fn differences(at: &str, a: &serde_json::Value, b: &serde_json::Value, out: &mut Vec<String>) {
    use serde_json::Value::{Array, Object};
    match (a, b) {
        (Object(x), Object(y)) => {
            for k in x.keys().chain(y.keys().filter(|k| !x.contains_key(*k))) {
                let none = serde_json::Value::Null;
                differences(&format!("{at}.{k}"), x.get(k).unwrap_or(&none), y.get(k).unwrap_or(&none), out);
            }
        }
        (Array(x), Array(y)) if x.len() == y.len() => {
            for (i, (p, q)) in x.iter().zip(y).enumerate() {
                differences(&format!("{at}[{i}]"), p, q, out);
            }
        }
        _ if a != b => out.push(format!("{at}: {a} vs {b}")),
        _ => {}
    }
}

/// Keyed and unkeyed gateways on one pool, one block each, real work: each is credited, and a
/// keyed gateway's find pays and books as anyone's.
#[test]
#[ignore]
fn gateways_with_and_without_a_key_share_one_window() {
    let (pool, stock_id, keyed_id) = (Identity::generate(), Identity::generate(), Identity::generate());
    let n = node();
    let (primed, port) = start_primed(&pool, &keyed_config(&n));
    wait_for_tip(&primed);
    let now = unix_now();
    let mut stock = Gateway::connect(port, &pool, &stock_id);
    let (mut keyed, _) = Gateway::connect_keyed(port, &pool, &keyed_id);

    let mut plain = share_on(4, TIP, now, None);
    grind_diff1(&mut plain);
    let mut masked = share_on(4, TIP, now, Some(&KEY));
    grind_diff1(&mut masked);
    masked.flags |= mining::FLAG_BLOCK;
    masked.xor_key = Some(KEY);

    let (s1, c1) = stock.submit(&plain);
    let (s2, c2) = keyed.submit(&masked);
    for (s, c) in [(s1, c1), (s2, c2)] {
        assert!(s == mining::ACCEPTED || s == mining::ACCEPTED_TENTATIVELY, "0x{s:02x} code {c}");
    }
    let st = settled_stats(&primed);
    assert_eq!(st["totals"]["work_accepted"], 2, "{st}");
    assert_eq!(st["totals"]["gateway_key_work"], 1, "only the keyed gateway's");
    let blocks = st["blocks"].as_array().unwrap();
    assert_eq!(blocks.len(), 2, "{st}");
    assert_eq!(blocks[0]["kind"], blocks[1]["kind"], "the same coinbase books the same way, key or none");
    assert_eq!(blocks[0]["coinbase_value"], blocks[1]["coinbase_value"]);
    let rows = st["clients"].as_array().unwrap();
    let row = |g: &Identity| rows.iter().find(|r| r["gateway"] == hex::encode(&g.sign_pk()[..8])).unwrap().clone();
    assert_eq!(row(&stock_id)["work"], row(&keyed_id)["work"]);
    assert!(row(&stock_id).get("gateway_key_shares").is_none(), "a stock gateway's row is the row it always was");
    assert_eq!(row(&keyed_id)["gateway_key_shares"], 1);
}
