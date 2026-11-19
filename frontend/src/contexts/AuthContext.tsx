import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { getStoredAuthToken, notifyAuthExpired, onAuthExpired, setStoredAuthToken } from '@/lib/auth';
import type { AuthContextValue, AuthTokenResponse, AuthUserResponse, CurrentUserResponse, UserProfile } from '@/types';

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const USER_STORAGE_KEY = 'sqrl-user-profile';
const API_BASE = (import.meta.env.VITE_BACKEND_API_BASE_URL || "/api").replace(/\/$/, '');

function buildAvatarSeed(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.charAt(0) ?? 'S';
  const second = parts[1]?.charAt(0) ?? parts[0]?.charAt(1) ?? 'Q';
  return `${first}${second}`.toUpperCase();
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
    avatarSeed: input.avatarSeed ?? buildAvatarSeed(input.name),
  };
}

function mapAuthUser(user: AuthUserResponse, previous?: UserProfile | null): UserProfile {
  const displayName = user.full_name?.trim() || user.username;
  return {
    id: user.id,
    username: user.username,
    name: displayName,
    email: user.email,
    role: user.username === 'admin' ? 'Administrator' : 'Workspace member',
    joinedAt: user.created_at,
    // A profile edit should not look like a fresh sign-in, so keep the
    // timestamp we already have when one is available.
    lastLoginAt: previous?.lastLoginAt ?? new Date().toISOString(),
    avatarSeed: buildAvatarSeed(displayName),
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

  if (res.status === 401) notifyAuthExpired();

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    let message = 'Authentication request failed.';
    if (typeof body.detail === 'string') {
      message = body.detail;
    } else if (Array.isArray(body.detail)) {
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
  const userRef = useRef<UserProfile | null>(user);

  useEffect(() => {
    userRef.current = user;
  }, [user]);

  useEffect(() => onAuthExpired(() => {
    setStoredAuthToken(null);
    setUser(null);
    writeUser(null);
  }), []);

  useEffect(() => {
    const token = getStoredAuthToken();
    if (!token) return;

    void authRequest<CurrentUserResponse>('/auth/me')
      .then((response) => {
        const nextUser = mapAuthUser(response.user, userRef.current);
        setUser(nextUser);
        writeUser(nextUser);
      })
      .catch(() => {
        // The cached profile is stale if /auth/me can't confirm it. Clear it
        // so ProtectedRoute doesn't render an authenticated shell.
        setStoredAuthToken(null);
        setUser(null);
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

  const updateProfile = useCallback(async ({ name, email }: { name?: string; email?: string }) => {
    const body: Record<string, string> = {};
    if (name !== undefined) body.full_name = name.trim();
    if (email !== undefined) body.email = email.trim();

    const response = await authRequest<CurrentUserResponse>('/auth/me', {
      method: 'PATCH',
      body: JSON.stringify(body),
    });

    const nextUser = mapAuthUser(response.user, userRef.current);
    setUser(nextUser);
    writeUser(nextUser);
    return nextUser;
  }, []);

  const changePassword = useCallback(
    async ({ currentPassword, newPassword }: { currentPassword: string; newPassword: string }) => {
      const response = await authRequest<AuthTokenResponse>('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });

      // Every session was revoked server-side, so adopt the replacement token
      // before the next request or this client would be signed out.
      setStoredAuthToken(response.access_token);
      const nextUser = mapAuthUser(response.user, userRef.current);
      setUser(nextUser);
      writeUser(nextUser);
    },
    [],
  );

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
    () => ({ user, isAuthenticated: user !== null, login, signup, updateProfile, changePassword, logout }),
    [user, login, signup, updateProfile, changePassword, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider');
  return ctx;
}