import type { BreakerState } from "../api/client";

const STATE_LABEL: Record<BreakerState, string> = {
  closed: "Closed",
  open: "Open",
  half_open: "Half-open",
  unknown: "Unknown",
};

export function breakerStateLabel(state: BreakerState): string {
  switch (state) {
    case "closed":
    case "open":
    case "half_open":
    case "unknown":
      return STATE_LABEL[state];
    default: {
      const _exhaustive: never = state;
      return _exhaustive;
    }
  }
}

export function breakerStateClass(state: BreakerState): string {
  switch (state) {
    case "closed":
      return "breaker-closed";
    case "open":
      return "breaker-open";
    case "half_open":
      return "breaker-half-open";
    case "unknown":
      return "breaker-unknown";
    default: {
      const _exhaustive: never = state;
      return _exhaustive;
    }
  }
}

interface Props {
  state: BreakerState;
}

export function BreakerStateBadge({ state }: Props) {
  return (
    <span className={`badge ${breakerStateClass(state)}`}>
      {breakerStateLabel(state)}
    </span>
  );
}
