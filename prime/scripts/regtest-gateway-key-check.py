#!/usr/bin/env python3
"""What regtest-gateway-key.sh checks, read from Knots, primed's stats and the gateways' own lines.

  check.py jobs  OUT lines.log api_port                      print job disclosures as JSON lines
  check.py mixed OUT bitcoin-cli datadir keyed_addr stock_addr
  check.py stock OUT bitcoin-cli datadir addr

The header is hashed here the way v2_stages and Knots do, by nothing shared with primed.
"""
import hashlib
import json
import subprocess
import sys
import urllib.request

NULL_KEY = bytes(16)


def tagged(tag, msg):
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + msg).digest()


def blake2b(b):
    return hashlib.blake2b(b, digest_size=32).digest()


def stages(b):
    """Profile 0 of block.cpp: the raw hash, and the block id with the mask on."""
    flags, clear_bits, key = b[110], b[111], b[112:128]
    mask = bytearray(32)
    if any(key):
        mask = bytearray(tagged('Bitcoin block hash PoW XOR mask', key))
        clear = clear_bits // 8
        for i in range(min(clear, 32)):
            mask[i] = 0
        if clear < 32:
            mask[clear] &= 0xff >> (clear_bits % 8)
    prev = b[4:36][::-1]
    hidden = bytearray(tagged('Bitcoin prevblock header, hashed', prev))
    hidden[:6] = bytes(6)
    h1 = tagged('Bitcoin block header 1', b[0:4] + prev + b[128:132] + b[36:68] + b[68:72] + b'\0' + b[72:76]
                + int.from_bytes(b[108:110], 'little').to_bytes(4, 'little') + bytes([flags, clear_bits])
                + tagged('Bitcoin block hash PoW XOR key', key))
    h2 = tagged('Merge-mining hook', h1 + bytes(32) + b[132:164])
    root = blake2b(bytes(4) + h2 + b[88:104])
    assert flags & 3 == 0
    raw = blake2b(bytes(hidden) + b[76:80] + b[80:84] + b[104:108] + b[84:88] + root)
    return raw, bytes(x ^ y for x, y in zip(raw, mask)), bytes(mask)


class Results:
    def __init__(self, out):
        self.out = out
        self.failed = 0

    def check(self, ok, what):
        print(('ok   ' if ok else 'FAIL ') + what)
        with open(self.out + '/summary.log', 'a') as f:
            f.write(('ok   ' if ok else 'FAIL ') + what + '\n')
        if not ok:
            self.failed += 1


def leading_zero_bits(h):
    return 256 - int.from_bytes(h, 'big').bit_length()


def jobs(out, lines, api):
    """Ask the gateway what it says of the job each notify was, while it still holds them."""
    notified, subscribed = {}, None
    for line in open(lines):
        parts = line.split(' ', 2)
        if len(parts) < 3 or not parts[2].startswith('{'):
            continue
        m = json.loads(parts[2])
        if m.get('method') == 'mining.notify':
            notified[m['params'][0]] = m['params'][2]
    for job, coinb1 in list(notified.items())[-12:]:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{api}/xbt/job?id={job}&pot=0', timeout=3) as r:
                print(json.dumps({'id': job, 'pot': 0, 'coinb1': coinb1, 'status': r.status, 'api': r.read().decode()}))
        except Exception as e:
            print(json.dumps({'id': job, 'pot': 0, 'coinb1': coinb1, 'status': 0, 'api': str(e)}))


def blocks_on_chain(cli, datadir):
    def rpc(*a):
        return subprocess.run([cli, '-datadir=' + datadir, *a], capture_output=True, text=True)
    return rpc


