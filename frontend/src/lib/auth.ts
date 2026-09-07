const AUTH_TOKEN_KEY = 'sqrl-auth-token';
const AUTH_EXPIRED_EVENT = 'sqrl-auth-expired';

export function getStoredAuthToken(): string | null {
  if (typeof window === 'undefined') return null;
  return window.localStorage.getItem(AUTH_TOKEN_KEY);
}

export function setStoredAuthToken(token: string | null): void {
  if (typeof window === 'undefined') return;
  if (token) {
    window.localStorage.setItem(AUTH_TOKEN_KEY, token);
    return;
  }
  window.localStorage.removeItem(AUTH_TOKEN_KEY);
}

export function notifyAuthExpired(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
}

export function onAuthExpired(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined;
  window.addEventListener(AUTH_EXPIRED_EVENT, listener);
  return () => window.removeEventListener(AUTH_EXPIRED_EVENT, listener);
}

export function authHeaders(headers: HeadersInit = {}): HeadersInit {
  const token = getStoredAuthToken();
  if (!token) return headers;
  return {
    ...(headers as Record<string, string>),
    Authorization: `Bearer ${token}`,
  };
}
