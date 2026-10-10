"""The squeeze of WoWBridge's long messages (0.9.9; addon Codec.lua Squeeze, Link.lua): when the WELCOME said z=1, a
message that takes two frames or more goes as TYPE_ZIP when that takes fewer. Its body (after the type byte) is blocks,
each a 2-byte big-endian length (high bit: the block is stored as it is) and that many bytes, squeezed in LZ4's block
format unless stored; the blocks unpacked and joined are the message as the addon had it, its own type byte first.

LZ4 block format: a token (literal length << 4 | match length - 4; 15 says more follows: bytes of 255 and a last one
below 255), the literals, the match's offset back (2 bytes, low first), and so on; the last token has literals only.
squeeze_block() is the addon's encoder byte for byte (the tests hold the two to it): greedy, one candidate per 4-byte hash
in a table of HASH slots. It needs no bit operations, which the client's Lua 5.1 makes calls of: about 0.2-0.3 ms per KB
in the game for text at a half to a fifth of its size."""
BLOCK = 4096        # bytes the addon squeezes at a time (Codec.SQUEEZE_BLOCK)
HASH = 4096
STORED = 0x8000
MIN_MATCH = 4


def _run(out, r):
    """a length past 15: bytes of 255, then the rest"""
    while r >= 255:
        out.append(255)
        r -= 255
    out.append(r)


def squeeze_block(data):
    """one block in LZ4's block format, as Codec.Squeeze makes it"""
    n = len(data)
    seen = [0] * HASH                    # 1-based positions, 0: none (as the addon's base makes the old ones)
    out = bytearray()
    i, anchor = 1, 1
    while i <= n - 3:
        b1, b2, b3, b4 = data[i - 1], data[i], data[i + 1], data[i + 2]
        h = (b1 * 7919 + b2 * 263 + b3 * 31 + b4) % HASH
        cand = seen[h]
        seen[h] = i
        if cand >= 1 and data[cand - 1:cand + 3] == data[i - 1:i + 3]:
            m = MIN_MATCH
            while i + m <= n and data[cand + m - 1] == data[i + m - 1]:
                m += 1
            lit, ml = i - anchor, m - MIN_MATCH
            out.append(min(lit, 15) << 4 | min(ml, 15))
            if lit >= 15:
                _run(out, lit - 15)
            out += data[anchor - 1:i - 1]
            off = i - cand
            out += bytes((off & 255, off >> 8))
            if ml >= 15:
                _run(out, ml - 15)
            i += m
            anchor = i
        else:
            i += 1
    lit = n - anchor + 1
    out.append(min(lit, 15) << 4)
    if lit >= 15:
        _run(out, lit - 15)
    out += data[anchor - 1:]
    return bytes(out)


def _length(z, i, v):
    """(length, next index): v, and when v is 15 the bytes after it added up to the first below 255"""
    if v == 15:
        while True:
            if i >= len(z):
                raise ValueError("a length runs past the block")
            b = z[i]
            i += 1
            v += b
            if b != 255:
                break
    return v, i


def unsqueeze_block(z):
    """the bytes an LZ4 block holds; ValueError when it does not add up"""
    out, i = bytearray(), 0
    while i < len(z):
        tok = z[i]
        lit, i = _length(z, i + 1, tok >> 4)
        if i + lit > len(z):
            raise ValueError("literals run past the block")
        out += z[i:i + lit]
        i += lit
        if i >= len(z):
            break
        if i + 2 > len(z):
            raise ValueError("an offset is cut")
        off = z[i] | z[i + 1] << 8
        m, i = _length(z, i + 2, tok & 15)
        m += MIN_MATCH
        start = len(out) - off
        if off == 0 or start < 0:
            raise ValueError("a match points before the start")
        if off >= m:
            out += out[start:start + m]
        else:                            # overlapping: byte by byte
            for k in range(m):
                out.append(out[start + k])
    return bytes(out)


def squeeze(body, block=BLOCK):
    """a body as the addon sends it after TYPE_ZIP: its blocks, squeezed unless that saves nothing"""
    out = bytearray()
    for at in range(0, len(body), block):
        b = body[at:at + block]
        z = squeeze_block(b)
        if len(z) < len(b):
            out += len(z).to_bytes(2, "big") + z
        else:
            out += (STORED | len(b)).to_bytes(2, "big") + b
    return bytes(out)


def unsqueeze(data):
    """the message a TYPE_ZIP body holds; ValueError when its blocks do not add up"""
    out, i = bytearray(), 0
    while i < len(data):
        if i + 2 > len(data):
            raise ValueError("a block's length is cut")
        head = data[i] << 8 | data[i + 1]
        n = head & ~STORED & 0xFFFF
        chunk = data[i + 2:i + 2 + n]
        if len(chunk) != n:
            raise ValueError("a block is cut")
        i += 2 + n
        out += chunk if head & STORED else unsqueeze_block(chunk)
    return bytes(out)
