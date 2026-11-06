import { getContext, setContext } from 'svelte';
import { api } from '$lib/services/api';
import { createOrganizeApi, INSTANCE_ORGANIZE_BASE, type OrganizeApi } from '$lib/services/api/organize';
import type { OrganizeScope } from '$lib/types/organize';

const KEY = Symbol('organize-scope');

let instanceApi: OrganizeApi | null = null;

export function provideOrganizeScope(scope: OrganizeScope): void {
	setContext(KEY, scope);
}

export function organizeScope(): OrganizeScope {
	return getContext<OrganizeScope | undefined>(KEY) ?? 'user';
}

export function organizeApiFor(scope: OrganizeScope): OrganizeApi {
	if (scope === 'user') return api;
	instanceApi ??= createOrganizeApi(api.getClient(), INSTANCE_ORGANIZE_BASE);
	return instanceApi;
}

export function useOrganizeApi(): OrganizeApi {
	return organizeApiFor(organizeScope());
}
