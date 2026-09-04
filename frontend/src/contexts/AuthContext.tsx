import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { getStoredAuthToken, setStoredAuthToken } from '@/lib/auth';

export interface UserProfile {
  id: string;
  username: string;
  name: string;
  email: string;
  role: string;
  joinedAt: string;
  lastLoginAt: string;
  avatarSeed: string;
}

interface AuthContextValue {
  user: UserProfile | null;
  isAuthenticated: boolean;
  login: (input: { username: string; password: string }) => Promise<UserProfile>;
  signup: (input: { username: string; password: string; name: string; email: string }) => Promise<UserProfile>;
  logout: () => Promise<void>;
}

interface AuthUserResponse {
  id: string;
  username: string;
  email: string;
  full_name?: string | null;
  created_at: string;
}

interface AuthTokenResponse {
  access_token: string;
  user: AuthUserResponse;
}

interface CurrentUserResponse {
  user: AuthUserResponse;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const USER_STORAGE_KEY = 'sqrl-user-profile';
const API_BASE = (import.meta.env.VITE_BACKEND_API_BASE_URL || "/api").replace(/\/$/, '');

function buildAvatarSeed(username: string, name: string): string {
  return `${username.trim().charAt(0) || 'S'}${name.trim().charAt(0) || 'Q'}`.toUpperCase();
}

function normalizeUser(input: Partial<UserProfile> | null): UserProfile | null {
  if (!input?.id || !input?.username || !input?.name || !input?.email) return null;
  return {
    id: input.id,
    username: input.username,
    name: input.name,
    email: input.email,
    role: input.role ?? 'Workspace member',
    joinedAt: input.joinedAt ?? new Date().toISOString(),
    lastLoginAt: input.lastLoginAt ?? new Date().toISOString(),
    avatarSeed: input.avatarSeed ?? buildAvatarSeed(input.username, input.name),
  };
}

function mapAuthUser(user: AuthUserResponse): UserProfile {
  const displayName = user.full_name?.trim() || user.username;
  return {
    id: user.id,
    username: user.username,
    name: displayName,
    email: user.email,
    role: user.username === 'admin' ? 'Administrator' : 'Workspace member',
    joinedAt: user.created_at,
    lastLoginAt: new Date().toISOString(),
    avatarSeed: buildAvatarSeed(user.username, displayName),
  };
}

function writeUser(user: UserProfile | null): void {
  if (typeof window === 'undefined') return;
  if (user) {
    window.localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(user));
    return;
  }
  window.localStorage.removeItem(USER_STORAGE_KEY);
}

async function authRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getStoredAuthToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...(init.headers || {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    let message = 'Authentication request failed.';
    if (typeof body.detail === 'string') {
      message = body.detail;
    } else if (Array.isArray(body.detail)) {
      // FastAPI/pydantic validation error shape
      message = body.detail
        .map((e: any) => `${(e.loc ?? []).slice(1).join('.')}: ${e.msg}`)
        .join('; ');
    }
    throw new Error(message);
  }

  return (await res.json()) as T;
}

function getInitialUser(): UserProfile | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(USER_STORAGE_KEY);
    if (!raw) return null;
    return normalizeUser(JSON.parse(raw) as Partial<UserProfile>);
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const [user, setUser] = useState<UserProfile | null>(getInitialUser);

  useEffect(() => {
    const token = getStoredAuthToken();
    if (!token || user) return;

    void authRequest<CurrentUserResponse>('/auth/me')
      .then((response) => {
        const nextUser = mapAuthUser(response.user);
        setUser(nextUser);
        writeUser(nextUser);
      })
      .catch(() => {
        setStoredAuthToken(null);
        writeUser(null);
      });
  }, []);

  useEffect(() => {
    writeUser(user);
  }, [user]);

  const login = useCallback(async ({ username, password }: { username: string; password: string }) => {
    const response = await authRequest<AuthTokenResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ identifier: username.trim(), password }),
    });

    const nextUser = mapAuthUser(response.user);
    setStoredAuthToken(response.access_token);
    setUser(nextUser);
    writeUser(nextUser);
    return nextUser;
  }, []);

  const signup = useCallback(async ({ username, password, name, email }: { username: string; password: string; name: string; email: string }) => {
    const response = await authRequest<AuthTokenResponse>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        username: username.trim(),
        password,
        email: email.trim(),
        full_name: name.trim(),
      }),
    });

    const nextUser = mapAuthUser(response.user);
    setStoredAuthToken(response.access_token);
    setUser(nextUser);
    writeUser(nextUser);
    return nextUser;
  }, []);

  const logout = useCallback(async () => {
    try {
      const token = getStoredAuthToken();
      if (token) {
        await authRequest('/auth/logout', { method: 'POST' });
      }
    } finally {
      setStoredAuthToken(null);
      setUser(null);
      writeUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, isAuthenticated: user !== null, login, signup, logout }),
    [user, login, signup, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider');
  return ctx;
}