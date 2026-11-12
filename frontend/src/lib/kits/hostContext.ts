import { readable, type Readable } from 'svelte/store';

export const KIT_ROWS_HOSTED_CONTEXT_KEY = Symbol('kit-rows-hosted');

export const NOT_HOSTED: Readable<boolean> = readable(false);
