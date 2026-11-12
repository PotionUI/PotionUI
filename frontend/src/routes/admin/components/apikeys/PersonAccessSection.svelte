<script lang="ts">
	import { onMount } from 'svelte';
	import * as adminApi from '$lib/services/admin-api';
	import { DetailSection } from '$lib/components/detail';
	import Icon from '$lib/components/Icon.svelte';
	import { Input, Pagination, Spinner } from '$lib/components/ui';
	import type { UserKeyCounts } from '$lib/services/api/apiKeys';
	import LoadErrorState from '$lib/components/ui/LoadErrorState.svelte';
	import OwnerAvatar from './OwnerAvatar.svelte';
	import UserApiAccessSwitches from './UserApiAccessSwitches.svelte';
	import { pageOfPeople, personKeysText, searchPeople } from './userAccess';

	type Person = { id: string; username: string; email: string };

	let people = $state<Person[]>([]);
	let loading = $state(true);
	let failed = $state(false);
	let query = $state('');
	let requested = $state(1);
	let counts = $state<Record<string, UserKeyCounts | undefined>>({});

	const matches = $derived(searchPeople(people, query));
	const current = $derived(pageOfPeople(matches, requested));

	async function load() {
		loading = true;
		failed = false;
		try {
			const response = await adminApi.getUsers();
			if (response.success && response.data) people = response.data.map((user) => ({ id: user.id, username: user.username, email: user.email }));
			else failed = true;
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	onMount(load);
</script>

<DetailSection label="API access per person">
	<div data-testid="person-access">
		{#if loading}
			<div class="flex items-center justify-center py-8"><Spinner /></div>
		{:else if failed}
			<LoadErrorState message="The people could not be loaded." onRetry={load} retrying={loading} compact />
		{:else}
			<div class="relative">
				<span class="pointer-events-none absolute inset-y-0 left-3 flex items-center text-fg-subtle"><Icon name="search" className="h-4 w-4" /></span>
				<Input
					type="search"
					class="pl-9 text-sm"
					placeholder="Search people"
					aria-label="Search people"
					autocomplete="off"
					bind:value={query}
					oninput={() => (requested = 1)}
				/>
			</div>
			<div class="mt-3 divide-y divide-line overflow-hidden rounded border border-line">
				{#each current.rows as person (person.id)}
					<div class="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5 sm:px-4" data-person={person.id}>
						<div class="flex min-w-0 flex-1 basis-56 items-center gap-3">
							<OwnerAvatar name={person.username} />
							<div class="min-w-0">
								<p class="truncate text-sm font-medium text-fg">{person.username}</p>
								<p class="truncate text-sm text-fg-subtle">{person.email}</p>
							</div>
						</div>
						<p class="w-24 font-mono text-sm tabular-nums text-fg-muted" data-person-keys={person.id}>{personKeysText(counts[person.id])}</p>
						<UserApiAccessSwitches userId={person.id} name={person.username} onChanged={(next) => (counts = { ...counts, [person.id]: next.keys })} />
					</div>
				{:else}
					<p class="px-3 py-6 text-center text-sm text-fg-subtle">No people match</p>
				{/each}
			</div>
			{#if current.pages > 1}
				<div class="mt-3 flex justify-end">
					<Pagination currentPage={current.page} totalPages={current.pages} size="sm" onPageChange={(next) => (requested = next)} />
				</div>
			{/if}
		{/if}
	</div>
</DetailSection>
