<script lang="ts">
	import { onMount } from 'svelte';
	import { DetailSection } from '$lib/components/detail';
	import { Button, IconButton } from '$lib/components/ui';
	import { getUserGroups } from '$lib/services/admin-api';
	import { MAX_HOUSE_FORMATS, SETTING_KEY, addFormat, asFormats, ratioOk, removeFormat } from './expandFormats';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();

	let formats = $derived(asFormats(settings[SETTING_KEY]));
	let groups = $state<Array<{ id: string; name: string }>>([]);
	let label = $state('');
	let ratio = $state('');
	let group = $state('');

	let valid = $derived(label.trim().length > 0 && ratioOk(ratio) && formats.length < MAX_HOUSE_FORMATS);

	onMount(() => {
		void getUserGroups()
			.then((response) => {
				if (response.success && response.data) groups = response.data.map((entry) => ({ id: entry.id, name: entry.name }));
			})
			.catch(() => (groups = []));
	});

	function nameOf(id: string | null): string {
		if (!id) return 'Everyone';
		return groups.find((entry) => entry.id === id)?.name ?? 'A group';
	}

	function add() {
		if (!valid) return;
		onSettingChange(SETTING_KEY, addFormat(formats, label, ratio, group || null));
		label = '';
		ratio = '';
	}
</script>

<DetailSection label="Expand formats" padded={false}>
	<div class="px-4 sm:px-5 py-4 flex flex-col gap-4" data-expand-formats>
		<p class="text-sm text-fg-muted">
			Named formats the Image editor's Expand task offers next to Square, Portrait, Story, Landscape and Wide banner.
			A format for a group is only offered to its members.
		</p>
		{#if formats.length}
			<ul class="divide-y divide-line rounded border border-line">
				{#each formats as format (format.id)}
					<li class="flex items-center gap-3 px-3 py-2 text-sm" data-expand-format-row={format.id}>
						<span class="min-w-0 flex-1 truncate text-fg">{format.label}</span>
						<span class="flex-none font-mono tabular-nums text-fg-muted">{format.ratio}</span>
						<span class="w-32 flex-none truncate text-fg-subtle">{nameOf(format.group_id)}</span>
						<IconButton icon="trash" label="Remove {format.label}" onclick={() => onSettingChange(SETTING_KEY, removeFormat(formats, format.id))} />
					</li>
				{/each}
			</ul>
		{/if}
		<div class="flex flex-wrap items-end gap-3">
			<label class="flex min-w-40 flex-1 flex-col gap-1 text-sm text-fg-muted">
				Name
				<input class="input h-8" maxlength="40" bind:value={label} placeholder="Team banner" data-expand-format-name />
			</label>
			<label class="flex w-28 flex-col gap-1 text-sm text-fg-muted">
				Ratio
				<input class="input h-8 font-mono tabular-nums" bind:value={ratio} placeholder="5:2" aria-invalid={ratio !== '' && !ratioOk(ratio)} data-expand-format-ratio />
			</label>
			<label class="flex w-44 flex-col gap-1 text-sm text-fg-muted">
				Offered to
				<select class="input h-8" bind:value={group} data-expand-format-group>
					<option value="">Everyone</option>
					{#each groups as entry (entry.id)}
						<option value={entry.id}>{entry.name}</option>
					{/each}
				</select>
			</label>
			<Button size="sm" disabled={!valid} onclick={add} attrs={{ 'data-expand-format-add': '' }}>Add format</Button>
		</div>
	</div>
</DetailSection>
