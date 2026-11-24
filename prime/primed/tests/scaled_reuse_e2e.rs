//! End-to-end: a gateway that offered DRS scaled reuse and was told yes is never turned to its
//! own script, and no other session changes.
//!
//! Every tip opens with a known gateway on its own payout, so that what it publishes before the
//! split arrives is its own solo work and not a coinbase paying only the pool. A gateway that
//! reuses has no such gap: while the split is late it stretches its last one over the new value.
//! A configure(gateway) there only interrupts the stretch (FlyTheElephant, PR #121). So a session
//! Prime acked scaled reuse to stays on the pool's script, as a `pins_script` session does.
//!
//! Keyed on the ack: with `scaled-reuse-ack` off the same hello is told no, does not reuse, and
//! is turned to its own script as before.

mod common;

use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use common::*;
use datum_wire::crypto::Identity;
use datum_wire::handshake::DRS_SCALED_REUSE;
use datum_wire::mining;

const TIP_BITS: u32 = 0x1903_a30c;
const GW: &str = "bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3";
const GW_SCRIPT: &str = "00201863143c14c5166804bd19203356da136c985678cd4d27a1b8c6329604903262";
const OTHER: &str = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq";
const UA: &str = "v0.5.0-beta/6d45c81b5a991cb3d8bc98e6fe175da915dfb454";
const TIP: [u8; 32] = [0x42; 32];
const NEXT_TIP: [u8; 32] = [0x43; 32];

fn unix_now() -> u32 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() as u32
}

fn node() -> MockNode {
    MockNode::start(Chain {
        height: HEIGHT - 1,
        tip: node_hex(&TIP),
        parent: node_hex(&[0x41; 32]),
        bits: TIP_BITS,
        next_bits: Some(TIP_BITS),
    })
}

/// A Prime whose window pays another miner and which remembers `GW` as the payout of each of
/// `gateways`, so each opens on its own script unless something says otherwise.
fn start(node: &MockNode, pool: &Identity, ack: bool, gateways: &[&Identity]) -> (Primed, u16) {
    let cfg = format!("{}\nhouse-loopback = false\nscaled-reuse-ack = {ack}", node.config(0.2));
    let now = unix_now();
    let (primed, port) = start_primed_seeded(pool, &cfg, |dir| {
        let meta = serde_json::json!({
            "target_work": 8, "lifetime_shares": 0, "lifetime_work": 0, "rebate_owed": 0,
            "carry": {OTHER: 2_000_000}, "last_seen": {OTHER: now - 60},
        });
        std::fs::write(dir.join("window.json"), meta.to_string()).unwrap();
        std::fs::write(dir.join("identities.txt"), format!("{OTHER}\n")).unwrap();
        let scripts: serde_json::Map<String, serde_json::Value> = gateways
            .iter()
            .map(|g| (hex::encode(g.sign_pk()), serde_json::json!({"identity": GW, "script_hex": GW_SCRIPT})))
            .collect();
        std::fs::write(dir.join("gateway-scripts.json"), serde_json::Value::Object(scripts).to_string()).unwrap();
    });
    let deadline = Instant::now() + Duration::from_secs(10);
    while stats(&primed)["node"]["height"].as_u64().unwrap_or(0) == 0 {
        assert!(Instant::now() < deadline, "primed never read the node's tip");
        std::thread::sleep(Duration::from_millis(100));
    }
    (primed, port)
}

/// The script (hex) a configure body names, for either version.
fn configured(body: &[u8]) -> String {
    assert_eq!(body[0], mining::SUB_CONFIGURE, "{body:?}");
    hex::encode(&body[3..3 + usize::from(body[2])])
}

fn configures(msgs: &[Vec<u8>]) -> Vec<String> {
    msgs.iter().filter(|m| m[0] == mining::SUB_CONFIGURE).map(|m| configured(m)).collect()
}

