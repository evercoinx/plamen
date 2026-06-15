# CROSS_VM_SERIALIZATION_CONFORMANCE

> **Trigger**: `NON_EVM_TARGET` flag (recon detects a non-EVM destination — Solana/Bitcoin/Move/Cosmos — via 32-byte pubkey constants, base58/bech32 handling, custom account/byte encoders such as `AccountEncoder`/Borsh-style packing, or a foreign chain-id/program-id).
> **Inject Into**: breadth agent owning the cross-chain/encoding focus, and depth-external.
> **Protocol Types**: any contract that SERIALIZES data on the EVM side for consumption by a DIFFERENT VM (cross-chain bridges, ZetaChain/LayerZero/Wormhole-style messaging, Solana/Bitcoin gateways).
> **Added in**: recall-recovery (post-DODO ground-truth audit — closes the outbound-encoding gap CROSS_CHAIN_MESSAGE_INTEGRITY does not cover; that skill covers INBOUND decode/auth only).

## Why this exists

`CROSS_CHAIN_MESSAGE_INTEGRITY` audits messages the contract RECEIVES (decode,
auth, replay). It does NOT audit whether the bytes the contract PRODUCES on the
EVM side match the layout the destination VM expects. An EVM contract that packs
a Solana account list, a Borsh struct, or a Bitcoin script with the wrong field
width, wrong byte order, wrong flag encoding, or wrong cursor advance will ship
**structurally-valid-on-EVM but corrupt-on-arrival** payloads — silently breaking
cross-chain execution or corrupting permissions/amounts. This is a recurring,
general bridge bug class and is INVISIBLE to pure EVM-side reasoning.

## Methodology

### Step 0 — Verify the serialization SCHEME matches the destination VM (GATES Steps 1-3)
Before ANY per-field check, confirm the encoder emits the destination VM's
NATIVE wire format — not EVM ABI. Native formats: Solana/Anchor = **Borsh**;
Cosmos = **Protobuf/Amino**; Move/Sui = **BCS**; Bitcoin = **script + varint/
CompactSize**; NEAR = Borsh. EVM `abi.encode`/`abi.encodePacked` emit EVM ABI
(32-byte-word-aligned head/tail offsets, dynamic-length prefixes) — a layout
that NONE of those VMs deserialize. So if the EVM side builds a payload with
`abi.encode`/`abi.encodePacked` (or returns such a blob) that a non-EVM program
will deserialize with its native decoder, the payload is **structurally
undecodable on arrival — a CONFIRMED scheme-mismatch finding regardless of
field-level correctness.** Field-level conformance (Steps 1-3) is meaningless if
the whole envelope is the wrong format; only proceed once the scheme is correct.

**FP guard** — do NOT flag when the destination demonstrably `abi.decode`s the
payload: an EVM-compatible reader, an EVM L2/sidechain target, or an on-chain
re-encoding hop that re-serializes before the foreign VM sees it. Confirm the
ACTUAL consumer's decoder before flagging. A round-trip to an EVM decoder is
fine; a hand-off to a Borsh/BCS/Protobuf/script decoder is not.

### Step 1 — Enumerate the serialization surfaces
List every site where the EVM contract builds bytes destined for a non-EVM VM:
`abi.encode`/`abi.encodePacked` into a cross-chain message, custom assembly byte
writers (`mstore`/`mload` cursors), account/instruction encoders (e.g.
custom account-meta encoders, Borsh packers), address/pubkey conversions (20-byte EVM address
↔ 32-byte Solana pubkey; `bytes20` truncation of a non-EVM address), and flag
packing (writable/signer bits, option discriminants).

### Step 2 — For each surface, verify field-by-field conformance to the destination layout
For EACH field written:
- **Width**: is the field written/read at the EXACT width the destination expects?
  (e.g. a 1-byte boolean flag read with a full 32-byte `mload`, or a 32-byte pubkey
  truncated to 20 bytes via `bytes20`.) A read/write wider or narrower than the
  field corrupts it and/or the following fields.
- **Cursor advance**: after writing/reading a field, does the pointer advance by the
  field's TRUE width? A 1-byte field whose cursor advances 1 byte but whose value
  is read from a 32-byte word overlaps the next field.
- **Byte order / endianness**: does the EVM side use the byte order the destination
  VM expects (Solana little-endian vs EVM big-endian for integers)?
- **Field count / order**: does the produced struct have the same number of fields,
  in the same order, as the destination's expected layout (and as the matching
  decoder, if round-tripped)?

### Step 3 — Trace the consequence of any mismatch
If a field is mis-encoded, which destination-side value is corrupted — an account
permission (writable/signer), a token/program address, an amount, or a recipient?
A mismatch that reaches any of those is a confirmed finding (corrupted cross-chain
execution / permission / value), severity per impact×likelihood. A mismatch in a
purely cosmetic field is Low/Informational. Report the encoding-conformance root
cause distinctly even if an adjacent bounds-check or value-binding finding already
touches the same encoder.

## Step Execution Checklist (MANDATORY)

| # | Step | Required | Done? | Notes |
|---|------|----------|-------|-------|
| 0 | Serialization SCHEME matches destination VM native format (NOT `abi.encode` for a Borsh/BCS/Protobuf/script target, unless the consumer `abi.decode`s it) | YES | ✓/✗/? | scheme-level; GATES rows 2-6 |
| 1 | Enumerated all EVM→non-EVM serialization surfaces | YES | ✓/✗/? | |
| 2 | Per-field width verified vs destination layout | YES | ✓/✗/? | flag-bit/pubkey-width corruption |
| 3 | Cursor-advance == true field width | YES | ✓/✗/? | overlap of following field |
| 4 | Byte order / endianness verified | YES | ✓/✗/? | |
| 5 | Field count/order matches destination + decoder | YES | ✓/✗/? | struct layout |
| 6 | Consequence traced to permission/address/amount/recipient | YES | ✓/✗/? | severity driver |

## Integration Point
Appended (via the driver's mechanical skill injection) to the cross-chain/encoding
breadth agent and depth-external when `NON_EVM_TARGET` is set. Not always-on — only
for protocols that serialize for a foreign VM.
