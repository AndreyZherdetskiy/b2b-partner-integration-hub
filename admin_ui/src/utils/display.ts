const SENSITIVE_KEYS = /secret|password|token|signing|api_key|authorization/i;
const MAX_PAYLOAD_CHARS = 1200;

export function maskPayloadValue(key: string, value: unknown): unknown {
  if (SENSITIVE_KEYS.test(key)) {
    return "***";
  }
  if (typeof value === "object" && value !== null) {
    if (Array.isArray(value)) {
      return value.map((item, index) => maskPayloadValue(String(index), item));
    }
    const obj = value as Record<string, unknown>;
    const masked: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj)) {
      masked[k] = maskPayloadValue(k, v);
    }
    return masked;
  }
  return value;
}

export function formatPayload(payload: Record<string, unknown>): string {
  const masked = maskPayloadValue("payload", payload) as Record<string, unknown>;
  const text = JSON.stringify(masked, null, 2);
  if (text.length <= MAX_PAYLOAD_CHARS) {
    return text;
  }
  return `${text.slice(0, MAX_PAYLOAD_CHARS)}\n… truncated (${text.length} chars total)`;
}

export async function copyText(text: string): Promise<void> {
  await navigator.clipboard.writeText(text);
}
