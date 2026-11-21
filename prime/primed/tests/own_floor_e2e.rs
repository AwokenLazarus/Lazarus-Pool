//! End-to-end: `own-min-payout`, against a real `primed` and a stand-in node.
//!
//! A miner too small to clear `min-payout` is in no coinbase, the one of a block it finds
//! included. With the key set, the split handed to its own gateway pays it what it is owed from
//! `own-min-payout` up; every other gateway's split is the one it always was, and so is every
//! split with the key unset.
//!
//! The first test needs no work and runs by default. The second finds two blocks on the same
//! coinbaser and so grinds two real diff-1 shares (about a minute each across all cores):
//!
//!     cargo test --release -p primed --test own_floor_e2e -- --ignored --nocapture

mod common;

use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use common::*;
use datum_wire::coinbase::{self, TxOut};
use datum_wire::coinbaser::Output;
use datum_wire::crypto::Identity;
use datum_wire::mining::{self, CoinbaseSection, PowSubmit};
use datum_wire::pow::Hash;
use datum_wire::verify::{self, JobSlot, Policy};
use tides::{BlockLog, BlockRecord, Ledger, SOURCE_DATUM};

const REGTEST_BITS: u32 = 0x207f_ffff;
const TIP: Hash = [0x42; 32];
/// A miner with most of the window, and one with a thousandth of it who runs a gateway.
const BIG: &str = "bc1qpxcy2pgedcfccfpw0p9xpzm3edkgajmjl5xe02";
const BIG_SCRIPT: &str = "001409b04505196e138c242e784a608b71cb6c8ecb72";
const SMALL: &str = "bc1qk3kxstl02hqnhynwtx0zws7merw6ynut52vtzs";
const SMALL_SCRIPT: &str = "0014b46c682fef55c13b926e599e2743dbc8dda24f8b";
const BIG_WORK: u64 = 999_000;
const SMALL_WORK: u64 = 1_000;
/// What earlier blocks left the small miner, under the floor then as now.
const SMALL_CARRY: u64 = 40_000;
const MIN_PAYOUT: u64 = 1_000_000;
const OWN_MIN_PAYOUT: u64 = 1_000;

fn unix_now() -> u32 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() as u32
}

fn node() -> MockNode {
    node_at(REGTEST_BITS)
}

fn node_at(bits: u32) -> MockNode {
    MockNode::start(Chain {
        height: HEIGHT - 1,
        tip: node_hex(&TIP),
        parent: node_hex(&[0x41; 32]),
        bits,
        next_bits: Some(bits),
    })
}

/// A Prime whose window holds both miners and which remembers `SMALL` as the payout of
/// `small_gw`'s key, as it would of a gateway that has mined here before.
fn start(node: &MockNode, pool: &Identity, own_floor: bool, small_gw: &Identity) -> (Primed, u16) {
    let key = if own_floor { format!("own-min-payout = {OWN_MIN_PAYOUT}") } else { String::new() };
    let cfg = format!(
        "{}\nhouse-loopback = false\nmin-payout = {MIN_PAYOUT}\nwindow-min-work = 100000000\n{key}",
        node.config(0.2)
    );
    let (primed, port) = start_primed_seeded(pool, &cfg, |dir| {
        let t = unix_now() - 600;
        let mut l = Ledger::open(dir).unwrap();
        l.credit(BIG, BIG_WORK, HEIGHT, t, SOURCE_DATUM).unwrap();
        l.credit(SMALL, SMALL_WORK, HEIGHT, t, SOURCE_DATUM).unwrap();
        l.set_carry(SMALL, SMALL_CARRY);
        l.persist_window().unwrap();
        let scripts = serde_json::json!({
            hex::encode(small_gw.sign_pk()): {"identity": SMALL, "script_hex": SMALL_SCRIPT},
        });
        std::fs::write(dir.join("gateway-scripts.json"), scripts.to_string()).unwrap();
    });
    let deadline = Instant::now() + Duration::from_secs(10);
    while stats(&primed)["node"]["height"].as_u64().unwrap_or(0) == 0 {
        assert!(Instant::now() < deadline, "primed never read the node's tip");
        std::thread::sleep(Duration::from_millis(100));
    }
    (primed, port)
}

/// The harness's 0.5% DATUM fee off a share of the reward.
fn earned(work: u64) -> u64 {
    (u128::from(VALUE) * u128::from(work) * 9_950 / u128::from(BIG_WORK + SMALL_WORK) / 10_000) as u64
}

fn paid_to(outputs: &[Output], script_hex: &str) -> Option<u64> {
    outputs.iter().find(|o| hex::encode(&o.script) == script_hex).map(|o| o.sats)
}

