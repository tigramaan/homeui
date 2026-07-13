import { getExtensions } from '@/extensions/api';
import { type CustomMenuItem } from '@/stores/ui/types';
import { request } from '@/utils/request';

export const getMenu = async () => request.get<CustomMenuItem[]>('/ui/menu')
  .then(({ data }) => data)
  .catch(() => []);

export const getExtensionMenuItems = async (): Promise<CustomMenuItem[]> => getExtensions()
  .then((extensions) => extensions.map((extension) => ({
    id: extension.id,
    title: extension.title,
    url: `/${extension.route}`,
  })))
  .catch(() => []);
