//! End-to-end: tips, against a real `primed` and a stand-in node.
//!
//! A miner whose username asks to tip gives the pool that share of what its own work earns. The
//! split every gateway is handed pays that miner less by the tip, the pool more by it, and
//! everyone else what they were paid before.

mod common;

use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use common::*;
use datum_wire::coinbaser::Output;
use datum_wire::crypto::Identity;
use tides::{Ledger, SOURCE_DATUM};

const REGTEST_BITS: u32 = 0x207f_ffff;
const TIP: [u8; 32] = [0x42; 32];
const BIG: &str = "bc1qpxcy2pgedcfccfpw0p9xpzm3edkgajmjl5xe02";
const BIG_SCRIPT: &str = "001409b04505196e138c242e784a608b71cb6c8ecb72";
const TIPPER: &str = "bc1qk3kxstl02hqnhynwtx0zws7merw6ynut52vtzs";
const TIPPER_SCRIPT: &str = "0014b46c682fef55c13b926e599e2743dbc8dda24f8b";
const BIG_WORK: u64 = 600_000;
const TIPPER_WORK: u64 = 400_000;

fn start(node: &MockNode, pool: &Identity, permille: u8) -> (Primed, u16) {
    let cfg = format!("{}\nhouse-loopback = false\nmin-payout = 546\nwindow-min-work = 100000000", node.config(0.2));
    let (primed, port) = start_primed_seeded(pool, &cfg, |dir| {
        let t = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() as u32 - 600;
        let mut l = Ledger::open(dir).unwrap();
        l.credit(BIG, BIG_WORK, HEIGHT, t, SOURCE_DATUM).unwrap();
        // half of the tipper's work asked to tip, half did not
        l.credit_tipped(TIPPER, TIPPER_WORK / 2, HEIGHT, t, SOURCE_DATUM, permille).unwrap();
        l.credit(TIPPER, TIPPER_WORK / 2, HEIGHT, t + 1, SOURCE_DATUM).unwrap();
        l.persist_window().unwrap();
    });
    let deadline = Instant::now() + Duration::from_secs(10);
    while stats(&primed)["node"]["height"].as_u64().unwrap_or(0) == 0 {
        assert!(Instant::now() < deadline, "primed never read the node's tip");
        std::thread::sleep(Duration::from_millis(100));
    }
    (primed, port)
}

fn paid_to(outputs: &[Output], script_hex: &str) -> u64 {
    outputs.iter().find(|o| hex::encode(&o.script) == script_hex).map_or(0, |o| o.sats)
}

#[test]
fn a_tipping_miner_is_paid_less_by_its_tip_the_pool_more_and_nobody_else_differently() {
    let node = MockNode::start(Chain {
        height: HEIGHT - 1,
        tip: node_hex(&TIP),
        parent: node_hex(&[0x41; 32]),
        bits: REGTEST_BITS,
        next_bits: Some(REGTEST_BITS),
    });
    let pool = Identity::generate();
    let (plain_primed, port) = start(&node, &pool, 0);
    let plain = Gateway::connect(port, &pool, &Identity::generate()).request_coinbaser_outputs(VALUE, &TIP).1;
    let st = settled_stats(&plain_primed);
    assert_eq!(st["window"]["sample_tip_sats"], 0, "{st}");
    drop(plain_primed);

    // 2% on half of its work: 1% of what the tipper earns
    let pool = Identity::generate();
    let (primed, port) = start(&node, &pool, 20);
    let tipped = Gateway::connect(port, &pool, &Identity::generate()).request_coinbaser_outputs(VALUE, &TIP).1;
    let was = paid_to(&plain, TIPPER_SCRIPT);
    let now = paid_to(&tipped, TIPPER_SCRIPT);
    let tip = was - now;
    assert!(tip.abs_diff(was / 100) <= 1, "tipped {tip} of {was}");
    assert_eq!(paid_to(&tipped, BIG_SCRIPT), paid_to(&plain, BIG_SCRIPT), "nobody else's output moves");
    assert_eq!(paid_to(&tipped, POOL_SCRIPT), paid_to(&plain, POOL_SCRIPT) + tip, "the tip is the pool's");
    assert_eq!(tipped.iter().map(|o| o.sats).sum::<u64>(), VALUE);

    let st = settled_stats(&primed);
    assert_eq!(st["pool"]["max_tip_percent"], 5.0, "{st}");
    let tips = st["window"]["tips"].as_array().unwrap();
    assert_eq!(tips.len(), 1, "{st}");
    assert_eq!(tips[0]["identity"], TIPPER);
    // the stats sample prices a 312 500 000 block; 1% of the tipper's share of that
    let sample = tips[0]["sats"].as_u64().unwrap();
    assert!(sample.abs_diff(tip * 312_500_000 / VALUE) <= 2, "{sample} against {tip}");
    assert_eq!(st["window"]["sample_tip_sats"], sample);
}