fn total(outputs: &[Output]) -> u64 {
    outputs.iter().map(|o| o.sats).sum()
}

#[test]
fn a_gateway_is_handed_a_split_that_pays_its_own_small_miner_and_no_other_gateway_is() {
    let node = node();
    let (small_gw, other_gw) = (Identity::generate(), Identity::generate());
    let pool = Identity::generate();
    let (primed, port) = start(&node, &pool, true, &small_gw);
    let owed = earned(SMALL_WORK) + SMALL_CARRY;
    assert!(owed > OWN_MIN_PAYOUT && owed < MIN_PAYOUT, "under the pool's floor: {owed}");

    // Any other gateway: the small miner is under the floor and in no output.
    let mut other = Gateway::connect(port, &pool, &other_gw);
    let (_, plain) = other.request_coinbaser_outputs(VALUE, &TIP);
    assert_eq!(paid_to(&plain, SMALL_SCRIPT), None);
    assert_eq!(paid_to(&plain, BIG_SCRIPT), Some(earned(BIG_WORK)));
    assert_eq!(total(&plain), VALUE);

    // Its own: paid first, this block's share and its carry, out of the pool's output.
    let mut own = Gateway::connect(port, &pool, &small_gw);
    let (_, outputs) = own.request_coinbaser_outputs(VALUE, &TIP);
    assert_eq!(hex::encode(&outputs[0].script), SMALL_SCRIPT, "{outputs:?}");
    assert_eq!(outputs[0].sats, owed, "what it is owed and not a sat more");
    assert_eq!(paid_to(&outputs, BIG_SCRIPT), Some(earned(BIG_WORK)), "nobody else's output moves");
    assert_eq!(total(&outputs), VALUE);
    assert_eq!(
        paid_to(&outputs, POOL_SCRIPT).unwrap() + owed,
        paid_to(&plain, POOL_SCRIPT).unwrap(),
        "it comes out of what the pool was holding for it"
    );

    // And the other gateway's next reply is still the plain one: the lowered floor is the
    // asking session's, not the pool's.
    let (_, again) = other.request_coinbaser_outputs(VALUE + 1, &TIP);
    assert_eq!(paid_to(&again, SMALL_SCRIPT), None);

    let st = settled_stats(&primed);
    assert_eq!(st["totals"]["own_floor_replies"], 1, "{st}");
    assert_eq!(st["totals"]["own_floor_blocks"], 0, "{st}");
    assert_eq!(st["pool"]["own_min_payout"], OWN_MIN_PAYOUT, "{st}");
    drop(primed);

    // The key unset: the small miner's own gateway is handed what everyone is.
    let pool = Identity::generate();
    let (primed, port) = start(&node, &pool, false, &small_gw);
    let mut own = Gateway::connect(port, &pool, &small_gw);
    let (_, outputs) = own.request_coinbaser_outputs(VALUE, &TIP);
    assert_eq!(outputs, plain);
    assert_eq!(settled_stats(&primed)["totals"]["own_floor_replies"], 0);
}

/// A share at `height` on `prev` whose coinbase pays `outputs` exactly.
fn split_share(outputs: &[Output], slot: u8, height: u32, prev: Hash, now: u32) -> PowSubmit {
    let outs: Vec<TxOut> = outputs.iter().map(|o| TxOut { value: o.sats, script: o.script.clone() }).collect();
    let mut s = pool_only_share(slot, height, prev, REGTEST_BITS, 0, now);
    let (cb, tidx, split_at) = coinbase::build(height, b"Lazarus", &outs, 0);
    s.coinbase = Some(CoinbaseSection {
        coinbase_id: 0,
        coinb1: cb[..split_at].to_vec(),
        coinb2: cb[split_at + coinbase::EXTRANONCE_SLOT..].to_vec(),
    });
    s.job.as_mut().unwrap().target_byte_index = tidx as u16;
    s.username = format!("{SMALL}.rig");
    s
}

fn block_hash(issued: &[Output], s: &PowSubmit) -> Hash {
    let pool = hex::decode(POOL_SCRIPT).unwrap();
    let policy = Policy {
        pool_script: &pool,
        issued: Some(issued),
        tolerance: 0,
        now: 0,
        min_pot: 0,
        gateway_script: None,
        empty_solo_fee_bps: 0,
        trusted_target: false,
        uncommitted_pot: 20,
        held_split: false,
        gateway_keys: false,
        issued_own: 0,
    };
    let mut slot = JobSlot::default();
    slot.absorb(s).unwrap();
    verify::verify(&mut slot, s, &policy).expect("the ground share verifies").hash
}

