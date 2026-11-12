<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Menu from '$lib/components/ui/Menu.svelte';
	import { Input } from '$lib/components/ui';
	import { filterUsers, type UserPickerUser } from '$lib/components/ui/userPicker';

	let {
		users,
		selectedId,
		onSelect
	}: {
		users: UserPickerUser[];
		selectedId: string | null;
		onSelect: (id: string | null) => void;
	} = $props();

	let query = $state('');

	const current = $derived(users.find((user) => user.id === selectedId) ?? null);
	const shown = $derived(filterUsers(users, query));
</script>

<Menu label="Filter by owner" width={300} align="left" gap={6} onclose={() => (query = '')}>
	{#snippet trigger({ open, toggle })}
		<button
			type="button"
			aria-haspopup="menu"
			aria-expanded={open}
			data-testid="owner-filter"
			class="flex min-h-9 min-w-0 max-w-full items-center gap-2 rounded border border-field-border bg-field-bg px-3 text-sm text-fg transition-colors hover:border-line-hover"
			onclick={toggle}
		>
			<Icon name="user" className="h-4 w-4 flex-shrink-0 text-fg-subtle" />
			<span class="truncate">{current ? current.username : 'All people'}</span>
			<Icon name="chevron-down" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
		</button>
	{/snippet}
	{#snippet children({ close })}
		<div class="px-1 pb-1.5">
			<Input
				type="search"
				class="h-8 text-sm"
				placeholder="Search people"
				aria-label="Search people"
				bind:value={query}
				autocomplete="off"
			/>
		</div>
		<div class="max-h-64 overflow-y-auto">
			{#if !query.trim()}
				<button
					type="button"
					role="menuitemradio"
					aria-checked={!current}
					class="flex w-full items-center gap-2.5 rounded px-2.5 py-2 text-left text-sm hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none"
					onclick={() => {
						close(true);
						onSelect(null);
					}}
				>
					<span class="flex h-4 w-4 flex-shrink-0 items-center justify-center text-signal">
						{#if !current}<Icon name="check" className="h-4 w-4" />{/if}
					</span>
					<span class="font-medium text-fg">All people</span>
				</button>
			{/if}
			{#each shown as user (user.id)}
				<button
					type="button"
					role="menuitemradio"
					aria-checked={current?.id === user.id}
					data-owner-option={user.id}
					class="flex w-full items-center gap-2.5 rounded px-2.5 py-2 text-left text-sm hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none"
					onclick={() => {
						close(true);
						onSelect(user.id);
					}}
				>
					<span class="flex h-4 w-4 flex-shrink-0 items-center justify-center text-signal">
						{#if current?.id === user.id}<Icon name="check" className="h-4 w-4" />{/if}
					</span>
					<span class="min-w-0 flex-1">
						<span class="block truncate font-medium text-fg">{user.username}</span>
						{#if user.email}<span class="block truncate text-fg-subtle">{user.email}</span>{/if}
					</span>
				</button>
			{/each}
			{#if shown.length === 0}
				<p class="px-3 py-4 text-center text-sm text-fg-subtle">No people match</p>
			{/if}
		</div>
	{/snippet}
</Menu>
