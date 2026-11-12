import { writable, type Readable } from 'svelte/store';
import type { FieldConfig } from '$lib/form/reactions';
import { fromComponentValue } from '$lib/utils/presetFormOverrides';

export interface MissingModelField {
	name: string;
	label: string;
}

export interface PublishedMissingModel {
	key: string;
	missing: MissingModelField | null;
}

function isEmptyTagsValue(value: unknown): boolean {
	if (value == null) return true;
	if (typeof value === 'string') return value.trim() === '';
	if (Array.isArray(value)) return value.length === 0;
	if (typeof value === 'object') {
		return Object.values(value as Record<string, unknown>).every(
			(entry) => !Array.isArray(entry) || entry.length === 0
		);
	}
	return false;
}

export function missingRequiredModelField(
	fields: FieldConfig[],
	formData: Record<string, unknown> | null | undefined,
	unverified: ReadonlySet<string> = new Set()
): MissingModelField | null {
	const data = formData ?? {};

	for (const field of fields ?? []) {
		if (field.visible === false) continue;

		if (field.name && field.type === 'model' && field.required && !field.readonly) {
			const wireValue = fromComponentValue(field.type, data[field.name]);
			if (typeof wireValue !== 'string' || wireValue.trim() === '' || unverified.has(field.name)) {
				return { name: field.name, label: field.title || field.name };
			}
		}

		if (field.name && field.type === 'tags' && field.required && !field.readonly) {
			if (isEmptyTagsValue(data[field.name])) {
				return { name: field.name, label: field.title || field.name };
			}
		}

		if (Array.isArray(field.children)) {
			const nested = missingRequiredModelField(field.children, data, unverified);
			if (nested) return nested;
		}
	}

	return null;
}

export function missingModelMessage(missing: MissingModelField): string {
	return `Select ${missing.label} to generate`;
}

const published = writable<Record<string, PublishedMissingModel>>({});

export const missingModelByTab: Readable<Record<string, PublishedMissingModel>> = {
	subscribe: published.subscribe
};

export function publishMissingModel(tabId: string, key: string, missing: MissingModelField | null): void {
	published.update((current) => {
		const previous = current[tabId];
		if (previous && previous.key === key && previous.missing?.name === missing?.name) return current;
		return { ...current, [tabId]: { key, missing } };
	});
}

export function clearMissingModel(tabId: string): void {
	published.update((current) => {
		if (!(tabId in current)) return current;
		const { [tabId]: _removed, ...rest } = current;
		return rest;
	});
}

export function missingModelFor(
	byTab: Record<string, PublishedMissingModel>,
	tabId: string,
	key: string
): MissingModelField | null {
	const entry = byTab[tabId];
	return entry && entry.key === key ? entry.missing : null;
}
