import type { ComponentType } from 'react';
import type i18n from '@/i18n/config';
import type { UserRole } from '@/stores/auth';

export interface ExtensionRequestInit {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
}

export interface ExtensionManifest {
  id: string;
  route: string;
  title: {
    ru: string;
    en: string;
  };
  entry: string;
  minimumWriteRole: UserRole;
  contractVersion: 1;
}

export interface ExtensionApiClient {
  request<T = unknown>(path: string, init?: ExtensionRequestInit): Promise<T>;
  get<T = unknown>(path: string): Promise<T>;
  post<T = unknown>(path: string, body?: unknown): Promise<T>;
  put<T = unknown>(path: string, body?: unknown): Promise<T>;
  patch<T = unknown>(path: string, body?: unknown): Promise<T>;
  delete<T = unknown>(path: string): Promise<T>;
}

export interface ExtensionModuleContext {
  api: ExtensionApiClient;
  i18n: typeof i18n;
  theme: string;
  role: UserRole | null;
  isAdmin: boolean;
  contractVersion: 1;
}

export type ExtensionComponent = ComponentType<ExtensionModuleContext>;

export interface ExtensionModule {
  default: ExtensionComponent;
}
