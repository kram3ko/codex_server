import { timestampDate, type Timestamp } from "@bufbuild/protobuf/wkt";

export function formatTime(timestamp?: Timestamp): string {
  if (!timestamp) {
    return "";
  }
  return timestampDate(timestamp).toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}
