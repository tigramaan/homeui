import { request } from '@/utils/request';
import type { ExtensionApiClient, ExtensionManifest, ExtensionRequestInit } from './types';

let extensionCache: ExtensionManifest[] | null = null;
let extensionRequest: Promise<ExtensionManifest[]> | null = null;

export const getExtensions = async () => {
  if (extensionCache !== null) {
    return extensionCache;
  }
  if (extensionRequest) {
    return extensionRequest;
  }
  extensionRequest = request.get<ExtensionManifest[]>('/api/extensions')
    .then(({ data }) => {
      extensionCache = Array.isArray(data) ? data : [];
      return extensionCache;
    })
    .catch(() => [])
    .finally(() => {
      extensionRequest = null;
    });
  return extensionRequest;
};

export const resetExtensionsCache = () => {
  extensionCache = null;
  extensionRequest = null;
};

export const isExtensionsCacheReady = () => extensionCache !== null;

export const findExtensionByRoute = async (route: string) => {
  const extensions = await getExtensions();
  return extensions.find((extension) => extension.route === route);
};

export const makeExtensionApiClient = (extensionId: string): ExtensionApiClient => {
  const call = async <T = unknown>(path: string, init: ExtensionRequestInit = {}): Promise<T> => {
    const normalizedPath = normalizeExtensionPath(path);
    const response = await fetch(`/api/extensions/${extensionId}${normalizedPath}`, {
      ...init,
      credentials: 'include',
      headers: {
        'Accept-Language': getLanguage(),
        'Content-Type': 'application/json',
        ...(init.headers || {}),
      },
    });
    if (!response.ok) {
      throw new Error(`Extension API failed with HTTP ${response.status}`);
    }
    const contentType = response.headers.get('content-type') || '';
    if (response.status === 204) {
      return undefined as T;
    }
    return contentType.includes('application/json')
      ? response.json() as Promise<T>
      : response.text() as Promise<T>;
  };

  return {
    request: call,
    get: (path) => call(path, { method: 'GET' }),
    post: (path, body) => call(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
    put: (path, body) => call(path, { method: 'PUT', body: JSON.stringify(body ?? {}) }),
    patch: (path, body) => call(path, { method: 'PATCH', body: JSON.stringify(body ?? {}) }),
    delete: (path) => call(path, { method: 'DELETE' }),
  };
};

const getLanguage = () => {
  if (typeof localStorage === 'undefined') {
    return 'ru';
  }
  return localStorage.getItem('language') || 'ru';
};

const normalizeExtensionPath = (path: string) => {
  if (!path || path.includes('://') || path.includes('..')) {
    throw new Error('Invalid extension API path');
  }
  return path.startsWith('/') ? path : `/${path}`;
};
