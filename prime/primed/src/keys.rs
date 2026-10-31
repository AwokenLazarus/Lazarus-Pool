//! Keys that blocks have made public.
//!
//! A gateway's own `xor_key` blinds its jobs only while it is secret, and a block carries it in
//! its header. Whoever hashed for that gateway knows the raw hash of every share it found, so with
//! the key it can read every later share under that key as a win or not. A key must not outlive the
//! block that shows it.
//!
//! The gateway chooses its keys and makes a fresh one per template, so an honest one never reuses
//! one. Prime does not take that on trust. It keeps the hash of every key a block has shown, from
//! the blocks gateways report to it and from the headers of the chain its node follows, and refuses
//! any later share under one of them, from any session and after a restart.
//!
//! A key never leaves the book while there is room. A block that a reorg takes off the chain was
//! still broadcast, and its key with it, so the book is also kept on disk (`revealed-keys.txt`,
//! one `key-hash height` line per key, appended): the chain alone would forget an orphan's key
//! at the next start. An older build ignores the file.

use std::collections::{HashMap, HashSet, VecDeque};
use std::io::Write;
use std::path::Path;
use std::sync::Mutex;
use std::time::Instant;

use datum_wire::pow::{self, Hash, HEADER_V2_LEN, V2_BIT};
use serde_json::json;

use crate::state::Shared;

/// Revealed keys kept: a week of blocks many times over, at 40 bytes each.
const MAX_KEYS: usize = 1 << 16;
/// Block headers already read, so a tip that moves by one costs one call.
const MAX_SCANNED: usize = 1 << 14;
/// How far back the first scan after a start reads: two weeks of blocks.
pub const BACKFILL_BLOCKS: u32 = 2016;
/// How far back a later scan may walk to reach a block it has read (a reorg, a missed poll).
pub const FOLLOW_BLOCKS: u32 = 64;

#[derive(Default)]
struct Inner {
    /// Key hash, and the lowest height a block showed it at.
    keys: HashMap<Hash, u32>,
    order: VecDeque<Hash>,
    scanned: HashSet<String>,
    scanned_order: VecDeque<String>,
}

pub const FILE: &str = "revealed-keys.txt";

#[derive(Default)]
pub struct RevealedKeys {
    inner: Mutex<Inner>,
    file: Mutex<Option<std::fs::File>>,
}

impl RevealedKeys {
    /// Load the book kept in `dir` and keep appending to it. Returns how many keys it held.
    pub fn open(&self, dir: &Path) -> std::io::Result<usize> {
        let path = dir.join(FILE);
        let text = match std::fs::read_to_string(&path) {
            Ok(t) => t,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
            Err(e) => return Err(e),
        };
        for line in text.lines() {
            let mut f = line.split_whitespace();
            let (Some(h), Some(at)) = (f.next(), f.next()) else { continue };
            let (Ok(h), Ok(at)) = (hex::decode(h), at.parse::<u32>()) else { continue };
            let Ok(h) = <Hash>::try_from(h.as_slice()) else { continue };
            self.insert(h, at);
        }
        *self.file.lock().unwrap() = Some(std::fs::OpenOptions::new().create(true).append(true).open(path)?);
        Ok(self.len())
    }

    /// Note a key a block at `height` has shown.
    pub fn note(&self, key: &[u8; 16], height: u32) {
        if key.iter().all(|&b| b == 0) {
            return;
        }
        let h = pow::xor_key_hash(key);
        if self.insert(h, height) {
            if let Some(f) = self.file.lock().unwrap().as_mut() {
                if let Err(e) = writeln!(f, "{} {height}", hex::encode(h)) {
                    log::error!("{FILE}: {e}; the key is held in memory and is on the chain");
                }
            }
        }
    }

    /// Whether the key was new to the book.
    fn insert(&self, h: Hash, height: u32) -> bool {
        let mut i = self.inner.lock().unwrap();
        if let Some(at) = i.keys.get_mut(&h) {
            *at = (*at).min(height);
            return false;
        }
        if i.order.len() >= MAX_KEYS {
            if let Some(old) = i.order.pop_front() {
                i.keys.remove(&old);
            }
        }
        i.keys.insert(h, height);
        i.order.push_back(h);
        true
    }

    /// The height of the block that showed this key, if one has.
    pub fn shown_at(&self, key_hash: &Hash) -> Option<u32> {
        self.inner.lock().unwrap().keys.get(key_hash).copied()
    }

    pub fn len(&self) -> usize {
        self.inner.lock().unwrap().keys.len()
    }

    fn scanned(&self, block: &str) -> bool {
        self.inner.lock().unwrap().scanned.contains(block)
    }

    fn mark_scanned(&self, block: &str) {
        let mut i = self.inner.lock().unwrap();
        if !i.scanned.insert(block.to_string()) {
            return;
        }
        i.scanned_order.push_back(block.to_string());
        if i.scanned_order.len() > MAX_SCANNED {
            if let Some(old) = i.scanned_order.pop_front() {
                i.scanned.remove(&old);
            }
        }
    }
}

