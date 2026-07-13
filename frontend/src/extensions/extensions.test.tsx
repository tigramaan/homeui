/* @vitest-environment happy-dom */

import { screen, waitFor } from '@testing-library/react';
import { routes } from '@/router/routes';
import { authStore, UserRole } from '@/stores/auth';
import { getExtensionMenuItems } from '@/stores/ui/api';
import { render } from '@/test/render';
import { ExtensionBoundary, ExtensionHost } from './extension-page';

const mocks = vi.hoisted(() => ({
  requestGet: vi.fn(),
}));

vi.mock('@/utils/request', () => ({
  request: {
    get: (...args) => mocks.requestGet(...args),
  },
}));

vi.mock('@/common/constants', () => ({
  APP_NAME: 'HomeUI',
  APP_SHORT_NAME: 'HomeUI',
  HIDE_COMPACT_MENU: false,
  LOGO: '/logo.svg',
  LOGO_COMPACT: '/logo-compact.svg',
}));

describe('HomeUI extensions frontend contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authStore.userRole = undefined;
  });

  test('registers bounded integration extension route without replacing existing routes', () => {
    const root = routes.find((route) => route.path === '/');
    const integrations = root?.children?.find((route) => route.path === 'integrations');

    expect(integrations?.children?.some((route) => route.path === 'alice')).toBe(true);
    expect(integrations?.children?.some((route) => route.path === '*')).toBe(true);
  });

  test('builds navigation items from extension titles and declared routes', async () => {
    mocks.requestGet.mockResolvedValueOnce({
      data: [{
        id: 'umec',
        route: 'integrations/umec',
        title: { ru: 'УМЭК', en: 'UMEC' },
        entry: '/extensions/umec/entry.js',
        minimumWriteRole: 'admin',
      }],
    });

    const items = await getExtensionMenuItems();

    expect(items).toEqual([{
      id: 'umec',
      title: { ru: 'УМЭК', en: 'UMEC' },
      url: '/integrations/umec',
    }]);
  });

  test('extension error boundary renders localized load error', () => {
    const Broken = () => {
      throw new Error('boom');
    };

    render(
      <ExtensionBoundary>
        <Broken />
      </ExtensionBoundary>,
    );

    expect(screen.getByText('Extension page failed to load')).toBeInTheDocument();
  });

  test('passes admin rendering context to lazy extension modules', async () => {
    authStore.userRole = UserRole.Admin;
    const entry = `data:text/javascript,${encodeURIComponent(`
      export default function Extension(props) {
        return props.isAdmin ? 'admin' : 'read-only';
      }
    `)}`;

    render(
      <ExtensionHost
        manifest={{
          id: 'umec',
          route: 'integrations/umec',
          title: { ru: 'УМЭК', en: 'UMEC' },
          entry,
          minimumWriteRole: UserRole.Admin,
        }}
      />,
    );

    await waitFor(() => expect(screen.getByText('admin')).toBeInTheDocument());
  });

  test('passes read-only rendering context to lazy extension modules', async () => {
    authStore.userRole = UserRole.User;
    const entry = `data:text/javascript,${encodeURIComponent(`
      export default function Extension(props) {
        return props.isAdmin ? 'admin' : 'read-only';
      }
    `)}`;

    render(
      <ExtensionHost
        manifest={{
          id: 'umec',
          route: 'integrations/umec',
          title: { ru: 'УМЭК', en: 'UMEC' },
          entry,
          minimumWriteRole: UserRole.Admin,
        }}
      />,
    );

    await waitFor(() => expect(screen.getByText('read-only')).toBeInTheDocument());
  });
});
