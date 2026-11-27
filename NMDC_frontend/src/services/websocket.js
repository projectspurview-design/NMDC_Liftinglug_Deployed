import {normalizeBase} from "./api";

/**
 * The Hook Detection service pushes the annotated frame and current state as JPEG
 * over /ws (about 10 messages per second).
 */
export function websocketUrl(base) {
  const b = normalizeBase(base);
  if (b.startsWith("https://")) return `wss://${b.slice("https://".length)}/ws`;
  if (b.startsWith("http://")) return `ws://${b.slice("http://".length)}/ws`;
  if (b.startsWith("wss://") || b.startsWith("ws://")) return `${b}/ws`;
  return `ws://${b}/ws`;
}

/**
 * Open a Hook Detection stream. Returns the raw WebSocket so callers can close it.
 * `onClose` always fires, including on connection failure, so callers can fall
 * back to polling or schedule a reconnect.
 */
export function connectWS(base, {onMessage, onOpen, onClose} = {}) {
  const socket = new WebSocket(websocketUrl(base));
  socket.onopen = () => onOpen?.();
  socket.onmessage = (event) => {
    try {
      onMessage?.(JSON.parse(event.data));
    } catch {
      // Ignore malformed frames rather than tearing down the stream.
    }
  };
  socket.onclose = () => onClose?.();
  socket.onerror = () => {
    // The browser follows up with onclose, which owns the reconnect logic.
  };
  return socket;
}
