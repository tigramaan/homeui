import { observer } from 'mobx-react-lite';
import { Component, lazy, type PropsWithChildren, Suspense, useEffect } from 'react';
import { useLoaderData } from 'react-router-dom';
import { Alert } from '@/components/alert';
import { Loader } from '@/components/loader';
import i18n from '@/i18n/config';
import { authStore, UserRole } from '@/stores/auth';
import { uiStore } from '@/stores/ui';
import { findExtensionByRoute, makeExtensionApiClient } from './api';
import type { ExtensionManifest, ExtensionModule } from './types';

export const extensionLoader = async ({ params }) => {
  const route = `integrations/${(params['*'] || '').replace(/\/+$/, '')}`;
  const manifest = await findExtensionByRoute(route);
  if (!manifest) {
    throw new Response('', { status: 404 });
  }
  if (manifest.contractVersion !== 1) {
    throw new Response('', { status: 409 });
  }
  return manifest;
};

export const ExtensionPage = () => {
  const manifest = useLoaderData() as ExtensionManifest;

  return <ExtensionHost manifest={manifest} />;
};

export const ExtensionHost = observer(({ manifest }: { manifest: ExtensionManifest }) => {
  const Component = lazy(() => import(/* @vite-ignore */ manifest.entry) as Promise<ExtensionModule>);
  const title = manifest.title[i18n.language as 'ru' | 'en'] || manifest.title.ru || manifest.title.en;

  useEffect(() => {
    uiStore.setCurrentPageTitle(title);
  }, [title]);

  return (
    <ExtensionBoundary>
      <Suspense fallback={<Loader />}>
        <Component
          api={makeExtensionApiClient(manifest.id)}
          i18n={i18n}
          theme={uiStore.theme}
          role={authStore.userRole}
          isAdmin={authStore.hasRights(UserRole.Admin)}
          contractVersion={manifest.contractVersion}
        />
      </Suspense>
    </ExtensionBoundary>
  );
});

export class ExtensionBoundary extends Component<PropsWithChildren, { hasError: boolean }> {
  constructor(props: PropsWithChildren) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return <ExtensionLoadError />;
    }
    return this.props.children;
  }
}

const ExtensionLoadError = () => (
  <Alert variant="danger">
    {i18n.t('extensions.load-error')}
  </Alert>
);
