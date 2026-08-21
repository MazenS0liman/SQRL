const AUTH_TOKEN_KEY = 'sqrl-auth-token';

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

export function authHeaders(headers: HeadersInit = {}): HeadersInit {
  const token = getStoredAuthToken();
  if (!token) return headers;
  return {
    ...(headers as Record<string, string>),
    Authorization: `Bearer ${token}`,
  };
}