def check_block(res, rpc, rec, keyed):
    h = rec['hash']
    got = rpc('getblockheader', h, 'false')
    res.check(got.returncode == 0, f'block {rec["height"]} {h[:16]} is known to Knots under the id primed recorded')
    if got.returncode:
        return None
    header = bytes.fromhex(got.stdout.strip())
    info = json.loads(rpc('getblockheader', h).stdout)
    res.check(info['confirmations'] > 0, f'  in the main chain ({info["confirmations"]} confirmations)')
    raw, block, mask = stages(header)
    res.check(block.hex() == h, '  this script hashes the header to that id')
    key, clear = header[112:128], header[111]
    if keyed:
        res.check(key != NULL_KEY and raw != block, f'  the key is in the header (non-null), raw hash != id, clear_bits {clear}')
        res.check(clear == 32 and leading_zero_bits(raw) >= clear, '  clear_bits are the share\'s own: 32 at pot 0, raw hash meets them')
        res.check(leading_zero_bits(mask) >= clear, '  the mask is clear for those bits')
    else:
        res.check(key == NULL_KEY and clear == 0 and raw == block, '  null key: no mask, clear_bits 0')
    res.check(rec['settled'] is True, f'  settled by primed ({rec["kind"]}, submit {rec["submit"]})')
    cb = json.loads(rpc('getblock', h, '2').stdout)['tx'][0]
    paid = {}
    for o in cb['vout']:
        a = o['scriptPubKey'].get('address')
        if a:
            paid[a] = paid.get(a, 0) + round(o['value'] * 1e8)
    split = dict(rec['split'])
    res.check(rec['kind'] != 'split' or all(paid.get(a, 0) >= v for a, v in split.items()),
              f'  the coinbase pays the split primed booked: {len(split)} payees, {sum(paid.values())} sats out')
    return header


def tamper(res, rpc, header_of, h):
    """A changed key is a different block with a different id. Find one that misses the target."""
    whole = bytes.fromhex(rpc('getblock', h, '0').stdout.strip())
    bits = int.from_bytes(whole[72:76], 'little')
    target = (bits & 0x7fffff) << (8 * ((bits >> 24) - 3))
    tried = 0
    for i in range(16):
        for flip in (0x01, 0x80, 0xff):
            t = bytearray(whole)
            t[112 + i] ^= flip
            _, block, _ = stages(bytes(t[:164]))
            tried += 1
            if int.from_bytes(block, 'big') > target:
                r = rpc('submitblock', t.hex())
                res.check(r.stdout.strip() == 'high-hash', f'Knots on a block with its key tampered: {r.stdout.strip() or r.stderr.strip()}')
                return
    res.check(False, f'no tampered key of {tried} missed regtest\'s target')