/// The key a raw v2 header carries, and its parent as the node prints it.
pub fn header_key(header: &[u8]) -> Option<([u8; 16], String)> {
    if header.len() != HEADER_V2_LEN || u32::from_le_bytes(header[..4].try_into().ok()?) & V2_BIT == 0 {
        return None;
    }
    let key: [u8; 16] = header[112..128].try_into().ok()?;
    let mut prev = [0u8; 32];
    prev.copy_from_slice(&header[4..36]);
    prev.reverse();
    Some((key, hex::encode(prev)))
}

/// Read the chain back from `tip` (at `height`) for keys its blocks showed, up to `depth` blocks
/// or the first one already read.
pub async fn scan(shared: &Shared, tip: &str, height: u32, depth: u32) {
    let (mut block, mut at, mut found, mut read) = (tip.to_string(), height, 0usize, 0u32);
    let started = Instant::now();
    for _ in 0..depth {
        if block.is_empty() || shared.revealed.scanned(&block) {
            break;
        }
        read += 1;
        let raw = match shared.rpc.call("getblockheader", json!([block, false])).await {
            Ok(serde_json::Value::String(h)) => hex::decode(h).ok(),
            Ok(_) => None,
            Err(e) => {
                log::debug!("getblockheader {block} for its xor_key: {e}");
                return;
            }
        };
        let Some((key, parent)) = raw.as_deref().and_then(header_key) else {
            // not a v2 header (or not one the node would give): nothing past it either
            shared.revealed.mark_scanned(&block);
            break;
        };
        if key.iter().any(|&b| b != 0) {
            shared.revealed.note(&key, at);
            found += 1;
        }
        shared.revealed.mark_scanned(&block);
        if at == 0 {
            break;
        }
        (block, at) = (parent, at - 1);
    }
    let ms = started.elapsed().as_millis();
    if found > 0 || depth == BACKFILL_BLOCKS || ms > 1000 {
        log::info!(
            "xor_keys: read {read} block header(s) back from height {height} in {ms} ms, {found} key(s) shown; {} held",
            shared.revealed.len()
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_shown_key_is_held_at_its_lowest_height_and_the_null_key_never() {
        let r = RevealedKeys::default();
        r.note(&[0u8; 16], 10);
        assert_eq!(r.len(), 0);
        r.note(&[7u8; 16], 12);
        r.note(&[7u8; 16], 11);
        r.note(&[7u8; 16], 15);
        assert_eq!(r.shown_at(&pow::xor_key_hash(&[7u8; 16])), Some(11));
        assert_eq!(r.shown_at(&pow::xor_key_hash(&[8u8; 16])), None);
    }

    #[test]
    fn the_book_is_kept_on_disk_and_read_back() {
        let d = std::env::temp_dir().join(format!("primed-keys-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let r = RevealedKeys::default();
        assert_eq!(r.open(&d).unwrap(), 0);
        r.note(&[7u8; 16], 12);
        r.note(&[7u8; 16], 11);
        r.note(&[8u8; 16], 20);
        drop(r);
        let again = RevealedKeys::default();
        assert_eq!(again.open(&d).unwrap(), 2);
        assert_eq!(again.shown_at(&pow::xor_key_hash(&[7u8; 16])), Some(12), "written once, at first sight");
        assert_eq!(again.shown_at(&pow::xor_key_hash(&[8u8; 16])), Some(20));
        // a torn last line is skipped, not fatal
        std::fs::write(d.join(FILE), format!("{} 5\nabc", hex::encode(pow::xor_key_hash(&[9u8; 16])))).unwrap();
        assert_eq!(RevealedKeys::default().open(&d).unwrap(), 1);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn the_book_is_bounded_oldest_out() {
        let r = RevealedKeys::default();
        for i in 0..(MAX_KEYS as u32 + 10) {
            let mut k = [1u8; 16];
            k[..4].copy_from_slice(&i.to_le_bytes());
            r.note(&k, i);
        }
        assert_eq!(r.len(), MAX_KEYS);
        let mut first = [1u8; 16];
        first[..4].copy_from_slice(&0u32.to_le_bytes());
        assert_eq!(r.shown_at(&pow::xor_key_hash(&first)), None);
    }

    #[test]
    fn a_header_gives_its_key_and_its_parent() {
        let mut h = [0u8; HEADER_V2_LEN];
        h[..4].copy_from_slice(&(0x2000_0000u32 | V2_BIT).to_le_bytes());
        h[4] = 0xaa;
        h[112..128].copy_from_slice(&[9u8; 16]);
        let (key, parent) = header_key(&h).unwrap();
        assert_eq!(key, [9u8; 16]);
        assert!(parent.ends_with("aa") && parent.len() == 64, "display order: {parent}");
        h[3] &= 0x7f;
        assert!(header_key(&h).is_none(), "a v1 header");
        assert!(header_key(&h[..80]).is_none());
    }
}