fn wait_settled(p: &Primed, n: usize) -> Vec<BlockRecord> {
    let deadline = Instant::now() + Duration::from_secs(45);
    loop {
        let b = BlockLog::open(&p.dir).read_all().unwrap();
        if b.len() >= n && b.iter().take(n).all(|r| r.settled && r.books.as_ref().is_some_and(|k| k.credits_live)) {
            return b;
        }
        assert!(Instant::now() < deadline, "{n} block(s) never settled: {b:?}");
        std::thread::sleep(Duration::from_millis(200));
    }
}

fn miner(p: &Primed, who: &str) -> serde_json::Value {
    let st = settled_stats(p);
    st["window"]["miners"].as_array().unwrap().iter().find(|m| m["identity"] == who).cloned().unwrap_or_default()
}

/// The small miner finds a block on its own gateway's split, and is paid in it. The record
/// says so, its carry is spent, and the next split handed to that gateway does not hand the
/// same carry out again. Then the worst case: the gateway finds the next block too, on the
/// same list, before it has asked for another (a coinbaser one block old is honoured). That
/// coinbase pays the carry a second time, which is on the books as paid twice, not lost sight
/// of: the miner owes it, and it comes off what it earns next.
#[test]
#[ignore]
fn a_small_miner_is_paid_in_the_block_it_finds_and_a_second_block_on_the_same_list_is_booked_as_debt() {
    let node = node();
    let small_gw = Identity::generate();
    let pool = Identity::generate();

    // The list is a function of the seeded window; ask a Prime that is then thrown away, so
    // the shares can be ground before the session that submits them opens.
    let issued = {
        let (probe, port) = start(&node, &pool, true, &small_gw);
        let mut gw = Gateway::connect(port, &pool, &small_gw);
        let out = gw.request_coinbaser_outputs(VALUE, &TIP).1;
        drop(probe);
        out
    };
    let owed = earned(SMALL_WORK) + SMALL_CARRY;
    assert_eq!((hex::encode(&issued[0].script), issued[0].sats), (SMALL_SCRIPT.to_string(), owed));

    let mut first = split_share(&issued, 1, HEIGHT, TIP, unix_now());
    grind_diff1(&mut first);
    let h1 = block_hash(&issued, &first);
    let mut second = split_share(&issued, 2, HEIGHT + 1, h1, unix_now());
    grind_diff1(&mut second);
    let h2 = block_hash(&issued, &second);

    let (primed, port) = start(&node, &pool, true, &small_gw);
    let mut gw = Gateway::connect(port, &pool, &small_gw);
    let (id, outputs) = gw.request_coinbaser_outputs(VALUE, &TIP);
    assert_eq!(outputs, issued);

    // block N: found by the small miner, who is in it
    first.job.as_mut().unwrap().coinbaser_id = id;
    assert_eq!(gw.submit(&first).0, mining::ACCEPTED);
    *node.chain.lock().unwrap() = Chain {
        height: HEIGHT,
        tip: node_hex(&h1),
        parent: node_hex(&TIP),
        bits: REGTEST_BITS,
        next_bits: Some(REGTEST_BITS),
    };
    let b = wait_settled(&primed, 1);
    assert_eq!((b[0].kind.as_str(), b[0].owed_sats, b[0].carry_paid), ("split", 0, SMALL_CARRY), "{:?}", b[0]);
    assert!(b[0].split.contains(&(SMALL.to_string(), owed)), "the record lists it as paid: {:?}", b[0].split);
    assert_eq!(b[0].carry_shortfall_sats, 0);
    assert!(b[0].carry_delta.contains(&(SMALL.to_string(), -(SMALL_CARRY as i64))), "{:?}", b[0].carry_delta);
    let m = miner(&primed, SMALL);
    assert_eq!(m["carry_sats"], 0, "its carry was paid by the block: {m}");
    let st = settled_stats(&primed);
    assert_eq!(st["totals"]["own_floor_blocks"], 1, "{st}");
    assert_eq!(st["window"]["carry_debt_sats"], 0, "{st}");

    // block N+1 on the same list, from before block N
    second.job.as_mut().unwrap().coinbaser_id = id;
    assert_eq!(gw.submit(&second).0, mining::ACCEPTED, "a coinbaser one block old is still honoured");
    node.confirmations.lock().unwrap().insert(node_hex(&h1), 2);
    *node.chain.lock().unwrap() = Chain {
        height: HEIGHT + 1,
        tip: node_hex(&h2),
        parent: node_hex(&h1),
        bits: REGTEST_BITS,
        next_bits: Some(REGTEST_BITS),
    };
    let b = wait_settled(&primed, 2);
    eprintln!("block N+1 on the list from before N: {:?}", b[1]);
    assert_eq!(b[1].kind.as_str(), "split");
    assert_eq!(b[1].carry_shortfall_sats, SMALL_CARRY, "the carry it paid a second time is counted: {:?}", b[1]);
    let st = settled_stats(&primed);
    assert_eq!(st["window"]["carry_debt_sats"], SMALL_CARRY, "and owed back by the miner it was paid to: {st}");
    assert_eq!(st["window"]["carry_debt_holders"], 1, "{st}");
}

