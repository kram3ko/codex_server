const UUID_BYTES = 16;
const VERSION_INDEX = 6;
const VARIANT_INDEX = 8;
const VERSION_4 = 0x40;
const VARIANT_RFC4122 = 0x80;
const HEX_RADIX = 16;

function withRfc4122Bits(byte: number, index: number): number {
  if (index === VERSION_INDEX) {
    return (byte & 0x0f) | VERSION_4;
  }
  if (index === VARIANT_INDEX) {
    return (byte & 0x3f) | VARIANT_RFC4122;
  }
  return byte;
}

// crypto.randomUUID() exists only in a secure context; the UI is also served
// over plain http on the LAN, where getRandomValues() is still available.
export function randomUuid(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(UUID_BYTES));
  const hex = Array.from(bytes, (byte, index) =>
    withRfc4122Bits(byte, index).toString(HEX_RADIX).padStart(2, "0")
  ).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
