import * as Network from 'expo-network';

export const BRIDGE_PORT = 8765;
const DISCOVERY_TIMEOUT_MS = 450;
const DISCOVERY_CONCURRENCY = 24;

// Keep legacy fixed endpoints as quick fallbacks. In the normal hotspot setup
// PC2 receives a DHCP address, so the phone's own subnet scan below remains the
// primary discovery path.
export const KNOWN_BRIDGE_HOSTS = ['10.0.1.41', '192.168.88.88'];

export type DiscoveryResult = {
  url: string;
  ip: string;
};

function isIpv4(value: string): boolean {
  const parts = value.split('.');
  return parts.length === 4 && parts.every((part) => {
    const number = Number(part);
    return /^\d+$/.test(part) && number >= 0 && number <= 255;
  });
}

export function subnetCandidates(ip: string): string[] {
  if (!isIpv4(ip)) return [];
  const octets = ip.split('.');
  const prefix = octets.slice(0, 3).join('.');
  const ownHost = Number(octets[3]);
  // Probe common gateway/PC addresses first, then the rest of the /24.
  const preferred = [1, 2, 10, 20, 40, 41, 42, 100, 101, 200, 254];
  const hosts = [...preferred, ...Array.from({ length: 254 }, (_, index) => index + 1)]
    .filter((host, index, all) => host !== ownHost && all.indexOf(host) === index);
  return hosts.map((host) => `${prefix}.${host}`);
}

function probeBridge(ip: string, clientId: string): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    let socket: WebSocket | null = null;
    const finish = (found: boolean) => {
      if (settled) return;
      settled = true;
      if (socket && socket.readyState !== WebSocket.CLOSED) socket.close();
      resolve(found);
    };
    const timer = setTimeout(() => finish(false), DISCOVERY_TIMEOUT_MS);
    try {
      socket = new WebSocket(`ws://${ip}:${BRIDGE_PORT}`);
      socket.onopen = () => {
        socket?.send(JSON.stringify({ type: 'hello', protocol_version: 1, client_id: clientId }));
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(String(event.data)) as { type?: string; protocol_version?: number };
          if (message.type === 'hello_ack' && message.protocol_version === 1) {
            clearTimeout(timer);
            finish(true);
          }
        } catch {
          // A non-bridge service on the port is not a discovery match.
        }
      };
      socket.onerror = () => {
        clearTimeout(timer);
        finish(false);
      };
      socket.onclose = () => {
        clearTimeout(timer);
        finish(false);
      };
    } catch {
      clearTimeout(timer);
      finish(false);
    }
  });
}

export async function discoverBridge(): Promise<DiscoveryResult | null> {
  const localIp = await Network.getIpAddressAsync();
  const candidates = [...KNOWN_BRIDGE_HOSTS, ...subnetCandidates(localIp)]
    .filter((ip, index, all) => all.indexOf(ip) === index);
  if (!candidates.length) return null;
  const clientId = `discovery-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  let cursor = 0;
  let found: DiscoveryResult | null = null;
  const worker = async () => {
    while (!found) {
      const index = cursor++;
      if (index >= candidates.length) return;
      const ip = candidates[index];
      if (await probeBridge(ip, clientId)) {
        found = { ip, url: `ws://${ip}:${BRIDGE_PORT}` };
        return;
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(DISCOVERY_CONCURRENCY, candidates.length) }, worker));
  return found;
}
