import { persistedStore } from './persistedStore';

export const mediaGroupByDay = persistedStore<boolean>('media-group-by-day', (raw) => raw !== 'false', true);