fn row<'a>(stats: &'a serde_json::Value, gateway: &Identity) -> &'a serde_json::Value {
    let hex = hex::encode(&gateway.sign_pk()[..8]);
    let rows = stats["clients"].as_array().unwrap();
    rows.iter().find(|r| r["gateway"] == hex.as_str()).unwrap_or_else(|| panic!("no row for {hex}: {stats}"))
}

#[test]
fn an_acked_scaled_reuse_gateway_is_never_turned_to_its_own_script() {
    let node = node();
    let (reuse_gw, plain_gw) = (Identity::generate(), Identity::generate());
    let pool = Identity::generate();
    let (primed, port) = start(&node, &pool, true, &[&reuse_gw, &plain_gw]);

    // Offered and acked: opened on the pool's script though its payout is known.
    let (mut reuse, first, selection) = Gateway::connect_convoy_flags(port, &pool, &reuse_gw, UA, DRS_SCALED_REUSE);
    assert_eq!(selection.first().copied(), Some(DRS_SCALED_REUSE), "told yes: {selection:?}");
    assert_eq!(configured(&first), POOL_SCRIPT, "left on the pool's script");
    // The same build without the offer: opened on its own script, as ever.
    let (mut plain, first, selection) = Gateway::connect_convoy_flags(port, &pool, &plain_gw, UA, 0);
    assert!(selection.first().copied() != Some(DRS_SCALED_REUSE), "{selection:?}");
    assert_eq!(configured(&first), GW_SCRIPT);

    // A reply turns a session to the pool's script; the reuse session was there already.
    let (before, _, _) = reuse.request_coinbaser_seeing(VALUE, &TIP);
    assert!(configures(&before).iter().all(|s| s == POOL_SCRIPT), "{:?}", configures(&before));
    let (before, _, _) = plain.request_coinbaser_seeing(VALUE, &TIP);
    assert_eq!(configures(&before), vec![POOL_SCRIPT.to_string()]);

    // A new tip: the plain session is turned back to its own script until the split for it
    // arrives, and the reuse session is told of the block and nothing else.
    *node.chain.lock().unwrap() = Chain {
        height: HEIGHT,
        tip: node_hex(&NEXT_TIP),
        parent: node_hex(&TIP),
        bits: TIP_BITS,
        next_bits: Some(TIP_BITS),
    };
    std::thread::sleep(Duration::from_millis(1500));
    let (before, _, _) = plain.request_coinbaser_seeing(VALUE, &NEXT_TIP);
    assert_eq!(configures(&before), vec![GW_SCRIPT.to_string(), POOL_SCRIPT.to_string()], "solo until the split");
    let (before, _, _) = reuse.request_coinbaser_seeing(VALUE, &NEXT_TIP);
    assert!(!configures(&before).iter().any(|s| s == GW_SCRIPT), "no configure(gateway): {:?}", configures(&before));

    let st = settled_stats(&primed);
    assert_eq!(row(&st, &reuse_gw)["scaled_reuse"], true, "{st}");
    assert!(row(&st, &plain_gw).get("scaled_reuse").is_none(), "a row without it is the row it always was");
    drop(primed);

    // The ack off: the same hello is told no, does not reuse, and is turned to its own script.
    let pool = Identity::generate();
    let (primed, port) = start(&node, &pool, false, &[&reuse_gw]);
    let (mut gw, first, selection) = Gateway::connect_convoy_flags(port, &pool, &reuse_gw, UA, DRS_SCALED_REUSE);
    assert!(selection.first().copied() != Some(DRS_SCALED_REUSE), "{selection:?}");
    assert_eq!(configured(&first), GW_SCRIPT, "offered, not acked: as before");
    // a session has a row once it has sent a frame
    gw.request_coinbaser(VALUE, &NEXT_TIP);
    assert!(row(&settled_stats(&primed), &reuse_gw).get("scaled_reuse").is_none());
}