def mixed(out, cli, datadir, keyed_addr, stock_addr):
    res = Results(out)
    st = json.load(open(out + '/stats-mixed.json'))
    t, rpc = st['totals'], blocks_on_chain(cli, datadir)
    rows = st['clients']
    keyed_rows = [r for r in rows if r.get('gateway_key_shares')]
    stock_rows = [r for r in rows if not r.get('gateway_key_shares') and r['accepted']]
    res.check(len(keyed_rows) == 1 and len(stock_rows) == 1, 'one keyed gateway and one stock gateway on the pool')
    k, s = keyed_rows[0], stock_rows[0]

    res.check(t['gateway_key_refused'] == 0 and t['gateway_key_false_blocks'] == 0, 'no key refused or falsely shown in an honest run')
    res.check(k['gateway_key_work'] == k['work'] and k['work'] == k['accepted'], f'keyed gateway: {k["accepted"]} shares, {k["work"]} credit, one per diff-1 share')
    res.check(s['work'] == s['accepted'], f'stock gateway: {s["accepted"]} shares, {s["work"]} credit, one per diff-1 share')
    res.check(k['rejected'] == 0 or k.get('last_reject') == 'stale', f'keyed gateway rejects: {k["rejected"]} (last {k.get("last_reject")})')
    res.check(s['rejected'] == 0 or s.get('last_reject') in ('stale', 'bad-coinbase-outputs'), f'stock gateway rejects: {s["rejected"]} (last {s.get("last_reject")})')
    miners = {m['identity']: m for m in st['window']['miners']}
    res.check(miners.get(keyed_addr, {}).get('work', 0) >= k['work'], f'the window credits the keyed miner: {miners.get(keyed_addr, {}).get("work")}')
    res.check(miners.get(stock_addr, {}).get('work', 0) >= s['work'], f'the window credits the stock miner: {miners.get(stock_addr, {}).get("work")}')
    res.check(t['template_checks'] >= 1 and t['template_checks_failed'] == 0, f'template checks on a keyed pool: {t["template_checks"]} run, {t["template_checks_failed"]} failed')
    res.check(k['accepted'] > t['gateway_key_blocks'] >= 1, f'keyed: {k["accepted"]} shares of which {t["gateway_key_blocks"]} shown as blocks, the rest credited as non-wins')

    primed_log = open(out + '/primed.log', errors='replace').read()
    res.check(f'{k["gateway"]}: template at height' in primed_log and 'passes' in primed_log.split(f'{k["gateway"]}: template at height', 1)[1].split('\n', 1)[0],
              'Knots passed a keyed job in proposal mode')
    log = open(out + '/gw-keyed.log', errors='replace').read()
    res.check('DATUM Pool gateway keys:     yes' in log, 'the keyed gateway was told')
    log_stock = open(out + '/gw-stock.log', errors='replace').read()
    res.check('Invalid data structure' not in log_stock and 'Bad configuration' not in log_stock, 'the stock gateway took its configure')
    res.check('DATUM Pool ABW:              disabled' in log_stock, 'and read ABW disabled, as it always has')

    disclosed = []
    for line in open(out + '/jobs.jsonl'):
        r = json.loads(line)
        if r['status'] == 200:
            disclosed.append(json.loads(r['api']))
    res.check(disclosed and all(a['mode'] == 'pool-key' and a['clear_bits'] == 32 for a in disclosed),
              f'/xbt/job says pool-key with clear_bits 32 on {len(disclosed)} recent jobs')
    keyhex = None
    keyed_blocks = [b for b in st['blocks'] if b['gateway'] == k['gateway']]
    stock_blocks = [b for b in st['blocks'] if b['gateway'] == s['gateway']]
    res.check(len(keyed_blocks) == t['gateway_key_blocks'], f'blocks by the keyed gateway on record: {len(keyed_blocks)}')
    for b in keyed_blocks:
        header = check_block(res, rpc, b, True)
        if header:
            keyhex = header[112:128].hex()
    for b in stock_blocks:
        check_block(res, rpc, b, False)
    if keyhex:
        lines = open(out + '/lines-keyed.log', errors='replace').read()
        res.check(keyhex not in lines, 'the key of a found block never appears in a stratum line')
        res.check(keyhex not in open(out + '/jobs.jsonl').read(), 'nor in a job disclosure')
    if keyed_blocks:
        tamper(res, rpc, None, keyed_blocks[-1]['hash'])
    return 1 if res.failed else 0


def stock(out, cli, datadir, addr):
    res = Results(out)
    st = json.load(open(out + '/stats-stock.json'))
    t, rpc = st['totals'], blocks_on_chain(cli, datadir)
    res.check(t['work_accepted'] == t['shares_accepted'] >= 1, f'{t["shares_accepted"]} shares, {t["work_accepted"]} credit')
    for b in st['blocks']:
        check_block(res, rpc, b, False)
    figures = {'shares': t['shares_accepted'], 'work': t['work_accepted'], 'rejected': t['shares_rejected'],
               'blocks': [{'kind': b['kind'], 'value': b['coinbase_value'], 'owed': b['owed_sats'], 'pool': b['pool_sats'],
                           'split': sorted(v for _, v in b['split']), 'carry_paid': b['carry_paid']} for b in st['blocks']]}
    with open(out + '/figures.json', 'w') as f:
        json.dump(figures, f, indent=1, sort_keys=True)
    return 1 if res.failed else 0


if __name__ == '__main__':
    mode, args = sys.argv[1], sys.argv[2:]
    sys.exit({'jobs': jobs, 'mixed': mixed, 'stock': stock}[mode](*args) or 0)
