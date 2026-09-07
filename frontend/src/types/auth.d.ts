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
