import { readable, type Readable } from 'svelte/store';

export interface ResourceNumbering {
	positionFor(field: string, itemKey: string): number | null;
}

export const RESOURCE_NUMBERING_CONTEXT_KEY = Symbol('resource-numbering');

export const EMPTY_RESOURCE_NUMBERING: Readable<ResourceNumbering | null> = readable(null);
