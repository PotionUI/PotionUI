import { writable } from 'svelte/store';
import type { KitNotice } from '$lib/kits/types';

export const kitNotices = writable<KitNotice[]>([]);
