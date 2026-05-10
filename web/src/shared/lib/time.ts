import type { Timestamp } from "@bufbuild/protobuf";

export function formatTime(timestamp?: Timestamp): string {
  if (!timestamp) {
    return "";
  }
  return timestamp.toDate().toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}
