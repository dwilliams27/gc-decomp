#!/usr/bin/env python3
"""Compare byte-level match for committed baseline vs O2+SR+prop_off best."""

TARGET_HEX = [
    "7C0802A6","90010004","9421FFD0","BF410018","8383002C","83E30028","881C0014","28000003",
    "418200C4","28000000","40820028","807C0018","2C030013","40800010","38030001","901C0018",
    "48000074","38000001","981C0014","48000068","28000002","40820060","809C0018","2C04001D",
    "40800010","38040001","901C0018","48000048","38000004","981C0014","8363002C","481370E9",
    "3BA00000","57A0103A","3B5B0000","3BDD0000","7F7B0214","807B001C","481370CD","3BBD0001",
    "93DA001C","2C1D0002","3B7B0004","3B5A0004","4180FFE4","809C0018","3C004330","C822C988",
    "387F0000","6C848000","90810014","90010010","C8010010","EC200828","48116721","7FE3FB78",
    "48117785","BB410018","80010034","38210030","7C0803A6","4E800020",
]

# O2+SR+prop_off best (from two_var_ref output: lwz r27... same as ud_after_bl)
O2SR_HEX = [
    "7C0802A6","90010004","9421FFD0","BF410018","8383002C","83E30028","881C0014","28000003",
    "418200C4","28000000","40820028","807C0018","2C030013","40800010","38030001","901C0018",
    "48000068","38000001","981C0014","48000068","28000002","40820060","809C0018","2C04001D",
    "40800010","38040001","901C0018","48000048","38000004","981C0014","8363002C","48000001",
    "3B400000","5740103A","7FDB0214","3B7A0000","3BBE0000","807E001C","48000001","3B5A0001",
    "937D001C","2C1A0002","3BDE0004","3BBD0004","4180FFE4","809C0018","3C004330","C8200000",
    "387F0000","6C848000","90810014","90010010","C8010010","EC200828","48000001","7FE3FB78",
    "48000001","BB410018","80010034","38210030","7C0803A6","4E800020",
]

# Baseline committed (63 insns, has extra mr)
BASELINE_HEX = [
    "7C0802A6","90010004","9421FFD0","BF410018","8383002C","83E30028","881C0014","28000003",
    "418200C4","28000000","40820028","807C0018","2C030013","40800010","38030001","901C0018",
    "4800006C","38000001","981C0014","4800006C","28000002","40820064","809C0018","2C04001D",
    "40800010","38040001","901C0018","4800004C","38000004","981C0014","83C3002C","7FDBF378",
    "48000001","3B400000","5740103A","3BBB0000","7FDEB214","3B600000","807E001C","48000001",
    "3B5A0001","937D001C","2C1A0002","3BDE0004","3BBD0004","4180FFE4","809C0018","3C004330",
    "C8200000","387F0000","6C848000","90810014","90010010","C8010010","EC200828","48000001",
    "7FE3FB78","48000001","BB410018","80010034","38210030","7C0803A6","4E800020",
]

target_bytes = bytes.fromhex(''.join(TARGET_HEX))

def byte_match(compiled_hex, name):
    compiled_bytes = bytes.fromhex(''.join(compiled_hex))

    # Byte-level SequenceMatcher comparison
    from difflib import SequenceMatcher
    sm = SequenceMatcher(None, target_bytes, compiled_bytes)
    ratio = sm.ratio() * 100

    # Simple byte identity (positional)
    min_len = min(len(target_bytes), len(compiled_bytes))
    matching = sum(1 for i in range(min_len) if target_bytes[i] == compiled_bytes[i])
    positional = matching / max(len(target_bytes), len(compiled_bytes)) * 100

    print(f"{name}:")
    print(f"  Instructions: {len(compiled_hex)} (target: {len(TARGET_HEX)})")
    print(f"  Bytes: {len(compiled_bytes)} (target: {len(target_bytes)})")
    print(f"  Byte-level SequenceMatcher: {ratio:.1f}%")
    print(f"  Positional byte identity: {matching}/{max(len(target_bytes), len(compiled_bytes))} = {positional:.1f}%")

    # Instruction-level exact match (ignoring branch relocations)
    exact = 0
    for i in range(min(len(compiled_hex), len(TARGET_HEX))):
        if compiled_hex[i] == TARGET_HEX[i]:
            exact += 1
        elif (int(compiled_hex[i],16) >> 26) in (16,18) and (int(compiled_hex[i],16) >> 26) == (int(TARGET_HEX[i],16) >> 26):
            exact += 1
    print(f"  Instruction exact match: {exact}/{len(TARGET_HEX)}")
    print()

byte_match(BASELINE_HEX, "Baseline (committed, O4,p, 63 insns)")
byte_match(O2SR_HEX, "O2+SR+prop_off (62 insns, addi for zero)")