/// A difficulty at which a diff-1 share is a share and not a block.
const TIP_BITS: u32 = 0x1903_a30c;

fn client_row(p: &Primed, gateway: &Identity) -> serde_json::Value {
    let st = settled_stats(p);
    let hex = hex::encode(&gateway.sign_pk()[..8]);
    st["clients"].as_array().unwrap().iter().find(|r| r["gateway"] == hex.as_str()).cloned().unwrap_or_default()
}

/// Production, 2026-11-21. A gateway that keeps a job's script from when the job was made builds
/// its first full job of a tip on its own script, and with the split in hand that job pays the
/// outputs its section keeps and sends the rest to the gateway. With no lowered floor the
/// gateway's script is not in its list and that is its own solo work: accepted, not credited.
/// With the floor lowered the list names the script, and the build before this fix refused the
/// share as a listed payee paid past its amount. Run against that build to see it refused:
///
///     PRIMED_BIN=/path/to/primed-ownfloor-06e97f18 cargo test --release -p primed --test own_floor_e2e \
///         a_job_that_kept -- --ignored --nocapture
#[test]
#[ignore]
fn a_job_that_kept_the_gateways_own_script_is_its_solo_work_and_not_refused() {
    let node = node_at(TIP_BITS);
    let small_gw = Identity::generate();
    let pool = Identity::generate();
    let issued = {
        let (probe, port) = start(&node, &pool, true, &small_gw);
        let mut gw = Gateway::connect(port, &pool, &small_gw);
        let out = gw.request_coinbaser_outputs(VALUE, &TIP).1;
        drop(probe);
        out
    };
    assert_eq!(hex::encode(&issued[0].script), SMALL_SCRIPT, "the list names the gateway's own script");
    assert_eq!(hex::encode(&issued[1].script), BIG_SCRIPT);

    // The section keeps the list's first two outputs and pays what it dropped (here the pool's
    // remainder) to the script the job was made with: the gateway's own.
    let kept: u64 = issued[..2].iter().map(|o| o.sats).sum();
    let outs = vec![
        TxOut { value: issued[0].sats, script: issued[0].script.clone() },
        TxOut { value: issued[1].sats, script: issued[1].script.clone() },
        TxOut { value: VALUE - kept, script: hex::decode(SMALL_SCRIPT).unwrap() },
    ];
    let mut share = pool_only_share(1, HEIGHT, TIP, TIP_BITS, 0, unix_now());
    let (cb, tidx, split_at) = coinbase::build(HEIGHT, b"Lazarus", &outs, 0);
    share.coinbase = Some(CoinbaseSection {
        coinbase_id: 0,
        coinb1: cb[..split_at].to_vec(),
        coinb2: cb[split_at + coinbase::EXTRANONCE_SLOT..].to_vec(),
    });
    share.job.as_mut().unwrap().target_byte_index = tidx as u16;
    share.username = format!("{SMALL}.rig");
    grind_diff1(&mut share);

    let (primed, port) = start(&node, &pool, true, &small_gw);
    let mut gw = Gateway::connect(port, &pool, &small_gw);
    let (id, outputs) = gw.request_coinbaser_outputs(VALUE, &TIP);
    assert_eq!(outputs, issued);
    share.job.as_mut().unwrap().coinbaser_id = id;
    let answer = gw.submit(&share);
    eprintln!("answer to the share on the gateway's own script: {answer:?}");
    assert_ne!(answer.0, mining::REJECTED, "refused with code {}", answer.1);

    let r = client_row(&primed, &small_gw);
    assert_eq!(
        (r["solo_full_shares"].as_u64(), r["work"].as_u64(), r["rejected"].as_u64()),
        (Some(1), Some(0), Some(0)),
        "the gateway's own solo work, accepted and not credited in the window: {r}"
    );
    let st = settled_stats(&primed);
    assert_eq!(st["totals"]["shares_rejected"], 0, "{st}");
}
